"""
Media Transformer — Pure Python image processing core.

This module contains ZERO AWS dependencies. It is fully testable without any
AWS execution context. All binary operations use io.BytesIO context managers
to keep the memory profile under the 512 MB Lambda ceiling.
"""

import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

# I-04: Cap decompression size to 50 MP to block decompression bomb attacks.
# A malicious PNG can represent hundreds of megapixels in a tiny file.
# PIL raises DecompressionBombError automatically beyond this threshold.
Image.MAX_IMAGE_PIXELS = 50_000_000

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

MAX_OUTPUT_WIDTH = 1280
MAX_OUTPUT_HEIGHT = 1280
WATERMARK_TEXT = "© Daisy"
WATERMARK_OPACITY = 128  # 0–255; 128 = 50% transparent
OUTPUT_FORMAT = "JPEG"
OUTPUT_QUALITY = 85

# H-03: Bundled open-source font (SIL Open Font License).
# arial.ttf is a Windows-only proprietary font absent on Amazon Linux.
_FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")


def _resize(
    img: "Image.Image",
    max_width: int = MAX_OUTPUT_WIDTH,
    max_height: int = MAX_OUTPUT_HEIGHT,
) -> "Image.Image":
    """Resize a PIL Image within the bounding box. Returns the modified image."""
    original_size = img.size
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    logger.debug("Resized %s → %s", original_size, img.size)
    return img


def resize_image(
    image_bytes: bytes,
    max_width: int = MAX_OUTPUT_WIDTH,
    max_height: int = MAX_OUTPUT_HEIGHT,
) -> bytes:
    """
    Resize an image to fit within the given bounding box while preserving the
    original aspect ratio. Uses LANCZOS resampling for high-quality output.

    Args:
        image_bytes: Raw binary content of the source image.
        max_width:   Maximum allowed output width in pixels.
        max_height:  Maximum allowed output height in pixels.

    Returns:
        bytes: Binary content of the resized image in JPEG format.
    """
    logger.info("Resizing image: max_width=%d, max_height=%d", max_width, max_height)

    with io.BytesIO(image_bytes) as input_buffer:
        with Image.open(input_buffer) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img = _resize(img, max_width, max_height)
            with io.BytesIO() as output_buffer:
                img.save(
                    output_buffer,
                    format=OUTPUT_FORMAT,
                    quality=OUTPUT_QUALITY,
                    optimize=True,
                )
                return output_buffer.getvalue()


def _apply_watermark_img(
    img: "Image.Image", text: str = WATERMARK_TEXT
) -> "Image.Image":
    """Apply watermark to a PIL Image. Returns a composited RGB image."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(12, img.width // 40)
    try:
        font = ImageFont.truetype(_FONT_PATH, font_size)
    except (IOError, OSError):
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = img.width - text_width - 10
    y = img.height - text_height - 10

    draw.text((x, y), text, fill=(255, 255, 255, WATERMARK_OPACITY), font=font)

    base_rgba = img.convert("RGBA")
    composited = Image.alpha_composite(base_rgba, overlay)
    return composited.convert("RGB")


def apply_watermark(image_bytes: bytes, text: str = WATERMARK_TEXT) -> bytes:
    """
    Overlay a semi-transparent text watermark in the bottom-right corner.

    Args:
        image_bytes: Raw binary content of the source image.
        text:        Watermark string to render.

    Returns:
        bytes: Binary content of the watermarked image in JPEG format.
    """
    logger.info("Applying watermark: text='%s'", text)

    with io.BytesIO(image_bytes) as input_buffer:
        with Image.open(input_buffer) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img = _apply_watermark_img(img, text)
            with io.BytesIO() as output_buffer:
                img.save(
                    output_buffer,
                    format=OUTPUT_FORMAT,
                    quality=OUTPUT_QUALITY,
                    optimize=True,
                )
                return output_buffer.getvalue()


def process_image(image_bytes: bytes) -> bytes:
    """
    Full processing pipeline: resize followed by watermark application.

    H-01: Single JPEG encode at pipeline exit. The private helpers _resize()
    and _apply_watermark_img() operate on PIL Image objects directly, avoiding
    the lossy double-compression of the previous bytes-in / bytes-out design.

    This is the single public entry point for the Media Transformer.
    It is intentionally decoupled from all AWS primitives.

    Args:
        image_bytes: Raw binary content of the source image.

    Returns:
        bytes: Fully processed image binary ready for S3 storage.
    """
    logger.info(
        "Starting image processing pipeline (input size: %d bytes)", len(image_bytes)
    )

    with io.BytesIO(image_bytes) as buf:
        with Image.open(buf) as img:
            img.load()  # Force eager decode before the buffer closes
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img = _resize(img)
            img = _apply_watermark_img(img)

    with io.BytesIO() as out:
        img.save(out, format=OUTPUT_FORMAT, quality=OUTPUT_QUALITY, optimize=True)
        result = out.getvalue()

    logger.info("Processing complete (output size: %d bytes)", len(result))
    return result
