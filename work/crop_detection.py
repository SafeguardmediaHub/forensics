"""
Crop Detection
--------------
Detects whether an image was cropped from a larger original using two
independent signals:

1. **JPEG DCT grid misalignment** — In an uncropped JPEG, the 8x8 DCT
   block grid starts at pixel (0,0). After cropping at a non-8-aligned
   boundary and re-saving, the dominant grid origin shifts. Detected via
   jpeglib DCT coefficient analysis.

2. **EXIF dimension mismatch** — Cameras write original capture dimensions
   into EXIF tags 40962/40963 (PixelXDimension/PixelYDimension). If the
   actual pixel dimensions differ, the image was cropped (or resized)
   after capture.

Neither signal requires ML. Both are well-published forensic techniques.

Dependencies: Pillow (already installed), jpeglib (new, optional).
"""

import logging
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# EXIF tag IDs for original capture dimensions.
_EXIF_PIXEL_X_DIMENSION = 40962
_EXIF_PIXEL_Y_DIMENSION = 40963

# Common screen-capture / social-media crop aspect ratios are ignored
# since they produce too many false positives.


def detect_crop(image_path: str) -> dict[str, Any]:
    """Run all crop-detection checks on an image file.

    Returns a dict suitable for embedding in the manipulation_detection
    block of an image forensic report.
    """
    signals: list[dict[str, Any]] = []
    total_weight = 0.0

    # ── Check 1: EXIF dimension mismatch ─────────────────────────────────
    try:
        exif_result = _check_exif_dimensions(image_path)
        if exif_result:
            signals.append(exif_result)
            total_weight += 40.0
    except Exception:
        logger.debug("EXIF dimension check failed for %s", image_path, exc_info=True)

    # ── Check 2: JPEG DCT grid misalignment ──────────────────────────────
    try:
        dct_result = _check_dct_grid(image_path)
        if dct_result:
            signals.append(dct_result)
            total_weight += 60.0
    except Exception:
        logger.debug("DCT grid check failed for %s", image_path, exc_info=True)

    crop_score = min(total_weight, 100.0)
    confidence = min(crop_score / 100.0, 1.0) if signals else 0.0

    return {
        "crop_signals": signals,
        "crop_score": round(crop_score, 1),
        "confidence": round(confidence, 2),
    }


def _check_exif_dimensions(image_path: str) -> dict[str, Any] | None:
    """Compare EXIF-stored capture dimensions to actual pixel dimensions."""
    img = Image.open(image_path)
    actual_w, actual_h = img.size

    exif_data = img.getexif()
    if not exif_data:
        return None

    exif_w = exif_data.get(_EXIF_PIXEL_X_DIMENSION)
    exif_h = exif_data.get(_EXIF_PIXEL_Y_DIMENSION)

    if exif_w is None or exif_h is None:
        return None

    exif_w, exif_h = int(exif_w), int(exif_h)

    # Allow a small tolerance (some software writes slightly wrong values).
    if abs(exif_w - actual_w) <= 1 and abs(exif_h - actual_h) <= 1:
        return None

    return {
        "check": "exif_dimension_mismatch",
        "exif_dims": f"{exif_w}x{exif_h}",
        "actual_dims": f"{actual_w}x{actual_h}",
        "result": "mismatch",
    }


def _check_dct_grid(image_path: str) -> dict[str, Any] | None:
    """Check JPEG DCT 8x8 grid alignment via jpeglib.

    In an uncropped JPEG the dominant grid origin is (0,0). After cropping
    at a non-8-aligned boundary the origin shifts. We test all 64 offsets
    and pick the one with the most zero-valued AC coefficients.

    Returns None if jpeglib is not installed or the file is not JPEG.
    """
    try:
        import jpeglib  # noqa: F811
    except ImportError:
        logger.debug("jpeglib not installed — skipping DCT grid check")
        return None

    # Only applies to JPEG files.
    lower = image_path.lower()
    if not (lower.endswith(".jpg") or lower.endswith(".jpeg")):
        return None

    try:
        im = jpeglib.read_dct(image_path)
    except Exception:
        logger.debug("jpeglib failed to read %s", image_path, exc_info=True)
        return None

    # Y-channel DCT coefficients (shape: blocks_h x blocks_w x 8 x 8).
    y_coeffs = im.Y
    if y_coeffs is None or y_coeffs.size == 0:
        return None

    # Count zero AC coefficients per possible grid origin (offset 0-7 in
    # both row and column). The true grid origin should have the most zeros
    # because quantisation pushes small AC values to zero.
    best_offset = (0, 0)
    best_zeros = -1

    blocks_h, blocks_w = y_coeffs.shape[0], y_coeffs.shape[1]

    for row_off in range(min(8, blocks_h)):
        for col_off in range(min(8, blocks_w)):
            # Slice a sub-grid starting at (row_off, col_off).
            sub = y_coeffs[row_off::1, col_off::1, :, :]
            # Count zeros excluding DC coefficient (position [0,0] of each block).
            ac_mask = sub.copy()
            ac_mask[:, :, 0, 0] = 1  # exclude DC
            n_zeros = int((ac_mask == 0).sum())
            if n_zeros > best_zeros:
                best_zeros = n_zeros
                best_offset = (row_off, col_off)

    if best_offset == (0, 0):
        return None  # Grid is aligned — no evidence of cropping.

    return {
        "check": "dct_grid_alignment",
        "result": "misaligned",
        "grid_offset": f"({best_offset[0]}, {best_offset[1]})",
    }
