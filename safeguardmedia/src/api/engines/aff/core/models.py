"""
AudioForensics — Core Data Models (Phase 0)

Every module in the system speaks this language.
Nothing imports from modules. Everything imports from here.

Design principles:
    - Immutable-by-convention dataclasses (no setters after creation)
    - All timestamps in seconds from start of recording
    - All probabilities in [0.0, 1.0]
    - All frequencies in Hz
    - All durations in seconds
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Enumerations ──────────────────────────────────────────────────────────────

class Severity(Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class Verdict(Enum):
    LIKELY_AUTHENTIC  = "likely_authentic"
    INCONCLUSIVE      = "inconclusive"
    LIKELY_TAMPERED   = "likely_tampered"
    TAMPERED          = "tampered"

    @property
    def label(self) -> str:
        return {
            "likely_authentic":  "Likely Authentic",
            "inconclusive":      "Inconclusive",
            "likely_tampered":   "Likely Tampered",
            "tampered":          "Tampered",
        }[self.value]

    @property
    def is_elevated(self) -> bool:
        return self in (Verdict.LIKELY_TAMPERED, Verdict.TAMPERED)


class ENFGrid(Enum):
    """Mains frequency grid — determines ENF band to analyse."""
    GRID_50HZ = 50   # Europe, Africa, Asia, Australia
    GRID_60HZ = 60   # North America, parts of South America, Japan

    @property
    def nominal(self) -> float:
        return float(self.value)

    @property
    def band_low(self) -> float:
        return self.nominal - 1.0

    @property
    def band_high(self) -> float:
        return self.nominal + 1.0

    @property
    def harmonic_2(self) -> float:
        """Second harmonic — often stronger signal than fundamental."""
        return self.nominal * 2.0


class AudioCodec(Enum):
    """Known audio codecs with forensic-relevant properties."""
    PCM_S16LE  = "pcm_s16le"    # Uncompressed WAV
    PCM_S24LE  = "pcm_s24le"
    PCM_F32LE  = "pcm_f32le"
    MP3        = "mp3"
    AAC        = "aac"
    OGG_VORBIS = "vorbis"
    OPUS       = "opus"
    FLAC       = "flac"
    AMR_NB     = "amr_nb"       # Narrowband — phone recordings
    AMR_WB     = "amr_wb"       # Wideband
    PCM_ALAW   = "pcm_alaw"     # Telephone
    PCM_MULAW  = "pcm_mulaw"
    ALAC       = "alac"         # Apple Lossless — iPhone Voice Memos default
    UNKNOWN    = "unknown"

    @property
    def is_lossy(self) -> bool:
        return self in (
            AudioCodec.MP3, AudioCodec.AAC, AudioCodec.OGG_VORBIS,
            AudioCodec.OPUS, AudioCodec.AMR_NB, AudioCodec.AMR_WB,
            AudioCodec.PCM_ALAW, AudioCodec.PCM_MULAW,
        )

    @property
    def is_mobile_lossless(self) -> bool:
        """
        Lossless codec used by mobile devices (iPhone ALAC).
        These are genuine lossless recordings but captured by a phone mic
        with limited bandwidth — different from studio PCM/FLAC.
        The noise tilt checks are unreliable on these for the same reasons
        as on lossy phone recordings (speech-silence variation).
        """
        return self in (AudioCodec.ALAC,)

    @property
    def typical_max_freq(self) -> Optional[float]:
        """Typical upper frequency limit in Hz — None means full bandwidth."""
        return {
            AudioCodec.AMR_NB:   4000.0,
            AudioCodec.PCM_ALAW: 4000.0,
            AudioCodec.PCM_MULAW:4000.0,
            AudioCodec.AMR_WB:   8000.0,
        }.get(self, None)


# ── Temporal Location ─────────────────────────────────────────────────────────

@dataclass
class TemporalLocation:
    """
    A time range within the audio recording.
    All values in seconds from the start of the file.
    """
    start_seconds: float
    end_seconds:   float

    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def midpoint(self) -> float:
        return (self.start_seconds + self.end_seconds) / 2.0

    def overlaps(self, other: "TemporalLocation") -> bool:
        return (self.start_seconds < other.end_seconds and
                self.end_seconds   > other.start_seconds)

    def to_timecode(self, seconds: float) -> str:
        """Convert seconds to MM:SS.mmm string."""
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m:02d}:{s:06.3f}"

    @property
    def start_timecode(self) -> str:
        return self.to_timecode(self.start_seconds)

    @property
    def end_timecode(self) -> str:
        return self.to_timecode(self.end_seconds)

    def __repr__(self) -> str:
        return f"[{self.start_timecode} → {self.end_timecode}]"


# ── Finding ───────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """
    A single forensic observation from one module.

    Findings are the primary output of each detection module.
    The fusion engine aggregates them into a verdict.
    """
    module:            str                        # e.g. "enf", "noise"
    title:             str                        # Short, human-readable
    description:       str                        # Full explanation
    severity:          Severity
    confidence:        float                      # [0.0, 1.0]
    temporal_location: Optional[TemporalLocation] # Where in the file
    metadata:          Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.confidence = float(max(0.0, min(1.0, self.confidence)))

    @property
    def is_significant(self) -> bool:
        return self.severity in (Severity.MEDIUM, Severity.HIGH)


# ── Audio Profile ─────────────────────────────────────────────────────────────

@dataclass
class AudioProfile:
    """
    Technical properties of the audio file extracted during ingestion.
    Populated by the IngestionPipeline before any analysis modules run.
    """
    # File identity
    file_path:         str
    file_size_bytes:   int
    sha256_hash:       str
    sha256_partial:    str          # First 1MB hash — fast integrity check

    # Container / format
    container_format:  str          # e.g. "mov,mp4,m4a,3gp,3g2,mj2", "wav"
    codec_name:        str          # Raw codec string from ffprobe
    codec_enum:        AudioCodec   # Parsed codec enum

    # Audio stream properties
    sample_rate:       int          # Hz — declared by file
    channels:          int          # 1=mono, 2=stereo
    duration_seconds:  float        # Total duration
    total_samples:     int          # duration × sample_rate
    bit_depth:         Optional[int]# 16, 24, 32 — None for lossy
    bitrate_bps:       Optional[int]# Declared bitrate

    # Encoder metadata
    encoder_string:    Optional[str]# e.g. "LAME3.100", "iTunes 12.9"
    creation_time:     Optional[str]# ISO timestamp from container metadata
    has_id3_tags:      bool         # MP3 ID3 tag present
    has_xmp_data:      bool         # XMP metadata present

    # Signal properties (computed during ingestion)
    is_mono:           bool
    dc_offset:         float        # DC bias in [-1.0, 1.0]
    peak_amplitude:    float        # Max absolute sample value
    rms_level:         float        # RMS amplitude
    is_clipped:        bool         # Any samples at ±1.0 (full scale)
    clipping_count:    int          # Number of clipped samples
    silence_ratio:     float        # Fraction of file that is silence
    effective_max_freq:Optional[float]  # Measured bandwidth cutoff

    # ENF grid (auto-detected or user-specified)
    enf_grid:          ENFGrid      # 50Hz or 60Hz

    # Extraction paths (set after ffmpeg extraction)
    wav_path_mono:     Optional[str] = None   # 16kHz mono WAV for analysis
    wav_path_full:     Optional[str] = None   # Original rate, original channels

    @property
    def duration_formatted(self) -> str:
        m = int(self.duration_seconds // 60)
        s = self.duration_seconds % 60
        return f"{m:02d}:{s:05.2f}"

    @property
    def file_size_mb(self) -> float:
        return self.file_size_bytes / (1024 * 1024)

    @property
    def nyquist(self) -> float:
        return self.sample_rate / 2.0

    @property
    def codec_is_lossy(self) -> bool:
        return self.codec_enum.is_lossy

    @property
    def short_hash(self) -> str:
        return self.sha256_hash[:16] + "…"


# ── Module Score ──────────────────────────────────────────────────────────────

@dataclass
class ModuleScore:
    """
    The output of one forensic module — a score plus its findings.
    Consumed by the fusion engine.
    """
    module:     str
    score:      float              # Tamper probability [0.0, 1.0]
    confidence: float              # How much to trust this score [0.0, 1.0]
    findings:   List[Finding]
    weight:     float = 1.0        # Base weight (overridden by fusion)
    skipped:    bool  = False      # True if module could not run
    skip_reason:Optional[str] = None
    elapsed_s:  float = 0.0
    metadata:   Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.score      = float(max(0.0, min(1.0, self.score)))
        self.confidence = float(max(0.0, min(1.0, self.confidence)))

    @property
    def is_elevated(self) -> bool:
        """Score exceeds the default suspicion threshold."""
        return self.score >= 0.30 and not self.skipped

    @property
    def significant_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.is_significant]


# ── Fusion Result ─────────────────────────────────────────────────────────────

@dataclass
class FusionResult:
    """
    Final output of the fusion engine.
    This is what gets reported and returned by the API.
    """
    verdict:              Verdict
    fused_probability:    float           # Final tamper probability
    confidence:           float           # Confidence in the verdict
    module_scores:        Dict[str, 'ModuleScore']
    adjusted_weights:     Dict[str, float]
    all_findings:         List[Finding]
    elevated_modules:     List[str]       # Modules that exceeded threshold
    corroboration_factor: float           # >1.0 if multiple modules agree
    has_conflict:         bool            # Modules significantly disagree
    conflict_description: str
    base_score:           float           # Pre-corroboration score
    total_elapsed_s:      float
    calibration_note:     str = (
        "Pre-calibration: thresholds have not been tuned to a labelled corpus. "
        "Verdicts are directionally reliable but probability values are approximate."
    )

    @property
    def verdict_label(self) -> str:
        return self.verdict.label

    @property
    def n_elevated(self) -> int:
        return len(self.elevated_modules)

    @property
    def high_severity_findings(self) -> List[Finding]:
        return [f for f in self.all_findings if f.severity == Severity.HIGH]


# ── Audio Case ────────────────────────────────────────────────────────────────

@dataclass
class AudioCase:
    """
    The central object passed through the entire analysis pipeline.

    Created by IngestionPipeline.ingest().
    Passed to each module in turn.
    Consumed by FusionModule to produce FusionResult.
    """
    case_id:       str
    audio_profile: AudioProfile
    module_scores: List[ModuleScore] = field(default_factory=list)
    created_at:    str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def create(cls, audio_profile: AudioProfile) -> "AudioCase":
        return cls(
            case_id       = str(uuid.uuid4()),
            audio_profile = audio_profile,
        )

    def add_score(self, score: ModuleScore) -> None:
        self.module_scores.append(score)

    def get_score(self, module: str) -> Optional[ModuleScore]:
        for s in self.module_scores:
            if s.module == module:
                return s
        return None

    @property
    def all_findings(self) -> List[Finding]:
        findings = []
        for s in self.module_scores:
            findings.extend(s.findings)
        return findings

    @property
    def has_results(self) -> bool:
        return len(self.module_scores) > 0


# ── Base Module Interface ─────────────────────────────────────────────────────

class BaseAudioModule:
    """
    Interface that every forensic module must implement.

    Modules are stateless — they receive an AudioCase, run analysis,
    and return a ModuleScore. They never modify the AudioCase directly.
    """

    MODULE_NAME: str = "base"
    DEFAULT_WEIGHT: float = 1.0

    def run(self, case: AudioCase) -> ModuleScore:
        """
        Run this module's analysis on the given case.
        Must not raise — return a skipped ModuleScore on error.
        """
        raise NotImplementedError

    def _skipped(self, reason: str) -> ModuleScore:
        """Return a zero-score skipped result."""
        return ModuleScore(
            module      = self.MODULE_NAME,
            score       = 0.0,
            confidence  = 0.0,
            findings    = [],
            weight      = self.DEFAULT_WEIGHT,
            skipped     = True,
            skip_reason = reason,
        )

    def _safe_run(self, case: AudioCase) -> ModuleScore:
        """Wrapper that catches all exceptions and returns skipped on error."""
        import time, traceback, logging
        t0 = time.time()
        log = logging.getLogger(f"af.{self.MODULE_NAME}")
        try:
            result = self.run(case)
            result.elapsed_s = round(time.time() - t0, 3)
            return result
        except Exception as e:
            log.error(f"{self.MODULE_NAME} failed: {e}", exc_info=True)
            s = self._skipped(f"Exception: {e}")
            s.elapsed_s = round(time.time() - t0, 3)
            return s
