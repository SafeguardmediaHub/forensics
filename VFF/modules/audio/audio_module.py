"""
VideoForensics — Audio Forensics Module (Phase 7)

Detects tampering through audio signal analysis:
    1. Spectral consistency — centroid abruptness, RMS energy consistency,
       spectral profile shift between first and second halves of audio
    2. Onset flux — audio event pattern analysis
    3. Audio-video synchronization — duration and PTS offset checks
    4. ENF (Electric Network Frequency) — mains hum phase continuity

Audio forensics is a distinct and independent domain from video analysis.
It catches tampering that leaves no visual trace — for example, when the
video frames are authentic but the audio track was replaced or re-spliced
at a different edit point than the video.

Conversely, it provides corroborating evidence when splice boundaries
detected by PRNU (Phase 4) and Temporal (Phase 5) modules align with
the audio splice timestamps found here.

Sub-score weights:
    Spectral:  0.60  (richest signal — multiple sub-measures)
    Sync:      0.40  (AV drift and duration mismatch)

Limitations:
    - Synthetic audio (tones, generated music) does not have the
      natural spectral variability of microphone recordings. Scores
      on test videos with synthetic audio should be interpreted with
      caution until the system is calibrated on real-world data.
    - ENF analysis is only meaningful when ambient electrical hum
      is present in the recording; it will not fire on recordings
      made in electrically quiet environments.
"""

from __future__ import annotations
import logging
import subprocess
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from core.base_module import BaseForensicModule, InsufficientDataError
from core.models import (
    ForensicCase, ModuleResult, Finding, Severity, TemporalLocation
)
from modules.audio.spectral_analyzer import AudioSpectralAnalyzer, SpectralResult
from modules.audio.sync_analyzer import AVSyncAnalyzer, AVSyncResult

logger = logging.getLogger("vf.module.audio")


class AudioModule(BaseForensicModule):
    """
    Forensic analysis of audio integrity and audio-video synchronization.
    """

    MODULE_NAME = "audio"

    WEIGHTS = {
        "spectral": 0.60,
        "sync":     0.40,
    }

    # Minimum audio duration for meaningful analysis
    MIN_DURATION_S = 2.0

    def __init__(self):
        super().__init__()
        self.spectral_analyzer = AudioSpectralAnalyzer()
        self.sync_analyzer     = AVSyncAnalyzer()

    def analyze(self, case: ForensicCase) -> ModuleResult:
        profile = case.video_profile
        if profile is None:
            raise InsufficientDataError("No VideoProfile available")

        video_path = case.working_file_path or case.original_file_path
        if not video_path or not Path(video_path).exists():
            raise InsufficientDataError("No video file accessible")

        video_path = Path(video_path)
        findings: List[Finding] = []
        sub_scores = {}

        self.logger.info(f"Audio analysis — case {case.case_id[:8]}...")

        # ── Extract audio ─────────────────────────────────────────────────
        samples, sr = self._extract_audio(video_path)

        has_audio = samples is not None and len(samples) > 0
        if has_audio:
            duration = len(samples) / sr
            self.logger.info(f"Audio extracted: {duration:.2f}s @ {sr}Hz")
        else:
            self.logger.warning("No audio stream found or extraction failed")

        # ── 1. Spectral analysis ──────────────────────────────────────────
        if has_audio and (len(samples) / sr) >= self.MIN_DURATION_S:
            spectral_result = self.spectral_analyzer.analyze(samples, sr)
            sub_scores["spectral"] = spectral_result.tampering_probability
            findings.extend(
                self._process_spectral(spectral_result, profile)
            )
        else:
            spectral_result = None
            sub_scores["spectral"] = 0.0
            if not has_audio:
                findings.append(self.make_finding(
                    title="No audio stream detected",
                    description=(
                        "No audio stream was found in the video container. "
                        "Genuine camera recordings almost always contain audio. "
                        "A missing audio track may indicate the original audio was "
                        "stripped and video-only content was re-packaged, or that "
                        "this is a screen recording or generated video."
                    ),
                    confidence=0.25,
                    severity=Severity.LOW,
                    uncertainty=0.30,
                    evidence={"has_audio": False},
                ))

        # ── 2. AV sync analysis ───────────────────────────────────────────
        sync_result = self.sync_analyzer.analyze(
            video_path,
            samples=samples if has_audio else None,
            sr=sr if has_audio else None,
        )
        sub_scores["sync"] = sync_result.tampering_probability
        findings.extend(self._process_sync(sync_result))

        # ── Weighted score ────────────────────────────────────────────────
        tampering_prob = sum(
            sub_scores.get(k, 0.0) * w
            for k, w in self.WEIGHTS.items()
        )
        tampering_prob = min(1.0, max(0.0, tampering_prob))

        # Reliability note
        reliability_note = ""
        if not has_audio:
            reliability_note = "No audio stream — spectral analysis skipped."
        elif spectral_result and spectral_result.centroid_mean < 200:
            reliability_note = (
                "Very low spectral centroid suggests synthetic or tonal audio. "
                "Spectral consistency thresholds may not be fully calibrated "
                "for this content type."
            )

        uncertainty = 0.15
        if not has_audio:
            uncertainty = 0.35

        summary = self._build_summary(sub_scores, spectral_result, sync_result, findings)

        self.logger.info(
            f"Audio analysis complete — "
            f"prob={tampering_prob:.3f} findings={len(findings)}"
        )

        # Build metadata dict
        meta = {
            "sub_scores":               {k: round(v, 4) for k, v in sub_scores.items()},
            "has_audio":                has_audio,
            "audio_duration_s":         round(len(samples) / sr, 3) if has_audio else 0.0,
            "audio_codec":              sync_result.audio_codec,
            "sample_rate":              sync_result.sample_rate,
            "channels":                 sync_result.channels,
            "duration_mismatch_s":      round(sync_result.duration_mismatch_s, 4),
            "av_start_offset_s":        round(sync_result.start_offset_s, 4),
            "enf_detected":             sync_result.enf_detected,
            "enf_score":                round(sync_result.enf_score, 4),
        }
        if spectral_result:
            meta.update({
                "centroid_abruptness_ratio":  round(spectral_result.centroid_abruptness_ratio, 2),
                "centroid_spike_count":       len(spectral_result.centroid_spike_timestamps),
                "centroid_spike_timestamps":  spectral_result.centroid_spike_timestamps[:5],
                "rms_abruptness_ratio":       round(spectral_result.rms_abruptness_ratio, 2),
                "rms_spike_count":            len(spectral_result.rms_spike_timestamps),
                "spectral_profile_distance":  round(spectral_result.spectral_profile_distance, 4),
                "onset_abruptness_ratio":     round(spectral_result.onset_abruptness_ratio, 2),
            })

        return self.make_result(
            tampering_probability=tampering_prob,
            uncertainty=uncertainty,
            summary=summary,
            findings=findings,
            reliable=has_audio,
            reliability_note=reliability_note,
            metadata=meta,
        )

    # ── Audio Extraction ──────────────────────────────────────────────────────

    def _extract_audio(
        self, video_path: Path
    ) -> Tuple[Optional[np.ndarray], int]:
        """
        Extract mono 44100 Hz audio from the video file using ffmpeg.
        Returns (samples_float64, sample_rate) or (None, 0) on failure.
        """
        import tempfile, os
        tmp_path = Path(tempfile.mktemp(suffix=".wav"))

        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(video_path),
                    "-vn",
                    "-acodec", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    str(tmp_path),
                ],
                capture_output=True, timeout=60
            )

            if proc.returncode != 0 or not tmp_path.exists():
                return None, 0

            with wave.open(str(tmp_path), "r") as wf:
                sr     = wf.getframerate()
                n      = wf.getnframes()
                raw    = wf.readframes(n)

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
            return samples, sr

        except Exception as e:
            logger.warning(f"Audio extraction failed: {e}")
            return None, 0
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    # ── Result Processors ─────────────────────────────────────────────────────

    def _process_spectral(
        self, result: SpectralResult, profile
    ) -> List[Finding]:
        """Convert SpectralResult findings to Finding objects."""
        findings = []

        for text in result.findings:
            text_lower = text.lower()

            if "centroid" in text_lower and result.centroid_abruptness_ratio >= 60:
                sev  = Severity.HIGH
                conf = min(0.85, 0.50 + result.centroid_abruptness_ratio / 200)
            elif "centroid" in text_lower or "profile mismatch" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.tampering_probability
            elif "rms" in text_lower or "onset" in text_lower:
                sev  = Severity.MEDIUM
                conf = min(0.70, result.tampering_probability + 0.10)
            else:
                sev  = Severity.LOW
                conf = 0.30

            f = self.make_finding(
                title="Audio spectral anomaly",
                description=text,
                confidence=max(0.10, conf),
                severity=sev,
                uncertainty=0.18,
                evidence={
                    "centroid_abruptness_ratio": round(result.centroid_abruptness_ratio, 2),
                    "rms_abruptness_ratio":      round(result.rms_abruptness_ratio, 2),
                    "spectral_profile_distance": round(result.spectral_profile_distance, 4),
                    "onset_abruptness_ratio":    round(result.onset_abruptness_ratio, 2),
                },
                raw_values={
                    "centroid_spike_timestamps": result.centroid_spike_timestamps[:5],
                    "rms_spike_timestamps":      result.rms_spike_timestamps[:5],
                },
            )

            # Add temporal location if spikes were localized
            all_spikes = (
                result.centroid_spike_timestamps +
                result.rms_spike_timestamps +
                result.onset_spike_timestamps
            )
            if all_spikes:
                f.temporal_location = TemporalLocation(
                    start_frame=0,
                    end_frame=profile.total_frames,
                    start_seconds=min(all_spikes),
                    end_seconds=max(all_spikes),
                )

            findings.append(f)

        return findings

    def _process_sync(self, result: AVSyncResult) -> List[Finding]:
        """Convert AVSyncResult findings to Finding objects."""
        findings = []

        for text in result.findings:
            text_lower = text.lower()
            if "enf" in text_lower:
                sev  = Severity.HIGH
                conf = result.enf_score
            elif "duration mismatch" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.duration_mismatch_score
            elif "start time" in text_lower or "offset" in text_lower:
                sev  = Severity.MEDIUM
                conf = result.start_offset_score
            else:
                sev  = Severity.LOW
                conf = 0.25

            findings.append(self.make_finding(
                title="Audio-video sync anomaly",
                description=text,
                confidence=max(0.10, conf),
                severity=sev,
                uncertainty=0.15,
                evidence={
                    "duration_mismatch_s": round(result.duration_mismatch_s, 4),
                    "start_offset_s":      round(result.start_offset_s, 4),
                    "enf_detected":        result.enf_detected,
                    "enf_score":           round(result.enf_score, 4),
                },
            ))

        return findings

    # ── Summary ───────────────────────────────────────────────────────────────

    def _build_summary(
        self,
        sub_scores: dict,
        spectral: Optional[SpectralResult],
        sync: AVSyncResult,
        findings: List[Finding],
    ) -> str:
        parts = ["Audio forensics examined spectral consistency, "
                 "audio-video synchronization"]
        if sync.enf_detected:
            parts.append(", and ENF phase continuity")
        parts.append(". ")

        if spectral:
            if spectral.centroid_abruptness_ratio >= 30:
                parts.append(
                    f"Spectral centroid abruptness ratio of "
                    f"{spectral.centroid_abruptness_ratio:.1f} detected "
                    f"(threshold: 30). "
                )
            else:
                parts.append("Spectral characteristics are consistent throughout. ")

            if spectral.spectral_profile_distance >= 0.85:
                parts.append(
                    f"Spectral profile distance between first and second half: "
                    f"{spectral.spectral_profile_distance:.3f}. "
                )

        if sync.duration_mismatch_s > 0.10:
            parts.append(
                f"Audio/video duration mismatch: {sync.duration_mismatch_s:.3f}s. "
            )

        if not findings:
            parts.append("No significant audio anomalies found.")

        return "".join(parts)
