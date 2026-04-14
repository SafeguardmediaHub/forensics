"""
Screenshot Detection
--------------------
Heuristic-based detection of whether an image originated from a screen
capture rather than a camera or direct export.

Combines multiple independent signals, each with a weighted score, into
a composite screenshot likelihood score (0-100).

Dependencies: Pillow and OpenCV (both already installed). No new packages.
"""

import logging
from typing import Any

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── Known screenshot tool identifiers ────────────────────────────────────────
# Matched case-insensitively against EXIF Software tag and PNG tEXt chunks.
_SCREENSHOT_SOFTWARE = (
    "snipping tool",
    "snip & sketch",
    "screenshot",
    "flameshot",
    "greenshot",
    "shareX",
    "sharex",
    "lightshot",
    "spectacle",
    "gnome-screenshot",
    "gnome screenshot",
    "xfce4-screenshooter",
    "scrot",
    "maim",
    "screencapture",      # macOS CLI
    "grab",               # macOS legacy
    "windows.screenshot",
    "xbox game bar",
    "snipaste",
    "ksnip",
    "shutter",
)

# ── Common screen resolutions ────────────────────────────────────────────────
_SCREEN_RESOLUTIONS = {
    # Standard
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900), (1280, 720),
    (1280, 800), (1280, 1024), (1024, 768), (1600, 900), (2560, 1440),
    (3840, 2160), (2560, 1600), (3440, 1440), (2880, 1800),
    # HiDPI / Retina (2x)
    (2560, 1440), (2880, 1620), (3072, 1728), (5120, 2880),
    # Common phone screen captures
    (1080, 1920), (1440, 2560), (1080, 2400), (1170, 2532),
    (1284, 2778), (1125, 2436), (1242, 2688), (750, 1334),
    (828, 1792), (1080, 2340),
}

# ── Standard screen DPIs ────────────────────────────────────────────────────
_SCREEN_DPIS = {72, 96, 120, 144, 192}

# ── Camera EXIF tags that screenshots never have ────────────────────────────
# Tag IDs for Make(271), Model(272), FocalLength(37386),
# ExposureTime(33434), FNumber(33437), ISOSpeedRatings(34855)
_CAMERA_EXIF_TAGS = {271, 272, 37386, 33434, 33437, 34855}

# ── Signal weights ──────────────────────────────────────────────────────────
_WEIGHTS = {
    "software_tag": 45,
    "missing_camera_exif": 20,
    "screen_dpi": 10,
    "screen_dimensions": 10,
    "no_exif_thumbnail": 8,
    "png_no_camera": 15,
    "sharp_edges": 12,
}


def detect_screenshot(image_path: str) -> dict[str, Any]:
    """Run all screenshot-detection checks on an image file.

    Returns a dict suitable for embedding in the manipulation_detection
    block of an image forensic report.
    """
    signals: list[dict[str, Any]] = []
    total_weight = 0.0

    try:
        img = Image.open(image_path)
    except Exception:
        logger.debug("Failed to open %s for screenshot detection", image_path)
        return {"screenshot_signals": [], "screenshot_score": 0, "confidence": 0.0}

    exif_data = img.getexif() or {}
    img_format = (img.format or "").upper()
    width, height = img.size

    # ── 1. Software tag ──────────────────────────────────────────────────
    software_match = _check_software_tag(img, exif_data)
    if software_match:
        signals.append(software_match)
        total_weight += _WEIGHTS["software_tag"]

    # ── 2. Missing camera EXIF ───────────────────────────────────────────
    camera_result = _check_camera_exif(exif_data)
    if camera_result:
        signals.append(camera_result)
        total_weight += _WEIGHTS["missing_camera_exif"]

    # ── 3. Screen DPI ────────────────────────────────────────────────────
    dpi_result = _check_dpi(img, exif_data)
    if dpi_result:
        signals.append(dpi_result)
        total_weight += _WEIGHTS["screen_dpi"]

    # ── 4. Screen-resolution dimensions ──────────────────────────────────
    dims_result = _check_screen_dimensions(width, height)
    if dims_result:
        signals.append(dims_result)
        total_weight += _WEIGHTS["screen_dimensions"]

    # ── 5. No EXIF thumbnail ─────────────────────────────────────────────
    thumb_result = _check_no_thumbnail(exif_data)
    if thumb_result:
        signals.append(thumb_result)
        total_weight += _WEIGHTS["no_exif_thumbnail"]

    # ── 6. PNG + no camera data combo ────────────────────────────────────
    if img_format == "PNG" and camera_result:
        signals.append({
            "check": "png_no_camera",
            "result": "PNG format with no camera metadata",
        })
        total_weight += _WEIGHTS["png_no_camera"]

    # ── 7. Sharp edges / no lens blur ────────────────────────────────────
    sharp_result = _check_edge_sharpness(image_path)
    if sharp_result:
        signals.append(sharp_result)
        total_weight += _WEIGHTS["sharp_edges"]

    screenshot_score = min(total_weight, 100.0)
    confidence = min(screenshot_score / 100.0, 1.0) if signals else 0.0

    return {
        "screenshot_signals": signals,
        "screenshot_score": round(screenshot_score, 1),
        "confidence": round(confidence, 2),
    }


def _check_software_tag(img: Image.Image, exif_data: dict) -> dict[str, Any] | None:
    """Check EXIF Software tag and PNG text chunks for screenshot tools."""
    # EXIF Software tag (305)
    software = exif_data.get(305, "")
    if not software:
        # Try PNG text chunks
        software = img.info.get("Software", "")

    if not software:
        return None

    software_lower = str(software).lower()
    for tool in _SCREENSHOT_SOFTWARE:
        if tool.lower() in software_lower:
            return {
                "check": "software_tag",
                "matched": tool,
                "raw_value": str(software),
            }
    return None


def _check_camera_exif(exif_data: dict) -> dict[str, Any] | None:
    """Check if camera-specific EXIF tags are missing."""
    present = {tag for tag in _CAMERA_EXIF_TAGS if tag in exif_data}
    missing = _CAMERA_EXIF_TAGS - present

    # If most camera tags are missing, flag it.
    if len(missing) >= 4:
        tag_names = {
            271: "Make", 272: "Model", 37386: "FocalLength",
            33434: "ExposureTime", 33437: "FNumber", 34855: "ISO",
        }
        missing_names = [tag_names.get(t, str(t)) for t in sorted(missing)]
        return {
            "check": "missing_camera_exif",
            "missing_tags": missing_names,
        }
    return None


def _check_dpi(img: Image.Image, exif_data: dict) -> dict[str, Any] | None:
    """Check if DPI matches common screen densities."""
    dpi = img.info.get("dpi")
    if dpi and isinstance(dpi, tuple) and len(dpi) >= 2:
        x_dpi, y_dpi = int(round(dpi[0])), int(round(dpi[1]))
        if x_dpi in _SCREEN_DPIS and x_dpi == y_dpi:
            # Only flag if camera EXIF is also missing (screens have these DPIs
            # but so do many exported photos).
            if not any(tag in exif_data for tag in (271, 272)):
                return {
                    "check": "screen_dpi",
                    "dpi": x_dpi,
                }
    return None


def _check_screen_dimensions(width: int, height: int) -> dict[str, Any] | None:
    """Check if image dimensions match common screen resolutions."""
    if (width, height) in _SCREEN_RESOLUTIONS:
        return {
            "check": "screen_dimensions",
            "matched": f"{width}x{height}",
        }
    return None


def _check_no_thumbnail(exif_data: dict) -> dict[str, Any] | None:
    """Check if EXIF thumbnail is absent (camera JPEGs always embed one)."""
    # IFD1 contains the thumbnail. If there's no EXIF at all, this check
    # is not meaningful (already covered by missing_camera_exif).
    if not exif_data:
        return None

    # Pillow's getexif() doesn't directly expose IFD1. Check for thumbnail
    # related tags: 513 (JPEGInterchangeFormat) and 514 (JPEGInterchangeFormatLength).
    has_thumbnail = 513 in exif_data or 514 in exif_data
    if not has_thumbnail and any(tag in exif_data for tag in (271, 272)):
        # Has some EXIF but no thumbnail — unusual for camera photos.
        return {
            "check": "no_exif_thumbnail",
            "result": "EXIF present but no embedded thumbnail",
        }
    return None


def _check_edge_sharpness(image_path: str) -> dict[str, Any] | None:
    """Check for unnaturally sharp edges typical of screen renders.

    Screenshots have mathematically perfect edges (text, UI elements)
    with no optical blur. Camera photos always have some degree of
    lens softness. Uses Laplacian variance as a proxy.
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            return None

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()

        # Very high Laplacian variance (>2000) suggests pixel-perfect
        # edges typical of screenshots. Camera photos rarely exceed ~1500
        # due to optical blur. Threshold is conservative to avoid false
        # positives on high-contrast photos.
        if laplacian_var > 2000:
            return {
                "check": "sharp_edges",
                "laplacian_variance": round(float(laplacian_var), 1),
                "result": "Unusually sharp edges detected",
            }
    except Exception:
        logger.debug("Edge sharpness check failed", exc_info=True)

    return None
