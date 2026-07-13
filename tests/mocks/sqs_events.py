"""
SQS / S3 event payload builders for handler unit and integration tests.

Mirrors the exact nested JSON structure emitted by S3 Event Notifications
when routed through SQS, as consumed by ``handler.lambda_handler``.

Structure (outer → inner):
  Lambda event
    └── Records[]           (SQS records)
          └── body          (JSON string — the SQS message body)
                └── Records[]     (S3 notification records)
                      └── s3
                            ├── bucket.name
                            └── object.key
"""

import json


def make_s3_notification(bucket: str, key: str) -> dict:
    """Return the inner S3 event notification structure (not yet serialised)."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                }
            }
        ]
    }


def make_sqs_event(bucket: str, key: str, num_records: int = 1) -> dict:
    """
    Build a Lambda SQS trigger event containing ``num_records`` identical
    S3 ObjectCreated notifications.

    Args:
        bucket:      S3 bucket name to embed in each notification body.
        key:         S3 object key (may be URL-encoded) to embed in each body.
        num_records: Number of SQS records in the batch.

    Returns:
        dict: Valid Lambda event dict ready to pass to ``lambda_handler``.
    """
    body = json.dumps(make_s3_notification(bucket, key))
    return {"Records": [{"body": body} for _ in range(num_records)]}


def make_sqs_event_raw_body(raw_body: str) -> dict:
    """Build a single-record SQS event with an arbitrary (possibly malformed) body."""
    return {"Records": [{"body": raw_body}]}


def make_sqs_event_missing_body_key() -> dict:
    """SQS record that is entirely missing the 'body' key — triggers KeyError."""
    return {"Records": [{}]}


def make_sqs_event_empty() -> dict:
    """Zero-record SQS event — valid structure, nothing to process."""
    return {"Records": []}


def make_sqs_event_no_records_key() -> dict:
    """Lambda event dict with no top-level 'Records' key."""
    return {}
