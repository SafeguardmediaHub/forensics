"""
VideoForensics — Optical Flow Analyzer
Detects temporal discontinuities through inter-frame motion analysis.

Forensic relevance:
    Natural video has smooth, physically plausible motion between frames.
    Editing artifacts show up as:
    - Abrupt jumps in flow magnitude (sudden start/stop of motion)
    - Direction reversals inconsistent with natural camera/subject motion
    - Motion field coherence breaks (different regions moving independently
      in ways that defy physical causation)
    - Flow magnitude outliers at specific timestamps (splice boundaries)

Optical flow is particularly useful for detecting:
    - Hard cuts (splice boundaries with discontinuous motion)
    - Speed manipulation (artificially sped-up or slowed-down segments)
    - Inserted still images or graphics within motion footage
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from scipy import stats, signal

logger = logging.getLogger("vf.temporal.flow")


@dataclass
class FlowAnalysisResult:
    """Results of optical flow analysis."""
    frames_analyzed: int               = 0

    # Flow magnitude statistics
    flow_magnitudes: List[float]       = field(default_factory=list)
    mean_flow_magnitude: float         = 0.0
    std_flow_magnitude: float          = 0.0

    # Discontinuity detection
    magnitude_spikes: List[float]      = field(default_factory=list)   # timestamps
    magnitude_drops: List[float]       = field(default_factory=list)   # timestamps
    direction_reversals: List[float]   = field(default_factory=list)   # timestamps

    # Motion consistency
    coherence_scores: List[float]      = field(default_factory=list)   # per-frame
    mean_coherence: float              = 0.0
    coherence_drops: List[float]       = field(default_factory=list)   # timestamps

    # Speed consistency
    speed_anomalies: List[float]       = field(default_factory=list)   # timestamps

    # Overall
    discontinuity_score: float         = 0.0
    findings: List[str]                = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class OpticalFlowAnalyzer:
    """
    Analyzes inter-frame optical flow for temporal discontinuities
    and unnatural motion patterns.
    """

    # Standard deviations above mean to flag as a spike
    SPIKE_THRESHOLD_STD = 3.0

    # Coherence score below this is suspicious
    COHERENCE_DROP_THRESHOLD = 0.30

    # Minimum frames for reliable analysis
    MIN_FRAMES = 10

    # Farneback flow parameters
    FARNEBACK_PARAMS = dict(
        pyr_scale=0.5,
        levels=3,
        winsize=12,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )

    def analyze(
        self,
        frames: List[np.ndarray],
        timestamps: List[float],
    ) -> FlowAnalysisResult:
        """
        Compute optical flow between consecutive frames and analyze
        for temporal discontinuities.
        """
        result = FlowAnalysisResult()
        result.frames_analyzed = len(frames)

        if len(frames) < self.MIN_FRAMES:
            result.add_finding(
                f"Insufficient frames for optical flow analysis ({len(frames)})"
            )
            return result

        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]

        # ── Compute flow between consecutive frames ────────────────────────
        flows         = []
        magnitudes    = []
        mean_vecs     = []
        coherences    = []

        for i in range(1, len(grays)):
            flow = cv2.calcOpticalFlowFarneback(
                grays[i-1], grays[i], None, **self.FARNEBACK_PARAMS
            )
            flows.append(flow)

            mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            magnitudes.append(float(mag.mean()))
            mean_vecs.append((float(flow[..., 0].mean()), float(flow[..., 1].mean())))

            # Motion coherence: how uniform is the motion field?
            # High coherence = all pixels moving similarly (camera pan/tilt)
            # Low coherence = chaotic motion (multiple independent objects)
            coherence = self._compute_coherence(flow)
            coherences.append(coherence)

        result.flow_magnitudes = magnitudes
        result.coherence_scores = coherences

        if not magnitudes:
            return result

        mags = np.array(magnitudes)
        result.mean_flow_magnitude = float(mags.mean())
        result.std_flow_magnitude  = float(mags.std())
        result.mean_coherence      = float(np.mean(coherences))

        # ── Detect magnitude spikes and drops ─────────────────────────────
        self._detect_magnitude_anomalies(mags, timestamps, result)

        # ── Detect direction reversals ─────────────────────────────────────
        self._detect_direction_reversals(mean_vecs, timestamps, result)

        # ── Detect coherence drops ─────────────────────────────────────────
        self._detect_coherence_drops(coherences, timestamps, result)

        # ── Speed consistency ──────────────────────────────────────────────
        self._analyze_speed_consistency(mags, timestamps, result)

        # ── Overall score ──────────────────────────────────────────────────
        result.discontinuity_score = self._compute_score(result)

        # ── Findings ──────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"Flow analysis — "
            f"frames={len(frames)} "
            f"mean_mag={result.mean_flow_magnitude:.4f} "
            f"spikes={len(result.magnitude_spikes)} "
            f"coherence_drops={len(result.coherence_drops)} "
            f"score={result.discontinuity_score:.3f}"
        )
        return result

    def _compute_coherence(self, flow: np.ndarray) -> float:
        """
        Measure how coherent (uniform) the motion field is.
        Returns 0–1, where 1 = perfectly uniform motion.
        """
        fx, fy = flow[..., 0], flow[..., 1]
        mag    = np.sqrt(fx**2 + fy**2)

        # Normalize by mean magnitude to get direction-only measure
        mean_mag = float(mag.mean())
        if mean_mag < 0.01:
            return 1.0   # Near-static frame: coherence is undefined, assume consistent

        # Coherence: ratio of mean magnitude to mean of per-pixel magnitudes
        # Perfect coherence = all vectors same direction → ratio approaches 1
        mean_fx = abs(fx.mean())
        mean_fy = abs(fy.mean())
        net_mag = float(np.sqrt(mean_fx**2 + mean_fy**2))
        coherence = net_mag / (mean_mag + 1e-6)
        return float(np.clip(coherence, 0.0, 1.0))

    def _detect_magnitude_anomalies(
        self,
        mags: np.ndarray,
        timestamps: List[float],
        result: FlowAnalysisResult,
    ) -> None:
        """Detect sudden spikes or drops in flow magnitude."""
        if result.std_flow_magnitude < 1e-6:
            return

        spike_thresh = result.mean_flow_magnitude + self.SPIKE_THRESHOLD_STD * result.std_flow_magnitude
        drop_thresh  = result.mean_flow_magnitude - self.SPIKE_THRESHOLD_STD * result.std_flow_magnitude

        for i, mag in enumerate(mags):
            ts = timestamps[i+1] if (i+1) < len(timestamps) else float(i)
            if mag > spike_thresh:
                result.magnitude_spikes.append(ts)
            elif drop_thresh > 0 and mag < drop_thresh:
                result.magnitude_drops.append(ts)

    def _detect_direction_reversals(
        self,
        mean_vecs: List[Tuple[float, float]],
        timestamps: List[float],
        result: FlowAnalysisResult,
    ) -> None:
        """
        Detect abrupt reversals in dominant motion direction.
        In natural video, motion direction changes gradually.
        An abrupt >90° reversal in one frame suggests a splice or cut.
        """
        if len(mean_vecs) < 3:
            return

        angles = [np.arctan2(vy, vx) for vx, vy in mean_vecs]

        for i in range(1, len(angles)):
            diff = abs(angles[i] - angles[i-1])
            diff = min(diff, 2 * np.pi - diff)   # Handle wraparound

            # Only flag when there's meaningful motion to measure direction of
            mag_i = np.sqrt(mean_vecs[i][0]**2 + mean_vecs[i][1]**2)
            if diff > np.pi / 2 and mag_i > 0.5:   # >90° and significant motion
                ts = timestamps[i+1] if (i+1) < len(timestamps) else float(i)
                result.direction_reversals.append(ts)

    def _detect_coherence_drops(
        self,
        coherences: List[float],
        timestamps: List[float],
        result: FlowAnalysisResult,
    ) -> None:
        """
        Detect sudden drops in motion field coherence.
        A coherence drop at a specific frame indicates the motion
        pattern became suddenly chaotic — consistent with a splice
        joining two clips with different camera motion characteristics.
        """
        if len(coherences) < 5:
            return

        coh = np.array(coherences)
        mean_coh = coh.mean()
        std_coh  = coh.std()

        for i, c in enumerate(coherences):
            ts = timestamps[i+1] if (i+1) < len(timestamps) else float(i)
            if c < self.COHERENCE_DROP_THRESHOLD and c < mean_coh - 2*std_coh:
                result.coherence_drops.append(ts)

    def _analyze_speed_consistency(
        self,
        mags: np.ndarray,
        timestamps: List[float],
        result: FlowAnalysisResult,
    ) -> None:
        """
        Look for abrupt changes in motion speed across the video.
        Uses a rolling window comparison to detect regions with
        significantly different speed characteristics.
        """
        if len(mags) < 20:
            return

        window = max(5, len(mags) // 10)
        for i in range(window, len(mags) - window):
            before = mags[i-window:i]
            after  = mags[i:i+window]

            mean_before = before.mean()
            mean_after  = after.mean()

            if mean_before < 0.01 and mean_after < 0.01:
                continue   # Both near-static — skip

            # Ratio change — speed doubles or halves suddenly
            ratio = max(mean_before, mean_after) / (min(mean_before, mean_after) + 0.01)
            if ratio > 3.0:
                ts = timestamps[i] if i < len(timestamps) else float(i)
                if not any(abs(ts - s) < 0.5 for s in result.speed_anomalies):
                    result.speed_anomalies.append(ts)

    def _compute_score(self, result: FlowAnalysisResult) -> float:
        """Aggregate flow signals into 0–1 discontinuity score."""
        n = result.frames_analyzed
        if n == 0:
            return 0.0

        score = 0.0

        # Magnitude spikes (sudden motion jumps)
        spike_rate = len(result.magnitude_spikes) / n
        if spike_rate > 0.05:
            score = max(score, 0.65)
        elif spike_rate > 0.02:
            score = max(score, 0.45)
        elif result.magnitude_spikes:
            score = max(score, 0.30)

        # Direction reversals
        if len(result.direction_reversals) > 3:
            score = max(score, 0.60)
        elif result.direction_reversals:
            score = max(score, 0.35)

        # Coherence drops
        coh_rate = len(result.coherence_drops) / n
        if coh_rate > 0.05:
            score = max(score, 0.55)
        elif result.coherence_drops:
            score = max(score, 0.30)

        # Speed anomalies
        if len(result.speed_anomalies) > 2:
            score = max(score, 0.60)
        elif result.speed_anomalies:
            score = max(score, 0.35)

        return min(1.0, score)

    def _generate_findings(self, result: FlowAnalysisResult) -> None:
        """Generate human-readable findings."""

        if result.magnitude_spikes:
            positions = [f"{t:.2f}s" for t in result.magnitude_spikes[:5]]
            result.add_finding(
                f"Optical flow magnitude spikes at {len(result.magnitude_spikes)} "
                f"position(s): {', '.join(positions)}. "
                f"Abrupt increases in inter-frame motion beyond "
                f"{self.SPIKE_THRESHOLD_STD}σ from the mean indicate "
                f"temporal discontinuities consistent with splicing or "
                f"content insertion."
            )

        if result.direction_reversals:
            positions = [f"{t:.2f}s" for t in result.direction_reversals[:5]]
            result.add_finding(
                f"Motion direction reversals detected at "
                f"{len(result.direction_reversals)} position(s): {', '.join(positions)}. "
                f"Abrupt >90° changes in dominant motion direction are "
                f"inconsistent with natural camera movement and suggest "
                f"content from different recordings was joined."
            )

        if result.coherence_drops:
            positions = [f"{t:.2f}s" for t in result.coherence_drops[:5]]
            result.add_finding(
                f"Motion field coherence drops at "
                f"{len(result.coherence_drops)} position(s): {', '.join(positions)}. "
                f"Sudden changes in how uniformly the frame is moving "
                f"indicate a transition between clips with different "
                f"camera motion characteristics."
            )

        if result.speed_anomalies:
            positions = [f"{t:.2f}s" for t in result.speed_anomalies[:5]]
            result.add_finding(
                f"Motion speed anomalies at {len(result.speed_anomalies)} "
                f"position(s): {', '.join(positions)}. "
                f"Abrupt changes in overall motion speed (>3× ratio) "
                f"suggest speed manipulation or content from a different "
                f"recording with a different frame rate."
            )
