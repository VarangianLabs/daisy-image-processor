"""
Unit tests for src/image_processor.py — pure Python media transformer.

🧪 TEST STRATEGY & MATRIX
--------------------------
Framework : pytest with PIL assertions (no AWS mocking required — zero
            AWS dependencies in this module by architectural design).
Strategy  : Feed raw bytes through each public function and validate the
            output bytes as a re-opened PIL Image. Covers:
              - resize_image()     — bounding-box resize, aspect preservation
              - apply_watermark()  — overlay compositing, font fallback
              - process_image()    — full single-encode pipeline

⚠️ EDGE CASES & VULNERABILITY VECTORS
---------------------------------------
  - Zero bytes            → PIL.UnidentifiedImageError (not a silent empty result)
  - Corrupt binary        → PIL.UnidentifiedImageError
  - RGBA mode input       → Must be composited / converted to RGB before JPEG save
  - Grayscale (L) input   → Accepted; JPEG supports L mode
  - 1×1 pixel             → font_size = max(12, 1//40) = 12; no ZeroDivisionError
  - Large image (2500²)   → Resized to ≤ MAX_OUTPUT_WIDTH × MAX_OUTPUT_HEIGHT
  - Small image (100²)    → thumbnail() must NOT upscale
  - Wide/portrait images  → Aspect ratio preserved within bounding box
  - Missing TTF font      → Falls back to PIL default font; no FileNotFoundError
  - Decompression bomb    → Image.MAX_IMAGE_PIXELS cap verified (security I-04)
  - Double JPEG encode    → Output must be single-encoded, parseable by PIL
"""

import io

import pytest
from PIL import Image

import image_processor as ip
from conftest import make_image_bytes


# ── Helper ────────────────────────────────────────────────────────────────────

def _reopen(raw: bytes) -> Image.Image:
    """Deserialise output bytes back to a PIL Image for structural assertions."""
    return Image.open(io.BytesIO(raw))


# ────────────────────────────────────────────────────────────────────────────
# resize_image
# ────────────────────────────────────────────────────────────────────────────

class TestResizeImage:

    def test_returns_non_empty_bytes(self, jpeg_200x200):
        result = ip.resize_image(jpeg_200x200)
        assert isinstance(result, bytes) and len(result) > 0

    def test_output_format_is_jpeg(self, jpeg_200x200):
        img = _reopen(ip.resize_image(jpeg_200x200))
        assert img.format == "JPEG"

    def test_png_input_converted_to_jpeg(self, png_200x200):
        img = _reopen(ip.resize_image(png_200x200))
        assert img.format == "JPEG"

    def test_rgba_input_converted_to_rgb(self, rgba_png):
        img = _reopen(ip.resize_image(rgba_png))
        assert img.mode == "RGB"

    def test_grayscale_input_accepted(self, grayscale_jpeg):
        result = ip.resize_image(grayscale_jpeg)
        assert len(result) > 0

    # -- Bounding-box enforcement --

    def test_large_image_width_within_max(self, large_jpeg):
        img = _reopen(ip.resize_image(large_jpeg))
        assert img.width <= ip.MAX_OUTPUT_WIDTH

    def test_large_image_height_within_max(self, large_jpeg):
        img = _reopen(ip.resize_image(large_jpeg))
        assert img.height <= ip.MAX_OUTPUT_HEIGHT

    def test_small_image_not_upscaled(self, small_jpeg):
        """100×100 must remain ≤100×100; thumbnail() never enlarges."""
        img = _reopen(ip.resize_image(small_jpeg))
        assert img.width <= 100
        assert img.height <= 100

    def test_custom_max_dimensions_respected(self, large_jpeg):
        result = ip.resize_image(large_jpeg, max_width=640, max_height=480)
        img = _reopen(result)
        assert img.width <= 640
        assert img.height <= 480

    # -- Aspect-ratio preservation --

    def test_wide_image_aspect_ratio_preserved(self, wide_jpeg):
        """3000×50 → width capped at 1280; height scaled proportionally."""
        img = _reopen(ip.resize_image(wide_jpeg))
        assert img.width <= ip.MAX_OUTPUT_WIDTH
        # Expected height ≈ 50 * (output_width / 3000); allow ±2 px for rounding.
        expected_h = int(50 * (img.width / 3000))
        assert abs(img.height - expected_h) <= 2

    def test_portrait_image_aspect_ratio_preserved(self, portrait_jpeg):
        """50×3000 → height capped at 1280; width scaled proportionally."""
        img = _reopen(ip.resize_image(portrait_jpeg))
        assert img.height <= ip.MAX_OUTPUT_HEIGHT
        expected_w = int(50 * (img.height / 3000))
        assert abs(img.width - expected_w) <= 2

    def test_exact_boundary_image_not_reduced_further(self):
        """Image at exactly 1280×1280 must not be further shrunk."""
        exact = make_image_bytes(1280, 1280, "RGB", "JPEG")
        img = _reopen(ip.resize_image(exact))
        assert img.width == 1280
        assert img.height == 1280

    # -- Failure modes --

    def test_zero_bytes_raises(self, zero_bytes):
        with pytest.raises(Exception):
            ip.resize_image(zero_bytes)

    def test_corrupt_bytes_raises(self, corrupt_bytes):
        with pytest.raises(Exception):
            ip.resize_image(corrupt_bytes)


# ────────────────────────────────────────────────────────────────────────────
# apply_watermark
# ────────────────────────────────────────────────────────────────────────────

class TestApplyWatermark:

    def test_returns_non_empty_bytes(self, jpeg_200x200):
        assert len(ip.apply_watermark(jpeg_200x200)) > 0

    def test_output_format_is_jpeg(self, jpeg_200x200):
        img = _reopen(ip.apply_watermark(jpeg_200x200))
        assert img.format == "JPEG"

    def test_output_mode_is_rgb(self, jpeg_200x200):
        img = _reopen(ip.apply_watermark(jpeg_200x200))
        assert img.mode == "RGB"

    def test_rgba_input_converted_to_rgb(self, rgba_png):
        img = _reopen(ip.apply_watermark(rgba_png))
        assert img.mode == "RGB"

    def test_grayscale_input_produces_output(self, grayscale_jpeg):
        assert len(ip.apply_watermark(grayscale_jpeg)) > 0

    def test_custom_watermark_text_accepted(self, jpeg_200x200):
        result = ip.apply_watermark(jpeg_200x200, text="CONFIDENTIAL")
        img = _reopen(result)
        assert img.format == "JPEG"

    def test_empty_watermark_text_does_not_crash(self, jpeg_200x200):
        """Rendering an empty string must not raise; output must be valid."""
        result = ip.apply_watermark(jpeg_200x200, text="")
        assert len(result) > 0

    def test_tiny_image_does_not_raise(self, tiny_jpeg):
        """
        1×1 px image: font_size = max(12, 1 // 40) = 12.
        Watermark text will overflow the image bounds — must not crash.
        """
        result = ip.apply_watermark(tiny_jpeg)
        assert len(result) > 0

    def test_font_fallback_when_ttf_missing(self, jpeg_200x200, monkeypatch):
        """
        If DejaVuSans.ttf is absent, the IOError path must fall back to
        PIL's built-in default font without propagating the exception.
        """
        monkeypatch.setattr(ip, "_FONT_PATH", "/nonexistent/DejaVuSans.ttf")
        result = ip.apply_watermark(jpeg_200x200)
        assert len(result) > 0

    def test_output_dimensions_unchanged_by_watermark(self, jpeg_200x200):
        """Watermarking must not alter image dimensions."""
        original = _reopen(jpeg_200x200)
        result = _reopen(ip.apply_watermark(jpeg_200x200))
        assert result.width == original.width
        assert result.height == original.height

    def test_zero_bytes_raises(self, zero_bytes):
        with pytest.raises(Exception):
            ip.apply_watermark(zero_bytes)

    def test_corrupt_bytes_raises(self, corrupt_bytes):
        with pytest.raises(Exception):
            ip.apply_watermark(corrupt_bytes)


# ────────────────────────────────────────────────────────────────────────────
# process_image — full single-encode pipeline
# ────────────────────────────────────────────────────────────────────────────

class TestProcessImage:

    def test_returns_non_empty_bytes(self, jpeg_200x200):
        assert len(ip.process_image(jpeg_200x200)) > 0

    def test_output_is_valid_jpeg(self, jpeg_200x200):
        img = _reopen(ip.process_image(jpeg_200x200))
        assert img.format == "JPEG"

    def test_output_mode_is_rgb(self, jpeg_200x200):
        img = _reopen(ip.process_image(jpeg_200x200))
        assert img.mode == "RGB"

    def test_large_image_width_within_max(self, large_jpeg):
        img = _reopen(ip.process_image(large_jpeg))
        assert img.width <= ip.MAX_OUTPUT_WIDTH

    def test_large_image_height_within_max(self, large_jpeg):
        img = _reopen(ip.process_image(large_jpeg))
        assert img.height <= ip.MAX_OUTPUT_HEIGHT

    def test_small_image_not_upscaled(self, small_jpeg):
        img = _reopen(ip.process_image(small_jpeg))
        assert img.width <= 100
        assert img.height <= 100

    def test_png_input_yields_jpeg_output(self, png_200x200):
        img = _reopen(ip.process_image(png_200x200))
        assert img.format == "JPEG"

    def test_rgba_png_produces_rgb_jpeg(self, rgba_png):
        img = _reopen(ip.process_image(rgba_png))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_grayscale_completes_pipeline(self, grayscale_jpeg):
        assert len(ip.process_image(grayscale_jpeg)) > 0

    def test_tiny_image_survives_full_pipeline(self, tiny_jpeg):
        """1×1 px image must survive resize + watermark without crashing."""
        assert len(ip.process_image(tiny_jpeg)) > 0

    def test_wide_image_output_within_bounds(self, wide_jpeg):
        img = _reopen(ip.process_image(wide_jpeg))
        assert img.width <= ip.MAX_OUTPUT_WIDTH
        assert img.height <= ip.MAX_OUTPUT_HEIGHT

    def test_portrait_image_output_within_bounds(self, portrait_jpeg):
        img = _reopen(ip.process_image(portrait_jpeg))
        assert img.width <= ip.MAX_OUTPUT_WIDTH
        assert img.height <= ip.MAX_OUTPUT_HEIGHT

    def test_output_is_single_encoded_jpeg(self, jpeg_200x200):
        """
        H-01 guardrail: the pipeline must produce exactly ONE JPEG encode.
        If bytes were double-encoded (bytes-in-bytes), _reopen() would raise
        UnidentifiedImageError or return a nonsense image.
        """
        result = ip.process_image(jpeg_200x200)
        img = _reopen(result)
        assert img.width > 0 and img.height > 0

    def test_zero_bytes_raises(self, zero_bytes):
        with pytest.raises(Exception):
            ip.process_image(zero_bytes)

    def test_corrupt_bytes_raises(self, corrupt_bytes):
        with pytest.raises(Exception):
            ip.process_image(corrupt_bytes)

    # -- Security guardrail --

    def test_decompression_bomb_pixel_cap_is_active(self):
        """
        Security guardrail I-04: MAX_IMAGE_PIXELS must be set to 50,000,000.
        A missing or higher value would allow decompression bomb attacks.
        """
        assert Image.MAX_IMAGE_PIXELS == 50_000_000

    # -- Constants sanity --

    def test_output_format_constant_is_jpeg(self):
        assert ip.OUTPUT_FORMAT == "JPEG"

    def test_max_output_dimensions_constants(self):
        assert ip.MAX_OUTPUT_WIDTH == 1280
        assert ip.MAX_OUTPUT_HEIGHT == 1280
