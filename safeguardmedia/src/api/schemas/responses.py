"""
Shared Pydantic response models.

All four forensic endpoints return a ForensicResult at the top level.
Engine-specific detail is nested in `engine_detail` so each engine
can include whatever it needs without breaking the shared contract.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class ForensicVerdict(str, Enum):
    LIKELY_AUTHENTIC = "likely_authentic"
    INCONCLUSIVE = "inconclusive"
    LIKELY_TAMPERED = "likely_tampered"
    TAMPERED = "tampered"


class FindingSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Finding(BaseModel):
    title: str
    module: str
    severity: FindingSeverity
    confidence: float
    description: str
    timestamp_s: Optional[float] = None  # position in media, if applicable


class ForensicResult(BaseModel):
    verdict: ForensicVerdict
    verdict_label: str
    probability: float          # 0.0 – 1.0 tampering probability
    confidence: float           # 0.0 – 1.0 confidence in the verdict
    findings: list[Finding]
    engine: str                 # which engine produced this result
    elapsed_s: float
    file_hash_sha256: Optional[str] = None
    engine_detail: dict[str, Any] = {}   # raw engine output for full auditability


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
