"""
VideoForensics — Noise & Signal Integrity Module (Phase 4)

The most powerful single forensic module in the system.
Every camera leaves a unique noise fingerprint (PRNU) in every frame it captures.
This module extracts that fingerprint and checks it for consistency.

Checks performed:
    1. PRNU fingerprint extraction and internal consistency
    2. Temporal PRNU consistency (per-frame correlation drops)
    3. Spatial PRNU consistency (regional fingerprint mismatches)
    4. Local noise statistics (variance, skewness across regions)
    5. Frequency domain noise analysis (periodic artifacts)
    6. Cross-frame noise correlation

Sub-score weights (sum to 1.0):
    PRNU internal consistency:   0.40
    PRNU temporal drops:         0.25
    PRNU spatial consistency:    0.20
    Noise pattern analysis:      0.15
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import cv2
import numpy as np

from core.base_module import BaseForensicModule, InsufficientDataError
from core.models import (
    ForensicCase, ModuleResult, Finding, Severity,
    TemporalLocation, FrameRegion
)
from modules.noise.prnu_extractor import PRNUExtractor, PRNUResult
from modules.noise.noise_analyzer import NoisePatternAnalyzer, NoisePatternResult
from config.settings import WORKING_DIR, MIN_FRAMES_FOR_ANALYSIS

logger = logging.getLogger("vf.module.noise")


class NoiseModule(BaseForensicModule):
    """
    Forensic analysis of sensor noise patterns and PRNU camera fingerprint.

    Reliability notes:
    - Best results with 50+ frames (PRNU needs averaging to emerge)
    - Low resolution video (<480p) reduces PRNU reliability
    - Heavy lossy compression degrades noise signal
    - Reliability flags are set automatically when data is insufficient
    """

    MODULE_NAME = "noise"

    WEIGHTS = {
        "prnu_consistency": 0.40,
        "prnu_temporal":    0.25,
        "prnu_spatial":     0.20,
        "noise_pattern":    0.15,
    }

    # Frames to use for analysis (capped for performance)
    MAX_ANALYSIS_FRAMES = 60
    # Minimum for any meaningful PRNU
    MIN_PRNU_FRAMES = 15

    def __init__(self):
        super().__init__()
        self.prnu_extractor      = PRNUExtractor()
        self.noise_analyzer      = NoisePatternAnalyzer()

    def analyze(self, case: ForensicCase) -> ModuleResult:
        profile = case.video_profile
        if profile is None:
            raise InsufficientDataError("No VideoProfile available")

        if profile.total_frames < self.MIN_PRNU_FRAMES:
            raise InsufficientDataError(
                f"Too few frames for noise analysis "
                f"({profile.total_frames}, need {self.MIN_PRNU_FRAMES})"
            )

        video_path = case.working_file_path or case.original_file_path
        if not video_path or not Path(video_path).exists():
            raise InsufficientDataError("No video file accessible")

        video_path = Path(video_path)
        findings: List[Finding] = []
        sub_scores = {}

        self.logger.info(f"Noise analysis — case {case.case_id[:8]}...")

        # ── Sample frames evenly across the video ─────────────────────────
        frames, timestamps = self._load_frames(video_path, profile)

        if len(frames) < self.MIN_PRNU_FRAMES:
            raise InsufficientDataError(
                f"Could only decode {len(frames)} frames "
                f"(need {self.MIN_PRNU_FRAMES})"
            )

        self.logger.info(f"Loaded {len(frames)} frames for analysis")

        # ── 1. PRNU Analysis ───────────────────────────────────────────────
        prnu_result = self.prnu_extractor.analyze(
            frame_iterator=iter(list(enumerate(frames))),
            frame_timestamps=timestamps,
        )
        pscore, p_findings = self._process_prnu_result(prnu_result, profile)
        sub_scores["prnu_consistency"] = prnu_result.tampering_probability
        sub_scores["prnu_temporal"]    = self._temporal_drop_score(prnu_result)
        sub_scores["prnu_spatial"]     = self._spatial_score(prnu_result)
        findings.extend(p_findings)

        # ── 2. Noise Pattern Analysis ──────────────────────────────────────
        noise_result = self.noise_analyzer.analyze(
            frame_iterator=iter(list(enumerate(frames[:30]))),
        )
        nscore, n_findings = self._process_noise_result(noise_result)
        sub_scores["noise_pattern"] = noise_result.tampering_probability
        findings.extend(n_findings)

        # ── 3. Weighted Score ──────────────────────────────────────────────
        tampering_prob = sum(
            sub_scores.get(k, 0.0) * w
            for k, w in self.WEIGHTS.items()
        )
        tampering_prob = min(1.0, max(0.0, tampering_prob))

        # ── 4. Reliability & Uncertainty ──────────────────────────────────
        reliable = prnu_result.consistency_reliable
        reliability_note = ""
        uncertainty = 0.12

        if not reliable:
            reliability_note = (
                f"PRNU fingerprint estimated from {prnu_result.frames_used} frames. "
                f"Reliability increases with more frames. "
                f"Results should be treated as indicative only."
            )
            uncertainty = 0.25

        if profile.is_low_resolution:
            reliability_note += " Low resolution reduces PRNU accuracy."
            uncertainty += 0.08

        # ── 5. Summary ────────────────────────────────────────────────────
        summary = self._build_summary(sub_scores, prnu_result, noise_result, findings)

        self.logger.info(
            f"Noise analysis complete — "
            f"prob={tampering_prob:.3f} "
            f"findings={len(findings)} "
            f"PRNU_frames={prnu_result.frames_used}"
        )

        return self.make_result(
            tampering_probability=tampering_prob,
            uncertainty=round(uncertainty, 3),
            summary=summary,
            findings=findings,
            reliable=reliable,
            reliability_note=reliability_note,
            metadata={
                "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
                "prnu_frames_used": prnu_result.frames_used,
                "prnu_fingerprint_quality": round(prnu_result.fingerprint_quality, 4),
                "prnu_internal_consistency": round(prnu_result.internal_consistency_score, 4),
                "prnu_temporal_mean": round(prnu_result.temporal_mean, 4),
                "prnu_temporal_std": round(prnu_result.temporal_std, 4),
                "prnu_temporal_drops": len(prnu_result.temporal_drops),
                "prnu_spatial_score": round(prnu_result.spatial_consistency_score, 4),
                "prnu_spatial_anomalies": len(prnu_result.spatial_anomaly_regions),
                "noise_variance_consistency": round(noise_result.local_variance_consistency, 4),
                "noise_frequency_periodicity": round(noise_result.frequency_periodicity_score, 4),
                "noise_regional_anomalies": noise_result.regional_anomalies,
                "reliable": reliable,
            }
        )

    # ── Frame Loading ─────────────────────────────────────────────────────────

    def _load_frames(
        self,
        video_path: Path,
        profile,
    ) -> Tuple[List[np.ndarray], List[float]]:
        """
        Load evenly-sampled frames from the video.
        Returns (frames, timestamps) where each frame is a BGR numpy array.
        """
        total   = profile.total_frames or 1
        target  = min(self.MAX_ANALYSIS_FRAMES, total)
        step    = max(1, total // target)

        frames     = []
        timestamps = []

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return frames, timestamps

        fps = profile.frame_rate or cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_idx = 0

        try:
            while len(frames) < target:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_idx % step == 0:
                    frames.append(frame.copy())
                    timestamps.append(frame_idx / fps)
                frame_idx += 1
        finally:
            cap.release()

        return frames, timestamps

    # ── Result Processors ─────────────────────────────────────────────────────

    def _process_prnu_result(
        self,
        result: PRNUResult,
        profile,
    ) -> Tuple[float, List[Finding]]:
        """Convert PRNUResult into Finding objects."""
        findings = []

        for finding_text in result.findings:
            # Map finding text to severity
            text_lower = finding_text.lower()
            if "strongly" in text_lower or "negative" in text_lower or "mismatch" in text_lower:
                sev  = Severity.HIGH
                conf = min(0.85, result.tampering_probability + 0.10)
            elif "very low" in text_lower or "inconsistent" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.tampering_probability
            elif "caution" in text_lower or "insufficient" in text_lower:
                sev  = Severity.LOW
                conf = 0.25
            else:
                sev  = Severity.MEDIUM
                conf = result.tampering_probability

            f = self.make_finding(
                title="PRNU camera fingerprint anomaly",
                description=finding_text,
                confidence=max(0.10, conf),
                severity=sev,
                uncertainty=0.18 if result.consistency_reliable else 0.30,
                evidence={
                    "prnu_internal_consistency": round(result.internal_consistency_score, 4),
                    "prnu_temporal_mean": round(result.temporal_mean, 4),
                    "prnu_temporal_drops": len(result.temporal_drops),
                    "prnu_spatial_score": round(result.spatial_consistency_score, 4),
                    "frames_used": result.frames_used,
                    "reliable": result.consistency_reliable,
                },
                raw_values={
                    "temporal_scores_sample": [
                        round(s, 4) for s in result.temporal_scores[:10]
                    ],
                    "spatial_block_scores": [
                        round(s, 4) for s in result.spatial_block_scores[:8]
                    ],
                },
            )

            # Attach temporal location for drop findings
            if result.temporal_drops and "temporal drop" in finding_text.lower():
                f.temporal_location = TemporalLocation(
                    start_frame=0,
                    end_frame=0,
                    start_seconds=min(result.temporal_drops),
                    end_seconds=max(result.temporal_drops),
                )

            findings.append(f)

        return result.tampering_probability, findings

    def _process_noise_result(
        self,
        result: NoisePatternResult,
    ) -> Tuple[float, List[Finding]]:
        """Convert NoisePatternResult into Finding objects."""
        findings = []

        for finding_text in result.findings:
            text_lower = finding_text.lower()
            if "periodic" in text_lower or "gan" in text_lower:
                sev  = Severity.HIGH
                conf = result.frequency_periodicity_score
            elif "high" in text_lower or "significant" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.local_variance_consistency
            else:
                sev  = Severity.LOW
                conf = 0.30

            findings.append(self.make_finding(
                title="Noise pattern anomaly",
                description=finding_text,
                confidence=max(0.15, conf),
                severity=sev,
                uncertainty=0.20,
                evidence={
                    "local_variance_consistency": round(result.local_variance_consistency, 4),
                    "frequency_periodicity": round(result.frequency_periodicity_score, 4),
                    "regional_anomalies": result.regional_anomalies,
                },
            ))

        return result.tampering_probability, findings

    # ── Sub-Score Helpers ─────────────────────────────────────────────────────

    def _temporal_drop_score(self, result: PRNUResult) -> float:
        """Convert temporal drop count into a 0–1 score."""
        if result.frames_used == 0:
            return 0.0
        drop_rate = len(result.temporal_drops) / result.frames_used
        return min(1.0, drop_rate * 5.0)

    def _spatial_score(self, result: PRNUResult) -> float:
        """Convert spatial anomaly count into a 0–1 score."""
        total_blocks = len(result.spatial_block_scores)
        if total_blocks == 0:
            return 0.0
        anomaly_rate = len(result.spatial_anomaly_regions) / total_blocks
        return min(1.0, anomaly_rate * 4.0)

    # ── Summary ───────────────────────────────────────────────────────────────

    def _build_summary(
        self,
        sub_scores: dict,
        prnu: PRNUResult,
        noise: NoisePatternResult,
        findings: List[Finding],
    ) -> str:
        parts = [
            f"Noise analysis examined PRNU camera fingerprint consistency "
            f"across {prnu.frames_used} frames. "
        ]

        consistency = prnu.internal_consistency_score
        if prnu.consistency_reliable:
            if consistency < 0.02:
                parts.append(
                    f"PRNU internal consistency is very low (NCC={consistency:.4f}), "
                    f"indicating possible mixed-source content. "
                )
            else:
                parts.append(
                    f"PRNU internal consistency NCC={consistency:.4f}. "
                )
        else:
            parts.append(
                f"Insufficient frames ({prnu.frames_used}) for fully reliable "
                f"PRNU fingerprint — results are indicative. "
            )

        if prnu.temporal_drops:
            parts.append(
                f"{len(prnu.temporal_drops)} temporal PRNU drop(s) detected. "
            )

        if noise.frequency_periodicity_score > 0.35:
            parts.append(
                f"Noise frequency periodicity detected "
                f"(score={noise.frequency_periodicity_score:.3f}). "
            )

        high_findings = [f for f in findings
                         if f.severity in (Severity.HIGH, Severity.CRITICAL)]
        if not high_findings:
            parts.append("No high-severity noise anomalies detected.")

        return "".join(parts)
