"""
VideoForensics System — Base Forensic Module
Every forensic module inherits from BaseForensicModule.
This enforces a consistent interface, standardized output, and
ensures every module contributes to the audit trail correctly.
"""

from __future__ import annotations
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core.models import (
    ForensicCase, ModuleResult, ModuleStatus,
    ConfidenceLevel, Finding, Severity
)
from config.settings import THRESHOLDS, ModuleThresholds


class BaseForensicModule(ABC):
    """
    Abstract base class for all forensic analysis modules.

    Every module must implement:
        - analyze(case) → ModuleResult

    Every module gets for free:
        - Standardized logging
        - Processing time measurement
        - Threshold access
        - Confidence level mapping
        - Finding helpers
        - Error handling wrapper
    """

    # Subclasses declare their module name
    MODULE_NAME: str = "base"

    def __init__(self):
        self.logger = logging.getLogger(f"vf.module.{self.MODULE_NAME}")
        self._thresholds: ModuleThresholds = THRESHOLDS.get(
            self.MODULE_NAME,
            ModuleThresholds(low=0.30, medium=0.55, high=0.75)
        )

    # ── Public Interface ───────────────────────────────────────────────────────

    def run(self, case: ForensicCase) -> ModuleResult:
        """
        Entry point called by the analysis pipeline.
        Wraps analyze() with timing, logging, and error handling.
        """
        self.logger.info(f"Starting analysis — case {case.case_id}")
        case.log_event(f"module_start", f"Module {self.MODULE_NAME} started")

        result = ModuleResult(module_name=self.MODULE_NAME, status=ModuleStatus.RUNNING)
        start_time = time.time()

        try:
            result = self.analyze(case)
            result.status = ModuleStatus.COMPLETED
            result.processing_time_seconds = time.time() - start_time
            self.logger.info(
                f"Completed — tampering_prob={result.tampering_probability:.3f} "
                f"findings={result.finding_count} "
                f"time={result.processing_time_seconds:.2f}s"
            )
            case.log_event(
                "module_complete",
                f"Module {self.MODULE_NAME} completed. "
                f"Tampering probability: {result.tampering_probability:.3f}. "
                f"Findings: {result.finding_count}"
            )

        except InsufficientDataError as e:
            result.status = ModuleStatus.SKIPPED
            result.reliability_flag = False
            result.reliability_note = str(e)
            result.processing_time_seconds = time.time() - start_time
            self.logger.warning(f"Skipped — insufficient data: {e}")
            case.log_event("module_skipped", f"{self.MODULE_NAME}: {e}")

        except Exception as e:
            result.status = ModuleStatus.FAILED
            result.error_message = str(e)
            result.processing_time_seconds = time.time() - start_time
            self.logger.error(f"Failed — {e}", exc_info=True)
            case.log_event("module_failed", f"{self.MODULE_NAME}: {e}")

        return result

    @abstractmethod
    def analyze(self, case: ForensicCase) -> ModuleResult:
        """
        Core analysis logic. Implemented by every subclass.
        Must return a fully populated ModuleResult.
        Should raise InsufficientDataError if the video
        does not have enough data for reliable analysis.
        """
        ...

    # ── Threshold Helpers ──────────────────────────────────────────────────────

    def map_confidence_level(self, probability: float) -> ConfidenceLevel:
        """
        Map a tampering probability to a ConfidenceLevel enum.
        Uses this module's specific thresholds from settings.
        """
        if probability < self._thresholds.low:
            return ConfidenceLevel.AUTHENTIC
        elif probability < self._thresholds.medium:
            return ConfidenceLevel.LIKELY_AUTHENTIC
        elif probability < self._thresholds.high:
            return ConfidenceLevel.INCONCLUSIVE
        else:
            return ConfidenceLevel.LIKELY_TAMPERED

    def severity_from_probability(self, probability: float) -> Severity:
        """Map a probability directly to a Severity level."""
        if probability < 0.25:   return Severity.INFO
        elif probability < 0.45: return Severity.LOW
        elif probability < 0.65: return Severity.MEDIUM
        elif probability < 0.80: return Severity.HIGH
        else:                    return Severity.CRITICAL

    # ── Finding Builders ───────────────────────────────────────────────────────

    def make_finding(
        self,
        title: str,
        description: str,
        confidence: float,
        severity: Optional[Severity] = None,
        uncertainty: float = 0.10,
        evidence: Optional[dict] = None,
        raw_values: Optional[dict] = None,
    ) -> Finding:
        """
        Convenience constructor for a Finding with this module's name pre-filled.
        Severity is inferred from confidence if not explicitly provided.
        """
        return Finding(
            module=self.MODULE_NAME,
            title=title,
            description=description,
            severity=severity or self.severity_from_probability(confidence),
            confidence=confidence,
            uncertainty=uncertainty,
            evidence=evidence or {},
            raw_values=raw_values or {},
        )

    def make_result(
        self,
        tampering_probability: float,
        uncertainty: float = 0.10,
        summary: str = "",
        findings: Optional[list] = None,
        reliable: bool = True,
        reliability_note: str = "",
        metadata: Optional[dict] = None,
    ) -> ModuleResult:
        """
        Convenience constructor for ModuleResult with
        confidence level automatically mapped.
        """
        return ModuleResult(
            module_name=self.MODULE_NAME,
            status=ModuleStatus.COMPLETED,
            tampering_probability=tampering_probability,
            uncertainty=uncertainty,
            confidence_level=self.map_confidence_level(tampering_probability),
            findings=findings or [],
            summary=summary,
            reliability_flag=reliable,
            reliability_note=reliability_note,
            metadata=metadata or {},
        )


# ─── Custom Exceptions ────────────────────────────────────────────────────────

class InsufficientDataError(Exception):
    """
    Raised by a module when the video does not contain
    enough data for a reliable analysis.
    Examples: too short, too low resolution, no audio track.
    The module will be marked SKIPPED rather than FAILED.
    """
    pass


class ForensicAnalysisError(Exception):
    """General analysis error that causes a module to be marked FAILED."""
    pass
