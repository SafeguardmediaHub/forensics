"""
VideoForensics — Fusion Module (Phase 8)

Orchestrates all forensic modules and combines their scores using
the adaptive fusion engine to produce a final, unified verdict.

This module:
    1. Runs all forensic modules on a case
    2. Collects their ModuleResult objects
    3. Feeds them to the FusionEngine
    4. Wraps the FusionResult in a standard ModuleResult

The fusion module IS the final analysis step before reporting (Phase 9).
Its output — the fused probability, verdict, and confidence — is what
gets displayed in the court-ready report.

Design decisions:
    - Modules run sequentially, not in parallel, to control memory usage
    - Each module failure is handled gracefully (score=0, unreliable=True)
    - The fusion result is stored in metadata for full auditability
    - All raw module results are also preserved for the audit trail
"""

from __future__ import annotations
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.base_module import BaseForensicModule, InsufficientDataError
from core.models import (
    ForensicCase, ModuleResult, Finding, Severity, ModuleStatus
)
from modules.fusion.fusion_engine import FusionEngine, FusionResult, ModuleScore, Verdict
from modules.metadata.metadata_module import MetadataModule
from modules.compression.compression_module import CompressionModule
from modules.noise.noise_module import NoiseModule
from modules.temporal.temporal_module import TemporalModule
from modules.lighting.lighting_module import LightingModule
from modules.audio.audio_module import AudioModule

logger = logging.getLogger("vf.module.fusion")


class FusionModule(BaseForensicModule):
    """
    Final fusion of all forensic module scores.
    Produces the definitive tampering probability and verdict.
    """

    MODULE_NAME = "fusion"

    def __init__(self):
        super().__init__()
        self.engine = FusionEngine()

        # Instantiate all forensic modules
        self._modules = {
            "metadata":    MetadataModule(),
            "compression": CompressionModule(),
            "noise":       NoiseModule(),
            "temporal":    TemporalModule(),
            "lighting":    LightingModule(),
            "audio":       AudioModule(),
        }

    def analyze(self, case: ForensicCase) -> ModuleResult:
        """
        Run all forensic modules, fuse their scores, return final verdict.
        """
        self.logger.info(f"Fusion — case {case.case_id[:8]}...")
        t_start = time.time()

        # ── Run each module ───────────────────────────────────────────────
        raw_results:    Dict[str, ModuleResult] = {}
        module_scores:  List[ModuleScore]       = []
        all_findings:   List[Finding]           = []

        for name, module in self._modules.items():
            t_mod = time.time()
            try:
                result = module.run(case)
                raw_results[name] = result

                # Collect findings from all modules
                all_findings.extend(result.findings)

                # Build ModuleScore for fusion
                module_scores.append(ModuleScore(
                    module_name           = name,
                    tampering_probability = result.tampering_probability,
                    uncertainty           = result.uncertainty,
                    finding_count         = result.finding_count,
                    reliable              = result.reliability_flag,
                    reliability_note      = result.reliability_note or "",
                ))

                self.logger.info(
                    f"  {name:<14} prob={result.tampering_probability:.3f} "
                    f"uncertainty={result.uncertainty:.3f} "
                    f"findings={result.finding_count} "
                    f"({time.time()-t_mod:.1f}s)"
                )

            except Exception as e:
                self.logger.warning(f"  {name} failed: {e}")
                module_scores.append(ModuleScore(
                    module_name           = name,
                    tampering_probability = 0.0,
                    uncertainty           = 0.50,
                    finding_count         = 0,
                    reliable              = False,
                    reliability_note      = f"Module failed: {e}",
                ))

        # ── Fuse scores ───────────────────────────────────────────────────
        fusion = self.engine.fuse(module_scores)

        elapsed = time.time() - t_start
        self.logger.info(
            f"Fusion complete — "
            f"prob={fusion.fused_probability:.3f} "
            f"verdict={fusion.verdict} "
            f"confidence={fusion.confidence:.3f} "
            f"({elapsed:.1f}s total)"
        )

        # ── Build fusion-level findings ───────────────────────────────────
        fusion_findings = self._build_fusion_findings(fusion)

        # ── Assemble metadata ─────────────────────────────────────────────
        metadata = {
            # Core fusion outputs
            "verdict":                   fusion.verdict,
            "verdict_label":             fusion.verdict_label,
            "fused_probability":         fusion.fused_probability,
            "confidence":                fusion.confidence,
            "base_score":                fusion.base_score,
            "corroboration_multiplier":  fusion.corroboration_multiplier,
            "n_elevated_modules":        fusion.n_elevated_modules,
            "modules_in_agreement":      fusion.modules_in_agreement,
            "modules_below_threshold":   fusion.modules_below_threshold,
            "agreement_ratio":           fusion.agreement_ratio,

            # Per-module raw scores
            "module_scores":             fusion.module_scores,
            "adjusted_weights":          fusion.adjusted_weights,
            "weighted_scores":           fusion.weighted_scores,

            # Uncertainty
            "mean_uncertainty":          fusion.mean_uncertainty,
            "uncertainty_range":         list(fusion.uncertainty_range),

            # Conflict / reliability
            "has_conflict":              fusion.has_conflict,
            "conflict_description":      fusion.conflict_description,
            "unreliable_modules":        fusion.unreliable_modules,

            # Performance
            "total_elapsed_s":           round(elapsed, 2),
            "total_findings":            len(all_findings),

            # Pre-calibration note
            "calibration_note": (
                "Probability scores are pre-calibration estimates. "
                "Absolute values will be tuned in Phase 11 against a "
                "labelled corpus. Relative ordering and verdict thresholds "
                "are reliable; exact probabilities are not yet court-ready."
            ),
        }

        return self.make_result(
            tampering_probability = fusion.fused_probability,
            uncertainty           = fusion.mean_uncertainty,
            summary               = fusion.summary,
            findings              = fusion_findings + all_findings,
            reliable              = len(fusion.unreliable_modules) < 3,
            reliability_note      = fusion.conflict_description or "",
            metadata              = metadata,
        )

    # ── Finding generation ────────────────────────────────────────────────────

    def _build_fusion_findings(self, fusion: FusionResult) -> List[Finding]:
        """Create top-level fusion findings for the report."""
        findings = []

        # Always emit a verdict finding
        if fusion.verdict == Verdict.TAMPERED:
            sev  = Severity.HIGH
            conf = fusion.confidence
            desc = (
                f"Forensic analysis yields verdict: TAMPERED "
                f"(fused probability={fusion.fused_probability:.3f}, "
                f"confidence={fusion.confidence:.3f}). "
                f"{fusion.n_elevated_modules} independent forensic domains "
                f"flagged anomalies: {', '.join(fusion.modules_in_agreement)}. "
                f"Corroboration multiplier: {fusion.corroboration_multiplier}×."
            )
        elif fusion.verdict == Verdict.LIKELY_TAMPERED:
            sev  = Severity.HIGH
            conf = fusion.confidence * 0.85
            desc = (
                f"Forensic analysis yields verdict: LIKELY TAMPERED "
                f"(fused probability={fusion.fused_probability:.3f}, "
                f"confidence={fusion.confidence:.3f}). "
                f"Elevated anomalies in: {', '.join(fusion.modules_in_agreement)}."
            )
        elif fusion.verdict == Verdict.INCONCLUSIVE:
            sev  = Severity.MEDIUM
            conf = 0.40
            desc = (
                f"Forensic analysis is inconclusive "
                f"(fused probability={fusion.fused_probability:.3f}). "
                f"Evidence is insufficient to determine tampering with confidence."
            )
        else:  # LIKELY_AUTHENTIC
            sev  = Severity.LOW
            conf = fusion.confidence
            desc = (
                f"Forensic analysis yields verdict: LIKELY AUTHENTIC "
                f"(fused probability={fusion.fused_probability:.3f}, "
                f"confidence={fusion.confidence:.3f}). "
                f"No forensic domain flagged significant anomalies."
            )

        findings.append(self.make_finding(
            title       = f"Fused verdict: {fusion.verdict_label}",
            description = desc,
            confidence  = conf,
            severity    = sev,
            uncertainty = fusion.mean_uncertainty,
            evidence    = {
                "fused_probability":        fusion.fused_probability,
                "confidence":               fusion.confidence,
                "n_elevated_modules":       fusion.n_elevated_modules,
                "modules_in_agreement":     fusion.modules_in_agreement,
                "corroboration_multiplier": fusion.corroboration_multiplier,
            },
        ))

        # Corroboration finding (only when multiple domains agree)
        if fusion.n_elevated_modules >= 2:
            findings.append(self.make_finding(
                title = "Multi-domain corroboration",
                description = (
                    f"{fusion.n_elevated_modules} independent forensic domains "
                    f"produced elevated tampering scores: "
                    f"{', '.join(fusion.modules_in_agreement)}. "
                    f"Independent corroboration across unrelated signal domains "
                    f"(camera sensor, temporal structure, lighting physics, audio spectrum) "
                    f"substantially reduces the probability of coincidental false positives."
                ),
                confidence  = min(0.90, fusion.confidence + 0.10),
                severity    = Severity.HIGH,
                uncertainty = fusion.mean_uncertainty,
                evidence    = {
                    "elevated_modules":   fusion.modules_in_agreement,
                    "agreement_ratio":    fusion.agreement_ratio,
                },
            ))

        # Conflict finding (when present)
        if fusion.has_conflict:
            findings.append(self.make_finding(
                title = "Partial-domain tampering pattern",
                description = fusion.conflict_description,
                confidence  = 0.50,
                severity    = Severity.MEDIUM,
                uncertainty = 0.25,
                evidence    = {
                    "modules_elevated": fusion.modules_in_agreement,
                    "modules_clean":    fusion.modules_below_threshold,
                },
            ))

        return findings
