#!/usr/bin/env python3
"""
run_chaos_suite.py  ─  Daisy Image Processor | Chaos & Event Simulator
=======================================================================

Generates edge-case fixtures in tests/fixtures/ and exercises
handler.lambda_handler against 10 adversarial scenario classes.

Metrics per scenario
  - Wall-clock duration  (time.perf_counter)
  - Peak heap allocation (tracemalloc)
  - Pass / Fail verdict  + one-line finding

Usage (from repo root):
  PYTHONPATH=vendor python .github/skills/chaos-event-simulator/scripts/run_chaos_suite.py

Exit code:  0 = all pass   |   N = number of failures
"""

import io
import json
import os
import struct
import sys
import time
import tracemalloc
from dataclasses import dataclass
from typing import Callable
from unittest.mock import MagicMock, patch

# ═══════════════════════════════════════════════════════════════════════════════
# 1. BOOTSTRAP  —  paths, env vars, boto3 mock  (must precede handler import)
# ═══════════════════════════════════════════════════════════════════════════════

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

os.environ.update(
    {
        "SOURCE_BUCKET": "daisy-source-chaos",
        "PROCESSED_BUCKET": "daisy-processed-chaos",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "chaos-key-id",
        "AWS_SECRET_ACCESS_KEY": "chaos-secret-key",
    }
)
os.makedirs(FIXTURES_DIR, exist_ok=True)

# Patch boto3.client BEFORE importing handler — handler builds its S3 client
# at module level, so the mock must be in place before the import executes.
_mock_s3 = MagicMock(name="chaos_mock_s3")
_boto3_patch = patch("boto3.client", return_value=_mock_s3)
_boto3_patch.start()

import handler  # noqa: E402 — must follow env + patch setup

SRC_BUCKET = "daisy-source-chaos"
NULL_CTX = None  # Lambda context object — unused by handler under test


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FIXTURE GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _jpeg(width: int = 64, height: int = 64) -> bytes:
    """Return a minimal valid JPEG as raw bytes."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 149, 237)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _jpeg_padded(target_mb: float) -> bytes:
    """Embed a valid JPEG in a block padded with null bytes to target_mb MB.

    JPEG decoders stop at the FFD9 end-of-image marker; trailing bytes are
    ignored, so PIL will either decode successfully or raise on the scan data.
    Either behaviour exercises the handler's exception boundary.
    """
    base = _jpeg()
    target = int(target_mb * 1024 * 1024)
    padding = max(0, target - len(base))
    return base + b"\x00" * padding


def _exe_stub() -> bytes:
    """Return a Windows PE/EXE stub — clearly not a valid image.

    Structure: MZ header (64 bytes) + e_lfanew + PE signature + minimal COFF.
    PIL's UnidentifiedImageError is the expected response when this is opened.
    """
    mz_header = b"MZ" + b"\x90\x00" * 29  # 58 filler bytes (total 60 with 'MZ')
    e_lfanew = struct.pack("<I", 0x40)      # PE header at offset 64
    pe_sig = b"PE\x00\x00"
    # COFF header: machine=x64, sections=2, timestamp=0, symtab=0, numsym=0,
    #              opthdrsz=240, characteristics=executable+large-addr
    coff = struct.pack("<HHIIIHH", 0x8664, 2, 0, 0, 0, 240, 0x22)
    return mz_header + e_lfanew + pe_sig + coff + b"\xCC" * 512


def _corrupt_exif_jpeg() -> bytes:
    """Return a JPEG with a truncated / malformed EXIF APP1 segment.

    The APP1 length field claims 256 bytes, but only 10 bytes of valid Exif
    prefix + broken TIFF IFD data follow.  PIL either raises during thumbnail
    expansion or silently recovers — both paths are handled by handler.py.
    """
    soi = b"\xff\xd8"
    app1_marker = b"\xff\xe1"
    claimed_length = struct.pack(">H", 256)    # lies: says 256 bytes follow
    exif_prefix = b"Exif\x00\x00"             # 6-byte canonical Exif header
    tiff_le_magic = b"\x49\x49\x2a\x00"       # little-endian TIFF magic
    broken_ifd_offset = b"\x08\x00\x00\x00"   # offset 8 — no IFD at that address
    bad_app1 = app1_marker + claimed_length + exif_prefix + tiff_le_magic + broken_ifd_offset
    # Append a real JPEG body (drop the duplicate SOI marker it already has)
    valid_body = _jpeg()[2:]
    return soi + bad_app1 + valid_body


def _sqs_event(bucket: str, key: str) -> dict:
    """Build a well-formed Lambda SQS trigger event for a single S3 record."""
    s3_notification = {
        "Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]
    }
    return {"Records": [{"body": json.dumps(s3_notification)}]}


def _arm_s3(raw_bytes: bytes) -> None:
    """Configure the global mock S3 client to return raw_bytes on get_object."""
    mock_body = MagicMock()
    mock_body.read.return_value = raw_bytes
    _mock_s3.reset_mock()
    _mock_s3.get_object.return_value = {"Body": mock_body}
    _mock_s3.put_object.return_value = {}


def _save_fixture(filename: str, data: bytes) -> str:
    """Persist fixture bytes to tests/fixtures/ and return the full path."""
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TEST HARNESS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Result:
    name: str
    passed: bool
    duration_s: float
    peak_mb: float
    finding: str


ScenarioFn = Callable[[], tuple[bool, str]]  # returns (passed, one-line finding)


def run(name: str, fn: ScenarioFn) -> Result:
    """Execute fn() under tracemalloc + perf_counter instrumentation."""
    tracemalloc.start()
    t0 = time.perf_counter()
    passed, finding = False, ""
    try:
        passed, finding = fn()
    except Exception as exc:  # scenario itself crashed — not the handler
        finding = f"SCENARIO ERROR — {type(exc).__name__}: {exc!s:.100}"
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return Result(name, passed, round(elapsed, 4), round(peak / 1_000_000, 2), finding)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════


def s1a_oversize_rejected() -> tuple[bool, str]:
    """S1a: 21 MB blob — must be rejected by the 20 MB guardrail before PIL."""
    data = b"\x00" * (21 * 1024 * 1024)
    _save_fixture("chaos_oversize_21mb.bin", data)
    _arm_s3(data)
    event = _sqs_event(SRC_BUCKET, "photos/huge_upload.jpg")
    try:
        handler.lambda_handler(event, NULL_CTX)
        return False, "FAIL — handler did NOT reject the 21 MB file (guardrail missing)"
    except RuntimeError as exc:
        passed = "photos/huge_upload.jpg" in str(exc)
        verdict = "20 MB guardrail active; key in failed_keys ✓" if passed else f"Wrong key in error: {exc}"
        return passed, verdict


def s1b_large_valid_processed() -> tuple[bool, str]:
    """S1b: 10 MB padded JPEG — clears the size check; PIL processes or errors gracefully."""
    data = _jpeg_padded(10)
    _save_fixture("chaos_large_10mb.jpg", data)
    _arm_s3(data)
    event = _sqs_event(SRC_BUCKET, "photos/large_valid.jpg")
    try:
        result = handler.lambda_handler(event, NULL_CTX)
        return True, f"PIL decoded padded JPEG; handler returned {result}"
    except RuntimeError:
        # PIL raised on the padded scan data — handler caught it and raised RuntimeError.
        # This is the correct graceful-failure path; the scenario still passes.
        return True, "PIL rejected trailing null bytes; handler routed to failed_keys ✓"


def s2_content_type_spoof() -> tuple[bool, str]:
    """S2: Windows PE binary delivered with a .jpg S3 key — PIL must reject it."""
    data = _exe_stub()
    _save_fixture("chaos_spoof_exe.jpg", data)
    _arm_s3(data)
    event = _sqs_event(SRC_BUCKET, "uploads/holiday_photo.jpg")
    try:
        handler.lambda_handler(event, NULL_CTX)
        return False, "FAIL — handler processed a PE binary as a JPEG (no PIL error raised)"
    except RuntimeError as exc:
        passed = "holiday_photo.jpg" in str(exc)
        verdict = (
            "PIL raised UnidentifiedImageError on PE stub; handler routed to failed_keys ✓"
            if passed
            else f"Unexpected failure content: {exc!s:.80}"
        )
        return passed, verdict


def s3_corrupt_exif() -> tuple[bool, str]:
    """S3: JPEG with truncated EXIF APP1 — no unhandled crash allowed."""
    data = _corrupt_exif_jpeg()
    _save_fixture("chaos_corrupt_exif.jpg", data)
    _arm_s3(data)
    event = _sqs_event(SRC_BUCKET, "uploads/corrupt_exif.jpg")
    try:
        result = handler.lambda_handler(event, NULL_CTX)
        return True, f"PIL recovered from bad EXIF; result: {result}"
    except RuntimeError:
        return True, "PIL raised on malformed IFD; handler routed to failed_keys ✓"


def s4a_malformed_json_body() -> tuple[bool, str]:
    """S4a: SQS body is not valid JSON — must trigger JSONDecodeError path."""
    event = {"Records": [{"body": "<<<NOT_VALID_JSON>>>"}]}
    try:
        handler.lambda_handler(event, NULL_CTX)
        return False, "FAIL — handler did not raise on invalid JSON body"
    except RuntimeError as exc:
        return True, f"JSONDecodeError caught; routed to failed_keys ✓  ({exc!s:.60})"


def s4b_missing_s3_key() -> tuple[bool, str]:
    """S4b: Valid JSON but s3.object.key absent — must trigger KeyError/IndexError."""
    body = json.dumps({"Records": [{"s3": {"bucket": {"name": SRC_BUCKET}}}]})
    event = {"Records": [{"body": body}]}
    try:
        handler.lambda_handler(event, NULL_CTX)
        return False, "FAIL — handler did not raise on missing s3.object.key"
    except RuntimeError:
        return True, "KeyError/IndexError on missing s3 sub-key caught; routed to failed_keys ✓"


def s4c_bucket_mismatch_injection() -> tuple[bool, str]:
    """S4c: Foreign bucket in event — confused-deputy attack vector."""
    event = _sqs_event("attacker-controlled-bucket", "exfil/payload.jpg")
    try:
        handler.lambda_handler(event, NULL_CTX)
        return False, "FAIL — handler processed a foreign-bucket event (confused-deputy guard missing)"
    except RuntimeError as exc:
        return True, f"Bucket mismatch guard active; confused-deputy rejected ✓  ({exc!s:.60})"


def s4d_unicode_key_injection() -> tuple[bool, str]:
    """S4d: Null byte + path-traversal characters in S3 object key.

    This scenario documents handler behaviour rather than asserting a block.
    S3 keys with path-traversal characters are valid S3 API input; the finding
    is whether the output key is passed through unsanitized.
    """
    injection_key = "../../etc/passwd\u0000injected.jpg"
    data = _jpeg()
    _arm_s3(data)
    event = _sqs_event(SRC_BUCKET, injection_key)
    try:
        handler.lambda_handler(event, NULL_CTX)
        if _mock_s3.put_object.called:
            output_key = _mock_s3.put_object.call_args.kwargs.get("Key", "?")
            # Document the unsanitized key as a finding (informational, not a block)
            return True, f"FINDING: put_object Key={output_key!r} — key not sanitized before S3 write"
        return True, "Processed; put_object not called (unexpected path)"
    except RuntimeError as exc:
        return True, f"Handler rejected traversal key gracefully: {exc!s:.80}"


def s4e_empty_batch() -> tuple[bool, str]:
    """S4e: Zero-record SQS batch — handler must return 200 with no S3 side effects."""
    _mock_s3.reset_mock()
    event = {"Records": []}
    result = handler.lambda_handler(event, NULL_CTX)
    put_called = _mock_s3.put_object.called
    passed = isinstance(result, dict) and result.get("statusCode") == 200 and not put_called
    detail = f"statusCode={result.get('statusCode')}, put_object_called={put_called}"
    return passed, detail + (" ✓" if passed else " — FAIL")


def s4f_missing_records_key() -> tuple[bool, str]:
    """S4f: Event envelope missing 'Records' key — must not crash."""
    _mock_s3.reset_mock()
    result = handler.lambda_handler({}, NULL_CTX)
    passed = isinstance(result, dict) and result.get("statusCode") == 200
    detail = f"result={result}"
    return passed, detail + (" ✓" if passed else " — FAIL: unexpected response shape")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SCENARIO REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

SCENARIOS: list[tuple[str, ScenarioFn]] = [
    ("S1a │ Oversize payload (21 MB) rejected",           s1a_oversize_rejected),
    ("S1b │ Large valid JPEG (10 MB) processed/caught",   s1b_large_valid_processed),
    ("S2  │ Content-type spoof  (EXE → .jpg key)",        s2_content_type_spoof),
    ("S3  │ Corrupt EXIF APP1 segment",                   s3_corrupt_exif),
    ("S4a │ Malformed SQS body  (invalid JSON)",          s4a_malformed_json_body),
    ("S4b │ Missing s3.object.key in notification",       s4b_missing_s3_key),
    ("S4c │ Bucket mismatch  (confused-deputy probe)",    s4c_bucket_mismatch_injection),
    ("S4d │ Unicode / path-traversal in object key",      s4d_unicode_key_injection),
    ("S4e │ Empty record batch  (zero records)",          s4e_empty_batch),
    ("S4f │ Missing 'Records' key in event envelope",     s4f_missing_records_key),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. REPORT RENDERER
# ═══════════════════════════════════════════════════════════════════════════════

_COL_NAME = 50
_COL_DUR = 10
_COL_MEM = 10
_COL_VER = 8
_TOTAL_W = _COL_NAME + _COL_DUR + _COL_MEM + _COL_VER + 14


def _print_report(results: list[Result]) -> int:
    failures = sum(1 for r in results if not r.passed)

    print()
    print(f"╔{'═' * _TOTAL_W}╗")
    print(f"║{'DAISY CHAOS SIMULATOR — RESULTS':^{_TOTAL_W}}║")
    print(f"╚{'═' * _TOTAL_W}╝")
    print()

    hdr = (
        f"  {'Scenario':<{_COL_NAME}}"
        f"  {'Duration':>{_COL_DUR}}"
        f"  {'Peak RAM':>{_COL_MEM}}"
        f"  {'Result':>{_COL_VER}}"
    )
    print(hdr)
    print("  " + "─" * (_TOTAL_W - 2))

    for r in results:
        verdict = "✓ PASS" if r.passed else "✗ FAIL"
        row = (
            f"  {r.name:<{_COL_NAME}}"
            f"  {r.duration_s:>{_COL_DUR - 1}.4f}s"
            f"  {r.peak_mb:>{_COL_MEM - 3}.1f} MB"
            f"    {verdict}"
        )
        print(row)
        if r.finding:
            indent = "  " + " " * _COL_NAME + "    "
            print(f"{indent}{r.finding[:90]}")

    print()
    print(f"  Fixtures written to : {FIXTURES_DIR}")
    total = len(results)
    passed = total - failures
    suffix = "✓ ALL PASSED" if failures == 0 else f"← {failures} FAILURE(S)"
    print(f"  Results             : {passed}/{total} passed   {suffix}")
    print()

    return failures


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = [run(name, fn) for name, fn in SCENARIOS]
    exit_code = _print_report(results)
    _boto3_patch.stop()
    sys.exit(exit_code)
