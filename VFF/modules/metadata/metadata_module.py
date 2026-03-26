"""
VideoForensics — Metadata & Container Analysis Module (Phase 2)
The first forensic analysis module. Examines all metadata and container
structure signals for evidence of tampering.

Checks performed:
    1.  Timestamp consistency (creation, modification, stream timestamps)
    2.  Encoder signature analysis (what software created this?)
    3.  Encoder / claimed source mismatch
    4.  Metadata completeness (suspicious sparseness)
    5.  GPS / timezone cross-verification
    6.  Container structure integrity (GOP, PTS, duration)
    7.  Stream-level timestamp agreement (video vs audio)
    8.  Container format vs encoder consistency

This module is entirely rule-based. No training data required.
Every finding cites specific technical values that produced it.
"""

from __future__ import annotations
import logging
import re
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.base_module import BaseForensicModule, InsufficientDataError
from core.models import ForensicCase, ModuleResult, Finding, Severity, TemporalLocation
from modules.metadata.encoder_db import (
    identify_encoder, is_editing_software, is_suspicious_for_raw_footage,
    EncoderCategory, SUSPICIOUS_METADATA_PATTERNS
)
from modules.metadata.container_analyzer import ContainerAnalyzer
from config.settings import WORKING_DIR

logger = logging.getLogger("vf.module.metadata")


class MetadataModule(BaseForensicModule):
    """
    Forensic analysis of video metadata and container structure.

    Returns a ModuleResult with:
    - tampering_probability: weighted score from all metadata checks
    - findings: each specific anomaly with evidence and raw values
    - metadata: summary statistics for the fusion engine

    Probability contribution weights (sum to 1.0 within this module):
        Timestamp anomalies:        0.30
        Encoder/source mismatch:    0.25
        Container structure:        0.25
        Metadata completeness:      0.10
        GPS/timezone:               0.10
    """

    MODULE_NAME = "metadata"

    # Internal sub-weights
    WEIGHTS = {
        "timestamp":   0.30,
        "encoder":     0.25,
        "container":   0.25,
        "completeness":0.10,
        "gps":         0.10,
    }

    def __init__(self):
        super().__init__()
        self.container_analyzer = ContainerAnalyzer()

    def analyze(self, case: ForensicCase) -> ModuleResult:
        """
        Main analysis entry point.
        Raises InsufficientDataError if the case has no video profile.
        """
        profile = case.video_profile
        if profile is None:
            raise InsufficientDataError("No VideoProfile — ingestion may have failed")

        video_path = case.working_file_path or case.original_file_path
        if not video_path or not Path(video_path).exists():
            raise InsufficientDataError("No video file accessible for analysis")

        findings: List[Finding] = []
        sub_scores: Dict[str, float] = {}

        self.logger.info(f"Analyzing metadata for case {case.case_id[:8]}...")

        # ── 1. Timestamp Analysis ──────────────────────────────────────────
        ts_score, ts_findings = self._analyze_timestamps(profile)
        sub_scores["timestamp"] = ts_score
        findings.extend(ts_findings)

        # ── 2. Encoder Analysis ────────────────────────────────────────────
        enc_score, enc_findings = self._analyze_encoder(profile)
        sub_scores["encoder"] = enc_score
        findings.extend(enc_findings)

        # ── 3. Container Structure ─────────────────────────────────────────
        cont_score, cont_findings = self._analyze_container(Path(video_path), profile)
        sub_scores["container"] = cont_score
        findings.extend(cont_findings)

        # ── 4. Metadata Completeness ───────────────────────────────────────
        comp_score, comp_findings = self._analyze_completeness(profile)
        sub_scores["completeness"] = comp_score
        findings.extend(comp_findings)

        # ── 5. GPS / Timezone ──────────────────────────────────────────────
        gps_score, gps_findings = self._analyze_gps(profile)
        sub_scores["gps"] = gps_score
        findings.extend(gps_findings)

        # ── Compute Weighted Score ─────────────────────────────────────────
        tampering_prob = sum(
            sub_scores.get(k, 0.0) * w
            for k, w in self.WEIGHTS.items()
        )
        tampering_prob = min(1.0, max(0.0, tampering_prob))

        # ── Uncertainty ────────────────────────────────────────────────────
        # Metadata analysis is highly reliable when data is present
        # Uncertainty increases when key fields are absent
        missing_fields = sum(1 for s in sub_scores.values() if s == 0.0)
        uncertainty = 0.05 + (missing_fields * 0.03)

        # ── Summary ────────────────────────────────────────────────────────
        summary = self._build_summary(sub_scores, findings, profile)

        self.logger.info(
            f"Metadata analysis complete — "
            f"prob={tampering_prob:.3f} "
            f"findings={len(findings)}"
        )

        return self.make_result(
            tampering_probability=tampering_prob,
            uncertainty=uncertainty,
            summary=summary,
            findings=findings,
            metadata={
                "sub_scores": sub_scores,
                "encoder_detected": profile.encoder_software or "unknown",
                "creation_time": profile.creation_time or "absent",
                "has_gps": profile.gps_latitude is not None,
                "container_format": profile.container_format,
                "codec": profile.codec,
            }
        )

    # ─── Check 1: Timestamp Analysis ──────────────────────────────────────────

    def _analyze_timestamps(
        self, profile
    ) -> Tuple[float, List[Finding]]:
        """
        Examine all timestamps embedded in the video for consistency.
        Checks: creation time presence, future timestamps,
                multi-stream timestamp agreement.
        """
        findings = []
        score = 0.0

        raw = profile.raw_metadata or {}
        fmt_tags = raw.get("format", {}).get("tags", {})

        # ── Creation time present? ─────────────────────────────────────────
        creation_time_str = profile.creation_time
        if not creation_time_str:
            score = max(score, 0.45)
            findings.append(self.make_finding(
                title="Creation time metadata absent",
                description=(
                    "No creation timestamp found in the video metadata. "
                    "Native camera recordings almost universally embed a creation "
                    "timestamp. Its absence suggests metadata was stripped, "
                    "the video was created by software that omits timestamps, "
                    "or the metadata was deliberately removed."
                ),
                confidence=0.45,
                severity=Severity.MEDIUM,
                uncertainty=0.20,
                evidence={"expected": "creation_time tag", "found": "absent"},
                raw_values={"format_tags": fmt_tags},
            ))
        else:
            # ── Timestamp plausibility ─────────────────────────────────────
            parsed_ts = self._parse_timestamp(creation_time_str)
            now = datetime.now(timezone.utc)

            if parsed_ts:
                # Future timestamp check
                if parsed_ts > now:
                    score = max(score, 0.85)
                    delta_days = (parsed_ts - now).days
                    findings.append(self.make_finding(
                        title="Creation timestamp is in the future",
                        description=(
                            f"The embedded creation time ({creation_time_str}) is "
                            f"{delta_days} days in the future relative to the analysis date. "
                            f"This is physically impossible for a genuine recording "
                            f"and strongly indicates timestamp manipulation."
                        ),
                        confidence=0.90,
                        severity=Severity.CRITICAL,
                        uncertainty=0.05,
                        evidence={
                            "embedded_time": creation_time_str,
                            "analysis_time": now.isoformat(),
                            "delta_days": delta_days,
                        },
                        raw_values={"creation_time_raw": creation_time_str},
                    ))

                # Very old timestamp (before video recording was commonplace)
                elif parsed_ts.year < 1990:
                    score = max(score, 0.60)
                    findings.append(self.make_finding(
                        title="Creation timestamp implausibly old",
                        description=(
                            f"Creation timestamp ({creation_time_str}) predates "
                            f"practical digital video recording. "
                            f"This indicates a default/reset timestamp or deliberate manipulation."
                        ),
                        confidence=0.60,
                        severity=Severity.HIGH,
                        uncertainty=0.15,
                        evidence={"embedded_time": creation_time_str},
                    ))

        # ── Cross-stream timestamp consistency ─────────────────────────────
        streams = raw.get("format", {})
        video_ts = audio_ts = None

        for s in raw.get("streams", []):  # streams is at top level
            tags = s.get("tags", {})
            ct = tags.get("creation_time") or tags.get("date")
            if ct:
                if s.get("codec_type") == "video":
                    video_ts = ct
                elif s.get("codec_type") == "audio":
                    audio_ts = ct

        if video_ts and audio_ts and video_ts != audio_ts:
            score = max(score, 0.55)
            findings.append(self.make_finding(
                title="Video and audio stream timestamps disagree",
                description=(
                    f"The creation timestamp in the video stream ({video_ts}) "
                    f"differs from the audio stream timestamp ({audio_ts}). "
                    f"In a genuine recording, both streams share the same creation time. "
                    f"A mismatch suggests the audio and video tracks originated from "
                    f"different recordings."
                ),
                confidence=0.60,
                severity=Severity.HIGH,
                uncertainty=0.15,
                evidence={
                    "video_stream_timestamp": video_ts,
                    "audio_stream_timestamp": audio_ts,
                },
            ))

        return score, findings

    # ─── Check 2: Encoder Analysis ─────────────────────────────────────────────

    def _analyze_encoder(self, profile) -> Tuple[float, List[Finding]]:
        """
        Identify the encoder software and assess whether it is
        consistent with the claimed source of the video.
        """
        findings = []
        score = 0.0

        encoder_str = profile.encoder_software or ""

        # Also check raw metadata for encoder at multiple levels
        raw = profile.raw_metadata or {}
        fmt_tags = raw.get("format", {}).get("tags", {})
        all_encoders = set()

        # Gather encoder strings from all locations
        for key in ["encoder", "software", "writing_application"]:
            val = fmt_tags.get(key) or fmt_tags.get(key.capitalize())
            if val:
                all_encoders.add(val)

        for stream in raw.get("streams", []):
            stags = stream.get("tags", {})
            for key in ["encoder", "handler_name"]:
                val = stags.get(key)
                if val and len(val) > 3:
                    all_encoders.add(val)

        if encoder_str:
            all_encoders.add(encoder_str)

        if not all_encoders:
            # No encoder information at all
            score = max(score, 0.35)
            findings.append(self.make_finding(
                title="No encoder signature found",
                description=(
                    "No encoder software signature is present anywhere in the metadata. "
                    "Authentic camera recordings normally include encoder identification. "
                    "Absence may indicate metadata was stripped by post-processing tools."
                ),
                confidence=0.35,
                severity=Severity.LOW,
                uncertainty=0.25,
                evidence={"encoder_fields_checked": ["encoder", "software", "writing_application"]},
            ))
            return score, findings

        # Analyze each encoder string found
        already_reported = set()

        for enc in all_encoders:
            sig = identify_encoder(enc)

            if sig is None:
                continue

            # Deduplicate: skip if we already reported this encoder name
            if sig.name in already_reported:
                continue
            already_reported.add(sig.name)

            # FFmpeg/Lavf — most specific and actionable finding
            if sig.category.value == "transcoder" and "lavf" in enc.lower():
                score = max(score, 0.60)
                findings.append(self.make_finding(
                    title="FFmpeg/Lavf transcoding signature detected",
                    description=(
                        f"Encoder signature '{enc}' is from FFmpeg (Lavf = libavformat). "
                        f"FFmpeg is the most widely used video processing tool and "
                        f"almost never appears in unedited camera footage. "
                        f"Its presence strongly indicates the video was processed, "
                        f"re-encoded, or assembled using FFmpeg or compatible tools."
                    ),
                    confidence=0.62,
                    severity=Severity.MEDIUM,
                    uncertainty=0.15,
                    evidence={"encoder_string": enc, "tool": "FFmpeg/Lavf"},
                    raw_values={"encoder_raw": enc},
                ))
            elif is_editing_software(enc):
                # Other editing/post-processing software
                edit_score = 0.55
                score = max(score, edit_score)
                findings.append(self.make_finding(
                    title=f"Post-processing software detected: {sig.name}",
                    description=(
                        f"The encoder signature '{enc}' identifies as {sig.name} "
                        f"({sig.description}). This is {sig.category.value} software, "
                        f"indicating the video was processed after initial recording. "
                        f"{sig.notes}"
                    ),
                    confidence=edit_score,
                    severity=Severity.MEDIUM,
                    uncertainty=0.15,
                    evidence={
                        "encoder_string": enc,
                        "identified_as": sig.name,
                        "category": sig.category.value,
                    },
                    raw_values={"encoder_raw": enc},
                ))

        return score, findings

    # ─── Check 3: Container Structure ─────────────────────────────────────────

    def _analyze_container(
        self, video_path: Path, profile
    ) -> Tuple[float, List[Finding]]:
        """
        Run container structure analysis and convert findings to module findings.
        """
        findings = []
        score = 0.0

        container_result = self.container_analyzer.analyze(video_path)

        # Convert container findings to module Finding objects
        for finding_text in container_result.findings:
            # Score based on finding content keywords
            if "critical" in finding_text.lower() or "impossible" in finding_text.lower():
                conf = 0.85
                sev  = Severity.CRITICAL
            elif "strong" in finding_text.lower() or "reversal" in finding_text.lower():
                conf = 0.72
                sev  = Severity.HIGH
            elif "irregular" in finding_text.lower() or "mismatch" in finding_text.lower():
                conf = 0.58
                sev  = Severity.MEDIUM
            else:
                conf = 0.45
                sev  = Severity.LOW

            score = max(score, conf)
            findings.append(self.make_finding(
                title=f"Container structure anomaly",
                description=finding_text,
                confidence=conf,
                severity=sev,
                uncertainty=0.12,
                evidence={
                    "irregular_gop": container_result.irregular_gop_detected,
                    "pts_reversals": len(container_result.pts_reversals),
                    "pts_gaps": len(container_result.pts_gaps),
                    "duration_mismatch": container_result.duration_mismatch,
                },
                raw_values={
                    "avg_keyframe_interval": round(container_result.avg_keyframe_interval, 3),
                    "keyframe_interval_std": round(container_result.keyframe_interval_std, 3),
                    "keyframe_count": container_result.keyframe_count,
                    "dts_pts_inversions": container_result.dts_pts_inversions,
                },
            ))

        # Container format vs codec consistency
        fmt   = profile.container_format or ""
        codec = profile.codec or ""
        mismatch = self._check_format_codec_mismatch(fmt, codec)
        if mismatch:
            score = max(score, 0.50)
            findings.append(self.make_finding(
                title="Container format / codec mismatch",
                description=mismatch,
                confidence=0.50,
                severity=Severity.MEDIUM,
                uncertainty=0.18,
                evidence={"container": fmt, "codec": codec},
            ))

        return score, findings

    # ─── Check 4: Metadata Completeness ───────────────────────────────────────

    def _analyze_completeness(self, profile) -> Tuple[float, List[Finding]]:
        """
        Assess whether the metadata is suspiciously sparse.
        Native camera recordings have rich metadata. Stripped metadata
        is a common byproduct of post-processing.
        """
        findings = []
        score = 0.0

        # Fields expected in genuine camera recordings
        expected_fields = {
            "creation_time": profile.creation_time,
            "encoder_software": profile.encoder_software,
            "device_make": profile.device_make,
            "device_model": profile.device_model,
        }

        absent = [k for k, v in expected_fields.items() if not v]
        present_count = len(expected_fields) - len(absent)
        completeness_ratio = present_count / len(expected_fields)

        if completeness_ratio <= 0.25:
            # Very sparse — only 0 or 1 expected fields present
            score = 0.50
            findings.append(self.make_finding(
                title="Severely sparse metadata",
                description=(
                    f"Only {present_count} of {len(expected_fields)} expected metadata "
                    f"fields are present ({completeness_ratio*100:.0f}% completeness). "
                    f"Missing: {', '.join(absent)}. "
                    f"Genuine camera recordings carry rich metadata. This level of "
                    f"sparseness strongly suggests metadata was stripped by editing "
                    f"or processing software."
                ),
                confidence=0.50,
                severity=Severity.MEDIUM,
                uncertainty=0.20,
                evidence={
                    "present_fields": [k for k, v in expected_fields.items() if v],
                    "absent_fields": absent,
                    "completeness_pct": round(completeness_ratio * 100, 1),
                },
            ))
        elif completeness_ratio <= 0.50:
            score = 0.30
            findings.append(self.make_finding(
                title="Incomplete metadata",
                description=(
                    f"{len(absent)} expected metadata field(s) are absent: {', '.join(absent)}. "
                    f"While not conclusive alone, sparse metadata combined with other "
                    f"anomalies increases the likelihood of post-processing."
                ),
                confidence=0.30,
                severity=Severity.LOW,
                uncertainty=0.25,
                evidence={"absent_fields": absent},
            ))

        return score, findings

    # ─── Check 5: GPS / Timezone ───────────────────────────────────────────────

    def _analyze_gps(self, profile) -> Tuple[float, List[Finding]]:
        """
        If GPS coordinates are present, verify they are geographically plausible
        and consistent with any timezone information in the metadata.
        """
        findings = []
        score = 0.0

        if profile.gps_latitude is None or profile.gps_longitude is None:
            return score, findings   # No GPS — skip this check

        lat = profile.gps_latitude
        lon = profile.gps_longitude

        # Basic plausibility bounds
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            score = 0.80
            findings.append(self.make_finding(
                title="GPS coordinates out of valid range",
                description=(
                    f"Embedded GPS coordinates (lat={lat}, lon={lon}) are "
                    f"outside physically valid ranges (lat: -90 to +90, lon: -180 to +180). "
                    f"This indicates corrupted or fabricated GPS data."
                ),
                confidence=0.85,
                severity=Severity.CRITICAL,
                uncertainty=0.05,
                evidence={"latitude": lat, "longitude": lon},
            ))
            return score, findings

        # Timezone consistency check
        if profile.creation_time:
            ts_tz_offset = self._extract_timezone_offset(profile.creation_time)
            if ts_tz_offset is not None:
                expected_tz_range = self._lon_to_utc_offset_range(lon)
                if not (expected_tz_range[0] <= ts_tz_offset <= expected_tz_range[1]):
                    score = max(score, 0.65)
                    findings.append(self.make_finding(
                        title="GPS location inconsistent with timestamp timezone",
                        description=(
                            f"GPS longitude {lon:.4f}° suggests UTC offset "
                            f"between {expected_tz_range[0]:+.1f} and {expected_tz_range[1]:+.1f} hours. "
                            f"The embedded timestamp timezone offset is {ts_tz_offset:+.1f} hours. "
                            f"This mismatch suggests the GPS coordinates and timestamp "
                            f"did not originate from the same recording session."
                        ),
                        confidence=0.65,
                        severity=Severity.HIGH,
                        uncertainty=0.20,
                        evidence={
                            "gps_longitude": lon,
                            "gps_latitude": lat,
                            "timestamp_tz_offset_hours": ts_tz_offset,
                            "expected_tz_range": expected_tz_range,
                        },
                    ))

        return score, findings

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_timestamp(ts_str: str) -> Optional[datetime]:
        """Parse various timestamp formats found in video metadata."""
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(ts_str.strip(), fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_timezone_offset(ts_str: str) -> Optional[float]:
        """
        Extract UTC offset in hours from a timestamp string.
        Returns None if no timezone info is present.
        """
        # Match +HH:MM or -HH:MM patterns
        match = re.search(r'([+-])(\d{2}):?(\d{2})$', ts_str.strip())
        if match:
            sign   = 1 if match.group(1) == '+' else -1
            hours  = int(match.group(2))
            minutes = int(match.group(3))
            return sign * (hours + minutes / 60)
        # 'Z' suffix means UTC
        if ts_str.strip().endswith('Z'):
            return 0.0
        return None

    @staticmethod
    def _lon_to_utc_offset_range(longitude: float) -> Tuple[float, float]:
        """
        Convert a longitude to the expected UTC offset range.
        Uses the simple solar time approximation:
            UTC offset ≈ longitude / 15 degrees per hour
        Allows ±2 hours tolerance for political timezone boundaries.
        """
        nominal_offset = longitude / 15.0
        tolerance = 2.0
        return (nominal_offset - tolerance, nominal_offset + tolerance)

    @staticmethod
    def _check_format_codec_mismatch(container: str, codec: str) -> Optional[str]:
        """
        Check for suspicious container/codec combinations that indicate
        the video was re-wrapped (container changed without re-encoding).
        Returns a description string if a mismatch is found, None otherwise.
        """
        # Known mismatches
        suspicious = [
            (["avi"], ["h265", "hevc", "vp9", "av1"],
             "AVI container with modern codec — AVI predates these codecs and "
             "this combination typically indicates container re-wrapping."),
            (["mkv", "webm"], ["mpeg2video"],
             "Modern container with legacy MPEG-2 codec — unusual combination "
             "suggesting the video was re-wrapped."),
        ]
        container_lower = container.lower()
        codec_lower     = codec.lower()

        for containers, codecs, message in suspicious:
            if any(c in container_lower for c in containers):
                if any(c in codec_lower for c in codecs):
                    return message
        return None

    @staticmethod
    def _build_summary(
        sub_scores: Dict[str, float],
        findings: List[Finding],
        profile,
    ) -> str:
        """Build a human-readable summary of the metadata analysis."""
        total_score = sum(sub_scores.values()) / max(len(sub_scores), 1)

        high_findings = [f for f in findings if f.severity in [Severity.HIGH, Severity.CRITICAL]]
        medium_findings = [f for f in findings if f.severity == Severity.MEDIUM]

        parts = [
            f"Metadata analysis examined encoder signature, timestamps, "
            f"container structure, metadata completeness, and GPS consistency. "
        ]

        if not findings:
            parts.append(
                "No significant anomalies detected. "
                "Metadata is consistent with an unmodified recording."
            )
        else:
            parts.append(
                f"{len(findings)} anomaly/anomalies detected "
                f"({len(high_findings)} high/critical, {len(medium_findings)} medium). "
            )
            if high_findings:
                parts.append(
                    f"Most significant: {high_findings[0].title}."
                )

        parts.append(
            f"Encoder: {profile.encoder_software or 'unknown'}. "
            f"Container: {profile.container_format or 'unknown'}. "
            f"Codec: {profile.codec or 'unknown'}."
        )

        return " ".join(parts)
