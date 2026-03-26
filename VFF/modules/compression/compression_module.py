"""
VideoForensics — Compression & Encoding Analysis Module (Phase 3)

Detects compression-based evidence of video tampering through:
    1. DCT coefficient analysis — double compression detection
    2. Bitrate profile analysis — anomalous frame size patterns
    3. Frame type distribution — unexpected I-frames and GOP irregularities
    4. Quantization parameter consistency — regional encoding differences

All checks are signal-processing based. No training data required.
Every finding cites the specific statistical values that produced it.

Forensic relevance:
    - Virtually all video editing involves at least one decode/re-encode cycle
    - Double compression leaves permanent statistical traces in DCT coefficients
    - Spliced content from different encoders shows bitrate discontinuities
    - Unusual I-frame positions mark editing boundaries
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
    TemporalLocation
)
from core.preprocessing.frame_extractor import FrameExtractor
from modules.compression.dct_analyzer import DCTAnalyzer, DCTAnalysisResult
from modules.compression.bitrate_analyzer import BitrateAnalyzer, BitrateAnalysisResult
from config.settings import WORKING_DIR, MIN_FRAMES_FOR_ANALYSIS

logger = logging.getLogger("vf.module.compression")


class CompressionModule(BaseForensicModule):
    """
    Forensic analysis of video compression characteristics.

    Sub-scores weighted to final tampering probability:
        DCT double compression:  0.40
        Bitrate anomalies:       0.35
        GOP/frame type:          0.25
    """

    MODULE_NAME = "compression"

    WEIGHTS = {
        "dct":     0.40,
        "bitrate": 0.35,
        "gop":     0.25,
    }

    def __init__(self):
        super().__init__()
        self.dct_analyzer     = DCTAnalyzer()
        self.bitrate_analyzer = BitrateAnalyzer()

    def analyze(self, case: ForensicCase) -> ModuleResult:
        """
        Main analysis entry point.
        Runs DCT analysis and bitrate analysis in sequence,
        then fuses their scores into a final result.
        """
        profile = case.video_profile
        if profile is None:
            raise InsufficientDataError("No VideoProfile available")

        if not profile.has_sufficient_frames:
            raise InsufficientDataError(
                f"Insufficient frames for compression analysis "
                f"({profile.total_frames} frames, need {MIN_FRAMES_FOR_ANALYSIS})"
            )

        video_path = case.working_file_path or case.original_file_path
        if not video_path or not Path(video_path).exists():
            raise InsufficientDataError("No video file accessible")

        video_path = Path(video_path)
        findings: List[Finding] = []
        sub_scores = {}

        self.logger.info(f"Compression analysis — case {case.case_id[:8]}...")

        # ── 1. Bitrate & Frame Type Analysis ──────────────────────────────
        # Run first — uses ffprobe data, fast, no frame decoding needed
        bitrate_result = self.bitrate_analyzer.analyze(video_path)
        bscore, b_findings = self._process_bitrate_result(bitrate_result, profile)
        sub_scores["bitrate"] = bscore
        sub_scores["gop"]     = bitrate_result.irregular_gop_score
        findings.extend(b_findings)

        # ── 2. DCT Coefficient Analysis ────────────────────────────────────
        # Requires frame decoding — more expensive but most powerful signal
        dct_result  = None
        dct_reliable = True

        if profile.has_video and profile.total_frames >= MIN_FRAMES_FOR_ANALYSIS:
            frame_gen = self._frame_generator(video_path, profile)
            dct_result = self.dct_analyzer.analyze_frames(
                frame_gen,
                total_frames=profile.total_frames,
            )
            dscore, d_findings = self._process_dct_result(dct_result, profile)
            sub_scores["dct"] = dscore
            findings.extend(d_findings)
        else:
            dct_reliable = False
            sub_scores["dct"] = 0.0
            self.logger.warning("Skipping DCT analysis — insufficient frames")

        # ── 3. Weighted Score ──────────────────────────────────────────────
        tampering_prob = sum(
            sub_scores.get(k, 0.0) * w
            for k, w in self.WEIGHTS.items()
        )
        tampering_prob = min(1.0, max(0.0, tampering_prob))

        # Uncertainty: higher if DCT was skipped or few frames
        frame_coverage = min(1.0, profile.total_frames / 300)
        uncertainty = 0.08 + (0.15 if not dct_reliable else 0.0) + (0.05 * (1 - frame_coverage))

        # ── 4. Summary ────────────────────────────────────────────────────
        summary = self._build_summary(sub_scores, findings, bitrate_result, dct_result)

        self.logger.info(
            f"Compression analysis complete — "
            f"prob={tampering_prob:.3f} "
            f"findings={len(findings)}"
        )

        return self.make_result(
            tampering_probability=tampering_prob,
            uncertainty=round(uncertainty, 3),
            summary=summary,
            findings=findings,
            reliable=True,
            metadata={
                "sub_scores": sub_scores,
                "dct_double_compress_prob": dct_result.double_compression_probability if dct_result else None,
                "bitrate_anomaly_count": len(bitrate_result.anomaly_timestamps),
                "unexpected_i_frames": len(bitrate_result.unexpected_i_frames),
                "i_frame_count": bitrate_result.i_frame_count,
                "gop_sizes": bitrate_result.gop_sizes[:10] if bitrate_result.gop_sizes else [],
                "frames_dct_analyzed": dct_result.frames_analyzed if dct_result else 0,
                "blocks_dct_analyzed": dct_result.blocks_analyzed if dct_result else 0,
            }
        )

    # ── Result Processors ─────────────────────────────────────────────────────

    def _process_bitrate_result(
        self,
        result: BitrateAnalysisResult,
        profile,
    ) -> Tuple[float, List[Finding]]:
        """Convert BitrateAnalysisResult into Finding objects."""
        findings = []
        score = result.tampering_probability

        for i, (ts, desc) in enumerate(zip(
            result.anomaly_timestamps,
            result.anomaly_descriptions
        )):
            if i >= 5:   # Cap at 5 findings from this sub-check
                break
            severity = Severity.HIGH if score > 0.60 else Severity.MEDIUM
            findings.append(self.make_finding(
                title="Bitrate anomaly detected",
                description=desc,
                confidence=min(0.85, score + 0.10),
                severity=severity,
                uncertainty=0.15,
                evidence={"timestamp_seconds": ts, "anomaly_score": result.anomaly_score},
                raw_values={
                    "mean_frame_size": round(result.mean_frame_size, 1),
                    "std_frame_size": round(result.std_frame_size, 1),
                },
            ))

        if result.unexpected_i_frames:
            loc = TemporalLocation(
                start_frame=0, end_frame=profile.total_frames,
                start_seconds=min(result.unexpected_i_frames),
                end_seconds=max(result.unexpected_i_frames),
            )
            for finding_text in result.findings:
                if "I-frame" in finding_text:
                    f = self.make_finding(
                        title="Unexpected keyframes at non-GOP boundaries",
                        description=finding_text,
                        confidence=0.68,
                        severity=Severity.HIGH,
                        uncertainty=0.12,
                        evidence={
                            "unexpected_i_frame_positions": result.unexpected_i_frames[:10],
                            "mean_gop": round(float(np.mean(result.gop_sizes)), 1) if result.gop_sizes else 0,
                        },
                    )
                    f.temporal_location = loc
                    findings.append(f)

        if result.irregular_gop_score > 0.45:
            findings.append(self.make_finding(
                title="Irregular GOP structure",
                description=(
                    f"GOP (Group of Pictures) intervals show high variability "
                    f"(irregularity score={result.irregular_gop_score:.3f}). "
                    f"A consistent encoding produces regular I-frame spacing. "
                    f"Irregular spacing indicates the video was edited or assembled "
                    f"from segments encoded separately."
                ),
                confidence=result.irregular_gop_score * 0.85,
                severity=Severity.MEDIUM,
                uncertainty=0.18,
                evidence={
                    "gop_irregularity_score": round(result.irregular_gop_score, 3),
                    "gop_sizes_sample": result.gop_sizes[:8],
                },
            ))

        return score, findings

    def _process_dct_result(
        self,
        result: DCTAnalysisResult,
        profile,
    ) -> Tuple[float, List[Finding]]:
        """Convert DCTAnalysisResult into Finding objects."""
        findings = []
        score = result.double_compression_probability

        for finding_text in result.findings:
            # Determine severity from score
            if score > 0.70:
                sev  = Severity.HIGH
                conf = score
            elif score > 0.45:
                sev  = Severity.MEDIUM
                conf = score
            else:
                sev  = Severity.LOW
                conf = score

            findings.append(self.make_finding(
                title="DCT coefficient anomaly — double compression signature",
                description=finding_text,
                confidence=conf,
                severity=sev,
                uncertainty=0.18,
                evidence={
                    "double_compression_probability": round(score, 3),
                    "histogram_periodicity": round(result.histogram_periodicity, 3),
                    "spatial_inconsistency": round(result.spatial_inconsistency_score, 3),
                },
                raw_values={
                    "ac_coeff_mean": round(result.ac_coeff_mean, 3),
                    "ac_coeff_std": round(result.ac_coeff_std, 3),
                    "ac_coeff_kurtosis": round(result.ac_coeff_kurtosis, 3),
                    "frames_analyzed": result.frames_analyzed,
                    "blocks_analyzed": result.blocks_analyzed,
                },
            ))

        return score, findings

    # ── Frame Generator ────────────────────────────────────────────────────────

    def _frame_generator(
        self,
        video_path: Path,
        profile,
    ) -> Iterator[Tuple[int, np.ndarray]]:
        """
        Generate sampled frames for DCT analysis.
        Uses every Nth frame to cover the full video within budget.
        """
        target_frames = self.dct_analyzer.SAMPLE_FRAMES
        total = profile.total_frames or 1
        step  = max(1, total // target_frames)

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return

        frame_idx = 0
        yielded   = 0

        try:
            while yielded < target_frames:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_idx % step == 0:
                    yield frame_idx, frame
                    yielded += 1
                frame_idx += 1
        finally:
            cap.release()

    # ── Summary Builder ────────────────────────────────────────────────────────

    def _build_summary(
        self,
        sub_scores: dict,
        findings: List[Finding],
        bitrate_result: BitrateAnalysisResult,
        dct_result: Optional[DCTAnalysisResult],
    ) -> str:
        """Build a human-readable module summary."""
        parts = [
            "Compression analysis examined DCT coefficient distributions, "
            "frame-level bitrate profiles, and GOP structure. "
        ]

        if dct_result:
            prob = dct_result.double_compression_probability
            if prob > 0.65:
                parts.append(
                    f"Strong double compression signature detected "
                    f"(probability={prob:.2f}). "
                )
            elif prob > 0.35:
                parts.append(f"Moderate double compression indicators (probability={prob:.2f}). ")
            else:
                parts.append(f"No significant double compression detected. ")

        anom_count = len(bitrate_result.anomaly_timestamps)
        if anom_count > 0:
            parts.append(f"{anom_count} bitrate anomaly/anomalies flagged. ")
        else:
            parts.append("Bitrate profile is consistent throughout. ")

        if bitrate_result.unexpected_i_frames:
            parts.append(
                f"{len(bitrate_result.unexpected_i_frames)} unexpected keyframe(s) detected. "
            )

        if not findings:
            parts.append("No significant compression anomalies found.")

        return "".join(parts)
