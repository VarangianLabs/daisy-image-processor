# Contributing to Daisy Image Processor

Thanks for looking at this project. Contributions are welcome — bug fixes, performance improvements, and documentation patches. Feature additions should open an issue for discussion first.

---

## Prerequisites

Verify these are installed and on the correct versions before continuing. All commands assume a native Linux shell (WSL2 or bare Linux).

```bash
python3 --version      # 3.12 or higher
terraform --version    # 1.5 or higher
docker --version       # 24.0 or higher
docker compose version # v2.0 or higher
```

---

## Local Setup

Four steps from a fresh clone to a running local environment:

```bash
# 1. Clone and enter the project
git clone <repo-url> daisy-image-processor && cd daisy-image-processor

# 2. Copy the environment template and review the values
cp .env.example .env

# 3. Create a Python virtual environment and install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt -r tests/requirements-test.txt

# 4. Start LocalStack and deploy
make local-up && make deploy-local
```

After step 4, LocalStack is running on `http://localhost:4566` and the Lambda, SQS queue, and both S3 buckets exist. You can now trigger the pipeline manually via the SQS queue or an S3 upload.

---

## Running Tests

The full test suite runs offline — no AWS credentials, no LocalStack, no Docker required.

```bash
make test
```

Tests mock all AWS SDK calls internally. If you add a feature that requires a new AWS resource or environment variable, add the corresponding mock and environment fixture to `tests/conftest.py`.

---

## Building the Lambda Package

If you change `src/requirements.txt` (add, remove, or pin a dependency), regenerate the vendor tree before building:

```bash
make vendor && make build
```

`make vendor` downloads Amazon Linux (`manylinux2014_x86_64`) binaries. Do not manually copy packages into `vendor/` — version drift between your host platform and the Lambda runtime is the most common cause of `Runtime.ImportModuleError` on deploy.

Never commit the `vendor/` directory. It is gitignored and is always generated from `src/requirements.txt`.

---

## Branch Naming

| Prefix | Use case |
|---|---|
| `feature/` | New functionality |
| `fix/` | Bug fixes |
| `chore/` | Tooling, deps, CI, formatting |
| `docs/` | Documentation only |

Example: `fix/watermark-font-fallback`, `chore/update-pillow-12.4`

---

## Pull Request Checklist

Before opening a PR, confirm:

- [ ] `make test` passes locally (all tests green)
- [ ] `make lint` produces no errors
- [ ] If you changed `src/requirements.txt` — `make vendor && make build` was run
- [ ] If you added a new environment variable — `.env.example` is updated
- [ ] Commit messages are descriptive (`fix: guard against zero-byte SQS body`, not `update`)

CI runs `make test` on every push and PR. A failing CI check blocks merge.
