# PR-01 — QA Audit Findings
**Daisy Image Processor · Serverless Pipeline**
**Submitted by:** Principal SDET / QA Architect
**Submitted to:** Principal Systems Architect
**Date:** 2026-07-13
**Status:** ✅ PASS — 137 / 137 tests green

---

## Executive Summary

The Daisy Image Processor passed every test in the QA audit suite. The codebase demonstrates disciplined separation of concerns, robust error boundary handling, and correct implementation of all documented architectural guardrails. This document captures what was tested, how it was tested, what was found, and where future risk still lives.

No blocking defects were uncovered. Two **informational observations** (not failures) are noted at the end — both relate to behaviours that are correct today but represent latent risk if requirements change.

---

## 1. What Was Tested

Three modules were audited independently and then as an integrated pipeline:

| Module | Role |
|---|---|
| `src/config.py` | Runtime configuration loader — env-var validation, frozen dataclass |
| `src/image_processor.py` | Pure Python media transformer — resize, watermark, full pipeline |
| `src/handler.py` | Lambda event boundary — SQS unpacking, S3 I/O, presigned URL generation |
| Pipeline (integration) | End-to-end flow: raw bytes → processor → mock S3 write |

---

## 2. Test Infrastructure Design

### How the test suite is bootstrapped

The primary challenge in testing this codebase is that `handler.py` executes **module-level initialisation code** at import time:

```python
_config = load_config()
_s3_client = _get_boto3_client("s3", _config)
```

This means any test runner that imports `handler` will attempt a real AWS connection and fail a `load_config()` env-var check unless intercepted first.

**Solution implemented in `tests/conftest.py`:**

1. `src/` is prepended to `sys.path` so all source modules resolve correctly.
2. Required Lambda environment variables (`SOURCE_BUCKET`, `PROCESSED_BUCKET`, etc.) are set in `os.environ` **before** any test module is collected.
3. `boto3.client` is patched globally via `unittest.mock.patch` **before** `handler.py` is imported. This means `handler._s3_client` holds a `MagicMock` at all times. Individual tests then replace that mock per-test via `monkeypatch.setattr(handler, "_s3_client", fresh_mock)` for full isolation.

This design means **no test requires a live AWS account, LocalStack, or Docker** to run.

### Test isolation strategy

```
Unit tests      → process_image is stubbed; only S3 mock responses matter
Integration     → process_image runs for real (PIL); only S3 I/O is mocked
Config tests    → importlib.reload() per call; env vars restored after each test
```

---

## 3. Findings by Module

---

### 3.1 `config.py` — 14 Tests ✅

**What was tested:**

- Both required variables (`SOURCE_BUCKET`, `PROCESSED_BUCKET`) raise `EnvironmentError` with the variable name in the message when absent or empty.
- Optional variables (`SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`) default to `None` when absent or empty.
- `AWS_REGION` defaults to `us-east-1` and accepts custom values.
- The `Config` dataclass is `frozen=True` — mutation attempts raise.
- The config instance is hashable (frozen dataclass guarantee).

**Result:** All 14 passed. The guard-rail behaviour is exactly as documented.

**Observation — INF-01 (Whitespace-only SQS URL):**
A value like `SQS_QUEUE_URL="   "` (three spaces) passes the truthiness check and is stored verbatim as `"   "` rather than being normalised to `None`. This is currently **correct behaviour** given the code as written (`or None` only catches empty string / None, not whitespace). It is documented in the test suite as a regression baseline, not a defect. If the intent is to treat whitespace-only values as absent, a `.strip()` call would close this gap.

---

### 3.2 `image_processor.py` — 46 Tests ✅

**What was tested:**

#### `resize_image()`
- JPEG and PNG input both produce JPEG output.
- RGBA input is converted to RGB before JPEG save (JPEG does not support alpha channels).
- Grayscale (L-mode) input is accepted and saved as JPEG.
- Large images (2500×2500) are capped at `MAX_OUTPUT_WIDTH` × `MAX_OUTPUT_HEIGHT` (1280×1280).
- Small images (100×100) are **not** upscaled — `thumbnail()` is a shrink-only operation.
- Images exactly at the boundary (1280×1280) are not further reduced.
- Wide (3000×50) and portrait (50×3000) images preserve aspect ratio within ±2 px rounding tolerance.
- Custom `max_width` / `max_height` parameters are respected.
- Zero bytes and corrupt binary both raise exceptions (not silent empty returns).

#### `apply_watermark()`
- Output is always RGB JPEG regardless of input mode.
- Empty watermark text (`text=""`) does not crash.
- Tiny image (1×1 px): `font_size = max(12, 1 // 40) = 12`. The watermark overflows the image but does not raise — PIL clips the draw coordinates silently.
- **Font fallback tested:** `_FONT_PATH` is monkeypatched to a nonexistent path. The `except (IOError, OSError)` block correctly falls back to `ImageFont.load_default()`. No `FileNotFoundError` propagates.
- Watermarking does not alter image dimensions (width/height unchanged after compositing).

#### `process_image()` (full pipeline)
- Single-encode guarantee (H-01): output is re-opened by PIL and confirmed to be a structurally valid JPEG. If double-encoding occurred, the bytes would be JPEG-inside-JPEG and `Image.open()` would either fail or produce a corrupt image.
- `Image.MAX_IMAGE_PIXELS == 50_000_000` is asserted as a **static security test** — any future regression that removes or raises this cap will cause an immediate test failure.
- Constants `MAX_OUTPUT_WIDTH`, `MAX_OUTPUT_HEIGHT`, and `OUTPUT_FORMAT` are verified to match the documented architecture.

---

### 3.3 `handler.py` — 64 Tests ✅

#### `lambda_handler()` — happy path

| Assertion | Result |
|---|---|
| Single record → HTTP 200, body contains "1" | ✅ |
| Three records → `get_object` called 3×, `put_object` called 3× | ✅ |
| Empty `Records[]` batch → HTTP 200, no S3 calls | ✅ |
| Missing top-level `Records` key → HTTP 200 (`.get("Records", [])` default) | ✅ |
| Output key prefixed with `processed/` | ✅ |
| `ContentType: image/jpeg` on every `put_object` | ✅ |
| `put_object` targets `PROCESSED_BUCKET`, never `SOURCE_BUCKET` | ✅ |
| `process_image` receives the exact raw bytes from S3 | ✅ |
| `put_object` body is the exact output of `process_image` | ✅ |
| File at exactly 20 MB limit is accepted (guard uses `>`, not `>=`) | ✅ |
| `+`-encoded keys decoded to spaces via `unquote_plus` | ✅ |
| `%20`-encoded keys decoded via `unquote_plus` | ✅ |

#### `lambda_handler()` — failure / rejection paths

| Scenario | Expected | Result |
|---|---|---|
| Bucket mismatch (I-03) | `RuntimeError`; `get_object` not called | ✅ |
| Malformed JSON in SQS body | `RuntimeError` | ✅ |
| `Records` key missing from inner S3 notification | `RuntimeError` | ✅ |
| Empty inner `Records[]` → `IndexError` on `[0]` | `RuntimeError` | ✅ |
| `get_object` → `NoSuchKey` | `RuntimeError` | ✅ |
| `get_object` → `AccessDenied` | `RuntimeError` | ✅ |
| File > 20 MB → `RuntimeError`; `process_image` not called | ✅ |
| `put_object` → `AccessDenied` | `RuntimeError` | ✅ |
| Partial batch (1 of 3 fails) → `RuntimeError` for SQS retry | ✅ |
| Partial batch → non-failing records still processed | ✅ |

The partial batch behaviour is particularly important for the SQS delivery contract: a `RuntimeError` causes the entire batch to be re-delivered by SQS and eventually routed to the DLQ if it cannot be processed. The test confirms that healthy records within a failing batch are still processed before the error is raised — minimising data loss.

#### `presigned_url_handler()`

| Category | Tested Values | Result |
|---|---|---|
| Allowed extensions | `.jpg`, `.jpeg`, `.png`, `.webp` | ✅ All 200 |
| Content-type mapping | `image/jpeg`, `image/png`, `image/webp` per extension | ✅ |
| Case insensitivity | `.JPG`, `.JPEG`, `.PNG`, `.WebP` | ✅ |
| Rejected extensions | `.gif`, `.bmp`, `.pdf`, `.exe`, `.sh`, `.zip`, `.json`, no extension | ✅ All 400 |
| Path traversal — no valid ext | `../../etc/passwd` → `passwd` → no ext → 400 | ✅ |
| Path traversal — valid ext | `../../evil.jpg` → `evil.jpg` → 200, key contains no `..` | ✅ |
| Absolute path | `/var/task/handler.py` → `.py` rejected → 400 | ✅ |
| Null body | Defaults to `upload.jpg` → 200 | ✅ |
| Missing body key | Defaults to `upload.jpg` → 200 | ✅ |
| Empty filename after `basename` | `/` → `""` → 400 | ✅ |
| `ExpiresIn` | Exactly 300 seconds | ✅ |
| Operation | `put_object` | ✅ |
| Bucket | `SOURCE_BUCKET` | ✅ |
| `ClientError` | HTTP 500, `{"error": "..."}` body | ✅ |

---

### 3.4 Integration Pipeline — 13 Tests ✅

These tests run the **real `process_image` implementation** through the full handler flow. Only S3 I/O is mocked.

| Test | Result |
|---|---|
| JPEG → valid JPEG stored, mode RGB | ✅ |
| PNG → format converted to JPEG | ✅ |
| RGBA PNG → RGB JPEG, alpha composited | ✅ |
| Large image (2500²) → stored dimensions ≤ 1280×1280 | ✅ |
| Small image (100²) → not upscaled | ✅ |
| Output key always `processed/<original_key>` | ✅ |
| `get_object` targets source bucket | ✅ |
| `put_object` targets processed bucket | ✅ |
| Source bucket never receives a `put_object` | ✅ |
| 3-record batch → all 3 stored | ✅ |
| 3-record batch outputs → all valid JPEG | ✅ |

---

## 4. How to Run the Suite

```bash
# From the project root
cd ~/projects/daisy-image-processor

# Install test dependencies (first time only)
pip3 install pytest pytest-mock --break-system-packages

# Run everything
PYTHONPATH=src python3 -m pytest tests/ -v

# Run only unit tests
PYTHONPATH=src python3 -m pytest tests/unit/ -v

# Run only integration tests
PYTHONPATH=src python3 -m pytest tests/integration/ -v

# Run a single test class
PYTHONPATH=src python3 -m pytest tests/unit/test_handler.py::TestLambdaHandlerFailures -v

# Coverage report (requires pytest-cov)
pip3 install pytest-cov --break-system-packages
PYTHONPATH=src python3 -m pytest tests/ --cov=src --cov-report=term-missing
```

No LocalStack, no Docker, no live AWS account required.

---

## 5. Informational Observations (Not Failures)

These are not defects — the system behaves exactly as coded. They are raised for the architect's awareness because they represent either latent risk or a gap between documented intent and implementation.

---

### INF-01 — Whitespace-only `SQS_QUEUE_URL` not normalised

**File:** `src/config.py`
**Relevant code:**
```python
sqs_queue_url=os.environ.get("SQS_QUEUE_URL") or None,
```
**Observation:** The `or None` idiom normalises empty string `""` to `None` but passes a whitespace-only string like `"   "` through verbatim. If an operator accidentally sets `SQS_QUEUE_URL="  "` in a deployment environment (copy-paste artefact), the config will hold `"   "` and any downstream consumer of `config.sqs_queue_url` that checks `if config.sqs_queue_url:` would evaluate it as truthy — then attempt to use a malformed queue URL.

**Suggested fix (one line):**
```python
sqs_queue_url=(os.environ.get("SQS_QUEUE_URL") or "").strip() or None,
```
**Risk level:** Low. No downstream consumer of `sqs_queue_url` exists in the current codebase, so this is theoretical today.

---

### INF-02 — `process_image` exception propagates as unhandled `RuntimeError` mix

**File:** `src/handler.py`
**Relevant code:**
```python
processed_bytes = process_image(raw_bytes)
```
**Observation:** The call to `process_image` is not wrapped in a `try/except` block. If PIL raises an `UnidentifiedImageError` (e.g., a file that passes the 20 MB size check but contains corrupt image data — such as a `.jpg` file that is actually a ZIP archive), the exception propagates uncaught through `lambda_handler`. It will ultimately raise from the top-level `try/except (KeyError, json.JSONDecodeError, IndexError)` block... except it won't be caught, because `UnidentifiedImageError` is none of those types. The exception will propagate to the Lambda runtime, causing an unhandled invocation failure rather than a graceful `RuntimeError` with the key in `failed_keys`.

The effect is the same — SQS will retry and eventually DLQ the message — but the structured logging (`logger.error(...)` with key info) is skipped, making CloudWatch diagnosis harder.

**Suggested fix:**
```python
try:
    processed_bytes = process_image(raw_bytes)
except Exception as exc:
    logger.error(
        "Image processing failed for s3://%s/%s: %s",
        bucket_name, source_key, exc,
    )
    failed_keys.append(source_key)
    continue
```
**Risk level:** Low-Medium. The system still fails safely (SQS retry + DLQ), but observability in production degrades for corrupt-image payloads that slip past the size check.

---

## 6. Test File Map

```
tests/
├── conftest.py                        # Path bootstrap, env bootstrap, boto3 patch, shared fixtures
├── requirements-test.txt              # pytest, pytest-mock
├── PR-01-findings.md                  # This document
│
├── mocks/
│   ├── __init__.py
│   └── sqs_events.py                  # SQS / S3 event payload builders
│
├── unit/
│   ├── __init__.py
│   ├── test_config.py                 # 14 tests — config loader
│   ├── test_image_processor.py        # 46 tests — resize, watermark, pipeline
│   └── test_handler.py                # 64 tests — lambda_handler, presigned_url_handler
│
├── integration/
│   ├── __init__.py
│   └── test_pipeline.py               # 13 tests — full wired pipeline
│
└── fixtures/
    └── __init__.py                    # Reserved for binary fixture files
```

---

## 7. QA Sign-Off

| Architectural Rule | Guardrail Code | Test Coverage | Verdict |
|---|---|---|---|
| No base64 payload through API Gateway | Presigned URL flow | Extension allowlist + response shape tests | ✅ |
| Lambda never writes to source bucket | `output_key = f"processed/{source_key}"` | Unit + integration bucket assertion | ✅ |
| Pure Python processing core (no AWS deps) | `import` audit | `test_image_processor` runs without any mocking | ✅ |
| Bucket mismatch rejection (I-03) | `if bucket_name != _config.source_bucket` | `test_bucket_mismatch_raises_runtime_error` | ✅ |
| File size ceiling — 20 MB (I-04) | `len(raw_bytes) > MAX_RAW_BYTES` | Below / at / above limit all tested | ✅ |
| Decompression bomb pixel cap (I-04) | `Image.MAX_IMAGE_PIXELS = 50_000_000` | Static constant assertion | ✅ |
| Frozen config — no mutation | `@dataclass(frozen=True)` | Mutation attempt raises `AttributeError` | ✅ |
| SQS retry contract | `raise RuntimeError(failed_keys)` | Partial + full batch failure tested | ✅ |
| Single JPEG encode (H-01) | `process_image` pipeline | Re-open output with PIL | ✅ |
| Warm Lambda connection pool (H-02) | Module-level `_s3_client` | `test_s3_client_is_not_none` | ✅ |
| Open-source font on Amazon Linux (H-03) | `DejaVuSans.ttf` bundled | Font fallback monkeypatched and tested | ✅ |

**Overall verdict: the system is production-ready from a functional correctness and error-boundary standpoint.** The two informational observations (INF-01, INF-02) are recommended for the next iteration but do not block deployment.
