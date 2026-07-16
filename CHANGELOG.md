# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-07-17

### Added
- **Serverless image processing pipeline**: S3 upload → SQS → Lambda → processed S3 bucket
- **Pre-signed URL handler** (`handler.presigned_url_handler`): direct client-to-S3 upload without routing binary through API Gateway, bypassing the 10 MB payload ceiling
- **PIL image transformations**: resize to configurable max dimensions (default 1280×1280), JPEG re-encode at quality 85, semi-transparent watermark overlay
- **SQS dead-letter queue**: failed records routed after 3 receive attempts with 14-day retention
- **IAM least-privilege policy**: `s3:GetObject` on source bucket only; `s3:PutObject` on processed bucket only; scoped CloudWatch Logs access
- **Security guardrails**: PIL DecompressionBomb cap at 50 MP; 20 MB raw-byte ceiling before PIL is invoked; file extension allowlist on pre-signed URL handler
- **Terraform IaC**: full infrastructure definition with S3 remote state support, DynamoDB lock table, versioning, lifecycle policies, and encryption at rest
- **Local development stack**: LocalStack 3.x via Docker Compose; boto3-based deploy script (`scripts/deploy_local.py`) with auto-detected bridge IP
- **Test suite**: 137 unit + integration tests, all offline (no real AWS calls); chaos stress suite (10 adversarial scenarios)
- **CI pipeline**: GitHub Actions — dependency audit (`pip-audit`), unit tests, mock boto3 patching
- **Live integration test runner** (`scripts/integration_test.py`): 6 end-to-end tests against LocalStack, 6/6 pass record documented in `docs/internal/System-Result.md`
- **Vendor strategy**: manylinux2014\_x86\_64 packages bundled via `scripts/build_vendor.sh`; zero Lambda layer dependency
- **Documentation**: architecture contract, dependency audit, internal PR reviews, deployment notes

### Security
- Confused-deputy attack prevention: Lambda rejects SQS events whose bucket name does not match the configured source bucket
- Infinite-loop prevention: Lambda IAM policy never grants `s3:PutObject` on the source bucket
- Input sanitization: PIL format detection (not filename extension) determines whether bytes are a valid image

---

## [Unreleased]

### Planned for v1.1.0
- WebP output format flag
- Thumbnail variant generation (3 sizes per upload)
- HEIC / WebP input format support
- Cost projection dashboard (`make cost-report`)
