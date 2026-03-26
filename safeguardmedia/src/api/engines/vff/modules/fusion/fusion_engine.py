"""
VideoForensics — Adaptive Fusion Engine (Phase 8)

Combines independent forensic module scores into a single calibrated
tampering probability and structured verdict.

Fusion design principles:
─────────────────────────
1. UNCERTAINTY-ADJUSTED WEIGHTS
   Each module declares its uncertainty (0–1). Modules with lower
   uncertainty carry more weight in the final score. This prevents
   a highly uncertain module (e.g. lighting on CGI content) from
   dominating the result.

2. CORROBORATION BONUS
   Multiple independent forensic domains agreeing is exponentially
   stronger evidence than a single high score. A score of 0.60 in
   one module could be a false positive; the same score across four
   independent domains is extremely unlikely without genuine tampering.

   Multiplier schedule:
     0 elevated modules → 1.00× (no boost)
     1 elevated module  → 1.00× (single domain — possible false positive)
     2 elevated modules → 1.25×
     3 elevated modules → 1.50×
     4+ elevated modules → 1.75×

   'Elevated' = module score ≥ ELEVATION_THRESHOLD (0.30)

3. CONFLICT DETECTION
   When modules strongly disagree (e.g. noise=0.8 but audio=0.0),
   the system flags this and slightly reduces confidence (but not
   the probability — disagreement can mean the tampering only
   affects one domain).

4. CONFIDENCE vs PROBABILITY
   These are separate quantities:
   - Probability: how likely is the video tampered? (0–1)
   - Confidence: how certain is the system of that probability? (0–1)
   Low confidence = "we have a reading but the evidence is weak or mixed"

5. VERDICT THRESHOLDS (pre-calibration)
   These will be tuned in Phase 11 once a calibration corpus is built.
   Current values are conservative starting points:
   < 0.20 → LIKELY_AUTHENTIC
   0.20–0.40 → INCONCLUSIVE
   0.40–0.65 → LIKELY_TAMPERED
   ≥ 0.65 → TAMPERED

Base module weights (sum to 1.0):
   noise:       0.25  (PRNU is the forensic gold standard)
   temporal:    0.20  (frame-level analysis — well established)
   metadata:    0.15  (strong signal, but can be legitimately absent)
   compression: 0.15  (double-compression artifacts)
   lighting:    0.15  (physics-based, content-dependent)
   audio:       0.10  (corroborating domain)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("vf.fusion")


# ── Verdict Enum ──────────────────────────────────────────────────────────────

class Verdict:
    LIKELY_AUTHENTIC = "likely_authentic"
    INCONCLUSIVE     = "inconclusive"
    LIKELY_TAMPERED  = "likely_tampered"
    TAMPERED         = "tampered"

    # Human-readable labels for reporting
    LABELS = {
        LIKELY_AUTHENTIC: "Likely Authentic",
        INCONCLUSIVE:     "Inconclusive",
        LIKELY_TAMPERED:  "Likely Tampered",
        TAMPERED:         "Tampered",
    }

    # Colour codes for reporting (for future UI use)
    COLORS = {
        LIKELY_AUTHENTIC: "green",
        INCONCLUSIVE:     "yellow",
        LIKELY_TAMPERED:  "orange",
        TAMPERED:         "red",
    }


# ── Module Score Input ────────────────────────────────────────────────────────

@dataclass
class ModuleScore:
    """Score from a single forensic module."""
    module_name:         str
    tampering_probability: float        # 0–1
    uncertainty:         float          # 0–1
    finding_count:       int            = 0
    reliable:            bool           = True
    reliability_note:    str            = ""


# ── Fusion Result ─────────────────────────────────────────────────────────────

@dataclass
class FusionResult:
    """Final fused assessment output."""

    # Core outputs
    fused_probability:    float                  # 0–1
    confidence:           float                  # 0–1
    verdict:              str                    # Verdict constant
    verdict_label:        str                    # Human-readable

    # Module contributions
    module_scores:        Dict[str, float]       # raw module probabilities
    adjusted_weights:     Dict[str, float]       # effective weights used
    weighted_scores:      Dict[str, float]       # module × weight contribution
    base_score:           float                  # weighted avg before corroboration
    corroboration_multiplier: float
    n_elevated_modules:   int

    # Disagreement analysis
    modules_in_agreement:    List[str]           # modules above threshold
    modules_below_threshold: List[str]           # modules that didn't flag
    agreement_ratio:         float               # elevated / total reliable

    # Uncertainty
    mean_uncertainty:     float
    uncertainty_range:    Tuple[float, float]    # (min, max) across modules

    # Flags
    has_conflict:         bool                   # modules strongly disagree
    conflict_description: str                    = ""
    unreliable_modules:   List[str]              = field(default_factory=list)

    # Summary text (for reporting)
    summary:              str                    = ""


# ── Fusion Engine ─────────────────────────────────────────────────────────────

class FusionEngine:
    """
    Combines scores from all forensic modules into a final verdict.
    """

    # Base weights — must sum to 1.0
    BASE_WEIGHTS: Dict[str, float] = {
        "noise":       0.25,
        "temporal":    0.20,
        "metadata":    0.15,
        "compression": 0.15,
        "lighting":    0.15,
        "audio":       0.10,
    }

    # Module is "elevated" if its score is at or above this
    ELEVATION_THRESHOLD = 0.30

    # A module is "strongly elevated" if above this (used for conflict detection)
    STRONG_THRESHOLD = 0.55

    # Corroboration multiplier schedule
    CORROBORATION_SCHEDULE = {
        0: 1.00,
        1: 1.00,
        2: 1.25,
        3: 1.50,
    }
    CORROBORATION_MAX = 1.75   # 4+ elevated modules

    # Verdict thresholds (pre-calibration)
    VERDICT_THRESHOLDS = [
        (0.20, Verdict.LIKELY_AUTHENTIC),
        (0.40, Verdict.INCONCLUSIVE),
        (0.65, Verdict.LIKELY_TAMPERED),
        (1.01, Verdict.TAMPERED),
    ]

    def fuse(self, module_scores: List[ModuleScore]) -> FusionResult:
        """
        Combine module scores into a final fused result.

        Args:
            module_scores: List of ModuleScore objects from all modules

        Returns:
            FusionResult with probability, verdict, confidence, and diagnostics
        """
        if not module_scores:
            return self._empty_result()

        # Build dicts for convenience
        scores      = {ms.module_name: ms.tampering_probability for ms in module_scores}
        uncertainties = {ms.module_name: ms.uncertainty for ms in module_scores}
        reliable    = {ms.module_name: ms.reliable for ms in module_scores}

        # ── Step 1: Uncertainty-adjusted weights ──────────────────────────
        adj_weights  = self._adjust_weights(module_scores)
        weighted_scores = {m: scores.get(m, 0.0) * adj_weights.get(m, 0.0)
                           for m in adj_weights}
        base_score   = float(sum(weighted_scores.values()))

        # ── Step 2: Corroboration ─────────────────────────────────────────
        reliable_modules = [ms.module_name for ms in module_scores if ms.reliable]
        elevated = [m for m in reliable_modules
                    if scores.get(m, 0.0) >= self.ELEVATION_THRESHOLD]
        n_elevated = len(elevated)
        corr_mult  = self.CORROBORATION_SCHEDULE.get(
            n_elevated, self.CORROBORATION_MAX
        )

        fused_prob = min(1.0, base_score * corr_mult)

        # ── Step 3: Conflict detection ────────────────────────────────────
        has_conflict, conflict_desc = self._detect_conflict(scores, reliable_modules)

        # ── Step 4: Confidence ────────────────────────────────────────────
        confidence = self._compute_confidence(
            module_scores, n_elevated, fused_prob, has_conflict
        )

        # ── Step 5: Verdict ───────────────────────────────────────────────
        verdict = self._assign_verdict(fused_prob)

        # ── Diagnostics ───────────────────────────────────────────────────
        below_threshold = [m for m in reliable_modules
                           if scores.get(m, 0.0) < self.ELEVATION_THRESHOLD]
        agreement_ratio = n_elevated / max(len(reliable_modules), 1)

        uncert_vals = [ms.uncertainty for ms in module_scores]
        unreliable  = [ms.module_name for ms in module_scores if not ms.reliable]

        summary = self._build_summary(
            fused_prob, verdict, n_elevated, elevated,
            below_threshold, corr_mult, has_conflict
        )

        result = FusionResult(
            fused_probability         = round(fused_prob, 4),
            confidence                = round(confidence, 4),
            verdict                   = verdict,
            verdict_label             = Verdict.LABELS[verdict],
            module_scores             = {m: round(v, 4) for m, v in scores.items()},
            adjusted_weights          = {m: round(v, 4) for m, v in adj_weights.items()},
            weighted_scores           = {m: round(v, 4) for m, v in weighted_scores.items()},
            base_score                = round(base_score, 4),
            corroboration_multiplier  = corr_mult,
            n_elevated_modules        = n_elevated,
            modules_in_agreement      = elevated,
            modules_below_threshold   = below_threshold,
            agreement_ratio           = round(agreement_ratio, 4),
            mean_uncertainty          = round(float(np.mean(uncert_vals)), 4),
            uncertainty_range         = (
                round(float(min(uncert_vals)), 4),
                round(float(max(uncert_vals)), 4),
            ),
            has_conflict              = has_conflict,
            conflict_description      = conflict_desc,
            unreliable_modules        = unreliable,
            summary                   = summary,
        )

        logger.info(
            f"Fusion — base={base_score:.3f} "
            f"n_elevated={n_elevated} mult={corr_mult}x "
            f"fused={fused_prob:.3f} "
            f"confidence={confidence:.3f} "
            f"verdict={verdict}"
        )

        return result

    # ── Internal Methods ──────────────────────────────────────────────────────

    def _adjust_weights(
        self, module_scores: List[ModuleScore]
    ) -> Dict[str, float]:
        """
        Adjust base weights by module uncertainty and reliability.
        Lower uncertainty → proportionally higher effective weight.
        Unreliable modules → weight reduced by 50%.
        Modules not in base weights → weight 0 (ignored).
        """
        raw = {}
        for ms in module_scores:
            if ms.module_name not in self.BASE_WEIGHTS:
                continue
            base  = self.BASE_WEIGHTS[ms.module_name]
            # Uncertainty adjustment: weight ∝ 1/(uncertainty + ε)
            # uncertainty=0.08 → 1/0.09 = 11.1
            # uncertainty=0.18 → 1/0.19 = 5.26
            adj   = base / (ms.uncertainty + 0.01)
            if not ms.reliable:
                adj *= 0.50
            raw[ms.module_name] = adj

        # Add zero weight for any base-weight modules that didn't run
        for m in self.BASE_WEIGHTS:
            if m not in raw:
                raw[m] = 0.0

        # Normalize to sum to 1.0
        total = sum(raw.values())
        if total < 1e-9:
            return {m: 1.0 / len(self.BASE_WEIGHTS) for m in self.BASE_WEIGHTS}

        return {m: v / total for m, v in raw.items()}

    def _detect_conflict(
        self,
        scores: Dict[str, float],
        reliable_modules: List[str],
    ) -> Tuple[bool, str]:
        """
        Detect when forensic modules strongly disagree.
        Conflict = at least one module strongly elevated AND
                   at least one reliable module well below the elevation threshold.
        This is expected for partial tampering (e.g. only video was spliced,
        not audio) and should reduce confidence but not probability.
        """
        strongly_elevated = [
            m for m in reliable_modules
            if scores.get(m, 0.0) >= self.STRONG_THRESHOLD
        ]
        strongly_low = [
            m for m in reliable_modules
            if scores.get(m, 0.0) < 0.15
        ]

        if strongly_elevated and strongly_low:
            desc = (
                f"Conflict: {', '.join(strongly_elevated)} strongly elevated "
                f"while {', '.join(strongly_low)} show no anomalies. "
                f"This may indicate partial tampering (only certain domains affected)."
            )
            return True, desc

        return False, ""

    def _compute_confidence(
        self,
        module_scores: List[ModuleScore],
        n_elevated: int,
        fused_prob: float,
        has_conflict: bool,
    ) -> float:
        """
        Compute confidence in the final verdict (separate from probability).
        High confidence requires: multiple elevated modules, low uncertainty,
        consistent signals, and reliable analyzers.
        """
        # Start from base confidence proportional to number of elevated modules
        conf = 0.30 + n_elevated * 0.12

        # Reduce for high mean uncertainty
        mean_uncert = np.mean([ms.uncertainty for ms in module_scores])
        conf -= mean_uncert * 0.40

        # Reduce for conflicts
        if has_conflict:
            conf -= 0.15

        # Reduce for unreliable modules
        n_unreliable = sum(1 for ms in module_scores if not ms.reliable)
        conf -= n_unreliable * 0.05

        # Increase when verdict is unambiguous (very high or very low probability)
        distance_from_boundary = min(
            abs(fused_prob - 0.20),
            abs(fused_prob - 0.40),
            abs(fused_prob - 0.65),
        )
        conf += distance_from_boundary * 0.30

        return float(np.clip(conf, 0.05, 0.99))

    def _assign_verdict(self, probability: float) -> str:
        """Map fused probability to verdict category."""
        for threshold, verdict in self.VERDICT_THRESHOLDS:
            if probability < threshold:
                return verdict
        return Verdict.TAMPERED

    def _build_summary(
        self,
        fused_prob: float,
        verdict: str,
        n_elevated: int,
        elevated: List[str],
        below: List[str],
        mult: float,
        has_conflict: bool,
    ) -> str:
        """Build a human-readable summary for the report."""
        label = Verdict.LABELS[verdict]
        parts = [f"Fused verdict: {label} (probability={fused_prob:.3f}). "]

        if n_elevated == 0:
            parts.append(
                "No forensic domain flagged anomalies above the elevation threshold. "
            )
        elif n_elevated == 1:
            parts.append(
                f"One forensic domain flagged anomalies: {elevated[0]}. "
                f"Single-domain evidence — possible false positive. "
            )
        else:
            parts.append(
                f"{n_elevated} independent forensic domains flagged anomalies: "
                f"{', '.join(elevated)}. "
                f"Corroboration multiplier: {mult}×. "
            )

        if below:
            parts.append(
                f"Domains showing no anomalies: {', '.join(below)}. "
            )

        if has_conflict:
            parts.append(
                "Conflict detected: some domains strongly elevated while others are clean. "
                "This may indicate partial or domain-specific tampering. "
            )

        return "".join(parts)

    def _empty_result(self) -> FusionResult:
        """Return a neutral result when no module scores are available."""
        return FusionResult(
            fused_probability         = 0.0,
            confidence                = 0.0,
            verdict                   = Verdict.INCONCLUSIVE,
            verdict_label             = Verdict.LABELS[Verdict.INCONCLUSIVE],
            module_scores             = {},
            adjusted_weights          = {},
            weighted_scores           = {},
            base_score                = 0.0,
            corroboration_multiplier  = 1.0,
            n_elevated_modules        = 0,
            modules_in_agreement      = [],
            modules_below_threshold   = [],
            agreement_ratio           = 0.0,
            mean_uncertainty          = 1.0,
            uncertainty_range         = (0.0, 1.0),
            has_conflict              = False,
            summary                   = "No module scores available — verdict is inconclusive.",
        )
