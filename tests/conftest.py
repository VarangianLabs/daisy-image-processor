"""
Root conftest — path bootstrap, Lambda env bootstrap, and shared image fixtures.

This file is loaded by pytest before any test module is collected. Three things
happen here that MUST occur before handler.py is imported:

  1. src/ is prepended to sys.path.
  2. Required Lambda environment variables are set in os.environ.
  3. boto3.client is patched so handler.py's module-level S3 client
     initialisation does not attempt a real AWS connection.

After these steps, any test module can safely ``import handler`` and operate
against a fully-mocked S3 client.
"""

import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# ── 1. Path bootstrap ─────────────────────────────────────────────────────────
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ── 2. Environment variable bootstrap ────────────────────────────────────────
# These must be present before config.load_config() runs at handler import time.
os.environ.setdefault("SOURCE_BUCKET", "daisy-source-test")
os.environ.setdefault("PROCESSED_BUCKET", "daisy-processed-test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
# Dummy credentials prevent botocore NoCredentialsError without real AWS access.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test-key-id")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test-secret-key")

# ── 3. Patch boto3.client globally ────────────────────────────────────────────
# handler.py calls boto3.client("s3", ...) at module level; this patch must be
# active before that import happens. After import, handler._s3_client holds a
# reference to _GLOBAL_MOCK_S3, which tests can reconfigure per-test.
_GLOBAL_MOCK_S3 = MagicMock(name="global_mock_s3")
_boto3_patcher = patch("boto3.client", return_value=_GLOBAL_MOCK_S3)
_boto3_patcher.start()


# ── Image factory (also exported for direct use in test modules) ──────────────

def make_image_bytes(
    width: int,
    height: int,
    mode: str = "RGB",
    fmt: str = "JPEG",
    colour=None,
) -> bytes:
    """
    Create a minimal solid-colour image and return its serialised bytes.

    Args:
        width:   Pixel width.
        height:  Pixel height.
        mode:    PIL mode string ("RGB", "RGBA", "L", etc.).
        fmt:     PIL format string ("JPEG", "PNG", etc.).
        colour:  Fill colour. Defaults are chosen per mode if None.

    Returns:
        bytes: Raw image bytes suitable for passing to image_processor functions.
    """
    if colour is None:
        if mode == "RGBA":
            colour = (100, 150, 200, 180)
        elif mode == "L":
            colour = 128
        else:
            colour = (120, 80, 60)

    img = Image.new(mode, (width, height), colour)
    # JPEG does not support alpha; convert before save to avoid PIL errors.
    if fmt == "JPEG" and mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── Session-scoped image fixtures (created once, shared across all tests) ─────

@pytest.fixture(scope="session")
def jpeg_200x200() -> bytes:
    """Standard 200×200 RGB JPEG — the baseline valid input."""
    return make_image_bytes(200, 200, "RGB", "JPEG")


@pytest.fixture(scope="session")
def png_200x200() -> bytes:
    """200×200 RGB PNG — tests format-conversion to JPEG on output."""
    return make_image_bytes(200, 200, "RGB", "PNG")


@pytest.fixture(scope="session")
def rgba_png() -> bytes:
    """200×200 RGBA PNG — tests alpha-channel compositing path."""
    return make_image_bytes(200, 200, "RGBA", "PNG")


@pytest.fixture(scope="session")
def grayscale_jpeg() -> bytes:
    """200×200 grayscale (L-mode) JPEG — single-channel edge case."""
    return make_image_bytes(200, 200, "L", "JPEG")


@pytest.fixture(scope="session")
def large_jpeg() -> bytes:
    """2500×2500 RGB JPEG — exceeds both MAX_OUTPUT_WIDTH and MAX_OUTPUT_HEIGHT."""
    return make_image_bytes(2500, 2500, "RGB", "JPEG")


@pytest.fixture(scope="session")
def tiny_jpeg() -> bytes:
    """1×1 pixel RGB JPEG — stress-tests watermark layout arithmetic."""
    return make_image_bytes(1, 1, "RGB", "JPEG")


@pytest.fixture(scope="session")
def wide_jpeg() -> bytes:
    """3000×50 RGB JPEG — only the width dimension exceeds the resize threshold."""
    return make_image_bytes(3000, 50, "RGB", "JPEG")


@pytest.fixture(scope="session")
def portrait_jpeg() -> bytes:
    """50×3000 RGB JPEG — only the height dimension exceeds the resize threshold."""
    return make_image_bytes(50, 3000, "RGB", "JPEG")


@pytest.fixture(scope="session")
def small_jpeg() -> bytes:
    """100×100 RGB JPEG — below both thresholds; must not be upscaled."""
    return make_image_bytes(100, 100, "RGB", "JPEG")


@pytest.fixture(scope="session")
def zero_bytes() -> bytes:
    """Empty byte string — simulates a zero-byte / corrupted S3 object."""
    return b""


@pytest.fixture(scope="session")
def corrupt_bytes() -> bytes:
    """8 KB of non-image binary garbage — triggers PIL UnidentifiedImageError."""
    return b"\x00\xFF\xAB\xCD\xDE\xAD\xBE\xEF" * 1024


# ── Function-scoped S3 mock fixture ──────────────────────────────────────────

@pytest.fixture
def mock_s3(monkeypatch):
    """
    Replace handler._s3_client with a fresh MagicMock for the duration of one
    test, then restore the original via monkeypatch teardown.

    Usage::

        def test_something(mock_s3):
            mock_s3.get_object.return_value = {"Body": ...}
    """
    import handler
    fresh_mock = MagicMock(name="per_test_s3_client")
    monkeypatch.setattr(handler, "_s3_client", fresh_mock)
    return fresh_mock
