"""
Integration tests — full data-flow from raw image bytes through the processing
pipeline to a mock S3 destination.

🧪 TEST STRATEGY & MATRIX
--------------------------
Framework  : pytest + unittest.mock (no real AWS calls, no LocalStack required).
Strategy   : Unlike unit tests, these tests use the REAL image_processor
             implementation (not patched). Only S3 I/O is mocked. This verifies
             that the handler correctly wires PIL processing output into the S3
             write path, and that the output is a structurally valid JPEG.

Coverage targets
  - JPEG input  → processed JPEG stored at processed/<key>
  - PNG input   → format converted to JPEG on output
  - RGBA input  → alpha-channel composited; stored as RGB JPEG
  - Large input → resized to ≤ MAX_OUTPUT_WIDTH × MAX_OUTPUT_HEIGHT
  - key prefix  → always "processed/<original_key>"
  - source bucket write isolation (infinite-loop prevention)
  - Multi-record batch → all records processed independently

⚠️ EDGE CASES & VULNERABILITY VECTORS
---------------------------------------
  - Stored bytes must be a valid, re-openable PIL JPEG (not raw garbage)
  - Processed bucket key prefix must match "processed/" exactly
  - No record of a PutObject call targeting the source bucket in any test
"""

import io
import json
import os
from unittest.mock import MagicMock

import pytest
from PIL import Image

import image_processor as ip
import handler


_SOURCE = os.environ["SOURCE_BUCKET"]
_PROCESSED = os.environ["PROCESSED_BUCKET"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rgb_jpeg(width: int = 400, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_rgba_png(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (80, 120, 200, 128)).save(buf, format="PNG")
    return buf.getvalue()


def _make_rgb_png(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (60, 100, 180)).save(buf, format="PNG")
    return buf.getvalue()


def _sqs_event(key: str) -> dict:
    body = json.dumps({
        "Records": [{"s3": {"bucket": {"name": _SOURCE}, "object": {"key": key}}}]
    })
    return {"Records": [{"body": body}]}


def _s3_resp(data: bytes) -> dict:
    return {"Body": MagicMock(read=MagicMock(return_value=data))}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def s3(monkeypatch):
    """Fresh per-test S3 mock wired into handler._s3_client."""
    mock = MagicMock(name="integration_s3")
    monkeypatch.setattr(handler, "_s3_client", mock)
    mock.put_object.return_value = {}
    return mock


def _stored_image(s3_mock) -> Image.Image:
    """Return the PIL Image that was stored via the last put_object call."""
    stored_bytes = s3_mock.put_object.call_args.kwargs["Body"]
    return Image.open(io.BytesIO(stored_bytes))


# ────────────────────────────────────────────────────────────────────────────
# Full pipeline — format coverage
# ────────────────────────────────────────────────────────────────────────────

class TestFullPipeline:

    def test_jpeg_input_produces_valid_jpeg_output(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg(400, 300))
        handler.lambda_handler(_sqs_event("scene.jpg"), None)
        img = _stored_image(s3)
        assert img.format == "JPEG"

    def test_jpeg_input_output_mode_is_rgb(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg(400, 300))
        handler.lambda_handler(_sqs_event("scene.jpg"), None)
        assert _stored_image(s3).mode == "RGB"

    def test_png_input_converted_to_jpeg(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_png())
        handler.lambda_handler(_sqs_event("diagram.png"), None)
        assert _stored_image(s3).format == "JPEG"

    def test_rgba_png_converted_to_rgb_jpeg(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgba_png())
        handler.lambda_handler(_sqs_event("hero.png"), None)
        img = _stored_image(s3)
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    # -- Resize validation --

    def test_large_image_output_width_within_max(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg(2500, 2500))
        handler.lambda_handler(_sqs_event("large.jpg"), None)
        assert _stored_image(s3).width <= ip.MAX_OUTPUT_WIDTH

    def test_large_image_output_height_within_max(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg(2500, 2500))
        handler.lambda_handler(_sqs_event("large.jpg"), None)
        assert _stored_image(s3).height <= ip.MAX_OUTPUT_HEIGHT

    def test_small_image_not_upscaled(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg(100, 100))
        handler.lambda_handler(_sqs_event("thumb.jpg"), None)
        img = _stored_image(s3)
        assert img.width <= 100
        assert img.height <= 100

    # -- Key routing --

    def test_output_key_is_prefixed_with_processed(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg())
        handler.lambda_handler(_sqs_event("photos/2024/beach.jpg"), None)
        assert s3.put_object.call_args.kwargs["Key"] == "processed/photos/2024/beach.jpg"

    def test_get_object_uses_source_bucket(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg())
        handler.lambda_handler(_sqs_event("photo.jpg"), None)
        assert s3.get_object.call_args.kwargs["Bucket"] == _SOURCE

    def test_put_object_targets_processed_bucket(self, s3):
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg())
        handler.lambda_handler(_sqs_event("photo.jpg"), None)
        assert s3.put_object.call_args.kwargs["Bucket"] == _PROCESSED

    # -- Architectural isolation --

    def test_source_bucket_never_receives_put_object(self, s3):
        """
        Critical loop-prevention check: no put_object call may ever reference
        the source bucket. Tested here end-to-end with the real processor.
        """
        s3.get_object.return_value = _s3_resp(_make_rgb_jpeg())
        handler.lambda_handler(_sqs_event("photo.jpg"), None)
        for c in s3.put_object.call_args_list:
            assert c.kwargs["Bucket"] != _SOURCE, (
                "Infinite-loop guardrail violated: source bucket was written to"
            )

    # -- Multi-record batch --

    def test_multi_record_batch_all_stored(self, s3):
        raw = _make_rgb_jpeg(400, 300)
        s3.get_object.return_value = _s3_resp(raw)

        body = json.dumps({
            "Records": [{"s3": {"bucket": {"name": _SOURCE}, "object": {"key": "img.jpg"}}}]
        })
        event = {"Records": [{"body": body}, {"body": body}, {"body": body}]}

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert s3.put_object.call_count == 3

    def test_multi_record_batch_all_outputs_are_valid_jpeg(self, s3):
        raw = _make_rgb_jpeg(200, 200)
        s3.get_object.return_value = _s3_resp(raw)

        body = json.dumps({
            "Records": [{"s3": {"bucket": {"name": _SOURCE}, "object": {"key": "img.jpg"}}}]
        })
        event = {"Records": [{"body": body}, {"body": body}]}

        handler.lambda_handler(event, None)

        for c in s3.put_object.call_args_list:
            img = Image.open(io.BytesIO(c.kwargs["Body"]))
            assert img.format == "JPEG"
