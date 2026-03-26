"""
VideoForensics System — Global Configuration
All system-wide settings, thresholds, and constants live here.
Thresholds are literature-derived and will be refined as case data accumulates.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any

# ─── Base Paths ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
WORKING_DIR = BASE_DIR / "working"
OUTPUT_DIR = BASE_DIR / "outputs"
LOG_DIR = BASE_DIR / "logs"

for d in [UPLOAD_DIR, WORKING_DIR, OUTPUT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── System Identity ───────────────────────────────────────────────────────────
SYSTEM_NAME = "VideoForensics"
SYSTEM_VERSION = "0.1.0"
BUILD_PHASE = "Phase 0 — Foundation"

# ─── Supported Formats ────────────────────────────────────────────────────────
SUPPORTED_CONTAINERS = [
    ".mp4", ".avi", ".mov", ".mkv", ".wmv",
    ".flv", ".webm", ".mts", ".m2ts", ".3gp"
]

SUPPORTED_CODECS = [
    "h264", "h265", "hevc", "vp8", "vp9",
    "av1", "mpeg2video", "mpeg4", "prores"
]

# ─── Hashing ──────────────────────────────────────────────────────────────────
HASH_ALGORITHMS = ["md5", "sha256"]          # Both computed at ingestion
PRIMARY_HASH = "sha256"                       # Used for chain of custody

# ─── Module Confidence Thresholds ─────────────────────────────────────────────
# All values sourced from peer-reviewed forensic literature.
# Scale: 0.0 (definitely authentic) → 1.0 (definitely tampered)
# These will be adaptively refined as case data accumulates.

@dataclass
class ModuleThresholds:
    """
    Tampering probability thresholds per module.
    Low / Medium / High define the confidence bands for reporting.
    """
    low: float      # Below this → no significant finding
    medium: float   # Between low and high → flagged for review
    high: float     # Above this → strong tampering indicator

THRESHOLDS: Dict[str, ModuleThresholds] = {
    "metadata":    ModuleThresholds(low=0.30, medium=0.55, high=0.75),
    "compression": ModuleThresholds(low=0.30, medium=0.55, high=0.75),
    "noise":       ModuleThresholds(low=0.25, medium=0.50, high=0.70),
    "temporal":    ModuleThresholds(low=0.30, medium=0.55, high=0.75),
    "lighting":    ModuleThresholds(low=0.35, medium=0.60, high=0.78),
    "audio":       ModuleThresholds(low=0.30, medium=0.55, high=0.75),
}

# ─── Fusion Engine Weights ────────────────────────────────────────────────────
# Initial weights based on domain expertise and published forensic research.
# The fusion engine will adaptively adjust these as case outcomes are recorded.
# Weights must sum to 1.0

FUSION_WEIGHTS: Dict[str, float] = {
    "metadata":    0.15,
    "compression": 0.20,
    "noise":       0.25,   # PRNU is the strongest single signal
    "temporal":    0.15,
    "lighting":    0.13,
    "audio":       0.12,
}

assert abs(sum(FUSION_WEIGHTS.values()) - 1.0) < 1e-6, \
    "Fusion weights must sum to 1.0"

# ─── Fusion Decision Bands ────────────────────────────────────────────────────
@dataclass
class FusionBands:
    authentic_ceiling: float   = 0.30   # Below → likely authentic
    review_ceiling: float      = 0.55   # Between → escalate to human review
    suspicious_ceiling: float  = 0.75   # Between → strong tampering signal
                                        # Above → very high tampering confidence

FUSION_BANDS = FusionBands()

# ─── Processing Limits ────────────────────────────────────────────────────────
MAX_FILE_SIZE_GB        = 10
MAX_DURATION_MINUTES    = 180
FRAME_EXTRACTION_FPS    = None   # None = extract at native FPS
MIN_FRAMES_FOR_ANALYSIS = 30     # Minimum frames needed for reliable analysis
MIN_DURATION_SECONDS    = 2      # Videos shorter than this cannot be analyzed

# ─── Audio Settings ───────────────────────────────────────────────────────────
AUDIO_SAMPLE_RATE       = None   # None = preserve native sample rate
AUDIO_LIP_SYNC_TOLERANCE_MS = 80 # Max acceptable AV offset in milliseconds

# ─── PRNU Settings ────────────────────────────────────────────────────────────
PRNU_MIN_FRAMES         = 50     # Minimum frames for stable fingerprint
PRNU_CORRELATION_MATCH  = 0.60   # Normalized cross-correlation threshold for device match
PRNU_CONSISTENCY_MIN    = 0.45   # Internal consistency threshold

# ─── Compression Analysis ────────────────────────────────────────────────────
DCT_DOUBLE_COMPRESS_SENSITIVITY = 0.65   # Sensitivity for double compression detection
BITRATE_ANOMALY_STD_MULTIPLIER  = 3.0    # Flag if bitrate deviates > N std deviations

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL               = os.getenv("VF_LOG_LEVEL", "INFO")
LOG_FORMAT              = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT         = "%Y-%m-%d %H:%M:%S"

# ─── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "VF_DATABASE_URL",
    f"sqlite:///{BASE_DIR}/database/forensics.db"
)

# ─── Security ─────────────────────────────────────────────────────────────────
ENCRYPTION_KEY_ENV      = "VF_ENCRYPTION_KEY"   # Must be set in production
AUDIT_HMAC_KEY_ENV      = "VF_AUDIT_HMAC_KEY"   # For audit log integrity
