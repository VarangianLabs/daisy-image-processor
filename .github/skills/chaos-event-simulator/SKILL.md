---
name: chaos-event-simulator
description: >-
  Elite Cloud Chaos & Event Simulator for the Daisy Image Processor Lambda. Use when
  stress-testing handler.py against production edge cases: massive payloads (10 MB+),
  content-type spoofing (EXE disguised as JPEG), corrupt EXIF APP1 triggers, malformed
  SQS event JSON, and schema-injection attacks. Generates fixture files in tests/fixtures/
  and runs a self-contained Python stress suite reporting wall-clock timing and peak
  tracemalloc memory per scenario. Trigger phrases: chaos test, stress test, edge case
  fixtures, payload mock, SQS injection, corrupt image, oversize file, MIME spoofing,
  simulate S3 events, memory thresholds, handler fuzzing, PIL crash, DLQ routing.
argument-hint: "scenario category (S1/S2/S3/S4) or 'all' to run full suite"
---

# Chaos & Event Simulator

## Purpose

Stress-test `handler.lambda_handler` against adversarial, production-realistic payloads
inside the native WSL/Linux environment — zero real AWS dependencies required.

## When to Use

- Validating new guards added to `handler.py` or `image_processor.py`
- Confirming error-handling paths route correctly to `failed_keys` → RuntimeError → DLQ
- Verifying PIL behaviour on corrupt, truncated, or spoofed images
- Generating documented edge-case fixtures for the regression test suite
- Pre-PR chaos gate to detect regressions in boundary guards

## Simulation Categories

| ID  | Category               | Attack Vector                                   | Expected Outcome             |
|-----|------------------------|-------------------------------------------------|------------------------------|
| S1a | Massive Payload        | 21 MB blob exceeds 20 MB handler ceiling        | Rejected before PIL          |
| S1b | Large valid JPEG       | 10 MB padded JPEG clears the size check         | Processed or PIL error caught |
| S2  | Content-Type Spoofing  | Windows PE binary served under a `.jpg` key     | PIL rejects; failed_keys     |
| S3  | Corrupt EXIF           | JPEG with truncated APP1 / IFD table            | Graceful error or recovery   |
| S4a | Malformed JSON body    | SQS body is not valid JSON                      | JSONDecodeError → failed_keys |
| S4b | Missing S3 key         | Valid JSON but `s3.object.key` absent           | KeyError → failed_keys       |
| S4c | Bucket mismatch        | Foreign bucket in event — confused-deputy probe | Bucket guard rejects         |
| S4d | Unicode/path-traversal | Null bytes and `../` in S3 object key           | Documents unsanitized key    |
| S4e | Empty batch            | Zero SQS records                                | 200 OK, no side effects      |
| S4f | Missing Records key    | Event envelope has no `Records` field           | 200 OK, no crash             |

## Execution

### Prerequisites

Vendor dependencies must be resolvable. Run from the repo root:

```bash
PYTHONPATH=vendor python .github/skills/chaos-event-simulator/scripts/run_chaos_suite.py
```

Or via the project Makefile if a `chaos` target has been added:

```bash
make chaos
```

### Output

```
╔═══════════════════════════════════════════════════════════════════════╗
║               DAISY CHAOS SIMULATOR — RESULTS                        ║
╚═══════════════════════════════════════════════════════════════════════╝

  Scenario                                           Duration    Peak RAM   Result
  ─────────────────────────────────────────────────────────────────────────────
  S1a │ Oversize payload (21 MB) rejected             0.0021s     22.0 MB  ✓ PASS
  S1b │ Large valid JPEG (10 MB) processed/caught     0.0450s     45.2 MB  ✓ PASS
  ...

  Fixtures written to: tests/fixtures/
  Results            : 10/10 passed  ✓ ALL PASSED
```

Exit code **0** = all pass. Exit code **≥1** = number of failures.

## Procedure

### Step 1 — Generate fixtures

The script auto-creates binary fixtures on first run:

| File                      | Content                                   |
|---------------------------|-------------------------------------------|
| `chaos_oversize_21mb.bin` | 21 MB of null bytes (triggers size guard) |
| `chaos_large_10mb.jpg`    | Valid JPEG padded to 10 MB                |
| `chaos_spoof_exe.jpg`     | Windows PE/EXE stub with `.jpg` key name  |
| `chaos_corrupt_exif.jpg`  | JPEG with truncated APP1/IFD segment      |

### Step 2 — Run each scenario

Per scenario the harness:
1. Arms `handler._s3_client` (mock) with fixture bytes via `get_object`
2. Builds the SQS event envelope (valid or adversarial)
3. Calls `handler.lambda_handler(event, None)`
4. Records wall-clock time (`time.perf_counter`) and peak heap (`tracemalloc`)
5. Asserts expected outcome (success, graceful failure, or specific exception)

### Step 3 — Interpret results

| Verdict | Meaning                                                   |
|---------|-----------------------------------------------------------|
| ✓ PASS  | Handler behaved correctly under stress                    |
| ✗ FAIL  | Guardrail missing or error path not covered               |

### Step 4 — Triage failures

| Failing scenario | Where to look in the codebase                              |
|------------------|------------------------------------------------------------|
| S1               | `_MAX_RAW_BYTES` guard in `handler.py`                    |
| S2 / S3          | PIL exception wrapper around `process_image()` call       |
| S4a–S4f          | `(KeyError, json.JSONDecodeError, IndexError)` catch block |

## Adding New Scenarios

Append a function to [run_chaos_suite.py](./scripts/run_chaos_suite.py) following the pattern:

```python
def my_scenario() -> tuple[bool, str]:
    """One-line description of what this stresses."""
    # 1. _arm_s3(data)               — arm mock S3 if S3 download needed
    # 2. event = _sqs_event(...)     — build adversarial SQS event
    # 3. call handler.lambda_handler — capture result or exception
    # 4. return (passed: bool, detail: str)
```

Add an entry to the `SCENARIOS` list:

```python
("Sx │ Short description", my_scenario),
```

## Related Files

- [scripts/run_chaos_suite.py](./scripts/run_chaos_suite.py) — Self-contained test runner
- [src/handler.py](../../../../src/handler.py) — Lambda boundary layer under test
- [src/image_processor.py](../../../../src/image_processor.py) — PIL transform layer under stress
- [tests/mocks/sqs_events.py](../../../../tests/mocks/sqs_events.py) — Production event builders
- [tests/conftest.py](../../../../tests/conftest.py) — Shared pytest bootstrap (reference for mock patterns)
