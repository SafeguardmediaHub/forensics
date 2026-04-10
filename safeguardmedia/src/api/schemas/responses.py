"""
Shared Pydantic response models.

All four forensic endpoints return a ForensicResult at the top level.
Engine-specific detail is nested in `engine_detail` so each engine
can include whatever it needs without breaking the shared contract.

Verdict fields (`verdict`, `verdict_label`, `probability`) are deprecated
and will always be emitted as null. They are retained for one release so
downstream consumers can migrate to `risk_score` / `risk_band` and the
`interpretation` object. Do not read them in new code.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel


class ForensicVerdict(str, Enum):
    """Deprecated. Kept only so the nullable `verdict` field still type-checks."""
    LIKELY_AUTHENTIC = "likely_authentic"
    INCONCLUSIVE = "inconclusive"
    LIKELY_TAMPERED = "likely_tampered"
    TAMPERED = "tampered"


class FindingSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RiskBand = Literal["low", "elevated", "high"]
CalibrationStatus = Literal["pre_calibration", "calibrated", "recalibrating"]
NextStepType = Literal["manual", "platform_feature"]


class Finding(BaseModel):
    title: str
    module: str
    severity: FindingSeverity
    confidence: float
    description: str
    timestamp_s: Optional[float] = None  # position in media, if applicable


class CheckUnavailable(BaseModel):
    """A check one of the engines could have run but didn't complete.

    Scoped to engine-internal checks only — this is never used to describe
    capabilities that live outside this project (C2PA, reverse lookup, etc.).
    Those are surfaced as suggestions in `Interpretation.next_steps`.
    """
    name: str
    reason: str


class NextStep(BaseModel):
    """One recommended follow-up action for the user.

    `manual` steps are things the human does themselves. `platform_feature`
    steps point at another capability in the wider platform — the backend
    maps `feature` to its own endpoint IDs when rendering.
    """
    action: str
    label: str
    type: NextStepType
    feature: Optional[str] = None


class Interpretation(BaseModel):
    summary: str
    what_this_means: str
    next_steps: list[NextStep]


class ForensicResult(BaseModel):
    # ── New (preferred) fields ────────────────────────────────────────────────
    risk_score: int                      # 0–100, signal strength (not truth)
    risk_band: RiskBand                  # "low" | "elevated" | "high"
    measurement_confidence: float        # 0.0–1.0, how trustworthy the measurement itself is
    calibration_status: CalibrationStatus
    elevated_detectors: list[str] = []
    checks_unavailable: list[CheckUnavailable] = []
    interpretation: Interpretation

    # ── Unchanged ─────────────────────────────────────────────────────────────
    findings: list[Finding]
    engine: str
    elapsed_s: float
    file_hash_sha256: Optional[str] = None
    engine_detail: dict[str, Any] = {}

    # ── Deprecated — always null, scheduled for removal next release ──────────
    verdict: Optional[ForensicVerdict] = None
    verdict_label: Optional[str] = None
    probability: Optional[float] = None
    tampering_likelihood: Optional[float] = None


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    submitted_at: datetime
    result: Optional[ForensicResult] = None
    error: Optional[str] = None
