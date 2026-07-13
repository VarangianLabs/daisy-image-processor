"""
Unit tests for src/config.py — runtime configuration loader.

🧪 TEST STRATEGY & MATRIX
--------------------------
Framework : pytest (stdlib-only, no AWS mocking needed).
Strategy  : Isolate each load_config() call by temporarily overriding
            os.environ, then reloading the module so module-level caches
            do not bleed between cases.

Coverage targets
  - Required variable validation (SOURCE_BUCKET, PROCESSED_BUCKET)
  - Optional variable defaults (AWS_REGION, SQS_QUEUE_URL, AWS_ENDPOINT_URL)
  - Dataclass immutability (frozen=True)
  - Empty-string treatment for required vs. optional fields

⚠️ EDGE CASES & VULNERABILITY VECTORS
---------------------------------------
  - Absent required var → must raise EnvironmentError (not silently use None)
  - Empty string required var → falsy, must also raise EnvironmentError
  - Empty SQS_QUEUE_URL → normalised to None (optional field; empty ≠ set)
  - Config mutation attempts → must raise because dataclass is frozen=True
"""

import importlib
import os

import pytest


# ── Reload helper ─────────────────────────────────────────────────────────────

def _load_with_env(**overrides) -> object:
    """
    Call load_config() with a controlled environment.

    ``overrides`` are layered on top of a minimal valid baseline.
    Pass a key with value ``None`` to delete that variable entirely.
    """
    import config as _cfg

    baseline = {
        "SOURCE_BUCKET": "test-source",
        "PROCESSED_BUCKET": "test-processed",
    }
    baseline.update(overrides)

    saved = {}
    try:
        for key, val in baseline.items():
            saved[key] = os.environ.get(key)
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

        importlib.reload(_cfg)
        return _cfg.load_config()
    finally:
        # Restore original env state to avoid test pollution.
        for key, original in saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        importlib.reload(_cfg)


# ── Happy-path tests ──────────────────────────────────────────────────────────

class TestLoadConfigHappyPath:

    def test_source_bucket_stored_correctly(self):
        cfg = _load_with_env(SOURCE_BUCKET="my-source")
        assert cfg.source_bucket == "my-source"

    def test_processed_bucket_stored_correctly(self):
        cfg = _load_with_env(PROCESSED_BUCKET="my-processed")
        assert cfg.processed_bucket == "my-processed"

    def test_aws_region_defaults_to_us_east_1_when_absent(self):
        cfg = _load_with_env(AWS_REGION=None)
        assert cfg.aws_region == "us-east-1"

    def test_aws_region_custom_value_persisted(self):
        cfg = _load_with_env(AWS_REGION="eu-west-2")
        assert cfg.aws_region == "eu-west-2"

    def test_sqs_queue_url_absent_yields_none(self):
        cfg = _load_with_env(SQS_QUEUE_URL=None)
        assert cfg.sqs_queue_url is None

    def test_sqs_queue_url_present(self):
        url = "https://sqs.us-east-1.amazonaws.com/123456/daisy-queue"
        cfg = _load_with_env(SQS_QUEUE_URL=url)
        assert cfg.sqs_queue_url == url

    def test_aws_endpoint_url_absent_yields_none(self):
        cfg = _load_with_env(AWS_ENDPOINT_URL=None)
        assert cfg.aws_endpoint_url is None

    def test_aws_endpoint_url_localstack(self):
        cfg = _load_with_env(AWS_ENDPOINT_URL="http://localhost:4566")
        assert cfg.aws_endpoint_url == "http://localhost:4566"

    def test_config_is_a_frozen_dataclass(self):
        """frozen=True means any attribute assignment must raise."""
        cfg = _load_with_env()
        with pytest.raises((AttributeError, TypeError)):
            cfg.source_bucket = "mutation-attempt"  # type: ignore[misc]

    def test_config_instance_is_hashable(self):
        """Frozen dataclasses are hashable by default — verify no regression."""
        cfg = _load_with_env()
        assert hash(cfg) is not None


# ── Guard-rail / EnvironmentError tests ───────────────────────────────────────

class TestLoadConfigMissingRequiredVars:

    def test_missing_source_bucket_raises_environment_error(self):
        with pytest.raises(EnvironmentError, match="SOURCE_BUCKET"):
            _load_with_env(SOURCE_BUCKET=None)

    def test_empty_source_bucket_raises_environment_error(self):
        """An empty string is falsy and must be treated as absent."""
        with pytest.raises(EnvironmentError, match="SOURCE_BUCKET"):
            _load_with_env(SOURCE_BUCKET="")

    def test_missing_processed_bucket_raises_environment_error(self):
        with pytest.raises(EnvironmentError, match="PROCESSED_BUCKET"):
            _load_with_env(PROCESSED_BUCKET=None)

    def test_empty_processed_bucket_raises_environment_error(self):
        with pytest.raises(EnvironmentError, match="PROCESSED_BUCKET"):
            _load_with_env(PROCESSED_BUCKET="")


# ── Optional field normalisation ──────────────────────────────────────────────

class TestOptionalFieldNormalisation:

    def test_empty_sqs_queue_url_normalised_to_none(self):
        """
        SQS_QUEUE_URL="" must be coerced to None — an empty URL string is
        semantically equivalent to 'not configured'.
        """
        cfg = _load_with_env(SQS_QUEUE_URL="")
        assert cfg.sqs_queue_url is None

    def test_whitespace_only_sqs_queue_url_normalised_to_none(self):
        """
        INF-01 fix: a whitespace-only SQS_QUEUE_URL must be stripped and
        normalised to None, identical to an absent variable.
        """
        cfg = _load_with_env(SQS_QUEUE_URL="   ")
        assert cfg.sqs_queue_url is None

    def test_whitespace_only_aws_endpoint_url_normalised_to_none(self):
        """
        INF-01 companion: AWS_ENDPOINT_URL follows the same normalisation rule
        so a whitespace-only value is never forwarded to boto3 as an endpoint.
        """
        cfg = _load_with_env(AWS_ENDPOINT_URL="   ")
        assert cfg.aws_endpoint_url is None
