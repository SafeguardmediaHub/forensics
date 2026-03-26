"""
AudioForensics — Metadata & Format Integrity Module (Phase 2)

Analyses container metadata, encoder fingerprints, timestamps, and
declared vs measured properties for signs of post-capture manipulation.

Metadata is the weakest forensic signal — it is easily forged —
so this module carries the lowest base weight in the fusion engine.
However it provides crucial corroboration when other modules also flag
anomalies, and it reliably catches naive editing (tools that strip
metadata, re-wrap in a different container, or leave encoder footprints).

Detection signals:

  1. Container / codec consistency
     Does the container format match the codec? An MP3 stream inside
     a WAV container, or an AAC stream in an MP3 container, indicates
     the file was re-wrapped — a common step after editing.

  2. Encoder fingerprinting
     Known editing software leaves characteristic encoder strings:
     - Audacity:   "Lavf..." with no device model
     - GarageBand: "Apple GarageBand"
     - FFmpeg:     "Lavf60.16.100" (version-specific)
     - Adobe:      "Adobe Audition"
     Legitimate recorders (phones, cameras) leave device-specific strings.

  3. Timestamp gap analysis
     The creation_time metadata tag should be close to the file's
     filesystem mtime. A large gap (days or months) suggests the file
     was modified after initial capture. A missing creation_time in
     a format that normally includes it is itself suspicious.

  4. Declared sample rate vs measured bandwidth
     If a file claims 44.1kHz but all spectral energy is below 4kHz,
     it was likely resampled upward — a common step to disguise that
     the source was a low-quality codec or phone recording.

  5. Bitrate consistency
     For CBR (constant bitrate) formats, the declared bitrate should
     match the measured bitrate from file size / duration. A large
     discrepancy suggests re-encoding or bitrate lying.

  6. Truncation detection
     If declared duration × sample_rate significantly exceeds the
     actual sample count in the extracted WAV, the file was truncated
     or the metadata was patched without re-encoding.

  7. Missing expected metadata
     MP3 files normally have ID3 tags with at least an encoder string.
     A completely tag-stripped MP3 is suspicious — stripping tags is
     a common step in naive editing workflows.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

from core.models import (
    AudioCase, AudioCodec, BaseAudioModule, Finding,
    ModuleScore, Severity, TemporalLocation,
)

log = logging.getLogger("af.modules.metadata")

MODULE_NAME    = "metadata"
DEFAULT_WEIGHT = 0.08


# ── Encoder knowledge base ────────────────────────────────────────────────────

# Known editing software encoder strings (substrings, case-insensitive)
_EDITING_ENCODERS: Dict[str, str] = {
    "audacity":          "Audacity audio editor",
    "adobe audition":    "Adobe Audition",
    "adobe premiere":    "Adobe Premiere",
    "garageband":        "Apple GarageBand",
    "logic pro":         "Apple Logic Pro",
    "reaper":            "REAPER DAW",
    "ocenaudio":         "ocenaudio editor",
    "wavosaur":          "Wavosaur editor",
    "soundforge":        "Sound Forge",
    "cubase":            "Steinberg Cubase",
    "ableton":           "Ableton Live",
    "fl studio":         "FL Studio",
    "goldwave":          "GoldWave editor",
    "wavepad":           "WavePad editor",
}

# Generic ffmpeg/libav encoders (not specific to a device — suspicious context)
_GENERIC_ENCODERS = [
    r"^lavf\d",           # Lavf60.16.100 — generic FFmpeg mux
    r"^lavc\d",           # Lavc — FFmpeg codec
    r"^libav",            # libav
    r"^ffmpeg",
]

# Known legitimate device/app encoder strings (NOT suspicious)
_LEGITIMATE_ENCODERS = [
    "apple",
    "samsung",
    "google",
    "huawei",
    "xiaomi",
    "oneplus",
    "android",
    "ios",
    "iphone",
    "quicktime",
    "whatsapp",
    "zoom",
    "teams",
    "meet",
    "skype",
    "lame",          # LAME MP3 encoder — common legitimate encoder
]

# Expected container ↔ codec pairings
_EXPECTED_CODECS: Dict[str, List[str]] = {
    "wav":                           ["pcm_s16le", "pcm_s24le", "pcm_f32le",
                                      "pcm_s32le", "pcm_alaw", "pcm_mulaw",
                                      "adpcm_ms", "pcm_s8"],
    "mp3":                           ["mp3", "mp3float"],
    "aac":                           ["aac"],
    "mov,mp4,m4a,3gp,3g2,mj2":      ["aac", "mp3", "pcm_s16le", "opus",
                                      "alac", "ac3"],
    "ogg":                           ["vorbis", "opus", "flac"],
    "flac":                          ["flac"],
    "amr":                           ["amr_nb", "amr_wb"],
    "matroska,webm":                 ["vorbis", "opus", "aac", "mp3", "flac"],
}


class MetadataModule(BaseAudioModule):
    """
    Metadata & Format Integrity analysis module.

    Inspects container metadata, encoder fingerprints, timestamps,
    and declared vs measured properties.
    """

    MODULE_NAME    = MODULE_NAME
    DEFAULT_WEIGHT = DEFAULT_WEIGHT

    # Thresholds
    TIMESTAMP_GAP_DAYS_MEDIUM = 7     # Gap beyond which we flag MEDIUM
    TIMESTAMP_GAP_DAYS_HIGH   = 180   # Gap beyond which we flag HIGH  (raised from 90 days)
    BITRATE_DISCREPANCY_PCT   = 25    # % difference before flagging
    BW_RATIO_SUSPICIOUS       = 0.30  # Measured BW < 30% of Nyquist → suspicious
    BW_RATIO_HIGH             = 0.15  # Measured BW < 15% of Nyquist → HIGH severity
    TRUNCATION_TOLERANCE_PCT  = 5     # % sample count discrepancy before flagging

    def run(self, case: AudioCase) -> ModuleScore:
        t0  = time.time()
        ap  = case.audio_profile
        findings: List[Finding] = []
        score_components: List[Tuple[float, float]] = []  # (score, weight)

        # ── 1. Container / codec consistency ──────────────────────────────
        f, s = self._check_container_codec(ap)
        findings.extend(f); score_components.extend(s)

        # ── 2. Encoder fingerprint ─────────────────────────────────────────
        f, s = self._check_encoder(ap)
        findings.extend(f); score_components.extend(s)

        # ── 3. Timestamp gap ───────────────────────────────────────────────
        f, s = self._check_timestamps(ap)
        findings.extend(f); score_components.extend(s)

        # ── 4. Declared SR vs measured bandwidth ───────────────────────────
        f, s = self._check_bandwidth_vs_samplerate(ap)
        findings.extend(f); score_components.extend(s)

        # ── 5. Bitrate consistency ─────────────────────────────────────────
        f, s = self._check_bitrate(ap)
        findings.extend(f); score_components.extend(s)

        # ── 6. Truncation ──────────────────────────────────────────────────
        f, s = self._check_truncation(ap)
        findings.extend(f); score_components.extend(s)

        # ── 7. Stripped metadata ───────────────────────────────────────────
        f, s = self._check_stripped_metadata(ap)
        findings.extend(f); score_components.extend(s)

        # ── Aggregate score ────────────────────────────────────────────────
        if not score_components:
            final_score = 0.0
            confidence  = 0.5
        else:
            weights     = [w for _, w in score_components]
            scores      = [s for s, _ in score_components]
            total_w     = sum(weights)
            final_score = sum(s * w for s, w in zip(scores, weights)) / total_w
            # Confidence: higher when we have multiple independent signals
            confidence  = min(0.85, 0.4 + 0.1 * len(score_components))

        # Severity bonus: HIGH findings push score up
        high_count = sum(1 for f in findings if f.severity == Severity.HIGH)
        if high_count >= 2:
            final_score = min(1.0, final_score + 0.15)
        elif high_count == 1:
            final_score = min(1.0, final_score + 0.08)

        return ModuleScore(
            module     = MODULE_NAME,
            score      = final_score,
            confidence = confidence,
            findings   = findings,
            weight     = DEFAULT_WEIGHT,
            elapsed_s  = round(time.time() - t0, 3),
            metadata   = {
                "n_checks":          7,
                "n_findings":        len(findings),
                "codec":             ap.codec_name,
                "container":         ap.container_format,
                "encoder":           ap.encoder_string,
                "has_creation_time": ap.creation_time is not None,
            },
        )

    # ── Detection methods ─────────────────────────────────────────────────────

    def _check_container_codec(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Check that the codec is appropriate for the declared container format.
        Mismatches indicate re-wrapping — a common post-edit step.
        """
        findings = []
        scores   = []

        container = ap.container_format.lower()
        codec     = ap.codec_name.lower()

        # Find which expected container key matches
        matched_expected = None
        for fmt_key, codecs in _EXPECTED_CODECS.items():
            if container == fmt_key or container in fmt_key or fmt_key in container:
                matched_expected = codecs
                break

        if matched_expected is None:
            # Unknown container — can't assess, low confidence
            return findings, scores

        if codec not in matched_expected:
            # Definite mismatch
            severity = Severity.HIGH
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Container/codec mismatch",
                description = (
                    f"Container format '{ap.container_format}' does not normally "
                    f"carry codec '{ap.codec_name}'. This combination is typical "
                    f"of files that were re-wrapped after editing — the audio was "
                    f"extracted from one format and placed into a different container "
                    f"without re-encoding."
                ),
                severity          = severity,
                confidence        = 0.80,
                temporal_location = None,
                metadata          = {
                    "container": ap.container_format,
                    "codec":     ap.codec_name,
                    "expected":  matched_expected,
                },
            ))
            scores.append((0.75, 1.5))  # Strong signal, higher weight

        return findings, scores

    def _check_encoder(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Examine the encoder string for signs of editing software.
        Legitimate recordings come from device-specific encoders.
        Edited files often carry generic FFmpeg/Audacity strings.
        """
        findings = []
        scores   = []
        enc = (ap.encoder_string or "").strip()

        if not enc:
            return findings, scores

        enc_lower = enc.lower()

        # Check for known editing software
        for key, label in _EDITING_ENCODERS.items():
            if key in enc_lower:
                findings.append(Finding(
                    module      = MODULE_NAME,
                    title       = "Editing software encoder fingerprint",
                    description = (
                        f"The encoder string '{enc}' matches {label}. "
                        f"This indicates the file was processed through "
                        f"audio editing software rather than captured directly "
                        f"by a recording device. While legitimate workflows do "
                        f"use editing software, this warrants scrutiny in a "
                        f"forensic context."
                    ),
                    severity          = Severity.MEDIUM,
                    confidence        = 0.70,
                    temporal_location = None,
                    metadata          = {"encoder": enc, "matched": label},
                ))
                scores.append((0.45, 1.0))
                return findings, scores  # One finding per encoder string

        # Check for generic FFmpeg/libav (not device-specific)
        for pattern in _GENERIC_ENCODERS:
            if re.match(pattern, enc_lower):
                # Only flag if no legitimate device context
                is_legitimate = any(leg in enc_lower for leg in _LEGITIMATE_ENCODERS)
                if not is_legitimate:
                    findings.append(Finding(
                        module      = MODULE_NAME,
                        title       = "Generic transcoding encoder detected",
                        description = (
                            f"The encoder string '{enc}' indicates this file was "
                            f"processed by a generic transcoding tool (FFmpeg/libav) "
                            f"rather than a specific device. This is consistent with "
                            f"a file that was converted or re-encoded after original "
                            f"capture."
                        ),
                        severity          = Severity.LOW,
                        confidence        = 0.50,
                        temporal_location = None,
                        metadata          = {"encoder": enc},
                    ))
                    scores.append((0.25, 0.5))
                break

        return findings, scores

    def _check_timestamps(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Compare creation_time metadata tag to file system modification time.
        A large gap suggests the file was modified after initial capture.
        """
        findings = []
        scores   = []

        if not ap.creation_time:
            # Missing creation_time in formats that normally include it
            # (MP4/M4A always include it; MP3 often does)
            container = ap.container_format.lower()
            if "mp4" in container or "m4a" in container or "mov" in container:
                findings.append(Finding(
                    module      = MODULE_NAME,
                    title       = "Missing creation timestamp",
                    description = (
                        f"The container format ({ap.container_format}) normally "
                        f"includes a creation_time metadata tag, but none was found. "
                        f"This tag is sometimes stripped by editing tools before "
                        f"export to remove provenance information."
                    ),
                    severity          = Severity.LOW,
                    confidence        = 0.45,
                    temporal_location = None,
                ))
                scores.append((0.20, 0.5))
            return findings, scores

        # Parse creation_time
        creation_dt = self._parse_timestamp(ap.creation_time)
        if creation_dt is None:
            return findings, scores  # Can't parse — skip

        # Get file mtime
        try:
            file_path = Path(ap.file_path)
            mtime     = file_path.stat().st_mtime
            mtime_dt  = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except Exception:
            return findings, scores

        # Ensure creation_dt is timezone-aware
        if creation_dt.tzinfo is None:
            creation_dt = creation_dt.replace(tzinfo=timezone.utc)

        gap_seconds = (mtime_dt - creation_dt).total_seconds()
        gap_days    = gap_seconds / 86400

        if gap_days > self.TIMESTAMP_GAP_DAYS_HIGH:
            severity   = Severity.HIGH
            score_val  = 0.60
            weight     = 1.2
            desc = (
                f"The file's creation_time metadata ({ap.creation_time}) is "
                f"{abs(gap_days):.0f} days before the file's modification time "
                f"({mtime_dt.strftime('%Y-%m-%d')}). This large gap strongly "
                f"suggests the file was substantially modified after the original "
                f"capture date."
            )
        elif gap_days > self.TIMESTAMP_GAP_DAYS_MEDIUM:
            severity   = Severity.MEDIUM
            score_val  = 0.35
            weight     = 1.0
            desc = (
                f"The file's creation_time metadata ({ap.creation_time}) is "
                f"{abs(gap_days):.0f} days before the file's modification time "
                f"({mtime_dt.strftime('%Y-%m-%d')}). This gap may indicate "
                f"post-capture editing or file transfer artefacts."
            )
        elif gap_days < -1:
            # mtime BEFORE creation_time — impossible, clock fraud
            severity   = Severity.HIGH
            score_val  = 0.70
            weight     = 1.5
            desc = (
                f"The file's modification time ({mtime_dt.strftime('%Y-%m-%d')}) "
                f"is BEFORE the creation_time metadata ({ap.creation_time}). "
                f"This is physically impossible and indicates either clock "
                f"manipulation or that the metadata was patched to an incorrect date."
            )
        else:
            return findings, scores  # Normal gap — no finding

        findings.append(Finding(
            module            = MODULE_NAME,
            title             = "Timestamp anomaly",
            description       = desc,
            severity          = severity,
            confidence        = 0.65,
            temporal_location = None,
            metadata          = {
                "creation_time": ap.creation_time,
                "mtime":         mtime_dt.isoformat(),
                "gap_days":      round(gap_days, 1),
            },
        ))
        scores.append((score_val, weight))
        return findings, scores

    def _check_bandwidth_vs_samplerate(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Compare declared sample rate (= Nyquist × 2) to measured spectral bandwidth.
        A file claiming high sample rate but containing only low-frequency content
        was likely resampled upward — a common disguise step.

        Only meaningful when the original file claims a high sample rate (>=32kHz).
        Low-rate recordings legitimately have narrow bandwidth; flagging them
        creates false positives on phone recordings and synthetic test audio.
        """
        findings = []
        scores   = []

        if ap.effective_max_freq is None:
            return findings, scores

        # Only flag when the file CLAIMS to be high-rate
        if ap.sample_rate < 32000:
            return findings, scores

        nyquist   = ap.sample_rate / 2.0
        bw_ratio  = ap.effective_max_freq / nyquist

        if bw_ratio < self.BW_RATIO_HIGH:
            severity  = Severity.HIGH
            score_val = 0.65
            weight    = 1.3
            desc = (
                f"The declared sample rate is {ap.sample_rate:,} Hz "
                f"(Nyquist: {nyquist:,.0f} Hz), but measured spectral content "
                f"only extends to {ap.effective_max_freq:,.0f} Hz "
                f"({bw_ratio*100:.1f}% of Nyquist). This is very strong evidence "
                f"of artificial upsampling — the audio was likely captured or "
                f"re-encoded at a much lower sample rate and then resampled upward "
                f"to disguise its true origin."
            )
        elif bw_ratio < self.BW_RATIO_SUSPICIOUS:
            severity  = Severity.MEDIUM
            score_val = 0.40
            weight    = 1.0
            desc = (
                f"The declared sample rate is {ap.sample_rate:,} Hz, but measured "
                f"spectral content only extends to {ap.effective_max_freq:,.0f} Hz "
                f"({bw_ratio*100:.1f}% of Nyquist). This may indicate that the "
                f"audio was captured at a lower sample rate or passed through a "
                f"lossy codec with a hard frequency cutoff before being resampled."
            )
        else:
            return findings, scores

        findings.append(Finding(
            module            = MODULE_NAME,
            title             = "Sample rate vs bandwidth mismatch",
            description       = desc,
            severity          = severity,
            confidence        = 0.72,
            temporal_location = None,
            metadata          = {
                "declared_sample_rate": ap.sample_rate,
                "nyquist_hz":           nyquist,
                "measured_bw_hz":       ap.effective_max_freq,
                "bw_ratio":             round(bw_ratio, 3),
            },
        ))
        scores.append((score_val, weight))
        return findings, scores

    def _check_bitrate(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Compare declared bitrate to bitrate implied by file size / duration.
        Significant discrepancies indicate the metadata was patched
        without re-encoding, or the file was re-encoded at a different quality.
        """
        findings = []
        scores   = []

        if not ap.bitrate_bps or ap.duration_seconds <= 0:
            return findings, scores

        # Only meaningful for lossy formats (PCM bitrate is fully determined by sr/ch/depth)
        if not ap.codec_enum.is_lossy:
            return findings, scores

        # Implied bitrate from file size
        implied_bps = int(ap.file_size_bytes * 8 / ap.duration_seconds)
        declared    = ap.bitrate_bps

        # Percentage discrepancy
        discrepancy_pct = abs(implied_bps - declared) / declared * 100

        if discrepancy_pct > self.BITRATE_DISCREPANCY_PCT:
            severity  = Severity.MEDIUM if discrepancy_pct < 50 else Severity.HIGH
            score_val = min(0.55, 0.25 + discrepancy_pct / 200)
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Bitrate inconsistency",
                description = (
                    f"Declared bitrate is {declared//1000} kbps, but file size "
                    f"({ap.file_size_bytes:,} bytes) over {ap.duration_seconds:.1f}s "
                    f"implies {implied_bps//1000} kbps — a {discrepancy_pct:.0f}% "
                    f"discrepancy. This may indicate the bitrate metadata was "
                    f"patched, or the file was re-encoded at a different quality "
                    f"than declared."
                ),
                severity          = severity,
                confidence        = 0.55,
                temporal_location = None,
                metadata          = {
                    "declared_kbps": declared // 1000,
                    "implied_kbps":  implied_bps // 1000,
                    "discrepancy_pct": round(discrepancy_pct, 1),
                },
            ))
            scores.append((score_val, 0.8))

        return findings, scores

    def _check_truncation(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Compare declared duration × sample_rate to actual extracted sample count.
        A large discrepancy means the file was truncated or metadata was patched.
        """
        findings = []
        scores   = []

        if not ap.wav_path_mono or ap.duration_seconds <= 0 or ap.sample_rate <= 0:
            return findings, scores

        # Count actual samples in extracted WAV
        try:
            sr, audio = wavfile.read(ap.wav_path_mono)
            actual_samples   = len(audio)
            # Declared: duration × analysis sample rate (16kHz)
            declared_samples = int(ap.duration_seconds * sr)
            if declared_samples == 0:
                return findings, scores
            discrepancy_pct  = abs(actual_samples - declared_samples) / declared_samples * 100
        except Exception:
            return findings, scores

        if discrepancy_pct > self.TRUNCATION_TOLERANCE_PCT:
            is_short = actual_samples < declared_samples
            direction = "shorter" if is_short else "longer"
            severity  = Severity.MEDIUM if discrepancy_pct < 20 else Severity.HIGH

            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Duration/sample count discrepancy",
                description = (
                    f"The declared duration ({ap.duration_seconds:.2f}s) implies "
                    f"{declared_samples:,} samples at {sr:,} Hz, but the extracted "
                    f"audio contains {actual_samples:,} samples "
                    f"({discrepancy_pct:.1f}% {direction}). "
                    f"{'The file appears truncated.' if is_short else 'The file contains more data than declared.'} "
                    f"This may indicate the metadata was patched to misrepresent "
                    f"the recording duration."
                ),
                severity          = severity,
                confidence        = 0.70,
                temporal_location = None,
                metadata          = {
                    "declared_samples": declared_samples,
                    "actual_samples":   actual_samples,
                    "discrepancy_pct":  round(discrepancy_pct, 1),
                    "is_truncated":     is_short,
                },
            ))
            scores.append((0.50 if is_short else 0.30, 1.0))

        return findings, scores

    def _check_stripped_metadata(
        self, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect signs of stripped metadata in MP3 files.
        
        Legitimate MP3 recordings always have device/app-specific metadata.
        When metadata is stripped by an editor, the file is left with only a
        generic muxer encoder string (e.g. "Lavf60.16.100") and no creation
        timestamp — or nothing at all.
        """
        findings = []
        scores   = []

        if ap.codec_enum != AudioCodec.MP3:
            return findings, scores

        enc = (ap.encoder_string or "").strip()
        enc_lower = enc.lower()

        # Completely empty: no encoder, no tags
        has_no_tags = not ap.has_id3_tags and not enc
        # Generic muxer only (Lavf), no creation time = likely stripped
        is_generic_only = (
            any(re.match(p, enc_lower) for p in _GENERIC_ENCODERS) and
            ap.creation_time is None
        )

        if has_no_tags or is_generic_only:
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Stripped or absent provenance metadata",
                description = (
                    f"This MP3 file contains {'no metadata tags' if has_no_tags else 'only a generic muxer encoder string'} "
                    f"and no creation timestamp. MP3 files recorded by phones, "
                    f"apps, and professional recorders always carry device-specific "
                    f"metadata. The absence of provenance metadata is consistent "
                    f"with tags being stripped or reset by an editing workflow."
                ),
                severity          = Severity.MEDIUM,
                confidence        = 0.58,
                temporal_location = None,
                metadata          = {"encoder": enc, "has_id3": ap.has_id3_tags},
            ))
            scores.append((0.30, 0.8))

        return findings, scores

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_timestamp(ts_str: str) -> Optional[datetime]:
        """Parse an ISO 8601 timestamp string to datetime. Returns None on failure."""
        if not ts_str:
            return None
        # Normalise: replace trailing 'Z', handle microseconds
        ts_str = ts_str.strip().replace("Z", "+00:00")
        # Remove microseconds sub-field if present: .000000 → nothing
        ts_str = re.sub(r"\.(\d{6})\+", r"+", ts_str)
        ts_str = re.sub(r"\.(\d{3})\+", r"+", ts_str)
        formats = [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d",
            "%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue
        return None
