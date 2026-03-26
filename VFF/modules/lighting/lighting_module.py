"""
VideoForensics — Lighting & Physics Consistency Module (Phase 6)

Detects lighting-based evidence of video tampering through:
    1. Illumination field consistency — temporal and spatial
    2. Section-level illumination shift — splice detection
    3. Light source direction stability — gradient orientation
    4. Colour temperature consistency — cross-frame and spatial
    5. Shadow boundary direction — physical plausibility
    6. Specular highlight consistency — reflection geometry

Limitations:
    - Synthetic/CGI content has physically consistent lighting by construction
      and may score low even though it's not "authentic camera footage"
    - Videos with legitimate lighting changes (indoor/outdoor, day/night)
      may score higher — this module is most reliable for single-environment footage
    - Best results with natural, real-world content

Sub-score weights:
    Illumination field:    0.40
    Shadow/highlight:      0.35
    Colour temperature:    0.25
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
from modules.lighting.illumination_analyzer import IlluminationAnalyzer, IlluminationResult
from modules.lighting.shadow_analyzer import ShadowHighlightAnalyzer, ShadowHighlightResult
from config.settings import WORKING_DIR

logger = logging.getLogger("vf.module.lighting")


class LightingModule(BaseForensicModule):
    """
    Forensic analysis of lighting and physics consistency.
    """

    MODULE_NAME = "lighting"

    WEIGHTS = {
        "illumination": 0.40,
        "shadow":       0.35,
        "colour_temp":  0.25,
    }

    MAX_FRAMES = 80
    MIN_FRAMES = 8

    def __init__(self):
        super().__init__()
        self.illum_analyzer  = IlluminationAnalyzer()
        self.shadow_analyzer = ShadowHighlightAnalyzer()

    def analyze(self, case: ForensicCase) -> ModuleResult:
        profile = case.video_profile
        if profile is None:
            raise InsufficientDataError("No VideoProfile available")

        if profile.total_frames < self.MIN_FRAMES:
            raise InsufficientDataError(
                f"Too few frames for lighting analysis ({profile.total_frames})"
            )

        video_path = case.working_file_path or case.original_file_path
        if not video_path or not Path(video_path).exists():
            raise InsufficientDataError("No video file accessible")

        video_path = Path(video_path)
        findings: List[Finding] = []
        sub_scores = {}

        self.logger.info(f"Lighting analysis — case {case.case_id[:8]}...")

        # ── Load frames ───────────────────────────────────────────────────
        frames, timestamps = self._load_frames(video_path, profile)

        if len(frames) < self.MIN_FRAMES:
            raise InsufficientDataError(f"Could only decode {len(frames)} frames")

        self.logger.info(f"Loaded {len(frames)} frames for lighting analysis")

        # ── 1. Illumination field ─────────────────────────────────────────
        illum_result = self.illum_analyzer.analyze(frames, timestamps)
        i_score, i_findings = self._process_illum_result(illum_result, profile)
        sub_scores["illumination"] = illum_result.tampering_probability
        sub_scores["colour_temp"]  = illum_result.colour_temp_consistency
        findings.extend(i_findings)

        # ── 2. Shadow & highlight ─────────────────────────────────────────
        shadow_result = self.shadow_analyzer.analyze(frames, timestamps)
        s_score, s_findings = self._process_shadow_result(shadow_result)
        sub_scores["shadow"] = shadow_result.tampering_probability
        findings.extend(s_findings)

        # ── Weighted score ────────────────────────────────────────────────
        # Use illumination and shadow weights; colour_temp is embedded in illumination
        tampering_prob = (
            self.WEIGHTS["illumination"] * illum_result.tampering_probability +
            self.WEIGHTS["shadow"]       * shadow_result.tampering_probability +
            self.WEIGHTS["colour_temp"]  * illum_result.colour_temp_consistency
        )
        tampering_prob = min(1.0, max(0.0, tampering_prob))

        # Reliability note — lighting analysis is content-dependent
        reliability_note = ""
        if profile.codec in ("hevc", "h265", "av1"):
            reliability_note = (
                "High-efficiency codecs apply strong smoothing that can "
                "affect illumination field estimates."
            )

        uncertainty = 0.18  # Lighting analysis has inherently higher uncertainty

        summary = self._build_summary(sub_scores, illum_result, shadow_result, findings)

        self.logger.info(
            f"Lighting analysis complete — "
            f"prob={tampering_prob:.3f} findings={len(findings)}"
        )

        return self.make_result(
            tampering_probability=tampering_prob,
            uncertainty=uncertainty,
            summary=summary,
            findings=findings,
            reliable=True,
            reliability_note=reliability_note,
            metadata={
                "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
                "frames_analyzed": len(frames),
                "temporal_cv": round(illum_result.temporal_cv, 4),
                "illum_abruptness_ratio": round(illum_result.illum_abruptness_ratio, 4),
                "illum_abruptness_score": round(illum_result.illum_abruptness_score, 4),
                "illum_temporal_drops": len(illum_result.illum_spike_timestamps),
                "gradient_direction_consistency": round(illum_result.gradient_direction_consistency, 4),
                "direction_variance": round(illum_result.direction_variance, 4),
                "colour_temp_consistency": round(illum_result.colour_temp_consistency, 4),
                "shadow_direction_consistency": round(shadow_result.shadow_direction_consistency, 4),
                "shadow_coverage_cv": round(shadow_result.shadow_coverage_cv, 4),
                "ambient_diffuse_consistency": round(shadow_result.ambient_diffuse_consistency, 4),
                "highlight_score": round(shadow_result.highlight_distribution_score, 4),
            }
        )

    # ── Frame Loading ─────────────────────────────────────────────────────────

    def _load_frames(
        self, video_path: Path, profile
    ) -> Tuple[List[np.ndarray], List[float]]:
        total = profile.total_frames or 1
        step  = max(1, total // self.MAX_FRAMES)
        fps   = profile.frame_rate or 30.0

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

    # ── Result Processors ─────────────────────────────────────────────────────

    def _process_illum_result(
        self, result: IlluminationResult, profile
    ) -> Tuple[float, List[Finding]]:
        findings = []

        for text in result.findings:
            text_lower = text.lower()
            if "significant" in text_lower or "shift" in text_lower:
                sev  = Severity.HIGH
                conf = min(0.80, result.tampering_probability + 0.10)
            elif "abrupt" in text_lower or "inconsistent" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.tampering_probability
            else:
                sev  = Severity.LOW
                conf = 0.30

            f = self.make_finding(
                title="Illumination field anomaly",
                description=text,
                confidence=max(0.10, conf),
                severity=sev,
                uncertainty=0.20,
                evidence={
                    "illum_abruptness_ratio": round(result.illum_abruptness_ratio, 4),
                    "illum_abruptness_score": round(result.illum_abruptness_score, 4),
                    "direction_variance": round(result.direction_variance, 4),
                    "colour_temp_consistency": round(result.colour_temp_consistency, 4),
                },
            )

            if result.illum_spike_timestamps:
                f.temporal_location = TemporalLocation(
                    start_frame=0,
                    end_frame=profile.total_frames,
                    start_seconds=min(result.illum_spike_timestamps),
                    end_seconds=max(result.illum_spike_timestamps),
                )

            findings.append(f)

        return result.tampering_probability, findings

    def _process_shadow_result(
        self, result: ShadowHighlightResult
    ) -> Tuple[float, List[Finding]]:
        findings = []

        for text in result.findings:
            text_lower = text.lower()
            if "shadow" in text_lower and "inconsistent" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.shadow_direction_consistency
            elif "ambient" in text_lower or "diffuse" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.ambient_diffuse_consistency
            else:
                sev  = Severity.LOW
                conf = 0.30

            findings.append(self.make_finding(
                title="Shadow/highlight physics anomaly",
                description=text,
                confidence=max(0.10, conf),
                severity=sev,
                uncertainty=0.22,
                evidence={
                    "shadow_direction_consistency": round(result.shadow_direction_consistency, 4),
                    "shadow_coverage_cv": round(result.shadow_coverage_cv, 4),
                    "ambient_diffuse_consistency": round(result.ambient_diffuse_consistency, 4),
                    "highlight_score": round(result.highlight_distribution_score, 4),
                },
            ))

        return result.tampering_probability, findings

    # ── Summary ───────────────────────────────────────────────────────────────

    def _build_summary(
        self,
        sub_scores: dict,
        illum: IlluminationResult,
        shadow: ShadowHighlightResult,
        findings: List[Finding],
    ) -> str:
        parts = ["Lighting analysis examined illumination field consistency, "
                 "light source direction, colour temperature, and shadow physics. "]

        if illum.illum_abruptness_score > 0.30:
            parts.append(
                f"Abrupt illumination change detected "
                f"(ratio={illum.illum_abruptness_ratio:.1f}). "
            )
        else:
            parts.append("Illumination changes are gradual and consistent. ")

        if illum.illum_spike_timestamps:
            parts.append(f"{len(illum.illum_spike_timestamps)} illumination spike(s) flagged. ")

        if illum.gradient_direction_consistency > 0.50:
            parts.append("Light source direction inconsistency detected. ")

        if not findings:
            parts.append("No significant lighting anomalies found.")

        return "".join(parts)
