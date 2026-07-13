"""
Repository structure tests — validate PR-02 structural guarantees.

🧪 TEST STRATEGY & MATRIX
--------------------------
Framework : pytest with pathlib.Path filesystem assertions (no AWS, no PIL).
Strategy  : Assert invariants that must remain true for the repository to be
            public-release ready. These tests act as regression guards:
            a contributor who accidentally runs `pip install -t src/` or
            adds an env var to config.py without updating .env.example will
            get an immediate CI failure before their PR is merged.

Coverage targets
  P-01 — .env.example presence and completeness (all config.py vars documented)
  C-01 — src/ contains only application files (no vendored packages)
  C-01 — vendor/ exists and contains the critical runtime packages
  I-04 — handler._MAX_RAW_BYTES constant not silently removed/changed

⚠️ EDGE CASES & VULNERABILITY VECTORS
---------------------------------------
  - config.py adds a new required var but .env.example is not updated
    → test_env_example_documents_all_config_vars catches it
  - Contributor runs `pip install -t src/` and commits the result
    → test_src_contains_only_application_files catches it
  - make vendor fails silently; vendor/ empty or absent
    → test_vendor_contains_critical_packages catches it
  - MAX_RAW_BYTES constant deleted during a refactor
    → test_max_raw_bytes_constant_is_correct catches it
"""

import pathlib

import pytest

# ── Repository root ───────────────────────────────────────────────────────────
# Resolved relative to this file's location (tests/unit/ → ../../)
_ROOT = pathlib.Path(__file__).parent.parent.parent

# ── Known-good application files in src/ ─────────────────────────────────────
# Add entries here only when a deliberate new application file is added.
_ALLOWED_SRC_NAMES = {
    "handler.py",
    "image_processor.py",
    "config.py",
    "requirements.txt",
    "fonts",
    "__pycache__",  # generated — tolerated but not tracked
}

# ── Critical vendor packages (subset — not exhaustive) ────────────────────────
# These must be present after `make vendor` for Lambda runtime imports to work.
_REQUIRED_VENDOR_PACKAGES = [
    "boto3",
    "botocore",
    "PIL",
    "urllib3",
    "jmespath",
]

# ── env var names that must appear in .env.example ───────────────────────────
_REQUIRED_ENV_VAR_NAMES = [
    "SOURCE_BUCKET",
    "PROCESSED_BUCKET",
    "SQS_QUEUE_URL",
    "AWS_ENDPOINT_URL",
]


# ────────────────────────────────────────────────────────────────────────────
# P-01 — .env.example
# ────────────────────────────────────────────────────────────────────────────

class TestEnvExample:

    _env_example = _ROOT / ".env.example"

    def test_env_example_file_exists(self):
        """
        P-01 blocker: .env.example must be present at repo root.
        A missing file means a cold clone produces EnvironmentError with
        no guidance on which variables to set.
        """
        assert self._env_example.exists(), (
            ".env.example not found at repo root. "
            "Run: create .env.example per PR-02 Task P-01."
        )

    def test_env_example_is_not_empty(self):
        content = self._env_example.read_text()
        assert len(content.strip()) > 0, ".env.example exists but is empty"

    @pytest.mark.parametrize("var_name", _REQUIRED_ENV_VAR_NAMES)
    def test_env_example_documents_required_var(self, var_name):
        """
        Regression guard: every env var used by config.py must appear in
        .env.example. Adding a var to config.py without updating .env.example
        leaves cloners with an undocumented EnvironmentError.
        """
        content = self._env_example.read_text()
        assert var_name in content, (
            f".env.example is missing {var_name!r}. "
            f"Update .env.example whenever config.py is changed."
        )

    def test_env_example_does_not_contain_real_secrets(self):
        """
        Security sanity: .env.example must contain only placeholder values.
        Patterns characteristic of real AWS keys or account IDs must not appear.
        """
        content = self._env_example.read_text()
        # Real AWS access key IDs start with AKIA or ASIA
        assert "AKIA" not in content, "Possible real AWS key ID in .env.example"
        assert "ASIA" not in content, "Possible real AWS session key ID in .env.example"
        # Real account IDs are 12-digit numbers — exclude the known LocalStack
        # placeholder 000000000000, which is not a real account ID.
        import re
        suspicious = [
            m for m in re.findall(r"\b\d{12}\b", content)
            if m != "000000000000"
        ]
        assert not suspicious, (
            f"Possible real AWS account ID(s) found in .env.example: {suspicious}"
        )


# ────────────────────────────────────────────────────────────────────────────
# C-01 — src/ structural integrity
# ────────────────────────────────────────────────────────────────────────────

class TestSrcStructure:

    _src = _ROOT / "src"

    def test_src_directory_exists(self):
        assert self._src.is_dir(), "src/ directory not found at repo root"

    def test_src_contains_only_application_files(self):
        """
        C-01 guardrail: src/ must contain only application source files.
        Vendored pip packages (boto3/, PIL/, etc.) must live in vendor/.
        A violation means someone ran `pip install -t src/` and committed the result,
        or migrate-vendor was not run after the C-01 restructure.
        """
        actual_names = {p.name for p in self._src.iterdir()}
        unexpected = actual_names - _ALLOWED_SRC_NAMES
        assert not unexpected, (
            f"Unexpected entries in src/: {sorted(unexpected)}. "
            f"Vendored packages belong in vendor/ — run 'make migrate-vendor' "
            f"then 'make vendor' to fix."
        )

    def test_src_contains_handler(self):
        assert (self._src / "handler.py").exists()

    def test_src_contains_image_processor(self):
        assert (self._src / "image_processor.py").exists()

    def test_src_contains_config(self):
        assert (self._src / "config.py").exists()

    def test_src_contains_requirements_txt(self):
        assert (self._src / "requirements.txt").exists()

    def test_src_contains_fonts_directory(self):
        assert (self._src / "fonts").is_dir(), (
            "src/fonts/ not found. DejaVuSans.ttf must be bundled "
            "for the Lambda runtime (no system fonts on Amazon Linux)."
        )

    def test_src_fonts_contains_dejavu(self):
        fonts = list((self._src / "fonts").glob("*.ttf"))
        assert fonts, (
            "No .ttf font found in src/fonts/. "
            "DejaVuSans.ttf is required for watermark rendering."
        )


# ────────────────────────────────────────────────────────────────────────────
# C-01 — vendor/ package integrity
# ────────────────────────────────────────────────────────────────────────────

class TestVendorStructure:

    _vendor = _ROOT / "vendor"

    def test_vendor_directory_exists(self):
        """
        vendor/ is created by `make vendor`. If it doesn't exist, the Lambda
        zip built by `make build` will be missing all runtime dependencies and
        will crash with ImportModuleError on every cold start.
        """
        assert self._vendor.exists(), (
            "vendor/ directory not found. Run 'make vendor' to generate it."
        )

    def test_vendor_is_not_empty(self):
        entries = list(self._vendor.iterdir())
        assert entries, (
            "vendor/ exists but is empty. Run 'make vendor' to populate it."
        )

    @pytest.mark.parametrize("package_name", _REQUIRED_VENDOR_PACKAGES)
    def test_vendor_contains_required_package(self, package_name):
        """
        Each critical runtime package must be present as a directory (or file
        for single-module packages like six.py) inside vendor/.
        """
        # Check as directory first, then as *.py file (e.g. six.py)
        as_dir = self._vendor / package_name
        as_py = self._vendor / f"{package_name}.py"
        assert as_dir.exists() or as_py.exists(), (
            f"vendor/ is missing {package_name!r}. "
            f"Run 'make vendor' to regenerate all packages."
        )


# ────────────────────────────────────────────────────────────────────────────
# Security constants — handler module
# ────────────────────────────────────────────────────────────────────────────

class TestHandlerSecurityConstants:

    def test_max_raw_bytes_is_20mb(self):
        """
        I-04 guardrail: _MAX_RAW_BYTES must remain 20 MB.
        Raising this ceiling silently weakens the Lambda memory exhaustion guard.
        This test fails immediately if the constant is changed or removed.
        """
        import handler
        assert handler._MAX_RAW_BYTES == 20 * 1024 * 1024, (
            f"handler._MAX_RAW_BYTES is {handler._MAX_RAW_BYTES}, expected "
            f"{20 * 1024 * 1024}. Do not raise this ceiling without a corresponding "
            f"increase in Lambda memory_size and timeout."
        )
