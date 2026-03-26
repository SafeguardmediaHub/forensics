"""
Forensics Test Harness
----------------------
Minimal automated tests for the visual forensics pipeline.

No external media files required — all test images are generated
synthetically using numpy and PIL so the suite is fully self-contained.

Run:
    python test_forensics.py

Exit code 0 = all tests passed.
Exit code 1 = one or more tests failed.
"""

import os
import sys
import json
import shutil
import tempfile
import traceback

import numpy as np
from PIL import Image

# ── Ensure the project root is on sys.path ───────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from visual_forens import generate_forensic_report


# ============================================================================
# Helpers
# ============================================================================

def _make_clean_image(path: str, size: int = 256) -> None:
    """
    Create a synthetic 'clean' image: a smooth HSV gradient with mild
    Gaussian noise.  Saved as PNG (lossless) so ELA has a valid baseline.
    """
    rng = np.random.default_rng(seed=42)

    # Smooth gradient base
    x = np.linspace(0, 1, size)
    y = np.linspace(0, 1, size)
    xv, yv = np.meshgrid(x, y)

    r = (xv * 200 + 28).astype(np.uint8)
    g = (yv * 200 + 28).astype(np.uint8)
    b = ((1 - xv * 0.5 - yv * 0.5) * 200 + 28).astype(np.uint8)

    # Mild uniform noise
    noise = rng.integers(-8, 9, size=(size, size, 3), dtype=np.int16)
    arr = np.clip(np.stack([r, g, b], axis=-1).astype(np.int16) + noise, 0, 255).astype(np.uint8)

    Image.fromarray(arr, 'RGB').save(path, 'PNG')


def _make_tampered_image(clean_path: str, tampered_path: str) -> None:
    """
    Create a tampered PNG that produces clear signals for all three detectors:

    ELA  — A large block is pre-compressed at JPEG quality=50, then pasted into
            the clean PNG losslessly.  The pre-compressed destination pixels have
            different residuals when ELA re-saves at quality=95, revealing the
            double-compression.

    Noise — The sharp luminance boundary between the smooth gradient background
            and the high-contrast checkerboard regions raises the local-variance
            standard deviation well above the Medium threshold.

    SIFT  — The 8-pixel-cell B&W checkerboard gives the feature detector plenty
            of keypoints; the copy of the same pattern at a spatially distant
            location is found by the FLANN matcher with Lowe's ratio test.
    """
    img = np.array(Image.open(clean_path).convert('RGB'))
    h, w = img.shape[:2]

    block_h = h // 2
    block_w = w // 2

    # Non-overlapping quadrants: top-left → source; bottom-right → destination
    src_y, src_x = 0, 0
    dst_y, dst_x = h // 2, w // 2

    # High-contrast 8-pixel-cell B&W checkerboard (SIFT-friendly)
    cell = 8
    checker = np.zeros((block_h, block_w, 3), dtype=np.uint8)
    for i in range(block_h):
        for j in range(block_w):
            checker[i, j] = 255 if (i // cell + j // cell) % 2 == 0 else 0

    # Source quadrant: uncompressed checkerboard in PNG
    img[src_y:src_y + block_h, src_x:src_x + block_w] = checker

    # Pre-compress ONLY the block via JPEG quality=50, then reload
    # (creates the double-compression signal ELA detects at the destination)
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        Image.fromarray(checker).save(tmp_path, 'JPEG', quality=50)
        compressed = np.array(Image.open(tmp_path).convert('RGB'))
    finally:
        os.unlink(tmp_path)

    # Destination quadrant: paste the pre-compressed copy (copy-move forgery)
    img[dst_y:dst_y + block_h, dst_x:dst_x + block_w] = compressed

    # Save as PNG (lossless) — the destination block retains JPEG-50 artifacts
    Image.fromarray(img, 'RGB').save(tampered_path, 'PNG')


# ============================================================================
# Test runner
# ============================================================================

PASS = '\033[92mPASS\033[0m'
FAIL = '\033[91mFAIL\033[0m'
_results = []


def _test(name: str, fn):
    try:
        fn()
        print(f'  [{PASS}]  {name}')
        _results.append(True)
    except AssertionError as e:
        print(f'  [{FAIL}]  {name}')
        print(f'           AssertionError: {e}')
        _results.append(False)
    except Exception:
        print(f'  [{FAIL}]  {name}')
        traceback.print_exc()
        _results.append(False)


# ============================================================================
# Tests
# ============================================================================

def test_clean_image_scores_low(clean_path, tmpdir):
    """A synthetic clean image should have low tampering likelihood."""
    report = generate_forensic_report(clean_path, tmpdir)
    assert 'error' not in report, f"report errored: {report.get('error')}"
    likelihood = report['overall_assessment']['tampering_likelihood']
    assert likelihood < 50, (
        f"Clean image scored {likelihood} — expected < 50. "
        "Check that the synthetic image is genuinely clean."
    )


def test_tampered_image_scores_higher(clean_path, tampered_path, clean_dir, tampered_dir):
    """Tampered image must score strictly higher than the clean original."""
    clean_report    = generate_forensic_report(clean_path,    clean_dir)
    tampered_report = generate_forensic_report(tampered_path, tampered_dir)

    assert 'error' not in clean_report,    f"clean report errored: {clean_report.get('error')}"
    assert 'error' not in tampered_report, f"tampered report errored: {tampered_report.get('error')}"

    clean_score    = clean_report['overall_assessment']['tampering_likelihood']
    tampered_score = tampered_report['overall_assessment']['tampering_likelihood']

    assert tampered_score > clean_score, (
        f"Tampered image ({tampered_score}) did not score higher than clean "
        f"({clean_score}). Consider lowering VF_ELA_MEDIUM_THRESHOLD or "
        f"VF_CLONE_MEDIUM_THRESHOLD."
    )


def test_is_video_frame_skips_exif_penalty(clean_path, tmpdir_a, tmpdir_b):
    """
    With is_video_frame=False, a PNG with no EXIF gets the missing-EXIF
    penalty.  With is_video_frame=True the same image must score lower
    (penalty omitted).
    """
    report_image = generate_forensic_report(
        clean_path, tmpdir_a, is_video_frame=False
    )
    report_frame = generate_forensic_report(
        clean_path, tmpdir_b, is_video_frame=True
    )

    assert 'error' not in report_image, report_image.get('error')
    assert 'error' not in report_frame, report_frame.get('error')

    score_image = report_image['overall_assessment']['tampering_likelihood']
    score_frame = report_frame['overall_assessment']['tampering_likelihood']

    assert score_frame <= score_image, (
        f"is_video_frame=True scored {score_frame} but is_video_frame=False "
        f"scored {score_image}. The video-frame path should be ≤ (no EXIF penalty)."
    )

    # Also assert user_friendly_summary is absent for video frames
    assert 'user_friendly_summary' not in report_frame, (
        "user_friendly_summary should not be generated for video frames."
    )

    # And reverse_search is absent for video frames
    assert 'reverse_search' not in report_frame, (
        "reverse_search should not be included for video frames."
    )


def test_is_video_frame_skips_jpeg_analysis(clean_path, tmpdir):
    """JPEG compression analysis must be skipped when is_video_frame=True."""
    report = generate_forensic_report(clean_path, tmpdir, is_video_frame=True)
    assert 'error' not in report, report.get('error')

    jpeg_result = report['manipulation_detection'].get('jpeg_compression', {})
    assert jpeg_result.get('skipped') is True, (
        f"Expected jpeg_compression to be skipped for video frame, "
        f"got: {jpeg_result}"
    )


def test_report_structure(clean_path, tmpdir):
    """Report must contain all required top-level keys."""
    report = generate_forensic_report(clean_path, tmpdir)
    assert 'error' not in report, report.get('error')

    required_keys = [
        'analysis_timestamp', 'metadata', 'manipulation_detection',
        'overall_assessment', 'verification',
    ]
    for key in required_keys:
        assert key in report, f"Missing key '{key}' in report"

    assessment = report['overall_assessment']
    for key in ('tampering_likelihood', 'verdict', 'confidence'):
        assert key in assessment, f"Missing key '{key}' in overall_assessment"

    assert isinstance(assessment['tampering_likelihood'], (int, float))
    assert 0 <= assessment['tampering_likelihood'] <= 100
    assert assessment['verdict'] in ('Likely Authentic', 'Possibly Tampered', 'Likely Tampered')


def test_forensic_config_loaded():
    """forensic_config.py must be importable and expose expected constants."""
    from forensic_config import (
        FA_TAMPERED_CONFIDENCE, FA_REVIEW_CONFIDENCE,
        VF_ELA_HIGH_THRESHOLD, VF_CLONE_HIGH_THRESHOLD,
        CLEANUP_MAX_AGE_HOURS,
    )
    assert FA_TAMPERED_CONFIDENCE > FA_REVIEW_CONFIDENCE, (
        "FA_TAMPERED_CONFIDENCE must be > FA_REVIEW_CONFIDENCE"
    )
    assert VF_ELA_HIGH_THRESHOLD > 0
    assert VF_CLONE_HIGH_THRESHOLD > 0
    assert CLEANUP_MAX_AGE_HOURS > 0


def test_forensic_primitives_importable():
    """forensic_primitives must export the four expected functions."""
    from forensic_primitives import (
        error_level_analysis,
        noise_analysis,
        copy_move_detection_sift,
        copy_move_detection_dct_fallback,
    )
    for fn in (error_level_analysis, noise_analysis,
               copy_move_detection_sift, copy_move_detection_dct_fallback):
        assert callable(fn), f"{fn} is not callable"


def test_no_cross_module_imports():
    """frame_analysis and visual_forens must not import each other."""
    import ast

    def _imports(filename):
        with open(os.path.join(ROOT, filename)) as fh:
            tree = ast.parse(fh.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, 'module', '') or ''
                found.add(mod)
                for alias in getattr(node, 'names', []):
                    found.add(alias.name)
        return found

    fa_imports = _imports('frame_analysis.py')
    vf_imports = _imports('visual_forens.py')

    assert not any('visual_forens' in m for m in fa_imports), \
        "frame_analysis.py imports visual_forens — decoupling broken"
    assert not any('frame_analysis' in m for m in vf_imports), \
        "visual_forens.py imports frame_analysis — decoupling broken"


# ============================================================================
# Main
# ============================================================================

def main():
    print("\nForensic Analysis Test Harness")
    print("=" * 50)

    with tempfile.TemporaryDirectory() as base_tmp:
        # Create synthetic test images
        clean_path    = os.path.join(base_tmp, 'clean.png')
        tampered_path = os.path.join(base_tmp, 'tampered.png')

        print("\nGenerating synthetic test images...")
        _make_clean_image(clean_path)
        _make_tampered_image(clean_path, tampered_path)
        print(f"  Clean image:    {clean_path}")
        print(f"  Tampered image: {tampered_path}")

        # Scratch output dirs for each test
        def _scratch(name):
            d = os.path.join(base_tmp, name)
            os.makedirs(d, exist_ok=True)
            return d

        print("\nRunning tests...")

        _test("Clean image scores low",
              lambda: test_clean_image_scores_low(clean_path, _scratch('t1')))

        _test("Tampered image scores higher than clean",
              lambda: test_tampered_image_scores_higher(
                  clean_path, tampered_path, _scratch('t2c'), _scratch('t2t')))

        _test("is_video_frame=True skips EXIF penalty",
              lambda: test_is_video_frame_skips_exif_penalty(
                  clean_path, _scratch('t3a'), _scratch('t3b')))

        _test("is_video_frame=True skips JPEG analysis",
              lambda: test_is_video_frame_skips_jpeg_analysis(
                  clean_path, _scratch('t4')))

        _test("Report structure is complete",
              lambda: test_report_structure(clean_path, _scratch('t5')))

        _test("forensic_config loads correctly",
              test_forensic_config_loaded)

        _test("forensic_primitives exports expected functions",
              test_forensic_primitives_importable)

        _test("No cross-module imports (decoupling)",
              test_no_cross_module_imports)

    print("\n" + "=" * 50)
    passed = sum(_results)
    total  = len(_results)
    print(f"Results: {passed}/{total} passed")

    if passed < total:
        print("\nSome tests failed. Review output above.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
        sys.exit(0)


if __name__ == '__main__':
    main()
