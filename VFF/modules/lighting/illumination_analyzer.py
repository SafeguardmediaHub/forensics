"""
VideoForensics — Illumination Field Analyzer
Analyzes spatial and temporal lighting consistency.

Primary forensic signal: ABRUPTNESS of illumination change.
  Genuine video: illumination changes gradually (max/mean ratio 1–5).
  Splice boundary: one abrupt spike far above the mean (ratio 50–100+).

This distinguishes gradual natural lighting transitions from the sudden
discontinuity introduced when clips filmed under different conditions are joined.

Checks:
    1. Illumination abruptness — abrupt vs gradual change (primary)
    2. Spike timestamps — localize the boundary
    3. Spatial illumination smoothness — within-frame field coherence
    4. Light source direction consistency across frames
    5. Colour temperature temporal stability
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Tuple

import cv2
import numpy as np
from scipy import ndimage

logger = logging.getLogger("vf.lighting.illumination")


@dataclass
class IlluminationResult:
    illum_abruptness_ratio: float       = 0.0
    illum_abruptness_score: float       = 0.0
    illum_spike_timestamps: List[float] = field(default_factory=list)
    illum_spike_frames: List[int]       = field(default_factory=list)
    temporal_mean_values: List[float]   = field(default_factory=list)
    temporal_cv: float                  = 0.0
    spatial_smoothness_score: float     = 0.0
    spatial_anomaly_regions: int        = 0
    direction_variance: float           = 0.0
    gradient_direction_consistency: float = 0.0
    colour_temp_temporal_cv: float      = 0.0
    colour_temp_consistency: float      = 0.0
    tampering_probability: float        = 0.0
    findings: List[str]                 = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class IlluminationAnalyzer:
    """
    Analyzes illumination field for forensic anomalies.
    Primary signal: abruptness ratio of frame-to-frame illumination change.
    Authentic: ratio ~2. Splice boundary: ratio 50-100+.
    """

    ILLUM_SIGMA              = 30
    GRID_BLOCKS              = 6
    ABRUPT_CLEAN_THRESHOLD   = 5.0
    ABRUPT_SUSPECT_THRESHOLD = 15.0
    ABRUPT_CERTAIN_THRESHOLD = 40.0
    SPIKE_MULTIPLE           = 8.0

    def analyze(self, frames: List[np.ndarray], timestamps: List[float]) -> IlluminationResult:
        result = IlluminationResult()

        if len(frames) < 5:
            result.add_finding("Insufficient frames for illumination analysis")
            return result

        logger.info(f"Illumination analysis — {len(frames)} frames")

        # Per-frame luminance means
        L_means = []
        for f in frames:
            L = cv2.cvtColor(f, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float64)
            L_means.append(float(L.mean()))
        result.temporal_mean_values = L_means
        L_arr = np.array(L_means)
        result.temporal_cv = float(L_arr.std() / (L_arr.mean() + 1e-6))

        self._detect_abruptness(L_arr, timestamps, result)

        illum_fields = [self._get_illum_field(f) for f in frames[:30]]
        self._analyze_spatial_smoothness(illum_fields, result)
        self._analyze_gradient_directions(frames, result)
        self._analyze_colour_temperature(frames, result)

        result.tampering_probability = self._compute_probability(result)
        self._generate_findings(result)

        logger.info(
            f"Illumination — abruptness={result.illum_abruptness_ratio:.2f} "
            f"score={result.illum_abruptness_score:.3f} "
            f"spikes={len(result.illum_spike_timestamps)} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    def _get_illum_field(self, frame: np.ndarray) -> np.ndarray:
        L = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float64)
        return ndimage.gaussian_filter(L, sigma=self.ILLUM_SIGMA)

    def _detect_abruptness(self, L_arr: np.ndarray, timestamps: List[float],
                            result: IlluminationResult) -> None:
        if len(L_arr) < 3:
            return
        d1 = np.abs(np.diff(L_arr))
        mean_d1 = float(d1.mean())
        max_d1  = float(d1.max())
        if mean_d1 < 1e-6:
            return

        ratio = max_d1 / mean_d1
        result.illum_abruptness_ratio = ratio

        if ratio <= self.ABRUPT_CLEAN_THRESHOLD:
            score = 0.0
        elif ratio >= self.ABRUPT_CERTAIN_THRESHOLD:
            score = 1.0
        else:
            score = (ratio - self.ABRUPT_CLEAN_THRESHOLD) / (
                self.ABRUPT_CERTAIN_THRESHOLD - self.ABRUPT_CLEAN_THRESHOLD)
        result.illum_abruptness_score = float(np.clip(score, 0.0, 1.0))

        spike_threshold = mean_d1 * self.SPIKE_MULTIPLE
        for i, diff in enumerate(d1):
            if diff > spike_threshold:
                ts = timestamps[i+1] if (i+1) < len(timestamps) else float(i)
                result.illum_spike_timestamps.append(ts)
                result.illum_spike_frames.append(i+1)

    def _analyze_spatial_smoothness(self, illum_fields: List[np.ndarray],
                                     result: IlluminationResult) -> None:
        if not illum_fields:
            return
        mean_field = np.mean(illum_fields, axis=0)
        smooth     = ndimage.gaussian_filter(mean_field, sigma=5)
        residual   = np.abs(mean_field - smooth)
        result.spatial_smoothness_score = min(
            1.0, float(residual.std() / (mean_field.std() + 1e-6)))
        h, w = mean_field.shape
        bh, bw = h // self.GRID_BLOCKS, w // self.GRID_BLOCKS
        block_means = [float(mean_field[i*bh:(i+1)*bh, j*bw:(j+1)*bw].mean())
                       for i in range(self.GRID_BLOCKS) for j in range(self.GRID_BLOCKS)]
        arr = np.array(block_means)
        std = arr.std()
        result.spatial_anomaly_regions = int(np.sum(np.abs(arr - arr.mean()) > 2.5*std)) if std > 0 else 0

    def _analyze_gradient_directions(self, frames: List[np.ndarray],
                                      result: IlluminationResult) -> None:
        directions = []
        for frame in frames:
            L  = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float64)
            gx = cv2.Sobel(L, cv2.CV_64F, 1, 0, ksize=5)
            gy = cv2.Sobel(L, cv2.CV_64F, 0, 1, ksize=5)
            mag = np.sqrt(gx**2 + gy**2)
            strong = mag > np.percentile(mag, 85)
            if strong.sum() < 100:
                continue
            angles = np.arctan2(gy[strong], gx[strong])
            directions.append(float(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())))
        if len(directions) < 3:
            return
        dirs = np.array(directions)
        R    = float(np.sqrt(np.sin(dirs).mean()**2 + np.cos(dirs).mean()**2))
        result.direction_variance = 1.0 - R
        # Raised threshold: complex natural scenes produce omnidirectional gradients
        result.gradient_direction_consistency = min(
            1.0, max(0.0, (result.direction_variance - 0.40) / 0.50))

    def _analyze_colour_temperature(self, frames: List[np.ndarray],
                                     result: IlluminationResult) -> None:
        """Use abruptness not total CV — total CV trends with scene content."""
        temporal_r = []
        for frame in frames:
            R = frame[:, :, 2].astype(np.float64)
            G = frame[:, :, 1].astype(np.float64)
            B = frame[:, :, 0].astype(np.float64)
            temporal_r.append(float((R / (R + G + B + 1e-6)).mean()))
        if len(temporal_r) < 3:
            return
        t = np.array(temporal_r)
        result.colour_temp_temporal_cv = float(t.std() / (t.mean() + 1e-6))
        d1 = np.abs(np.diff(t))
        if d1.mean() < 1e-8:
            result.colour_temp_consistency = 0.0
            return
        abruptness = float(d1.max() / d1.mean())
        result.colour_temp_consistency = min(1.0, max(0.0, (abruptness - 5.0) / 25.0))

    def _compute_probability(self, result: IlluminationResult) -> float:
        score = max(0.0, result.illum_abruptness_score * 0.85)
        if result.illum_spike_timestamps:
            score = max(score, 0.50 + 0.05 * min(5, len(result.illum_spike_timestamps)))
        score = max(score, result.gradient_direction_consistency * 0.40)
        score = max(score, result.colour_temp_consistency * 0.35)
        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: IlluminationResult) -> None:
        if result.illum_abruptness_score > 0.30:
            pos_str = ""
            if result.illum_spike_timestamps:
                positions = [f"{t:.2f}s" for t in result.illum_spike_timestamps[:5]]
                pos_str = f" at {', '.join(positions)}"
            result.add_finding(
                f"Abrupt illumination change detected "
                f"(abruptness ratio={result.illum_abruptness_ratio:.1f}, "
                f"score={result.illum_abruptness_score:.3f}){pos_str}. "
                f"Genuine video has gradual illumination transitions (ratio 1–5). "
                f"A high ratio indicates a sudden jump consistent with content "
                f"from a different lighting environment being spliced in."
            )
        if result.gradient_direction_consistency > 0.70:  # Raised to reduce false positives on complex content
            result.add_finding(
                f"Inconsistent light source direction across frames "
                f"(circular variance={result.direction_variance:.3f}, "
                f"score={result.gradient_direction_consistency:.3f}). "
                f"In a genuine recording the primary light direction is stable. "
                f"High variance suggests content from different lighting setups."
            )
        if result.colour_temp_consistency > 0.60:
            result.add_finding(
                f"Colour temperature temporal instability "
                f"(CV={result.colour_temp_temporal_cv:.4f}, "
                f"score={result.colour_temp_consistency:.3f}). "
                f"Significant frame-to-frame shift in overall colour balance "
                f"indicates transitions between different lighting environments."
            )
