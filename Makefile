# Daisy Image Processor — Makefile
#
# Usage:  make <target>
#         make help        — list all targets with descriptions
#
# Prerequisites (local development):
#   docker, python3.12, pip, zip
#   terraform >= 1.5  (only required for deploy-local-tf)
#
# Typical local workflow:
#   make local-up        ← start LocalStack
#   make vendor          ← download Lambda dependencies
#   make build           ← package src/ + vendor/ into lambda.zip
#   make deploy-local    ← deploy to LocalStack via Python script (fast, idempotent)
#   make test            ← run unit test suite (no Docker or AWS required)
#   make local-down      ← tear down LocalStack

.DEFAULT_GOAL := help
.PHONY: local-up local-down vendor build migrate-vendor deploy-local deploy-local-tf \
        destroy test lint chaos clean help

# Use the project venv if it exists; fall back to system python3.
PYTHON := $(if $(wildcard .venv/bin/python3),.venv/bin/python3,python3)

# ── LocalStack ─────────────────────────────────────────────────────────────────

local-up:  ## Start LocalStack (Docker must be running)
	docker compose up -d

local-down:  ## Stop and remove the LocalStack container
	docker compose down

# ── Lambda Package ─────────────────────────────────────────────────────────────

vendor:  ## Download Lambda runtime packages into vendor/ (Amazon Linux x86_64)
	@bash scripts/build_vendor.sh

build: vendor  ## Build terraform/lambda.zip from src/ + vendor/
	@rm -f terraform/lambda.zip
	@cd src    && zip -r ../terraform/lambda.zip . -x "*.pyc" -x "__pycache__/*" -x "*.pyo" -q
	@cd vendor && zip -r ../terraform/lambda.zip . -x "*.pyc" -x "__pycache__/*" -x "*.pyo" -q
	@echo "✓ Built terraform/lambda.zip ($$(du -sh terraform/lambda.zip | cut -f1))"

migrate-vendor:  ## One-time: remove legacy vendored packages from src/ after C-01 restructure
	@echo "Removing legacy vendored packages from src/..."
	@rm -rf src/boto3          src/boto3-*.dist-info
	@rm -rf src/botocore       src/botocore-*.dist-info
	@rm -rf src/PIL            src/pillow*.dist-info src/pillow.libs
	@rm -rf src/urllib3        src/urllib3-*.dist-info
	@rm -rf src/s3transfer     src/s3transfer-*.dist-info
	@rm -rf src/jmespath       src/jmespath-*.dist-info
	@rm -rf src/dateutil       src/python_dateutil-*.dist-info
	@rm -f  src/six.py
	@rm -rf src/six-*.dist-info src/bin
	@echo "✓ Migration complete. Run 'make vendor' to populate vendor/"

# ── Terraform ──────────────────────────────────────────────────────────────────
# deploy-local uses -backend=false so no S3 remote state bucket is needed for
# LocalStack development. State is held in memory for the session only.
# For persistent state, create terraform/backend_override.tf (gitignored).
# See terraform/DEPLOYMENT-NOTES.md for instructions.

deploy-local: build  ## Build zip then deploy all resources to LocalStack (fast, idempotent)
	@echo "Deploying to LocalStack via Python script..."
	PYTHONPATH=vendor $(PYTHON) scripts/deploy_local.py

deploy-local-tf: build  ## Build zip then deploy via Terraform (requires backend_override.tf)
	@echo "Deploying via Terraform (see terraform/DEPLOYMENT-NOTES.md for backend setup)..."
	terraform -chdir=terraform init \
		-input=false -reconfigure
	terraform -chdir=terraform apply \
		-var-file="local.tfvars" -auto-approve -parallelism=1

destroy:  ## Tear down all LocalStack infrastructure (does not stop the container)
	terraform -chdir=terraform destroy \
		-var-file="local.tfvars" -auto-approve

# ── Quality ────────────────────────────────────────────────────────────────────

test:  ## Run full test suite (no AWS or Docker required; uses vendor/ for PIL)
	PYTHONPATH=src:vendor $(PYTHON) -m pytest tests/ --tb=short -q

lint:  ## Lint src/ and tests/ (ruff preferred, falls back to flake8)
	@$(PYTHON) -m ruff check src/ tests/ 2>/dev/null \
		|| $(PYTHON) -m flake8 src/ tests/ \
		|| echo "Neither ruff nor flake8 found — skipping lint"

chaos:  ## Run the Chaos & Event Simulator stress suite (no AWS or Docker required)
	PYTHONPATH=vendor $(PYTHON) .github/skills/chaos-event-simulator/scripts/run_chaos_suite.py

# ── Housekeeping ───────────────────────────────────────────────────────────────

clean:  ## Remove build artefacts and Python byte-code caches
	@rm -f terraform/lambda.zip
	@find . -type d -name "__pycache__" | grep -v ".venv" | xargs rm -rf 2>/dev/null || true
	@find . \( -name "*.pyc" -o -name "*.pyo" \) | grep -v ".venv" | xargs rm -f 2>/dev/null || true
	@echo "✓ Clean complete"

help:  ## List all available targets
	@echo ""
	@echo "  Daisy Image Processor — Makefile targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
