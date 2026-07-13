# Product-Lead-PR-02 — Open-Source Shipment Review
**Daisy Image Processor · Public Release Readiness Audit**
**Submitted by:** VP of Product & Lead Developer Experience Engineer
**Submitted to:** Principal Architect · Infrastructure Engineer · Advisory Platform Engineer
**Date:** 2026-07-13
**Status:** 🟡 ACTION REQUIRED — Engineering response needed before public release

---

## Executive Briefing

The Daisy Image Processor has passed its internal QA audit (PR-01, 137/137 tests green). The core engineering is sound: IAM scoping is correct, the SQS decoupling pattern is solid, the decompression bomb guard is in place, and the Lambda boundary/processor separation is clean.

This document addresses the next gate: **public release readiness**.

A developer who clones this repository today cannot deploy it. There is no README, no deployment script, no license, no CI pipeline, and the source tree is structurally misleading for contributors. The fixes are not large in scope, but they must be executed in the correct order and by the right people.

The findings below are broken into three ownership tracks — one per engineer. Each section explains **what the problem is, why it matters to an external developer, and exactly what needs to be built**.

---

## 🔴 IMMEDIATE SEVERITY — Pre-Release Blockers

These items must be resolved before any public tagging occurs. They represent either a security risk or a complete abandonment trigger for an adopting developer.

| # | Finding | Owner | Severity |
|---|---------|-------|----------|
| B-01 | `terraform/terraform.tfstate` committed to source control | Principal Architect | 🔴 Critical |
| B-02 | No `README.md` at repository root | Product Lead (me) | 🔴 Critical |
| B-03 | No `LICENSE` file | Product Lead (me) | 🔴 Critical |
| B-04 | Vendored packages unstructured inside `src/` | Infrastructure Engineer | 🔴 High |
| B-05 | No `.env.example` — `EnvironmentError` on first cold run | Advisory Platform Engineer | 🔴 High |

---

## 🟡 PRE-RELEASE REQUIREMENTS — Must ship with v1.0.0

| # | Finding | Owner | Priority |
|---|---------|-------|----------|
| R-01 | No `Makefile` or deploy runbook — no path from clone to running | Infrastructure Engineer | 🟡 High |
| R-02 | No `.github/workflows/ci.yml` — tests never run on push | Infrastructure Engineer | 🟡 High |
| R-03 | No `CONTRIBUTING.md` — contributors cannot onboard | Advisory Platform Engineer | 🟡 High |
| R-04 | `.github/` contains internal AI agent artifacts, not contributor docs | Advisory Platform Engineer | 🟡 Medium |
| R-05 | `tests/PR-01-findings.md` lives inside the test runner directory | Advisory Platform Engineer | 🟡 Low |

---

## 🟢 POST-RELEASE QUALITY — Target v1.1.0

| # | Finding | Owner | Priority |
|---|---------|-------|----------|
| Q-01 | No architecture diagram in any public-facing document | Principal Architect | 🟢 Medium |
| Q-02 | No cost projection table (the product's commercial wedge vs. Cloudinary) | Product Lead (me) | 🟢 Medium |
| Q-03 | No `terraform/README.md` with remote state backend example | Infrastructure Engineer | 🟢 Medium |
| Q-04 | No `CHANGELOG.md` or GitHub Release tag | Product Lead (me) | 🟢 Low |
| Q-05 | No GitHub issue templates or PR template | Advisory Platform Engineer | 🟢 Low |

---

---

# 🏛️ TRACK 1 — Principal Architect

**Your domain:** Security posture, dependency health, architectural documentation, and macro-level structural decisions that govern how the public will understand and trust the codebase.

---

### Task A-01 — Remove `terraform.tfstate` from Source Control (🔴 IMMEDIATE)

**The problem:** `terraform/terraform.tfstate` is committed to the repository. This file contains resource metadata, ARNs, and account-contextual state. In a LocalStack-only context the blast radius is limited, but it establishes a pattern that is actively dangerous. If any contributor runs `terraform apply` against a real AWS account and commits the result, account IDs, resource ARNs, and potentially role data will be in the public git history permanently.

The `.gitignore` already has the correct rule — the file predates or bypassed it.

**What needs to be done:**

1. Remove the file from git tracking without deleting it from disk (it may be needed locally):
   ```bash
   git rm --cached terraform/terraform.tfstate
   git rm --cached terraform/terraform.tfstate.backup 2>/dev/null || true
   ```
2. Verify `.gitignore` at `terraform/` scope covers both patterns:
   ```
   *.tfstate
   *.tfstate.backup
   ```
3. Add a `terraform/README.md` note (delegated to Infrastructure Engineer, Task C-03) that warns contributors explicitly: *"State is local only. For team deployments, configure an S3 remote backend before applying."*
4. Commit the removal as an isolated, clearly-labelled commit: `chore: remove terraform state from version control`.

**Why it matters:** Any corporate adopter's security team will reject a dependency that ships with state files in its history. It signals the project is not production-aware.

---

### Task A-02 — Audit Vendored Dependency Versions (🟡 Pre-Release)

**The problem:** The `src/` directory bundles runtime packages: `boto3`, `botocore`, `urllib3`, `Pillow`, `jmespath`, `s3transfer`, `dateutil`, and `six`. The current pinned versions (from `*.dist-info/METADATA`) need to be verified against known CVEs before the repo goes public.

**What needs to be done:**

1. Run a CVE scan against the bundled versions currently in `src/`:
   ```bash
   pip-audit --requirement src/requirements.txt
   ```
   If `pip-audit` is not installed: `pip install pip-audit`.
2. Pay specific attention to `urllib3` (historically high CVE surface in HTTP handling) and `Pillow` (historically high CVE surface in image parsing).
3. If any vulnerabilities are found at MEDIUM or above, escalate the `requirements.txt` pin to a patched version and trigger a rebuild of the vendored tree (Infrastructure Engineer, Task C-01).
4. Document the scan result as a single line in `CHANGELOG.md` (Product Lead task): *"v1.0.0 — Dependencies audited, no known CVEs at release."*

**Why it matters:** A public image processing library that ships with a known Pillow CVE will be flagged by automated dependency scanners (Dependabot, Snyk) within hours of publication. The GitHub "Security" tab will display a vulnerability alert on the repo landing page.

---

### Task A-03 — Produce `docs/architecture.md` (🟢 Post-Release)

**The problem:** The internal architecture document `daisy.md` is detailed and well-structured, but it is written as an AI orchestration contract, not as a public-facing architecture reference. It includes internal process notes (`[cite: 1]` references, AI cycle descriptions) that would confuse external contributors.

**What needs to be done:**

1. Create `docs/architecture.md` containing:
   - A **Mermaid flowchart** of the complete data path (Client → Pre-signed URL → S3 Source → SQS → Lambda → S3 Processed → CDN). Source the flow from `daisy.md` Section 5.
   - The **three architectural guardrails** (no base64 API ingestion, no source bucket writes, no infinite trigger loops) presented as a numbered constraint list with their technical rationale.
   - The **IAM least-privilege model** — a prose summary of what the Lambda role can and cannot do, referencing `terraform/main.tf` without duplicating the HCL.
2. This document becomes the canonical reference linked from the `README.md` "Architecture" section.

---

---

# 🔧 TRACK 2 — Infrastructure Engineer

**Your domain:** Lambda packaging, Terraform configuration, Docker/LocalStack orchestration, CI pipeline, deployment automation, and the shell-level mechanics that take a developer from `git clone` to a running deployment.

---

### Task C-01 — Separate Vendored Packages from Application Source (🔴 IMMEDIATE)

**The problem:** `src/` currently contains a mix of application code and pip-installed runtime dependencies. A contributor reading the repository cannot identify which files are the product's intellectual property and which are third-party libraries. The cognitive load is immediately disqualifying.

Additionally, there is no script that explains how the vendored packages were generated. If someone rebuilds the Lambda zip from a clean clone, they will not know to run `pip install -r src/requirements.txt -t src/` first — and if they do, they will overwrite the existing vendored copies with potentially different versions.

**What needs to be done:**

1. Create a `vendor/` directory at the repo root for Lambda runtime dependencies. Move all vendored packages there:
   ```
   vendor/
     boto3/
     botocore/
     jmespath/
     s3transfer/
     urllib3/
     dateutil/
     six.py
     *.dist-info/
     PIL/
     pillow.libs/
     bin/
   ```
2. Application source (`src/`) should contain only: `handler.py`, `image_processor.py`, `config.py`, `requirements.txt`, and `fonts/`.
3. Create `scripts/build_vendor.sh`:
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   rm -rf vendor/
   pip install -r src/requirements.txt -t vendor/ --platform manylinux2014_x86_64 \
     --implementation cp --python-version 3.12 --only-binary=:all:
   ```
   The `--platform` flag is critical: Pillow must be the Amazon Linux binary, not the local developer's architecture.
4. Update the Lambda zip build target in the `Makefile` (Task C-02) to include both `src/` and `vendor/` in the archive.

**Why it matters:** This is the most disorienting aspect of the repository for a first-time contributor. Fixing it transforms `src/` from an opaque directory into a readable 5-file module.

---

### Task C-02 — Create `Makefile` with Standard Targets (🟡 Pre-Release)

**The problem:** There is no single command that takes a developer from a clean clone to a running local environment. The steps are implied by the architecture document but never stated as executable commands. A developer must independently know Terraform, Docker, LocalStack, and the Lambda packaging pattern to proceed.

**What needs to be done:**

Create a `Makefile` at the repo root with the following targets:

| Target | Purpose |
|--------|---------|
| `make local-up` | `docker-compose up -d` — boots LocalStack |
| `make local-down` | `docker-compose down` — tears down LocalStack |
| `make vendor` | Runs `scripts/build_vendor.sh` — regenerates vendored packages |
| `make build` | Packages `src/` + `vendor/` into `terraform/lambda.zip` |
| `make deploy-local` | `terraform -chdir=terraform init` + `apply -var-file="local.tfvars" -auto-approve` |
| `make destroy` | `terraform -chdir=terraform destroy -var-file="local.tfvars" -auto-approve` |
| `make test` | `PYTHONPATH=src python3 -m pytest tests/ --tb=short` |
| `make lint` | `ruff check src/ tests/` (or `flake8` if preferred) |

The `build` target must depend on `vendor` to prevent a stale zip from being deployed:
```makefile
build: vendor
    cd src && zip -r ../terraform/lambda.zip . && cd ../vendor && zip -r ../terraform/lambda.zip .
```

**Why it matters:** `make local-up && make build && make deploy-local` is the entire onboarding path. Without this, every developer re-discovers the same sequence independently.

---

### Task C-03 — Create `.github/workflows/ci.yml` (🟡 Pre-Release)

**The problem:** There is no automated check on any push or pull request. The 137-test suite only runs when a developer manually executes it. For a public open-source repository, this means the first community PR could silently break the pipeline.

**What needs to be done:**

Create `.github/workflows/ci.yml` that:
1. Triggers on `push` to `main` and all `pull_request` events.
2. Runs on `ubuntu-latest` using Python 3.12.
3. Installs test dependencies from `tests/requirements-test.txt`.
4. Sets the required environment variables (`SOURCE_BUCKET`, `PROCESSED_BUCKET`) as mock values — the test suite uses mocks and does not require live AWS.
5. Runs `pytest tests/ --tb=short -q`.

The workflow must **not** require AWS credentials, LocalStack, or Docker to pass — the existing test suite is fully mockable and should remain so.

**Why it matters:** The green CI badge on the README landing page is the first signal of a maintained project. Its absence is the first signal of an abandoned one.

---

### Task C-04 — Create `terraform/README.md` (🟢 Post-Release)

Document the Terraform module: variable reference table, the note that `terraform.tfstate` must never be committed, and an S3 remote backend configuration block that teams can copy when moving to a shared deployment.

---

---

# 🧭 TRACK 3 — Advisory Platform Engineer

**Your domain:** Developer onboarding experience, repository hygiene for the WSL/Linux-native environment, concise setup commands, and ensuring the workspace structure communicates clearly to an external contributor landing on this repo for the first time.

---

### Task P-01 — Create `.env.example` at Repo Root (🔴 IMMEDIATE)

**The problem:** The four environment variables required to run this Lambda locally (`SOURCE_BUCKET`, `PROCESSED_BUCKET`, `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`) are defined only inside `src/config.py`. A developer who runs the application without setting them receives a raw Python `EnvironmentError` with no guidance on where the values come from or how to set them.

**What needs to be done:**

Create `.env.example` at the repo root:

```bash
# Daisy Image Processor — Environment Configuration
# Copy this file to .env and fill in the values for your environment.
# For LocalStack (local development): source this file before running Terraform.

# Required: S3 bucket that receives raw uploaded images.
SOURCE_BUCKET=source-images-bucket

# Required: S3 bucket that stores processed output images. NEVER the same as SOURCE_BUCKET.
PROCESSED_BUCKET=processed-images-bucket

# Optional: SQS queue URL. Required for the Lambda handler to consume messages.
SQS_QUEUE_URL=http://localhost:4566/000000000000/image-processing-queue

# Optional: Override AWS endpoint. Set to LocalStack URL for local development.
# Leave blank or unset in production.
AWS_ENDPOINT_URL=http://localhost:4566
```

Verify `.gitignore` has a rule for `.env` (the populated version, not the `.env.example` template). Add if missing.

**Why it matters:** This is the first file a developer reads after cloning. It answers "what do I configure?" without requiring them to read application source code.

---

### Task P-02 — Create `CONTRIBUTING.md` (🟡 Pre-Release)

**The problem:** There is no guide for how to contribute to this project. A developer who wants to submit a fix has no information on: branch naming, how to run tests locally, how to start LocalStack, or what the PR expectations are.

**What needs to be done:**

Create `CONTRIBUTING.md` at the repo root covering:

1. **Prerequisites** (exact Linux-native commands):
   ```bash
   python3 --version          # must be 3.12+
   terraform --version        # must be 1.5+
   docker --version           # must be 24+
   docker compose version     # must be v2+
   ```
2. **Local setup** — four steps: clone, copy `.env.example` to `.env`, `make local-up`, `make deploy-local`.
3. **Running tests** — `make test`. Tests run fully offline with no AWS credentials required.
4. **Branch naming** — `feature/`, `fix/`, `chore/` prefixes.
5. **PR expectations** — tests must pass in CI, no vendored packages modified directly (use `make vendor` to regenerate).

Keep the language peer-advisory in tone: short command blocks, no enterprise formality.

---

### Task P-03 — Restructure `.github/` for Public Audience (🟡 Pre-Release)

**The problem:** The `.github/` directory currently houses internal AI development process artifacts: `copilot-instructions.md`, `Context.md`, `Architects-View-PR-01.md`, this document (`Product-Lead-PR-02.md`), and a `skills/` tree. On a public GitHub repository, the `.github/` directory is surfaced to contributors and is expected to contain only: `ISSUE_TEMPLATE/`, `PULL_REQUEST_TEMPLATE.md`, and `workflows/`. Internal documents create confusion and signal an unpolished project.

**What needs to be done:**

1. Create `docs/internal/` at the repo root.
2. Move the following into `docs/internal/`:
   - `daisy.md` → `docs/internal/architecture-contract.md`
   - `.github/Context.md` → `docs/internal/context.md`
   - `.github/Architects-View-PR-01.md` → `docs/internal/PR-01-architect-view.md`
   - `.github/Product-Lead-PR-02.md` (this document) → `docs/internal/PR-02-product-lead-view.md`
   - `tests/PR-01-findings.md` → `docs/internal/PR-01-qa-findings.md`
3. The `.github/skills/` and `.github/prompts/` trees can remain (they are not rendered by GitHub's UI) but `copilot-instructions.md` should be reviewed — it currently exposes internal agent operational rules that are not useful to external contributors.
4. Create `.github/ISSUE_TEMPLATE/bug_report.md` and `.github/ISSUE_TEMPLATE/feature_request.md` with standard GitHub templates.
5. Create `.github/PULL_REQUEST_TEMPLATE.md` with a checklist: tests passing, `.env.example` updated if env vars changed, `make vendor` run if dependencies changed.

**Why it matters:** A contributor landing on `.github/` and finding `copilot-instructions.md` and `Architects-View-PR-01.md` will conclude this is a personal project not intended for external use. The directory must communicate: *"We accept contributions and here is how."*

---

---

## Execution Order

The following sequence prevents blockers from cascading:

```
Week 1 — Security & Structure
  Principal Architect:    A-01 (remove terraform.tfstate)
  Infrastructure Eng:     C-01 (separate vendor/src)
  Advisory Platform Eng:  P-01 (create .env.example)

Week 2 — Automation & Onboarding
  Infrastructure Eng:     C-02 (Makefile), C-03 (CI workflow)
  Advisory Platform Eng:  P-02 (CONTRIBUTING.md), P-03 (restructure .github/)
  Product Lead:           README.md, LICENSE

Week 3 — Documentation & Release
  Principal Architect:    A-02 (dependency CVE audit), A-03 (docs/architecture.md)
  Infrastructure Eng:     C-04 (terraform/README.md)
  Product Lead:           CHANGELOG.md, v1.0.0 tag, GitHub Release
```

---

## Definition of Done

The repository is considered **public-release ready** when all of the following are true:

- [ ] `git clone && make local-up && make build && make deploy-local` completes without error on a clean Linux machine with no prior project knowledge
- [ ] `make test` passes with `137/137` (or higher) green
- [ ] GitHub Actions CI badge shows green on the `main` branch
- [ ] `terraform.tfstate` is absent from git history or cleanly excluded
- [ ] `LICENSE` file is present at the repo root
- [ ] `README.md` answers: what it is, who it is for, prerequisites, and how to deploy — all within the first screenful
- [ ] `src/` contains only application code (≤ 5 files + `fonts/`)
- [ ] `.env.example` is present and documents all four environment variables

---

*— VP of Product & Lead Developer Experience Engineer*
*Product-Lead-PR-02 · Daisy Image Processor · 2026-07-13*
