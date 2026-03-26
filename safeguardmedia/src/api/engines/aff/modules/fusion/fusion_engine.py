"""
AudioForensics — Fusion Engine (Phase 8)

The fusion engine combines the independent scores from all analytic modules
into a single fused probability and verdict. This is the most critical
component in the pipeline: a naive weighted average would systematically
under-report when only one module flags an anomaly (e.g. a clean MP3-to-WAV
re-encode has only a compression artifact signal) and over-report when
modules flag the same underlying cause for different reasons.

The engine implements four corrections over a naive weighted average:

  1. ENF-Absent Weight Redistribution
     The ENF module carries 30% of the total weight. Outdoor recordings,
     recordings in electrically shielded environments, or recordings made
     far from power infrastructure will have no measurable ENF signal —
     the module will be skipped or score near zero through no fault of the
     recording. When ENF cannot be measured, its weight is redistributed
     proportionally among all other non-skipped modules so the fused score
     is not artificially depressed by a 30% zero.

  2. Single-Module Escalation
     When one module produces a very high score (≥ 0.65) and carries
     meaningful weight (≥ 0.08), the fused score cannot fall below
     62% of that module's score. Without this, a single high-confidence
     finding (e.g. COMP=0.85 indicating definite lossy re-encoding) would
     be diluted to ~0.15 by the silent modules — producing an incorrect
     LIKELY_AUTHENTIC verdict for a clearly tampered file.

  3. Corroboration Multiplier
     When multiple independent modules all flag the same recording, each
     additional elevated module (score ≥ 0.35) increases the fused score
     by a diminishing bonus: +10% for the second module, +7% for the third,
     +5% for the fourth, etc. The rationale: false positives from different
     physical measurement methods are statistically independent — two
     independent false positives simultaneously is far less likely than one.

  4. Conflict Detection
     A conflict is flagged when the fused score suggests tampering but the
     result is internally inconsistent — e.g. the fused score is elevated
     but most modules show near-zero scores (one module is doing all the
     work with low confidence), or when a module score is very high but
     its domain partner is very low in unexpected ways.

Verdict thresholds:
    < 0.20  → LIKELY_AUTHENTIC
    < 0.40  → INCONCLUSIVE
    < 0.65  → LIKELY_TAMPERED
    ≥ 0.65  → TAMPERED
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from core.models import (
    AudioCase, Finding, FusionResult, ModuleScore, Verdict,
)

log = logging.getLogger("af.fusion")

# ── Module registry ───────────────────────────────────────────────────────────
# Canonical weight for each module — must sum to 1.0
BASE_WEIGHTS: Dict[str, float] = {
    "enf":         0.30,
    "noise":       0.22,
    "compression": 0.18,
    "reverberation": 0.12,
    "voice":       0.10,
    "metadata":    0.08,
}

# ENF-absent: modules that receive redistributed weight
ENF_MODULE = "enf"

# ── Verdict thresholds ────────────────────────────────────────────────────────
THRESH_AUTHENTIC       = 0.20
THRESH_INCONCLUSIVE    = 0.40
THRESH_LIKELY_TAMPERED = 0.65

# ── Fusion parameters ─────────────────────────────────────────────────────────
ELEVATED_THRESHOLD     = 0.35   # Score above which a module is 'elevated'
ESCALATION_MIN_SCORE   = 0.65   # Module score above which escalation applies
ESCALATION_MIN_WEIGHT  = 0.08   # Module weight above which escalation applies
ESCALATION_FLOOR       = 0.62   # Fused score floor = this × module_score
CORR_BONUSES           = [0.10, 0.07, 0.05, 0.04, 0.03]  # per additional elevated module
CONFLICT_SCORE_THRESH  = 0.40   # Fused score above which conflict is interesting
CONFLICT_MODULE_THRESH = 0.55   # Single-module score that seems under-explained

# ── Metadata isolation ────────────────────────────────────────────────────────
# Metadata is a corroborating witness, not a primary judge.
# It only contributes to the fused score when at least one audio module is
# independently elevated. Clean audio with a suspicious wrapper is not evidence.
# Even when corroborating, its score is capped so it cannot swing a verdict alone.
METADATA_MODULE    = "metadata"
AUDIO_MODULES      = {"enf", "noise", "compression", "reverberation", "voice"}
METADATA_MAX_SCORE = 0.25   # Hard cap on metadata score contribution when corroborating


class FusionEngine:
    """
    Fuses ModuleScore objects from all analytic modules into a FusionResult.

    Usage:
        engine = FusionEngine()
        result = engine.fuse(case)
    """

    def __init__(self, base_weights: Optional[Dict[str, float]] = None):
        self._base_weights = base_weights or dict(BASE_WEIGHTS)

    def fuse(self, case: AudioCase, elapsed_s: float = 0.0) -> FusionResult:
        """
        Run the full fusion pipeline and return a FusionResult.

        Args:
            case:      AudioCase with all module scores populated.
            elapsed_s: Total elapsed time for reporting (optional).

        Returns:
            FusionResult with verdict, fused probability, and all findings.
        """
        t0 = time.time()

        # ── Collect all module scores ──────────────────────────────────────
        module_scores: Dict[str, ModuleScore] = {}
        for ms in case.module_scores:
            module_scores[ms.module] = ms

        # ── Step 1: Determine active (non-skipped) modules ─────────────────
        active: Dict[str, ModuleScore] = {
            name: ms for name, ms in module_scores.items()
            if not ms.skipped
        }

        if not active:
            return self._null_result(module_scores, elapsed_s + time.time() - t0)

        # ── Step 2: Compute adjusted weights ──────────────────────────────
        adjusted_weights = self._compute_weights(active)

        # ── Step 3: ENF-absent detection ──────────────────────────────────
        enf_present = self._is_enf_present(active)
        if not enf_present and ENF_MODULE in adjusted_weights:
            adjusted_weights = self._redistribute_enf_weight(adjusted_weights, active)

        # ── Step 4: Metadata isolation ────────────────────────────────────
        # Zero out metadata score if no audio module is independently elevated.
        # Cap metadata score at METADATA_MAX_SCORE even when corroborating.
        audio_elevated = any(
            active[name].score >= ELEVATED_THRESHOLD
            for name in active
            if name in AUDIO_MODULES
        )
        if METADATA_MODULE in active:
            ms_meta = active[METADATA_MODULE]
            if not audio_elevated:
                # No audio evidence — metadata has no say in the verdict
                # Use a shadow score of 0 for fusion without mutating the ModuleScore
                active = dict(active)
                active[METADATA_MODULE] = ModuleScore(
                    module     = ms_meta.module,
                    score      = 0.0,
                    confidence = ms_meta.confidence,
                    findings   = ms_meta.findings,
                    weight     = ms_meta.weight,
                    skipped    = ms_meta.skipped,
                    skip_reason= ms_meta.skip_reason,
                    elapsed_s  = ms_meta.elapsed_s,
                    metadata   = ms_meta.metadata,
                )
            else:
                # Audio evidence present — cap metadata contribution
                capped = min(ms_meta.score, METADATA_MAX_SCORE)
                if capped < ms_meta.score:
                    active = dict(active)
                    active[METADATA_MODULE] = ModuleScore(
                        module     = ms_meta.module,
                        score      = capped,
                        confidence = ms_meta.confidence,
                        findings   = ms_meta.findings,
                        weight     = ms_meta.weight,
                        skipped    = ms_meta.skipped,
                        skip_reason= ms_meta.skip_reason,
                        elapsed_s  = ms_meta.elapsed_s,
                        metadata   = ms_meta.metadata,
                    )

        # ── Step 5: Weighted base score ───────────────────────────────────
        total_w = sum(adjusted_weights.get(name, 0.0) for name in active)
        if total_w < 1e-9:
            return self._null_result(module_scores, elapsed_s + time.time() - t0)

        base_score = sum(
            active[name].score * adjusted_weights.get(name, 0.0)
            for name in active
        ) / total_w

        # ── Step 6: Single-module escalation ──────────────────────────────
        fused = self._apply_escalation(base_score, active, adjusted_weights)

        # ── Step 7: Corroboration multiplier ──────────────────────────────
        # Metadata is excluded from the elevated list used for corroboration:
        # it should not count as an independent corroborating signal.
        elevated_modules = [
            name for name, ms in active.items()
            if ms.score >= ELEVATED_THRESHOLD and name != METADATA_MODULE
        ]
        fused, corroboration_factor = self._apply_corroboration(fused, elevated_modules, active)

        # ── Step 8: Clamp ─────────────────────────────────────────────────
        fused = float(np.clip(fused, 0.0, 1.0))

        # ── Step 9: Confidence ────────────────────────────────────────────
        confidence = self._compute_confidence(active, adjusted_weights, elevated_modules)

        # ── Step 10: Verdict ──────────────────────────────────────────────
        verdict = self._score_to_verdict(fused)

        # ── Step 11: Conflict detection ───────────────────────────────────
        has_conflict, conflict_description = self._detect_conflict(
            active, adjusted_weights, fused, elevated_modules
        )

        # ── Step 11: Collect all findings ─────────────────────────────────
        all_findings: List[Finding] = []
        for ms in module_scores.values():
            all_findings.extend(ms.findings)
        # Sort: HIGH first, then by module weight desc
        sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        all_findings.sort(
            key=lambda f: (
                sev_order.get(f.severity.name if hasattr(f.severity,'name') else str(f.severity), 9),
                -adjusted_weights.get(f.module, 0.0),
            )
        )

        # ── Step 12: Calibration note ─────────────────────────────────────
        calibration_note = self._calibration_note(enf_present, active, module_scores)

        total_elapsed = elapsed_s + (time.time() - t0)

        return FusionResult(
            verdict              = verdict,
            fused_probability    = fused,
            confidence           = confidence,
            module_scores        = module_scores,
            adjusted_weights     = adjusted_weights,
            all_findings         = all_findings,
            elevated_modules     = elevated_modules,
            corroboration_factor = corroboration_factor,
            has_conflict         = has_conflict,
            conflict_description = conflict_description,
            base_score           = base_score,
            total_elapsed_s      = round(total_elapsed, 3),
            calibration_note     = calibration_note,
        )

    # ── Weight computation ────────────────────────────────────────────────────

    def _compute_weights(self, active: Dict[str, ModuleScore]) -> Dict[str, float]:
        """
        Assign weights to active modules.
        Active modules get their base weight; skipped modules get zero.
        Weights are NOT renormalised here — that happens per step.
        """
        weights = {}
        for name in active:
            weights[name] = self._base_weights.get(name, 0.05)
        return weights

    @staticmethod
    def _is_enf_present(active: Dict[str, ModuleScore]) -> bool:
        """
        ENF is considered 'present' (measurable) when the ENF module ran
        and produced a non-trivial SNR. We use the module score as a proxy:
        if it ran but scored 0.0, that means the signal was genuinely absent.
        If the module was skipped, ENF is absent.
        """
        if ENF_MODULE not in active:
            return False
        enf_ms  = active[ENF_MODULE]
        enf_snr = enf_ms.metadata.get("snr_db", 0.0) or 0.0
        # If the module scored highly it found something real (phase discontinuity
        # etc.) — keep its weight even if the baseline SNR is low.
        if enf_ms.score >= 0.30:
            return True
        return bool(enf_ms.metadata.get("enf_present", False)) or enf_snr > 5.0

    @staticmethod
    def _redistribute_enf_weight(
        weights: Dict[str, float],
        active:  Dict[str, ModuleScore],
    ) -> Dict[str, float]:
        """
        When ENF is not measurable, redistribute its weight proportionally
        among all other active modules.
        """
        enf_w    = weights.pop(ENF_MODULE, 0.0)
        if enf_w < 1e-9 or not weights:
            return weights
        remaining_total = sum(weights.values())
        if remaining_total < 1e-9:
            return weights
        adjusted = {
            name: w + w / remaining_total * enf_w
            for name, w in weights.items()
        }
        # Restore ENF key with zero weight (for transparency)
        adjusted[ENF_MODULE] = 0.0
        return adjusted

    # ── Fusion steps ──────────────────────────────────────────────────────────

    @staticmethod
    def _apply_escalation(
        base_score:  float,
        active:      Dict[str, ModuleScore],
        weights:     Dict[str, float],
    ) -> float:
        """
        Prevent a single high-confidence module from being diluted to near-zero
        by the silent majority of modules.
        """
        fused = base_score
        for name, ms in active.items():
            w = weights.get(name, 0.0)
            if ms.score >= ESCALATION_MIN_SCORE and w >= ESCALATION_MIN_WEIGHT:
                floor = ms.score * ESCALATION_FLOOR
                fused = max(fused, floor)
        return fused

    @staticmethod
    def _apply_corroboration(
        fused:            float,
        elevated_modules: List[str],
        active:           Dict[str, ModuleScore],
    ) -> Tuple[float, float]:
        """
        Boost the fused score when multiple independent modules agree.
        Returns (adjusted_fused, corroboration_factor).
        """
        n = len(elevated_modules)
        if n < 2:
            return fused, 1.0

        # Diminishing bonuses per additional elevated module
        bonus = sum(CORR_BONUSES[:n - 1])
        factor = 1.0 + bonus
        return float(min(1.0, fused * factor)), round(factor, 4)

    @staticmethod
    def _compute_confidence(
        active:           Dict[str, ModuleScore],
        weights:          Dict[str, float],
        elevated_modules: List[str],
    ) -> float:
        """
        Weighted average of module-level confidences, boosted by inter-module
        agreement and penalised by disagreement.
        """
        total_w = sum(weights.get(n, 0.0) for n in active)
        if total_w < 1e-9:
            return 0.40

        w_conf = sum(
            active[name].confidence * weights.get(name, 0.0)
            for name in active
        ) / total_w

        # Agreement bonus among elevated modules
        if len(elevated_modules) >= 2:
            elev_scores = [active[n].score for n in elevated_modules if n in active]
            if elev_scores:
                mean_s = float(np.mean(elev_scores))
                std_s  = float(np.std(elev_scores))
                agreement = 1.0 - std_s / (mean_s + 1e-6)
                w_conf   *= (1.0 + 0.15 * max(0.0, agreement))

        return float(min(0.92, max(0.30, w_conf)))

    @staticmethod
    def _score_to_verdict(score: float) -> Verdict:
        if score < THRESH_AUTHENTIC:
            return Verdict.LIKELY_AUTHENTIC
        elif score < THRESH_INCONCLUSIVE:
            return Verdict.INCONCLUSIVE
        elif score < THRESH_LIKELY_TAMPERED:
            return Verdict.LIKELY_TAMPERED
        else:
            return Verdict.TAMPERED

    @staticmethod
    def _detect_conflict(
        active:           Dict[str, ModuleScore],
        weights:          Dict[str, float],
        fused:            float,
        elevated_modules: List[str],
    ) -> Tuple[bool, str]:
        """
        Detect internally inconsistent results worth flagging to the analyst.

        Two conflict patterns:
        A) One module is very high but no other module supports it — the
           finding may be a false positive, or the tamper is very targeted.
        B) Fused score is elevated but the individual module confidence is low —
           the verdict is uncertain.
        """
        if fused < CONFLICT_SCORE_THRESH or not elevated_modules:
            return False, ""

        # Pattern A: sole-supporter conflict
        n_elevated = len(elevated_modules)
        if n_elevated == 1:
            solo = elevated_modules[0]
            ms   = active.get(solo)
            if ms and ms.score >= CONFLICT_MODULE_THRESH and ms.confidence < 0.65:
                desc = (
                    f"Only one module ({solo}, score={ms.score:.2f}) is elevated. "
                    f"The finding lacks corroboration from other analytic domains. "
                    f"This may indicate a targeted, isolated anomaly or a "
                    f"module-specific false positive. Further review recommended."
                )
                return True, desc

        # Pattern B: fused score elevated but all modules have low confidence
        if fused >= THRESH_INCONCLUSIVE:
            mean_conf = float(np.mean([
                active[n].confidence for n in elevated_modules if n in active
            ])) if elevated_modules else 0.0
            if mean_conf < 0.50:
                desc = (
                    f"Fused score ({fused:.2f}) suggests tampering but the "
                    f"elevated modules have low mean confidence ({mean_conf:.2f}). "
                    f"Results should be treated as indicative rather than conclusive."
                )
                return True, desc

        return False, ""

    @staticmethod
    def _calibration_note(
        enf_present:   bool,
        active:        Dict[str, ModuleScore],
        all_scores:    Dict[str, ModuleScore],
    ) -> str:
        notes = []
        if not enf_present:
            notes.append(
                "ENF signal not detected — ENF module weight redistributed to other modules."
            )
        skipped = [n for n, ms in all_scores.items() if ms.skipped]
        if skipped:
            notes.append(f"Skipped modules: {', '.join(sorted(skipped))}.")
        n_active = len(active)
        if n_active < 3:
            notes.append(
                f"Only {n_active} module(s) produced usable scores — "
                f"confidence in verdict is reduced."
            )
        return "  ".join(notes) if notes else ""

    # ── Null result ───────────────────────────────────────────────────────────

    @staticmethod
    def _null_result(
        module_scores: Dict[str, ModuleScore],
        elapsed_s:     float,
    ) -> FusionResult:
        """Return when no modules produced usable scores."""
        return FusionResult(
            verdict              = Verdict.INCONCLUSIVE,
            fused_probability    = 0.0,
            confidence           = 0.30,
            module_scores        = module_scores,
            adjusted_weights     = {},
            all_findings         = [],
            elevated_modules     = [],
            corroboration_factor = 1.0,
            has_conflict         = False,
            conflict_description = "",
            base_score           = 0.0,
            total_elapsed_s      = round(elapsed_s, 3),
            calibration_note     = "No modules produced usable scores.",
        )
