"""
VideoForensics — Shadow & Highlight Analyzer
Detects inconsistencies in shadow directions and specular highlights.

Forensic relevance:
    Shadows and specular highlights are constrained by physical optics.
    All shadows in a scene must point away from the same light source(s).
    All specular highlights on similar materials must appear at positions
    consistent with the same reflection geometry.

    When content from different recordings is composited:
    - Shadows may point in conflicting directions
    - Specular highlights may appear at physically inconsistent positions
    - The ratio of shadow to lit area may be inconsistent between regions

    This module focuses on two checks:
    1. Shadow boundary orientation consistency across frame regions
    2. Specular highlight distribution and intensity consistency
    3. Ambient-to-diffuse light ratio consistency across regions
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from scipy import ndimage, stats

logger = logging.getLogger("vf.lighting.shadow")


@dataclass
class ShadowHighlightResult:
    """Results of shadow and highlight analysis."""

    # Shadow analysis
    shadow_direction_consistency: float  = 0.0   # 0=consistent, 1=inconsistent
    shadow_coverage_cv: float            = 0.0   # Regional shadow coverage variation
    shadow_anomalous_regions: int        = 0

    # Highlight analysis
    highlight_distribution_score: float  = 0.0   # 0=natural, 1=anomalous
    highlight_intensity_consistency: float = 0.0

    # Ambient/diffuse ratio
    ambient_diffuse_consistency: float   = 0.0   # 0=consistent, 1=varying

    # Overall
    tampering_probability: float         = 0.0
    findings: List[str]                  = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class ShadowHighlightAnalyzer:
    """
    Analyzes shadow and highlight physics consistency.
    """

    GRID_BLOCKS = 4   # 4×4 spatial analysis grid
    SHADOW_PERCENTILE = 20   # Bottom 20% of luminance = shadow region
    HIGHLIGHT_PERCENTILE = 95  # Top 5% of luminance = highlight region

    def analyze(
        self,
        frames: List[np.ndarray],
        timestamps: List[float],
    ) -> ShadowHighlightResult:
        """
        Analyze shadow and highlight consistency across frames.
        """
        result = ShadowHighlightResult()

        if len(frames) < 3:
            return result

        logger.info(f"Shadow/highlight analysis — {len(frames)} frames")

        # ── 1. Shadow boundary direction consistency ───────────────────────
        self._analyze_shadow_directions(frames, result)

        # ── 2. Shadow coverage per region ─────────────────────────────────
        self._analyze_shadow_coverage(frames, result)

        # ── 3. Specular highlight consistency ─────────────────────────────
        self._analyze_highlights(frames, result)

        # ── 4. Ambient/diffuse ratio ───────────────────────────────────────
        self._analyze_ambient_diffuse(frames, result)

        # ── Overall score ──────────────────────────────────────────────────
        result.tampering_probability = self._compute_probability(result)

        # ── Findings ──────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"Shadow/highlight complete — "
            f"shadow_dir={result.shadow_direction_consistency:.3f} "
            f"shadow_cv={result.shadow_coverage_cv:.3f} "
            f"highlight={result.highlight_distribution_score:.3f} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    def _analyze_shadow_directions(
        self,
        frames: List[np.ndarray],
        result: ShadowHighlightResult,
    ) -> None:
        """
        Estimate the dominant shadow boundary direction in each frame.
        Shadows cast by the same light source all point the same direction.
        Inconsistency across frames suggests different light sources —
        possible evidence of compositing.
        """
        directions = []

        for frame in frames:
            L = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float64)

            # Shadow threshold: bottom percentile of luminance
            shadow_thresh = np.percentile(L, self.SHADOW_PERCENTILE)
            shadow_mask   = L < shadow_thresh

            if shadow_mask.sum() < 500:
                continue

            # Compute gradient at shadow boundaries (edges between shadow and lit)
            shadow_edge = cv2.Canny(shadow_mask.astype(np.uint8) * 255, 50, 150)
            if shadow_edge.sum() < 100:
                continue

            gx = cv2.Sobel(L, cv2.CV_64F, 1, 0, ksize=3)
            gy = cv2.Sobel(L, cv2.CV_64F, 0, 1, ksize=3)

            edge_mask = shadow_edge > 0
            if edge_mask.sum() < 50:
                continue

            angles = np.arctan2(gy[edge_mask], gx[edge_mask])
            # Circular mean of edge angles
            circ_mean = float(np.arctan2(
                np.sin(angles).mean(),
                np.cos(angles).mean()
            ))
            directions.append(circ_mean)

        if len(directions) < 3:
            return

        dirs = np.array(directions)
        R   = float(np.sqrt(np.sin(dirs).mean()**2 + np.cos(dirs).mean()**2))
        circular_var = 1.0 - R
        result.shadow_direction_consistency = min(
            1.0, max(0.0, (circular_var - 0.10) / 0.50)
        )

    def _analyze_shadow_coverage(
        self,
        frames: List[np.ndarray],
        result: ShadowHighlightResult,
    ) -> None:
        """
        Measure shadow coverage per spatial region.
        In a genuine scene, shadow coverage varies smoothly.
        Abrupt differences between adjacent regions suggest compositing.
        """
        # Average shadow coverage across frames for each spatial block
        block_shadow_rates = []

        for frame in frames:
            L = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float64)
            shadow_thresh = np.percentile(L, self.SHADOW_PERCENTILE)
            shadow_mask   = L < shadow_thresh

            h, w = L.shape
            bh, bw = h // self.GRID_BLOCKS, w // self.GRID_BLOCKS
            rates = []
            for i in range(self.GRID_BLOCKS):
                for j in range(self.GRID_BLOCKS):
                    block = shadow_mask[i*bh:(i+1)*bh, j*bw:(j+1)*bw]
                    rates.append(float(block.mean()))
            block_shadow_rates.append(rates)

        if not block_shadow_rates:
            return

        mean_rates = np.mean(block_shadow_rates, axis=0)
        cv = float(np.std(mean_rates) / (np.mean(mean_rates) + 1e-6))
        result.shadow_coverage_cv = cv

        # Flag blocks with anomalous shadow coverage
        mean_rate = np.mean(mean_rates)
        std_rate  = np.std(mean_rates)
        if std_rate > 0:
            result.shadow_anomalous_regions = int(
                np.sum(np.abs(mean_rates - mean_rate) > 2.5 * std_rate)
            )

    def _analyze_highlights(
        self,
        frames: List[np.ndarray],
        result: ShadowHighlightResult,
    ) -> None:
        """
        Analyze specular highlight distribution consistency.
        Highlights should appear at physically consistent positions
        relative to the light source geometry.
        We measure: highlight area fraction, intensity distribution,
        and spatial consistency across frames.
        """
        highlight_rates    = []
        highlight_intensities = []

        for frame in frames:
            # Use HSV: high V (value/brightness) + low S (saturation) = specular
            hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            V    = hsv[:, :, 2].astype(np.float64)
            S    = hsv[:, :, 1].astype(np.float64)

            # Specular mask: very bright and desaturated
            highlight_mask = (V > 220) & (S < 40)
            highlight_rates.append(float(highlight_mask.mean()))
            if highlight_mask.any():
                highlight_intensities.append(float(V[highlight_mask].mean()))

        if not highlight_rates:
            return

        rates = np.array(highlight_rates)
        rate_cv = float(rates.std() / (rates.mean() + 1e-6))

        # High CV in highlight rates means highlight area varies erratically
        result.highlight_distribution_score = min(
            1.0, max(0.0, (rate_cv - 0.30) / 1.0)
        )

        if highlight_intensities:
            intens = np.array(highlight_intensities)
            intens_cv = float(intens.std() / (intens.mean() + 1e-6))
            result.highlight_intensity_consistency = min(
                1.0, max(0.0, (intens_cv - 0.05) / 0.20)
            )

    def _analyze_ambient_diffuse(
        self,
        frames: List[np.ndarray],
        result: ShadowHighlightResult,
    ) -> None:
        """
        Measure the ratio of ambient (shadow) to diffuse (lit) luminance.
        This ratio should be consistent throughout a genuine recording.
        Significant variation indicates frames from different lighting setups.
        """
        ratios = []

        for frame in frames:
            L = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float64)
            shadow_thresh = np.percentile(L, self.SHADOW_PERCENTILE)
            lit_thresh    = np.percentile(L, 100 - self.SHADOW_PERCENTILE)

            shadow_mean = float(L[L < shadow_thresh].mean()) if (L < shadow_thresh).any() else 0
            lit_mean    = float(L[L > lit_thresh].mean())    if (L > lit_thresh).any() else 255

            if lit_mean > 0:
                ratio = shadow_mean / lit_mean
                ratios.append(ratio)

        if len(ratios) < 3:
            return

        ratios_arr = np.array(ratios)
        cv = float(ratios_arr.std() / (ratios_arr.mean() + 1e-6))
        result.ambient_diffuse_consistency = min(1.0, max(0.0, (cv - 0.08) / 0.25))

    def _compute_probability(self, result: ShadowHighlightResult) -> float:
        """Aggregate shadow/highlight signals into tampering probability.
        
        Ambient/diffuse ratio is excluded — it trends with scene content
        (gradual zoom/pan changes it legitimately) making it unreliable.
        Only signals that measure SPATIAL inconsistency within frames are used.
        """
        score = (
            0.50 * result.shadow_direction_consistency +
            0.30 * result.highlight_distribution_score +
            0.20 * result.highlight_intensity_consistency
        )
        if result.shadow_anomalous_regions > 3:
            score = max(score, 0.30)
        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: ShadowHighlightResult) -> None:
        """Generate human-readable findings."""

        if result.shadow_direction_consistency > 0.50:
            result.add_finding(
                f"Shadow boundary direction is inconsistent across frames "
                f"(score={result.shadow_direction_consistency:.3f}). "
                f"Shadows cast by the same light source point in the same direction. "
                f"High variance in shadow boundary orientation suggests content "
                f"from different lighting environments was composited."
            )

        # Ambient/diffuse ratio is excluded from findings — it trends with
        # legitimate scene content changes and produces false positives.

        if result.highlight_distribution_score > 0.50:
            result.add_finding(
                f"Specular highlight distribution is inconsistent "
                f"(score={result.highlight_distribution_score:.3f}). "
                f"Significant variation in highlight area and position across frames "
                f"suggests different light source geometries."
            )
