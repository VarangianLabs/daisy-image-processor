# Architect's View — PR 01
## Daisy Image Processor: Full-Spectrum Codebase Health Audit

> **Author:** Principal Platform Architect  
> **Date:** 2026-07-13  
> **Status:** Open — Action Required  
> **Scope:** Full codebase — `src/`, `terraform/`, `docker-compose.yml`, `.gitignore`, `.agent/`  
> **Audience:** All engineers contributing to this repository

---

## Foreword

This document is not optional reading. Every finding in this PR represents either a production outage vector, a security exposure, or a maintenance debt that will compound. Before any new feature work proceeds, the issues in the **Immediate** tier must be resolved and merged. The **Hardening** tier items are to be worked in parallel in the same sprint. The **Health** tier items are tracked as backlog stories.

Read this document fully. Then we will work through it together.

---

## Table of Contents

1. [Architecture Snapshot](#1-architecture-snapshot)
2. [Immediate Priority — Production & Security Blockers](#2-immediate-priority--production--security-blockers)
3. [Hardening Priority — Architectural Debt](#3-hardening-priority--architectural-debt)
4. [Health Priority — Code Quality & Observability](#4-health-priority--code-quality--observability)
5. [Fix Assignments & Proposed Diffs](#5-fix-assignments--proposed-diffs)
6. [PR Acceptance Criteria](#6-pr-acceptance-criteria)

---

## 1. Architecture Snapshot

The Daisy Image Processor is a serverless event-driven pipeline with the following data flow:

```
Client
  │
  ▼
presigned_url_handler (Lambda)
  │  returns a 5-min pre-signed S3 PUT URL
  ▼
S3 Source Bucket  ──[ObjectCreated event]──▶  SQS Queue  ──[batch_size=1]──▶  lambda_handler (Lambda)
                                                                                     │
                                                                              process_image()
                                                                             (resize → watermark)
                                                                                     │
                                                                                     ▼
                                                                            S3 Processed Bucket
```

**What the architecture gets right:**
- Clean decoupling: `image_processor.py` has zero AWS imports. Fully unit-testable.
- The SQS buffer correctly absorbs traffic spikes from S3 upload bursts.
- IAM policy follows least-privilege principles: Lambda reads only from Source, writes only to Processed.
- The infinite-loop guardrail (Lambda never writes back to Source Bucket) is correctly enforced at both the IAM policy and code level.
- DLQ is wired to the main queue with a `maxReceiveCount` of 3.
- `source_code_hash` is used on the Lambda resource, ensuring Terraform detects code changes.

**Where the architecture fails:**
There are 6 issues that will cause the system to either not function at all in production, leak data, or be exploitable. They are documented below with full context.

---

## 2. Immediate Priority — Production & Security Blockers

These are non-negotiable. The system cannot go to a real AWS environment in its current state.

---

### I-01 — LocalStack URL Hardcoded in Lambda Environment

**File:** `terraform/main.tf` — Lambda `environment` block  
**Risk Level:** CRITICAL — Silent production outage  

#### The Problem

The Terraform `aws_lambda_function` resource unconditionally injects `AWS_ENDPOINT_URL = "http://localhost:4566"` into the Lambda's environment:

```hcl
environment {
  variables = {
    SOURCE_BUCKET    = var.source_bucket_name
    PROCESSED_BUCKET = var.processed_bucket_name
    SQS_QUEUE_URL    = aws_sqs_queue.image_processing_queue.id
    AWS_ENDPOINT_URL = "http://localhost:4566"   # ← THIS LINE
    ENVIRONMENT      = var.environment
  }
}
```

The `_get_boto3_client()` function in `handler.py` correctly applies this URL conditionally only if it is set:

```python
if config.aws_endpoint_url:
    kwargs["endpoint_url"] = config.aws_endpoint_url
```

However, because Terraform always sets the variable, the condition is always true. Every `get_object` and `put_object` call will be routed to `http://localhost:4566` on a real AWS deployment — a non-existent address — causing every Lambda invocation to fail silently after the TCP timeout.

#### The Fix

Drive the endpoint URL from the `environment` variable so it is only injected for local deployments:

```hcl
AWS_ENDPOINT_URL = var.environment == "local" ? "http://localhost:4566" : ""
```

---

### I-02 — Empty `.gitignore` — State File and Binary Artifacts Unprotected

**File:** `.gitignore` (empty)  
**Risk Level:** CRITICAL — Infrastructure state exposure + repository corruption  

#### The Problem

The `.gitignore` file exists but contains nothing. The following artifacts are sitting unprotected in the working directory right now:

| File / Directory | Why It Is Dangerous |
|---|---|
| `terraform/terraform.tfstate` | Contains account lineage UUIDs, all resource ARNs, S3 hashes |
| `terraform/lambda.zip` | 25MB binary. Commits this once and the git history is permanently bloated |
| `.localstack/` | LocalStack's internal state and service cache |
| `terraform/.terraform/` | Provider binaries, lock file — should never be committed |

One `git init && git add .` from a new team member commits all of this. The tfstate file already on disk contains `lineage: "d5b246e5-7ba7-743a-fbcb-e165abfe2bb6"` and all resource ARNs. This is not theoretical — the file is present.

#### The Fix

Populate `.gitignore` before any git initialization:

```gitignore
# Terraform
terraform/*.tfstate
terraform/*.tfstate.backup
terraform/.terraform/
terraform/lambda.zip
terraform/.terraform.lock.hcl

# LocalStack
.localstack/

# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.egg-info/

# IDE
.vscode/settings.json
```

---

### I-03 — Untrusted `bucket_name` Taken Directly from SQS Payload

**File:** `src/handler.py` — inside `lambda_handler` loop  
**Risk Level:** HIGH — Confused deputy / SSRF-adjacent attack vector  

#### The Problem

The bucket name used for `get_object` is extracted verbatim from the SQS message body — an untrusted input:

```python
body = json.loads(record["body"])
s3_record = body["Records"][0]["s3"]
source_key = urllib.parse.unquote_plus(s3_record["object"]["key"])
bucket_name = s3_record["bucket"]["name"]   # ← from untrusted input

response = s3_client.get_object(Bucket=bucket_name, Key=source_key)
```

An attacker who can inject a message into the SQS queue (e.g., via an IAM misconfiguration or a compromised queue policy) can set `bucket_name` to any value. The Lambda's IAM policy limits what it can read, but the code's intent is clear: it should only ever read from the configured `SOURCE_BUCKET`. That contract is not enforced in code.

#### The Fix

Validate the event's bucket name against the configured `config.source_bucket` at the top of each loop iteration:

```python
if bucket_name != config.source_bucket:
    logger.error(
        "Bucket mismatch: expected '%s', got '%s' — rejecting record",
        config.source_bucket,
        bucket_name,
    )
    failed_keys.append(source_key or "unknown")
    continue
```

---

### I-04 — No Image Size or Decompression Bomb Guards

**File:** `src/handler.py`, `src/image_processor.py`  
**Risk Level:** HIGH — Memory exhaustion / Lambda OOM loop into DLQ  

#### The Problem

There are two distinct failure modes here:

**Failure Mode A — Oversized File**  
`response["Body"].read()` on a 300MB file will read the entire binary into Lambda memory before any size check occurs. With a 512MB memory limit, this leaves almost no headroom for PIL to then decompress and process the image. The Lambda OOMs, the message retries 3 times, then lands permanently in the DLQ.

**Failure Mode B — Decompression Bomb**  
PIL's default `MAX_IMAGE_PIXELS` limit is approximately 178 million pixels. A PNG can compress pixel data at ratios exceeding 100:1. A 1.5MB PNG file can legally represent a 200M pixel image. When PIL decodes it, the uncompressed pixel array can consume several gigabytes — well beyond what the Lambda can hold. The code currently has no protection against this.

#### The Fix

Two independent guards, both required:

```python
# In handler.py — immediately after Body.read()
MAX_RAW_BYTES = 20 * 1024 * 1024  # 20MB hard ceiling
if len(raw_bytes) > MAX_RAW_BYTES:
    logger.error(
        "Rejected s3://%s/%s — file size %d bytes exceeds %d byte limit",
        bucket_name, source_key, len(raw_bytes), MAX_RAW_BYTES,
    )
    failed_keys.append(source_key)
    continue
```

```python
# In image_processor.py — at module level, before any function definitions
from PIL import Image
Image.MAX_IMAGE_PIXELS = 50_000_000  # 50MP ceiling — raise DecompressionBombError beyond this
```

---

### I-05 — `PROCESSED_BUCKET` is Optional but Never Guarded Before Use

**File:** `src/config.py`, `src/handler.py`  
**Risk Level:** MEDIUM-HIGH — Late-stage runtime crash with no diagnostic clarity  

#### The Problem

`load_config()` treats `PROCESSED_BUCKET` as optional:

```python
processed_bucket: str | None  # declared as Optional

processed_bucket=os.environ.get("PROCESSED_BUCKET") or None,  # silently None if absent
```

But `handler.py` uses it unconditionally:

```python
s3_client.put_object(
    Bucket=config.processed_bucket,  # None if env var missing
    ...
)
```

Boto3 will throw a `ParamValidationError: Invalid type for parameter Bucket, value: None`. This error occurs only after the full image has been downloaded and processed — wasting compute time, burning Lambda duration costs, and producing a misleading error message that points at boto3 internals rather than the missing environment variable.

The contract is clear: this service cannot function without a destination bucket. It should be a required variable with the same fail-fast treatment as `SOURCE_BUCKET`.

#### The Fix

```python
# In config.py — load_config()
processed_bucket = os.environ.get("PROCESSED_BUCKET")
if not processed_bucket:
    raise EnvironmentError("Missing required environment variable: PROCESSED_BUCKET")
```

---

### I-06 — No Remote Terraform Backend — State Corruption and No Locking

**File:** `terraform/providers.tf`  
**Risk Level:** HIGH — Data loss / infrastructure corruption in any multi-person workflow  

#### The Problem

There is no `backend` block in `providers.tf`. Terraform writes its state to a local file: `terraform/terraform.tfstate`. The consequences are:

- **No locking.** Two developers running `terraform apply` simultaneously will corrupt the state file. There is no mechanism to prevent this.
- **No recovery.** There are no previous state versions. A botched `apply` is unrecoverable without manually reconstructing state.
- **Not portable.** CI/CD pipelines cannot access local state. Automated deployments are impossible in the current architecture.
- **Already on disk.** The state file is already present in the repo directory, containing live resource metadata, ready to be accidentally committed (see I-02).

#### The Fix

Add a remote backend. For the project's AWS target, S3 + DynamoDB is the standard:

```hcl
terraform {
  backend "s3" {
    bucket         = "daisy-tfstate-store"
    key            = "daisy-image-processor/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "daisy-tfstate-lock"
    encrypt        = true
  }
}
```

For local development, a `local` backend override file can be used without changing the shared configuration. After adding the backend, run `terraform init -migrate-state` to move the existing local state.

---

## 3. Hardening Priority — Architectural Debt

These are scheduled for the current sprint alongside the Immediate items. They do not cause immediate outages but represent design gaps that will create compounding problems under load or at scale.

---

### H-01 — Double Lossy JPEG Compression in the Processing Pipeline

**File:** `src/image_processor.py`  

The `process_image()` pipeline currently encodes to JPEG twice:

```
resize_image():    PIL decode → transform → JPEG encode → bytes
apply_watermark(): JPEG decode → draw → JPEG encode → bytes
```

Each JPEG encode is irreversible lossy compression. The second encode compresses already-compressed data. At `quality=85`, two passes introduce measurable blocking artifacts — particularly visible in gradients and on the watermark text itself. The higher the volume of images processed, the more this matters from a quality-of-service perspective.

The fix is to refactor the internal functions to operate on PIL `Image` objects and perform a single JPEG encode at the pipeline exit:

```python
def process_image(image_bytes: bytes) -> bytes:
    with io.BytesIO(image_bytes) as buf:
        with Image.open(buf) as img:
            img.load()  # Force eager decode before buffer closes
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img = _resize(img)             # returns Image, no encode
            img = _apply_watermark(img)    # returns Image, no encode

    with io.BytesIO() as out:
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()
```

---

### H-02 — boto3 Client Recreated on Every Lambda Invocation

**File:** `src/handler.py`  

Both `lambda_handler` and `presigned_url_handler` call `load_config()` and `_get_boto3_client()` inside the function body. Lambda execution contexts are reused across warm invocations. Module-level initialization persists across those reuses, avoiding TCP connection pool reconstruction on every call.

```python
# Current — runs on every invocation
def lambda_handler(event, context):
    config = load_config()
    s3_client = _get_boto3_client("s3", config)

# Correct — runs once on cold start, reused on warm starts
_config = load_config()
_s3_client = _get_boto3_client("s3", _config)

def lambda_handler(event, context):
    # use _config and _s3_client directly
```

This is a standard Lambda performance pattern. On warm invocations, this eliminates connection setup overhead — typically 20–50ms per call under VPC, more under cross-region routing.

---

### H-03 — `arial.ttf` Will Always Fail on Lambda — Watermark is Non-Functional

**File:** `src/image_processor.py`  

```python
try:
    font = ImageFont.truetype("arial.ttf", font_size)
except (IOError, OSError):
    font = ImageFont.load_default()
```

`arial.ttf` is a proprietary Windows font. It does not exist on Amazon Linux (the Lambda execution environment). This `try` block **always** falls to the `except` branch. `ImageFont.load_default()` returns a fixed-size 10px bitmap font that ignores the calculated `font_size` entirely. The watermark is effectively broken on any image above ~200px wide.

The fix is to bundle a permissively-licensed font:
1. Download `DejaVuSans.ttf` (SIL Open Font License) or `NotoSans-Regular.ttf` (Apache 2.0)
2. Place it in `src/fonts/`
3. Update the reference:

```python
_FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")

try:
    font = ImageFont.truetype(_FONT_PATH, font_size)
except (IOError, OSError):
    font = ImageFont.load_default()
```

---

### H-04 — S3 Buckets Missing All Security Configuration

**File:** `terraform/main.tf`  

Both S3 bucket resources (`aws_s3_bucket.source`, `aws_s3_bucket.processed`) are bare declarations with only `bucket` and `force_destroy`. The following companion resources are absent:

| Missing Resource | Impact |
|---|---|
| `aws_s3_bucket_server_side_encryption_configuration` | Data at rest is stored unencrypted |
| `aws_s3_bucket_public_access_block` | A future misconfigured bucket policy could expose images publicly with no safety net |
| `aws_s3_bucket_versioning` (on processed bucket) | No recovery from accidental overwrite or delete of processed output |
| `aws_s3_bucket_lifecycle_configuration` (on processed bucket) | Processed images accumulate indefinitely, growing storage costs without bound |

This is a single-engineer project today. It will not be tomorrow.

---

### H-05 — Pre-signed URL Handler Accepts Arbitrary File Extensions

**File:** `src/handler.py` — `presigned_url_handler`  

`os.path.basename(filename)` correctly prevents path traversal. It does not validate the file extension. A request for `filename: "payload.php"` or `filename: "shell.exe"` passes through and receives a valid pre-signed S3 PUT URL. While the Lambda's `image_processor.py` will fail when PIL tries to open a PHP file, the object is already in the Source Bucket at that point, and the error cycles through the DLQ.

Additionally, `ContentType` is hardcoded to `"image/jpeg"` regardless of the actual file extension. PNG uploads receive incorrect MIME metadata.

```python
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CONTENT_TYPE_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

ext = os.path.splitext(filename)[1].lower()
if ext not in ALLOWED_EXTENSIONS:
    return {"statusCode": 400, "body": json.dumps({"error": "Unsupported file type"})}

content_type = CONTENT_TYPE_MAP[ext]
```

---

### H-06 — SQS Visibility Timeout Buffer is Too Narrow

**File:** `terraform/main.tf`  

```hcl
visibility_timeout_seconds = var.lambda_timeout + 5
```

With `lambda_timeout = 30`, this sets the visibility timeout to 35 seconds — only 5 seconds of headroom. AWS documentation recommends setting the SQS visibility timeout to **6× the Lambda function timeout** to account for:
- Function cold start initialization time
- Batch retry scenarios where the same message is reprocessed
- Network latency between SQS and Lambda in cross-AZ scenarios

At 35 seconds, a near-timeout Lambda invocation causes the message to re-surface in the queue and be processed a second time before the first invocation has marked it complete.

```hcl
visibility_timeout_seconds = var.lambda_timeout * 6
```

---

### H-07 — IAM CloudWatch Logs Permission Scoped Too Broadly

**File:** `terraform/main.tf` — `aws_iam_policy.lambda_policy`  

```hcl
Resource = "arn:aws:logs:*:*:*"
```

This grants `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents` across every log group in every region in the account. If this role is ever compromised or misconfigured, the blast radius extends to all CloudWatch logs across the account.

Scope it to the Lambda's specific log group:

```hcl
Resource = [
  "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/daisy-image-processor",
  "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/daisy-image-processor:*"
]
```

---

## 4. Health Priority — Code Quality & Observability

These are tracked as backlog stories. They do not block production but represent the difference between a system that can be maintained and one that slowly degrades.

---

### Q-01 — No Unit Tests for `image_processor.py`

The architectural decision to keep `image_processor.py` free of AWS dependencies was made explicitly so it could be unit-tested without an execution context. That contract is currently unfulfilled. There are zero test files in the workspace.

The minimum test surface for `image_processor.py`:
- `resize_image()` shrinks an oversized image within bounds
- `resize_image()` preserves aspect ratio correctly
- `resize_image()` does not upscale images smaller than the bounds
- `apply_watermark()` returns valid JPEG bytes
- `process_image()` completes the full pipeline without raising
- RGBA and P-mode images are converted to RGB without error

---

### Q-02 — Dead Weight in Lambda Deployment Package

The `src/` directory contains:
- `six.py` — a Python 2/3 compatibility shim. Python 3.12 does not need it.
- `src/bin/jp.py` — the JMESPath CLI tool, not a library. Has no role in Lambda execution.

The current Lambda package is **25MB zipped**. Lambda's direct-upload limit is 50MB; beyond that, deployment via S3 is required. Beyond 250MB unzipped, deployment is impossible without container images. Remove these files and extract the heavy vendored packages (`boto3`, `botocore`, `PIL`, `urllib3`) into a Lambda Layer. The function package should be under 5MB.

---

### Q-03 — No DLQ CloudWatch Alarm

Failed messages accumulate silently in `image_processing_dlq`. There is no `aws_cloudwatch_metric_alarm` on `ApproximateNumberOfMessagesVisible`. Processing failures are invisible until someone manually inspects the queue. A single alarm on the DLQ with an SNS notification covers the minimum observability requirement.

---

### Q-04 — `requirements.txt` Uses Unbounded `>=` Constraints

```
boto3>=1.34.0
botocore>=1.34.0
Pillow>=10.0.0
```

Any `pip install -r requirements.txt` in CI can resolve to a breaking new major version. This should be paired with a `pip-compile`-generated lock file (`requirements.lock`) or pinned to exact versions for the deployed package.

---

### Q-05 — `docker-compose.yml` LocalStack Image Not Pinned to Patch Version

```yaml
image: localstack/localstack:3
```

`localstack:3` will pull any 3.x.y release. A patch release that changes the LocalStack API contract for SQS or Lambda will silently break the local dev environment for all team members simultaneously. Pin to a specific tested version: `localstack/localstack:3.8.1`.

---

## 5. Fix Assignments & Proposed Diffs

| ID | File(s) | Owner | Priority | Estimated Effort |
|---|---|---|---|---|
| I-01 | `terraform/main.tf` | Infra | Immediate | 15 min |
| I-02 | `.gitignore` | Any | Immediate | 5 min |
| I-03 | `src/handler.py` | Backend | Immediate | 10 min |
| I-04 | `src/handler.py`, `src/image_processor.py` | Backend | Immediate | 15 min |
| I-05 | `src/config.py` | Backend | Immediate | 5 min |
| I-06 | `terraform/providers.tf` | Infra | Immediate | 30 min |
| H-01 | `src/image_processor.py` | Backend | Hardening | 30 min |
| H-02 | `src/handler.py` | Backend | Hardening | 10 min |
| H-03 | `src/image_processor.py`, `src/fonts/` | Backend | Hardening | 20 min |
| H-04 | `terraform/main.tf` | Infra | Hardening | 30 min |
| H-05 | `src/handler.py` | Backend | Hardening | 10 min |
| H-06 | `terraform/main.tf` | Infra | Hardening | 5 min |
| H-07 | `terraform/main.tf` | Infra | Hardening | 5 min |
| Q-01 | `tests/` (new) | Backend | Backlog | 45 min |
| Q-02 | `src/`, `terraform/main.tf` | Both | Backlog | 2 hrs |
| Q-03 | `terraform/main.tf` | Infra | Backlog | 20 min |
| Q-04 | `src/requirements.txt` | Backend | Backlog | 15 min |
| Q-05 | `docker-compose.yml` | Infra | Backlog | 5 min |

---

## 6. PR Acceptance Criteria

This PR is not mergeable until all **Immediate** items are resolved. The following checklist must be completed before review is requested:

### Immediate Gate
- [ ] `terraform/main.tf` — `AWS_ENDPOINT_URL` is conditional on `var.environment`
- [ ] `.gitignore` — populated with all artifact patterns listed in I-02
- [ ] `src/handler.py` — `bucket_name` validated against `config.source_bucket` per record
- [ ] `src/handler.py` — raw byte size checked against 20MB ceiling before `process_image()`
- [ ] `src/image_processor.py` — `Image.MAX_IMAGE_PIXELS = 50_000_000` set at module level
- [ ] `src/config.py` — `PROCESSED_BUCKET` is required with a fail-fast `EnvironmentError`
- [ ] `terraform/providers.tf` — remote backend block added OR a documented decision to defer with a tracked issue

### Hardening Gate (same sprint)
- [ ] `src/image_processor.py` — single JPEG encode at pipeline exit, no double-compression
- [ ] `src/handler.py` — `_config` and `_s3_client` initialized at module level
- [ ] `src/fonts/` — bundled open-source font, `apply_watermark()` updated to reference it
- [ ] `src/handler.py` — file extension allowlist and per-type `ContentType` in `presigned_url_handler`
- [ ] `terraform/main.tf` — SQS visibility timeout changed to `var.lambda_timeout * 6`
- [ ] `terraform/main.tf` — CloudWatch Logs IAM resource scoped to the Lambda log group
- [ ] `terraform/main.tf` — S3 bucket encryption and public access block resources added

---

*This document supersedes any verbal discussion. Changes to findings require a comment on this file in the PR thread, not a message in chat.*
