"""
VideoForensics System — Core Data Models
These dataclasses are the shared language of the entire system.
Every module speaks in these types. The fusion engine reads them.
The report engine renders them.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import uuid


# ─── Enumerations ─────────────────────────────────────────────────────────────

class ConfidenceLevel(Enum):
    """Human-readable confidence band for any finding or final verdict."""
    AUTHENTIC         = "authentic"           # Strong signal of no tampering
    LIKELY_AUTHENTIC  = "likely_authentic"    # Lean authentic, minor anomalies
    INCONCLUSIVE      = "inconclusive"        # Cannot determine — escalate
    LIKELY_TAMPERED   = "likely_tampered"     # Lean tampered, notable signals
    TAMPERED          = "tampered"            # Strong signal of tampering

class Severity(Enum):
    """Severity of an individual forensic finding."""
    INFO     = "info"       # Noted but not suspicious
    LOW      = "low"        # Minor anomaly, likely benign
    MEDIUM   = "medium"     # Notable — warrants attention
    HIGH     = "high"       # Strong tampering indicator
    CRITICAL = "critical"   # Near-certain evidence of tampering

class ModuleStatus(Enum):
    """Execution status of a forensic module."""
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    SKIPPED    = "skipped"   # Insufficient data to run


# ─── Spatial & Temporal Location ──────────────────────────────────────────────

@dataclass
class FrameRegion:
    """
    A rectangular region within a video frame.
    Coordinates are normalized 0.0–1.0 relative to frame dimensions.
    This keeps the model resolution-independent.
    """
    x: float          # Left edge, 0.0–1.0
    y: float          # Top edge, 0.0–1.0
    width: float      # Width, 0.0–1.0
    height: float     # Height, 0.0–1.0

    def to_pixel(self, frame_width: int, frame_height: int) -> Tuple[int, int, int, int]:
        """Convert normalized coords to pixel coordinates."""
        return (
            int(self.x * frame_width),
            int(self.y * frame_height),
            int(self.width * frame_width),
            int(self.height * frame_height),
        )

@dataclass
class TemporalLocation:
    """Precise location in time within the video."""
    start_frame: int
    end_frame: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


# ─── Individual Finding ───────────────────────────────────────────────────────

@dataclass
class Finding:
    """
    A single forensic finding from any module.
    Every anomaly, flag, or observation is expressed as a Finding.
    This is the atomic unit of forensic evidence in the system.
    """
    finding_id: str                           = field(default_factory=lambda: str(uuid.uuid4()))
    module: str                               = ""
    title: str                                = ""
    description: str                          = ""
    severity: Severity                        = Severity.INFO
    confidence: float                         = 0.0   # 0.0–1.0 probability this finding is genuine
    uncertainty: float                        = 0.0   # How uncertain is this confidence estimate
    temporal_location: Optional[TemporalLocation] = None
    spatial_location: Optional[FrameRegion]  = None
    evidence: Dict[str, Any]                  = field(default_factory=dict)
    # Technical values that produced this finding — for explainability
    raw_values: Dict[str, Any]                = field(default_factory=dict)
    timestamp: datetime                       = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "module": self.module,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "confidence": round(self.confidence, 4),
            "uncertainty": round(self.uncertainty, 4),
            "evidence": self.evidence,
            "raw_values": self.raw_values,
            "timestamp": self.timestamp.isoformat(),
        }


# ─── Module Result ────────────────────────────────────────────────────────────

@dataclass
class ModuleResult:
    """
    The complete output of one forensic module's analysis.
    Every module returns exactly one of these.
    """
    module_name: str
    status: ModuleStatus                    = ModuleStatus.PENDING
    tampering_probability: float            = 0.0    # 0.0–1.0
    uncertainty: float                      = 0.0    # Module's self-reported uncertainty
    confidence_level: ConfidenceLevel       = ConfidenceLevel.INCONCLUSIVE
    findings: List[Finding]                 = field(default_factory=list)
    summary: str                            = ""
    processing_time_seconds: float          = 0.0
    reliability_flag: bool                  = True   # False if insufficient data
    reliability_note: str                   = ""     # Explanation if unreliable
    metadata: Dict[str, Any]               = field(default_factory=dict)
    error_message: Optional[str]            = None

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def highest_severity(self) -> Optional[Severity]:
        if not self.findings:
            return None
        order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM,
                 Severity.LOW, Severity.INFO]
        for s in order:
            if any(f.severity == s for f in self.findings):
                return s
        return None

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_name": self.module_name,
            "status": self.status.value,
            "tampering_probability": round(self.tampering_probability, 4),
            "uncertainty": round(self.uncertainty, 4),
            "confidence_level": self.confidence_level.value,
            "finding_count": self.finding_count,
            "highest_severity": self.highest_severity.value if self.highest_severity else None,
            "summary": self.summary,
            "processing_time_seconds": round(self.processing_time_seconds, 2),
            "reliability_flag": self.reliability_flag,
            "reliability_note": self.reliability_note,
            "findings": [f.to_dict() for f in self.findings],
            "error_message": self.error_message,
        }


# ─── Video Profile ────────────────────────────────────────────────────────────

@dataclass
class VideoProfile:
    """
    Technical profile of the video established at ingestion.
    Used by modules to calibrate their analysis appropriately.
    """
    # File identity
    original_filename: str      = ""
    file_size_bytes: int        = 0
    sha256_hash: str            = ""
    md5_hash: str               = ""

    # Container
    container_format: str       = ""
    container_duration: float   = 0.0     # seconds

    # Video stream
    codec: str                  = ""
    width: int                  = 0
    height: int                 = 0
    frame_rate: float           = 0.0
    total_frames: int           = 0
    bit_depth: int              = 8
    color_space: str            = ""
    avg_bitrate_kbps: float     = 0.0
    has_video: bool             = True

    # Audio stream
    has_audio: bool             = False
    audio_codec: str            = ""
    audio_sample_rate: int      = 0
    audio_channels: int         = 0
    audio_bitrate_kbps: float   = 0.0

    # Metadata
    creation_time: Optional[str]    = None
    encoder_software: Optional[str] = None
    gps_latitude: Optional[float]   = None
    gps_longitude: Optional[float]  = None
    device_make: Optional[str]      = None
    device_model: Optional[str]     = None
    raw_metadata: Dict[str, Any]    = field(default_factory=dict)

    # Quality flags (set by ingestion module)
    is_low_resolution: bool     = False   # Below 480p
    is_very_short: bool         = False   # Under MIN_DURATION_SECONDS
    has_sufficient_frames: bool = True    # Meets MIN_FRAMES_FOR_ANALYSIS

    @property
    def resolution_label(self) -> str:
        if self.height >= 2160: return "4K"
        if self.height >= 1080: return "1080p"
        if self.height >= 720:  return "720p"
        if self.height >= 480:  return "480p"
        return f"{self.height}p (low)"

    @property
    def duration_minutes(self) -> float:
        return self.container_duration / 60


# ─── Case — The Top-Level Container ───────────────────────────────────────────

@dataclass
class ForensicCase:
    """
    The complete forensic case for one video submission.
    This is the root object — everything hangs off of this.
    """
    case_id: str                            = field(default_factory=lambda: str(uuid.uuid4()))
    submitted_at: datetime                  = field(default_factory=datetime.utcnow)
    submitted_by: str                       = "system"
    original_file_path: Optional[Path]     = None
    working_file_path: Optional[Path]      = None

    # Video profile established at ingestion
    video_profile: Optional[VideoProfile]  = None

    # Results from each module
    module_results: Dict[str, ModuleResult] = field(default_factory=dict)

    # Fusion engine output
    overall_tampering_probability: float   = 0.0
    overall_confidence_level: ConfidenceLevel = ConfidenceLevel.INCONCLUSIVE
    overall_uncertainty: float             = 0.0
    fusion_weights_used: Dict[str, float]  = field(default_factory=dict)
    requires_human_review: bool            = False
    human_review_reason: str               = ""

    # Final verdict
    verdict_summary: str                   = ""
    completed_at: Optional[datetime]       = None
    total_processing_seconds: float        = 0.0

    # Audit
    audit_log: List[Dict[str, Any]]        = field(default_factory=list)

    def add_module_result(self, result: ModuleResult) -> None:
        self.module_results[result.module_name] = result

    def log_event(self, event: str, detail: str = "", actor: str = "system") -> None:
        self.audit_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "detail": detail,
            "actor": actor,
        })

    @property
    def total_findings(self) -> int:
        return sum(r.finding_count for r in self.module_results.values())

    @property
    def modules_completed(self) -> List[str]:
        return [
            name for name, r in self.module_results.items()
            if r.status == ModuleStatus.COMPLETED
        ]

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "submitted_at": self.submitted_at.isoformat(),
            "submitted_by": self.submitted_by,
            "overall_tampering_probability": round(self.overall_tampering_probability, 4),
            "overall_confidence_level": self.overall_confidence_level.value,
            "overall_uncertainty": round(self.overall_uncertainty, 4),
            "requires_human_review": self.requires_human_review,
            "human_review_reason": self.human_review_reason,
            "verdict_summary": self.verdict_summary,
            "total_findings": self.total_findings,
            "modules_completed": self.modules_completed,
            "total_processing_seconds": round(self.total_processing_seconds, 2),
            "video_profile": {
                "filename": self.video_profile.original_filename if self.video_profile else None,
                "resolution": self.video_profile.resolution_label if self.video_profile else None,
                "duration_minutes": round(self.video_profile.duration_minutes, 2) if self.video_profile else None,
                "codec": self.video_profile.codec if self.video_profile else None,
                "has_audio": self.video_profile.has_audio if self.video_profile else None,
            } if self.video_profile else None,
        }
