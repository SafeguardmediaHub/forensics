"""
Forensic Configuration
----------------------
All tunable thresholds for both engines, sourced from environment variables
so they can be adjusted without touching code.

To calibrate against a ground-truth dataset, tune these values and restart
the Celery workers + Flask app.  No code changes required.

Environment variables and their defaults
-----------------------------------------

Frame Analysis — verdict
  FA_TAMPERED_CONFIDENCE      80.0   confidence >= this AND multi-HIGH → LIKELY_TAMPERED
  FA_REVIEW_CONFIDENCE        30.0   confidence >= this               → REVIEW_RECOMMENDED

Frame Analysis — severity point values
  FA_SEV_HIGH_PTS             25.0
  FA_SEV_MEDIUM_PTS           12.0
  FA_SEV_LOW_PTS               4.0

Frame Analysis — spatial finding thresholds (per-frame ELA / noise / clone)
  FA_ELA_HIGH_THRESHOLD       15.0   ela_score above this → HIGH finding
  FA_ELA_MEDIUM_THRESHOLD      8.0   ela_score above this → MEDIUM finding
  FA_NOISE_HIGH_THRESHOLD     50.0   noise_inconsistency_score above this → HIGH finding
  FA_NOISE_MEDIUM_THRESHOLD   25.0   noise_inconsistency_score above this → MEDIUM finding
  FA_CLONE_HIGH_THRESHOLD      5.0   clone_score above this → HIGH finding
  FA_CLONE_MEDIUM_THRESHOLD    2.0   clone_score above this → MEDIUM finding

Visual Forensics — tampering likelihood scoring (images)
  VF_ELA_HIGH_PTS             30.0
  VF_ELA_MEDIUM_PTS           18.0
  VF_NOISE_HIGH_PTS           30.0
  VF_NOISE_MEDIUM_PTS         18.0
  VF_CLONE_HIGH_PTS           35.0
  VF_CLONE_MEDIUM_PTS         20.0
  VF_JPEG_RECOMPRESS_PTS      25.0
  VF_MISSING_EXIF_PTS         17.0

Visual Forensics — same detector thresholds (used when scoring each image)
  VF_ELA_HIGH_THRESHOLD       15.0
  VF_ELA_MEDIUM_THRESHOLD      8.0
  VF_NOISE_HIGH_THRESHOLD     50.0
  VF_NOISE_MEDIUM_THRESHOLD   25.0
  VF_CLONE_HIGH_THRESHOLD      5.0
  VF_CLONE_MEDIUM_THRESHOLD    2.0

  VF_TAMPERED_THRESHOLD       60.0   tampering_likelihood above this → Likely Tampered
  VF_REVIEW_THRESHOLD         30.0   tampering_likelihood above this → Possibly Tampered

Visual Forensics — video aggregate weights
  VF_VIDEO_MEAN_WEIGHT         0.7   weight given to mean frame score
  VF_VIDEO_MAX_WEIGHT          0.3   weight given to worst-frame score

  VF_VIDEO_TAMPERED_THRESHOLD 60.0   aggregate above this → Likely Tampered (video)
  VF_VIDEO_REVIEW_THRESHOLD   30.0   aggregate above this → Possibly Tampered (video)

File cleanup
  CLEANUP_MAX_AGE_HOURS       24     job dirs/uploads older than this are deleted
  CLEANUP_INTERVAL_SECONDS  3600     how often the Celery beat cleanup task runs
"""

import os


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ── Frame Analysis — verdict ─────────────────────────────────────────────────
FA_TAMPERED_CONFIDENCE   = _float('FA_TAMPERED_CONFIDENCE',  80.0)
FA_REVIEW_CONFIDENCE     = _float('FA_REVIEW_CONFIDENCE',    30.0)

# ── Frame Analysis — severity point values ───────────────────────────────────
FA_SEV_HIGH_PTS          = _float('FA_SEV_HIGH_PTS',   25.0)
FA_SEV_MEDIUM_PTS        = _float('FA_SEV_MEDIUM_PTS', 12.0)
FA_SEV_LOW_PTS           = _float('FA_SEV_LOW_PTS',     4.0)

# ── Frame Analysis — spatial finding thresholds ──────────────────────────────
FA_ELA_HIGH_THRESHOLD    = _float('FA_ELA_HIGH_THRESHOLD',    15.0)
FA_ELA_MEDIUM_THRESHOLD  = _float('FA_ELA_MEDIUM_THRESHOLD',   8.0)
FA_NOISE_HIGH_THRESHOLD  = _float('FA_NOISE_HIGH_THRESHOLD',  50.0)
FA_NOISE_MEDIUM_THRESHOLD = _float('FA_NOISE_MEDIUM_THRESHOLD', 25.0)
FA_CLONE_HIGH_THRESHOLD  = _float('FA_CLONE_HIGH_THRESHOLD',   5.0)
FA_CLONE_MEDIUM_THRESHOLD = _float('FA_CLONE_MEDIUM_THRESHOLD', 2.0)

# ── Visual Forensics — tampering likelihood scoring (images) ─────────────────
VF_ELA_HIGH_PTS          = _float('VF_ELA_HIGH_PTS',          30.0)
VF_ELA_MEDIUM_PTS        = _float('VF_ELA_MEDIUM_PTS',        18.0)
VF_NOISE_HIGH_PTS        = _float('VF_NOISE_HIGH_PTS',        30.0)
VF_NOISE_MEDIUM_PTS      = _float('VF_NOISE_MEDIUM_PTS',      18.0)
VF_CLONE_HIGH_PTS        = _float('VF_CLONE_HIGH_PTS',        35.0)
VF_CLONE_MEDIUM_PTS      = _float('VF_CLONE_MEDIUM_PTS',      20.0)
VF_JPEG_RECOMPRESS_PTS   = _float('VF_JPEG_RECOMPRESS_PTS',   25.0)
VF_MISSING_EXIF_PTS      = _float('VF_MISSING_EXIF_PTS',      17.0)

# ── Visual Forensics — detector thresholds (also used when scoring) ──────────
VF_ELA_HIGH_THRESHOLD    = _float('VF_ELA_HIGH_THRESHOLD',    15.0)
VF_ELA_MEDIUM_THRESHOLD  = _float('VF_ELA_MEDIUM_THRESHOLD',   8.0)
VF_NOISE_HIGH_THRESHOLD  = _float('VF_NOISE_HIGH_THRESHOLD',  50.0)
VF_NOISE_MEDIUM_THRESHOLD = _float('VF_NOISE_MEDIUM_THRESHOLD', 25.0)
VF_CLONE_HIGH_THRESHOLD  = _float('VF_CLONE_HIGH_THRESHOLD',   5.0)
VF_CLONE_MEDIUM_THRESHOLD = _float('VF_CLONE_MEDIUM_THRESHOLD', 2.0)

VF_TAMPERED_THRESHOLD    = _float('VF_TAMPERED_THRESHOLD',    60.0)
VF_REVIEW_THRESHOLD      = _float('VF_REVIEW_THRESHOLD',      30.0)

# ── Visual Forensics — video aggregate weights ───────────────────────────────
VF_VIDEO_MEAN_WEIGHT          = _float('VF_VIDEO_MEAN_WEIGHT',          0.7)
VF_VIDEO_MAX_WEIGHT           = _float('VF_VIDEO_MAX_WEIGHT',           0.3)
VF_VIDEO_TAMPERED_THRESHOLD   = _float('VF_VIDEO_TAMPERED_THRESHOLD',  60.0)
VF_VIDEO_REVIEW_THRESHOLD     = _float('VF_VIDEO_REVIEW_THRESHOLD',    30.0)

# ── File cleanup ─────────────────────────────────────────────────────────────
CLEANUP_MAX_AGE_HOURS         = _int('CLEANUP_MAX_AGE_HOURS',      24)
CLEANUP_INTERVAL_SECONDS      = _int('CLEANUP_INTERVAL_SECONDS', 3600)

# ── Video Forensics (VFO_*) ───────────────────────────────────────────────────
VFO_TAMPERED_CONFIDENCE       = _float('VFO_TAMPERED_CONFIDENCE',       75.0)
VFO_REVIEW_CONFIDENCE         = _float('VFO_REVIEW_CONFIDENCE',         35.0)
VFO_BITRATE_ZSCORE_THRESHOLD  = _float('VFO_BITRATE_ZSCORE_THRESHOLD',   2.5)
VFO_DOUBLE_ENC_BENFORD_THRESH = _float('VFO_DOUBLE_ENC_BENFORD_THRESH',  0.15)
VFO_FACE_CONSISTENCY_ZSCORE   = _float('VFO_FACE_CONSISTENCY_ZSCORE',    2.5)
VFO_ENF_PHASE_ZSCORE          = _float('VFO_ENF_PHASE_ZSCORE',           3.0)
VFO_MOTION_DIVERGENCE_RATIO   = _float('VFO_MOTION_DIVERGENCE_RATIO',    3.0)
VFO_TIMESTAMP_DELTA_HOURS     = _float('VFO_TIMESTAMP_DELTA_HOURS',      1.0)
