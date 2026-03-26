"""
VideoForensics — Temporal Integrity Module (Phase 5)

Detects tampering through inter-frame temporal analysis:
    1. Frame duplication — frozen frames, repeated frames, looping
    2. Optical flow discontinuities — motion magnitude spikes,
       direction reversals, coherence drops, speed anomalies
    3. Inter-frame similarity profile — statistical fingerprint
       of how frames relate to each other over time

Temporal analysis is complementary to PRNU (Phase 4):
    - PRNU detects WHAT camera a frame came from
    - Temporal analysis detects WHERE the edit boundary is
    Together they provide strong spatial + temporal localization.

Sub-score weights:
    Duplication:  0.45  (frozen frames are a very strong signal)
    Flow:         0.40  (motion discontinuities)
    Similarity:   0.15  (inter-frame profile consistency)
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

from core.base_module import BaseForensicModule, InsufficientDataError
from core.models import (
    ForensicCase, ModuleResult, Finding, Severity, TemporalLocation
)
from modules.temporal.duplication_detector import FrameDuplicationDetector, DuplicationResult
from modules.temporal.flow_analyzer import OpticalFlowAnalyzer, FlowAnalysisResult
from config.settings import WORKING_DIR, MIN_FRAMES_FOR_ANALYSIS

logger = logging.getLogger("vf.module.temporal")


class TemporalModule(BaseForensicModule):
    """
    Forensic analysis of temporal integrity across video frames.
    """

    MODULE_NAME = "temporal"

    WEIGHTS = {
        "duplication": 0.45,
        "flow":        0.40,
        "similarity":  0.15,
    }

    # How many frames to analyze (capped for performance)
    MAX_FRAMES = 120
    # Minimum needed
    MIN_FRAMES  = 10

    def __init__(self):
        super().__init__()
        self.dup_detector  = FrameDuplicationDetector()
        self.flow_analyzer = OpticalFlowAnalyzer()

    def analyze(self, case: ForensicCase) -> ModuleResult:
        profile = case.video_profile
        if profile is None:
            raise InsufficientDataError("No VideoProfile available")

        if profile.total_frames < self.MIN_FRAMES:
            raise InsufficientDataError(
                f"Too few frames for temporal analysis ({profile.total_frames})"
            )

        video_path = case.working_file_path or case.original_file_path
        if not video_path or not Path(video_path).exists():
            raise InsufficientDataError("No video file accessible")

        video_path = Path(video_path)
        findings: List[Finding] = []
        sub_scores = {}

        self.logger.info(f"Temporal analysis — case {case.case_id[:8]}...")

        # ── Load frames ───────────────────────────────────────────────────
        frames, timestamps = self._load_frames(video_path, profile)

        if len(frames) < self.MIN_FRAMES:
            raise InsufficientDataError(
                f"Could only decode {len(frames)} frames"
            )

        self.logger.info(
            f"Loaded {len(frames)} frames "
            f"({timestamps[0]:.2f}s – {timestamps[-1]:.2f}s)"
        )

        # ── 1. Frame duplication ──────────────────────────────────────────
        dup_result  = self.dup_detector.analyze(frames, timestamps, profile.frame_rate)
        d_findings  = self._process_dup_result(dup_result, profile)
        sub_scores["duplication"] = dup_result.duplication_score
        findings.extend(d_findings)

        # ── 2. Optical flow ───────────────────────────────────────────────
        # Use a subsample for flow — computationally expensive
        flow_frames, flow_ts = self._subsample(frames, timestamps, target=50)
        flow_result = self.flow_analyzer.analyze(flow_frames, flow_ts)
        f_findings  = self._process_flow_result(flow_result, profile)
        sub_scores["flow"] = flow_result.discontinuity_score
        findings.extend(f_findings)

        # ── 3. Inter-frame similarity profile ────────────────────────────
        sim_score, s_findings = self._analyze_similarity_profile(
            frames, timestamps, profile
        )
        sub_scores["similarity"] = sim_score
        findings.extend(s_findings)

        # ── Weighted score ────────────────────────────────────────────────
        tampering_prob = sum(
            sub_scores.get(k, 0.0) * w
            for k, w in self.WEIGHTS.items()
        )
        tampering_prob = min(1.0, max(0.0, tampering_prob))

        # Uncertainty: lower when we have many frames
        frame_coverage = min(1.0, len(frames) / 150)
        uncertainty = round(0.10 + 0.10 * (1 - frame_coverage), 3)

        summary = self._build_summary(sub_scores, dup_result, flow_result, findings)

        self.logger.info(
            f"Temporal analysis complete — "
            f"prob={tampering_prob:.3f} findings={len(findings)}"
        )

        return self.make_result(
            tampering_probability=tampering_prob,
            uncertainty=uncertainty,
            summary=summary,
            findings=findings,
            reliable=True,
            metadata={
                "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
                "frames_analyzed": len(frames),
                "frozen_frames": dup_result.frozen_frame_count,
                "frozen_rate": round(dup_result.frozen_frame_rate, 4),
                "frozen_segments": len(dup_result.frozen_segments),
                "hash_duplicates": dup_result.hash_duplicate_count,
                "flow_spikes": len(flow_result.magnitude_spikes),
                "flow_direction_reversals": len(flow_result.direction_reversals),
                "flow_coherence_drops": len(flow_result.coherence_drops),
                "speed_anomalies": len(flow_result.speed_anomalies),
                "mean_flow_magnitude": round(flow_result.mean_flow_magnitude, 4),
            }
        )

    # ── Frame Loading ─────────────────────────────────────────────────────────

    def _load_frames(
        self, video_path: Path, profile
    ) -> Tuple[List[np.ndarray], List[float]]:
        """Load up to MAX_FRAMES evenly-sampled frames."""
        total  = profile.total_frames or 1
        step   = max(1, total // self.MAX_FRAMES)
        fps    = profile.frame_rate or 30.0

        frames, timestamps = [], []
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return frames, timestamps

        idx = 0
        try:
            while len(frames) < self.MAX_FRAMES:
                ret, frame = cap.read()
                if not ret:
                    break
                if idx % step == 0:
                    frames.append(frame.copy())
                    timestamps.append(idx / fps)
                idx += 1
        finally:
            cap.release()

        return frames, timestamps

    def _subsample(
        self,
        frames: List[np.ndarray],
        timestamps: List[float],
        target: int,
    ) -> Tuple[List[np.ndarray], List[float]]:
        """Return a uniform subsample of frames."""
        if len(frames) <= target:
            return frames, timestamps
        step = len(frames) // target
        idxs = list(range(0, len(frames), step))[:target]
        return [frames[i] for i in idxs], [timestamps[i] for i in idxs]

    # ── Result Processors ─────────────────────────────────────────────────────

    def _process_dup_result(
        self, result: DuplicationResult, profile
    ) -> List[Finding]:
        """Convert DuplicationResult to Finding objects."""
        findings = []

        for text in result.findings:
            text_lower = text.lower()
            if "frozen" in text_lower and result.frozen_frame_rate > 0.05:
                sev  = Severity.HIGH
                conf = min(0.90, 0.50 + result.frozen_frame_rate * 2)
            elif "non-consecutive" in text_lower:
                sev  = Severity.MEDIUM
                conf = 0.60
            else:
                sev  = Severity.LOW
                conf = 0.35

            f = self.make_finding(
                title="Frame duplication detected",
                description=text,
                confidence=conf,
                severity=sev,
                uncertainty=0.08,
                evidence={
                    "frozen_frame_count": result.frozen_frame_count,
                    "frozen_frame_rate": round(result.frozen_frame_rate, 4),
                    "frozen_segments": len(result.frozen_segments),
                    "hash_duplicate_count": result.hash_duplicate_count,
                },
                raw_values={
                    "frozen_segments_timestamps": result.frozen_segments[:5],
                },
            )

            # Attach temporal location for frozen segments
            if result.frozen_segments:
                f.temporal_location = TemporalLocation(
                    start_frame=0, end_frame=profile.total_frames,
                    start_seconds=result.frozen_segments[0][0],
                    end_seconds=result.frozen_segments[-1][1],
                )

            findings.append(f)

        return findings

    def _process_flow_result(
        self, result: FlowAnalysisResult, profile
    ) -> List[Finding]:
        """Convert FlowAnalysisResult to Finding objects."""
        findings = []

        for text in result.findings:
            text_lower = text.lower()
            if "spike" in text_lower or "reversal" in text_lower:
                sev  = Severity.HIGH
                conf = min(0.80, result.discontinuity_score + 0.10)
            elif "coherence" in text_lower or "speed" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.discontinuity_score
            else:
                sev  = Severity.LOW
                conf = 0.30

            findings.append(self.make_finding(
                title="Optical flow discontinuity",
                description=text,
                confidence=max(0.15, conf),
                severity=sev,
                uncertainty=0.15,
                evidence={
                    "flow_spikes": len(result.magnitude_spikes),
                    "direction_reversals": len(result.direction_reversals),
                    "coherence_drops": len(result.coherence_drops),
                    "speed_anomalies": len(result.speed_anomalies),
                    "mean_flow_magnitude": round(result.mean_flow_magnitude, 4),
                },
                raw_values={
                    "spike_timestamps": result.magnitude_spikes[:5],
                    "reversal_timestamps": result.direction_reversals[:5],
                },
            ))

        return findings

    # ── Inter-Frame Similarity Profile ────────────────────────────────────────

    def _analyze_similarity_profile(
        self,
        frames: List[np.ndarray],
        timestamps: List[float],
        profile,
    ) -> Tuple[float, List[Finding]]:
        """
        Analyze the statistical profile of inter-frame similarity (MAD).
        A genuine continuous recording has a characteristic MAD distribution.
        Abrupt changes in the distribution — especially bi-modality —
        indicate the video contains segments with different motion characteristics,
        consistent with spliced content from different recordings.
        """
        findings = []
        score = 0.0

        if len(frames) < 10:
            return score, findings

        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
        mads  = np.array([
            float(cv2.absdiff(grays[i-1], grays[i]).mean())
            for i in range(1, len(grays))
        ])

        if len(mads) < 5:
            return score, findings

        # Statistical properties of the MAD distribution
        mad_mean = float(mads.mean())
        mad_std  = float(mads.std())
        mad_cv   = mad_std / (mad_mean + 1e-6)  # Coefficient of variation

        # Bimodality test using Hartigan's dip test approximation
        # High bimodality → two distinct motion regimes → possible splice
        bimodality = self._bimodality_score(mads)

        # Outlier rate — frames far from the mean
        if mad_std > 0:
            outlier_rate = float(np.mean(np.abs(mads - mad_mean) > 3 * mad_std))
        else:
            outlier_rate = 0.0

        if bimodality > 0.60 and mad_cv > 0.40:
            score = 0.55
            findings.append(self.make_finding(
                title="Bimodal inter-frame similarity distribution",
                description=(
                    f"Inter-frame similarity profile shows two distinct motion regimes "
                    f"(bimodality score={bimodality:.3f}, CV={mad_cv:.3f}). "
                    f"A genuine continuous recording has a unimodal MAD distribution. "
                    f"Bimodality indicates the video contains segments with "
                    f"fundamentally different motion characteristics — "
                    f"consistent with content from different recordings being spliced together."
                ),
                confidence=0.55,
                severity=Severity.MEDIUM,
                uncertainty=0.20,
                evidence={
                    "bimodality_score": round(bimodality, 3),
                    "mad_cv": round(mad_cv, 3),
                    "mad_mean": round(mad_mean, 4),
                    "mad_std": round(mad_std, 4),
                },
            ))
        elif outlier_rate > 0.05:
            score = 0.35
            findings.append(self.make_finding(
                title="Elevated inter-frame similarity outliers",
                description=(
                    f"{outlier_rate*100:.1f}% of consecutive frame pairs show "
                    f"similarity values more than 3σ from the video mean. "
                    f"Elevated outlier rates suggest isolated frames or segments "
                    f"with different visual content characteristics."
                ),
                confidence=0.35,
                severity=Severity.LOW,
                uncertainty=0.25,
                evidence={
                    "outlier_rate": round(outlier_rate, 4),
                    "mad_mean": round(mad_mean, 4),
                    "mad_std": round(mad_std, 4),
                },
            ))

        return score, findings

    @staticmethod
    def _bimodality_score(data: np.ndarray) -> float:
        """
        Estimate bimodality using the bimodality coefficient:
        BC = (skewness² + 1) / (kurtosis + 3*(n-1)²/((n-2)(n-3)))
        BC > 0.555 suggests bimodality.
        Returns a score normalized to [0, 1].
        """
        from scipy import stats as sp_stats
        n = len(data)
        if n < 5:
            return 0.0
        skew = float(sp_stats.skew(data))
        kurt = float(sp_stats.kurtosis(data))

        # Excess kurtosis correction for small samples
        kurt_corrected = kurt + 3 * (n-1)**2 / ((n-2) * (n-3)) if n > 3 else kurt
        denom = abs(kurt_corrected) + 3
        if denom < 1e-6:
            return 0.0

        bc = (skew**2 + 1) / denom
        # Normalize: BC of 0.555 = threshold, map to [0,1]
        return float(np.clip((bc - 0.2) / 0.8, 0.0, 1.0))

    # ── Summary ───────────────────────────────────────────────────────────────

    def _build_summary(
        self,
        sub_scores: dict,
        dup: DuplicationResult,
        flow: FlowAnalysisResult,
        findings: List[Finding],
    ) -> str:
        parts = ["Temporal analysis examined frame duplication, "
                 "optical flow continuity, and inter-frame similarity. "]

        if dup.frozen_frame_count > 0:
            parts.append(
                f"{dup.frozen_frame_count} frozen frame(s) detected "
                f"({dup.frozen_frame_rate:.1%}). "
            )
        else:
            parts.append("No frozen or duplicated frames detected. ")

        n_disc = len(flow.magnitude_spikes) + len(flow.direction_reversals)
        if n_disc > 0:
            parts.append(f"{n_disc} optical flow discontinuity/anomalies flagged. ")
        else:
            parts.append("Optical flow is consistent throughout. ")

        if not findings:
            parts.append("No significant temporal anomalies found.")

        return "".join(parts)
