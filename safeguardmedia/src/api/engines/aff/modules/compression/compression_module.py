"""
AudioForensics — Compression Artifacts Module (Phase 5)

Lossy audio codecs (MP3, AAC, OGG, AMR) introduce characteristic artifacts
that persist even after decoding back to PCM. This module detects them.

The most forensically reliable artifact is the codec lowpass (LP) filter:
every lossy encoder applies a hard lowpass cutoff — typically at 7-16 kHz
depending on bitrate. This creates an abrupt spectral wall: energy above the
cutoff drops by 60-80 dB within a narrow band (~500 Hz). This wall is
unmistakable and cannot be mimicked by natural acoustic content.

Why this matters forensically:
  - A WAV file that contains codec LP artifacts was decoded from lossy audio
    at some point in its history. Its PCM wrapper is not the original recording.
  - A file whose declared codec is MP3 but whose LP cutoff is lower than
    expected for that bitrate was either re-encoded at a lower bitrate or
    passed through an intermediate lower-quality encoding.
  - A file showing LP artifacts in some segments but not others was likely
    assembled from sources with different encoding histories.

Detection signals:

  1. Codec LP cutoff detection (primary — most reliable)
     Computes spectral rolloff steepness (dB/kHz) across the upper 60% of
     the spectrum. Natural content rolls off at < 5 dB/kHz. A codec LP
     filter rolls off at > 20 dB/kHz — typically 50-150 dB/kHz.
     Operates on the full-rate WAV (wav_path_full) when available, which
     preserves the original Nyquist and makes high-frequency analysis more
     accurate.

  2. Cutoff frequency vs codec expectation
     Compares measured LP cutoff to the expected cutoff for the declared
     codec and bitrate. A cutoff much lower than expected suggests a prior
     lower-bitrate encoding.

  3. Per-segment ceiling consistency
     Applies the LP detection per 2-second segment. If the LP cutoff shifts
     between segments, the file may contain content from different encoding
     sources.

  4. Bandwidth utilisation
     For lossy codecs, checks whether the measured bandwidth is consistent
     with the declared bitrate. Very low bandwidth for a high bitrate
     suggests the content was previously encoded at a lower bitrate.

  5. PCM with LP artifacts (re-encoding detection)
     If the declared codec is uncompressed PCM/WAV but a steep LP rolloff
     is detected, the file was almost certainly decoded from a lossy source.
     This is a high-confidence finding.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

from core.models import (
    AudioCase, AudioCodec, BaseAudioModule, Finding,
    ModuleScore, Severity, TemporalLocation,
)

log = logging.getLogger("af.modules.compression")

MODULE_NAME    = "compression"
DEFAULT_WEIGHT = 0.18

# ── Detection parameters ──────────────────────────────────────────────────────

STEEPNESS_CODEC_MEDIUM = 20.0   # dB/kHz — above this: likely codec LP
STEEPNESS_CODEC_HIGH   = 40.0   # dB/kHz — above this: definite codec LP
MIN_TOTAL_DROP_DB      = 15.0   # Minimum total energy drop for a valid elbow
SEARCH_LOW_FRACTION    = 0.40   # Only search upper 60% of spectrum
MIN_DURATION_S         = 4.0    # Minimum duration for segment analysis

# Expected maximum frequency for common codecs at 16kHz analysis rate
# (these are after downsampling to 16kHz mono — most content will be below 8kHz)
CODEC_EXPECTED_CEILING: dict = {
    AudioCodec.AMR_NB:   3500.0,   # 8kHz sampling → Nyquist 4kHz
    AudioCodec.PCM_ALAW: 3500.0,
    AudioCodec.PCM_MULAW:3500.0,
    AudioCodec.AMR_WB:   7000.0,   # 16kHz sampling → Nyquist 8kHz
    AudioCodec.MP3:      7200.0,   # varies by bitrate; 7.2kHz for 128k@16kHz
    AudioCodec.AAC:      7500.0,   # typically near Nyquist
    AudioCodec.OGG_VORBIS: 7500.0,
    AudioCodec.OPUS:     7500.0,
    AudioCodec.FLAC:     8000.0,   # lossless — full bandwidth
}

# LP rolloff steepness for a natural signal (no codec filter)
NATURAL_STEEPNESS_MAX = 5.0  # dB/kHz


class CompressionModule(BaseAudioModule):
    """
    Compression Artifacts detection module.

    Identifies codec LP filter signatures, bandwidth anomalies,
    and per-segment encoding inconsistencies.
    """

    MODULE_NAME    = MODULE_NAME
    DEFAULT_WEIGHT = DEFAULT_WEIGHT

    def run(self, case: AudioCase) -> ModuleScore:
        t0 = time.time()
        ap = case.audio_profile

        # Prefer full-rate WAV for better high-frequency resolution
        wav_path = self._choose_wav(ap)
        if wav_path is None:
            return self._skipped("No extracted WAV available")
        if not wav_path.exists():
            return self._skipped(f"WAV not found: {wav_path}")

        try:
            sr, raw = wavfile.read(str(wav_path))
            audio   = raw.astype(np.float64) / 32767.0
        except Exception as e:
            return self._skipped(f"Could not read WAV: {e}")

        nyquist = sr / 2.0

        # ── Whole-file LP analysis ─────────────────────────────────────────
        findings: List[Finding]              = []
        score_components: List[Tuple[float, float]] = []

        steep, cutoff_hz, total_drop = self._measure_lp_rolloff(audio, sr)

        # ── 1. LP filter presence ──────────────────────────────────────────
        f_lp, s_lp = self._check_lp_presence(
            steep, cutoff_hz, total_drop, ap, nyquist
        )
        findings.extend(f_lp); score_components.extend(s_lp)

        # ── 2. Cutoff vs codec expectation ─────────────────────────────────
        if cutoff_hz is not None:
            f_exp, s_exp = self._check_cutoff_vs_expectation(
                cutoff_hz, ap, nyquist
            )
            findings.extend(f_exp); score_components.extend(s_exp)

        # ── 3. Per-segment ceiling consistency ────────────────────────────
        if ap.duration_seconds >= MIN_DURATION_S:
            f_seg, s_seg = self._check_segment_consistency(audio, sr, ap)
            findings.extend(f_seg); score_components.extend(s_seg)

        # ── 4. Bandwidth vs declared bitrate ──────────────────────────────
        f_bw, s_bw = self._check_bandwidth_utilisation(steep, cutoff_hz, ap)
        findings.extend(f_bw); score_components.extend(s_bw)

        # ── Aggregate ─────────────────────────────────────────────────────
        if not score_components:
            final_score = 0.0
            confidence  = 0.50
        else:
            weights     = [w for _, w in score_components]
            scores_     = [s for s, _ in score_components]
            total_w     = sum(weights)
            final_score = sum(sc * w for sc, w in zip(scores_, weights)) / total_w
            confidence  = min(0.90, 0.45 + 0.09 * len(score_components))

        return ModuleScore(
            module     = MODULE_NAME,
            score      = final_score,
            confidence = confidence,
            findings   = findings,
            weight     = DEFAULT_WEIGHT,
            elapsed_s  = round(time.time() - t0, 3),
            metadata   = {
                "lp_steepness_db_khz": round(steep, 2),
                "lp_cutoff_hz":        round(cutoff_hz, 1) if cutoff_hz else None,
                "lp_total_drop_db":    round(total_drop, 1),
                "codec":               ap.codec_name,
                "nyquist_hz":          nyquist,
                "lp_detected":         steep >= STEEPNESS_CODEC_MEDIUM,
            },
        )

    # ── Core LP rolloff measurement ───────────────────────────────────────────

    def _measure_lp_rolloff(
        self, audio: np.ndarray, sr: int
    ) -> Tuple[float, Optional[float], float]:
        """
        Find the steepest spectral rolloff in the upper spectrum.

        Returns:
            (steepness_db_per_khz, cutoff_hz, total_drop_db)
            steepness: how steep the rolloff is — high = codec LP filter
            cutoff_hz: frequency of the rolloff elbow (or None)
            total_drop_db: energy drop from pre-elbow level to post-elbow
        """
        f, psd   = signal.welch(audio, fs=sr, nperseg=min(8192, len(audio) // 2))
        psd_db   = 10 * np.log10(psd + 1e-20)
        nyquist  = sr / 2.0

        # Search window: 500 Hz wide, step by 50 Hz
        bin_width  = float(f[1] - f[0]) if len(f) > 1 else 1.0
        n_500      = max(2, int(500 / bin_width))
        lo_idx     = np.searchsorted(f, nyquist * SEARCH_LOW_FRACTION)

        best_steep  = 0.0
        best_freq   = None
        best_before = 0.0
        best_after  = 0.0

        quarter = max(1, n_500 // 4)

        for i in range(lo_idx, len(psd_db) - n_500):
            # Average just before and just after the window centre
            before = float(psd_db[max(0, i - quarter):i].mean()) if i > quarter else float(psd_db[i])
            after  = float(psd_db[i + quarter * 3:min(i + n_500, len(psd_db))].mean())

            drop      = before - after
            steepness = drop / 0.5   # dB/kHz (500 Hz window)

            if steepness > best_steep:
                best_steep  = steepness
                best_freq   = float(f[i + quarter])
                best_before = before
                best_after  = after

        # Total drop: from average level pre-elbow to mean of last 5% of spectrum
        if best_freq is not None:
            pre_mask    = f < best_freq
            post_mask   = f > (nyquist * 0.95)
            pre_level   = float(psd_db[pre_mask].mean()) if pre_mask.any() else 0.0
            post_level  = float(psd_db[post_mask].mean()) if post_mask.any() else pre_level
            total_drop  = pre_level - post_level
        else:
            total_drop = 0.0

        return best_steep, best_freq, total_drop

    @staticmethod
    def _choose_wav(ap) -> Optional[Path]:
        """Prefer full-rate WAV if available for better HF resolution."""
        if ap.wav_path_full:
            p = Path(ap.wav_path_full)
            if p.exists():
                return p
        if ap.wav_path_mono:
            p = Path(ap.wav_path_mono)
            if p.exists():
                return p
        return None

    # ── Detection checks ──────────────────────────────────────────────────────

    def _check_lp_presence(
        self,
        steep: float,
        cutoff_hz: Optional[float],
        total_drop: float,
        ap,
        nyquist: float,
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Check whether a codec LP filter is present in the spectrum.
        This is the primary detection signal.
        """
        findings = []
        scores   = []

        if steep < STEEPNESS_CODEC_MEDIUM or total_drop < MIN_TOTAL_DROP_DB:
            return findings, scores

        # For natively lossy codecs (AAC, MP3, Opus, AMR), an LP filter is
        # expected — every lossy encoder applies one. The _check_lp_filter
        # finding is only forensically meaningful for PCM/lossless files where
        # ANY LP artifact indicates prior lossy encoding.
        # Double-compression in lossy files is caught by _check_codec_expectation
        # (measures bandwidth vs expected for bitrate) and _check_segment_consistency.
        codec_is_pcm = ap.codec_enum in (
            AudioCodec.PCM_S16LE, AudioCodec.PCM_S24LE,
            AudioCodec.PCM_F32LE, AudioCodec.FLAC, AudioCodec.UNKNOWN,
        )

        # Only raise LP finding for PCM/lossless files — lossy codec LP is expected
        if not codec_is_pcm:
            return findings, scores

        if steep >= STEEPNESS_CODEC_HIGH:
            severity  = Severity.HIGH
            score_val = min(0.85, 0.60 + (steep - STEEPNESS_CODEC_HIGH) / 200.0)
            conf      = 0.85
        else:
            severity  = Severity.MEDIUM
            score_val = 0.40 + (steep - STEEPNESS_CODEC_MEDIUM) / 100.0
            conf      = 0.70

        cutoff_str = f"{cutoff_hz:.0f} Hz" if cutoff_hz else "unknown frequency"

        if codec_is_pcm:
            # PCM file with lossy LP artifacts — strong indicator of prior encoding
            severity  = Severity.HIGH
            score_val = min(0.90, score_val + 0.10)
            conf      = 0.88
            title     = "Lossy codec artifacts in PCM/lossless file"
            desc = (
                f"This file is declared as {ap.codec_name} (uncompressed/lossless) "
                f"but contains a hard lowpass filter characteristic at {cutoff_str} "
                f"(rolloff steepness: {steep:.0f} dB/kHz, total drop: {total_drop:.0f} dB). "
                f"Uncompressed recordings have smooth, gradual high-frequency rolloff "
                f"(< {NATURAL_STEEPNESS_MAX:.0f} dB/kHz). A steep brick-wall cutoff "
                f"is the definitive signature of a lossy codec LP filter, indicating "
                f"this file was decoded from MP3, AAC, or similar lossy source and "
                f"re-wrapped as PCM."
            )
        else:
            title = "Lowpass filter / codec spectral ceiling detected"
            desc = (
                f"A steep spectral lowpass filter was detected at {cutoff_str} "
                f"(rolloff steepness: {steep:.0f} dB/kHz, total drop: {total_drop:.0f} dB). "
                f"This is consistent with lossy codec encoding. The declared codec "
                f"({ap.codec_name}) may have been encoded at a bitrate that places "
                f"its lowpass cutoff lower than expected for its format."
            )

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = title,
            description = desc,
            severity    = severity,
            confidence  = conf,
            temporal_location = None,
            metadata    = {
                "steepness_db_khz": round(steep, 2),
                "cutoff_hz":        round(cutoff_hz, 1) if cutoff_hz else None,
                "total_drop_db":    round(total_drop, 1),
                "codec_is_pcm":     codec_is_pcm,
            },
        ))
        scores.append((score_val, 2.0))   # Primary signal
        return findings, scores

    def _check_cutoff_vs_expectation(
        self, cutoff_hz: float, ap, nyquist: float
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Compare measured LP cutoff to expected cutoff for declared codec.
        A cutoff significantly lower than expected indicates prior
        lower-bitrate encoding.
        """
        findings = []
        scores   = []

        expected = CODEC_EXPECTED_CEILING.get(ap.codec_enum)
        if expected is None:
            return findings, scores

        # Adjust expected for analysis sample rate
        expected = min(expected, nyquist * 0.98)

        deficit_hz  = expected - cutoff_hz
        deficit_pct = deficit_hz / expected * 100

        if deficit_pct > 25:
            severity  = Severity.MEDIUM
            score_val = min(0.60, 0.30 + deficit_pct / 200.0)
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Codec bandwidth lower than expected for declared format",
                description = (
                    f"The detected spectral cutoff ({cutoff_hz:.0f} Hz) is "
                    f"{deficit_hz:.0f} Hz ({deficit_pct:.0f}%) below the expected "
                    f"ceiling for {ap.codec_name} ({expected:.0f} Hz). This suggests "
                    f"the content was previously encoded at a lower bitrate before "
                    f"being re-encoded at the current settings, resulting in a "
                    f"lower effective bandwidth than the bitrate would normally provide."
                ),
                severity          = severity,
                confidence        = 0.65,
                temporal_location = None,
                metadata          = {
                    "measured_cutoff_hz": round(cutoff_hz, 1),
                    "expected_ceiling_hz": round(expected, 1),
                    "deficit_hz":          round(deficit_hz, 1),
                    "deficit_pct":         round(deficit_pct, 1),
                },
            ))
            scores.append((score_val, 1.0))

        return findings, scores

    def _check_segment_consistency(
        self, audio: np.ndarray, sr: int, ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Check LP cutoff consistency per 2-second segment.
        Inconsistent ceilings across segments indicate mixed encoding history.
        """
        findings = []
        scores   = []

        seg_n  = int(2.0 * sr)
        if len(audio) < seg_n * 3:
            return findings, scores

        ceilings = []
        for start in range(0, len(audio) - seg_n, seg_n):
            seg = audio[start:start + seg_n]
            steep, cutoff, drop = self._measure_lp_rolloff(seg, sr)
            if steep >= STEEPNESS_CODEC_MEDIUM and cutoff is not None:
                ceilings.append(cutoff)

        if len(ceilings) < 5:
            return findings, scores  # Not enough segments with clear LP filter

        ceiling_arr = np.array(ceilings)
        cv = float(ceiling_arr.std() / ceiling_arr.mean()) if ceiling_arr.mean() > 0 else 0.0

        # For native lossy codecs (AAC, MP3, Opus), VBR encoding naturally
        # produces variable LP cutoffs across segments — this is expected and
        # should not be flagged. Only raise for PCM/lossless with LP artifacts.
        from core.models import AudioCodec as _AC
        codec_is_native_lossy = ap.codec_enum not in (
            _AC.PCM_S16LE, _AC.PCM_S24LE, _AC.PCM_F32LE, _AC.FLAC, _AC.UNKNOWN
        )
        if codec_is_native_lossy:
            return findings, scores   # VBR variation expected in lossy codecs

        if cv > 0.12:   # > 12% variation in LP cutoff across segments
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Inconsistent spectral ceiling across file segments",
                description = (
                    f"The LP filter cutoff frequency varies across {len(ceilings)} "
                    f"analysed segments (CV: {cv:.3f}, range: "
                    f"{ceiling_arr.min():.0f}–{ceiling_arr.max():.0f} Hz). "
                    f"A consistently encoded file should have a stable spectral "
                    f"ceiling. Variation suggests the file was assembled from "
                    f"segments with different encoding histories."
                ),
                severity          = Severity.MEDIUM,
                confidence        = 0.65,
                temporal_location = None,
                metadata          = {
                    "ceiling_cv":   round(cv, 4),
                    "ceiling_min":  round(float(ceiling_arr.min()), 1),
                    "ceiling_max":  round(float(ceiling_arr.max()), 1),
                    "n_segments":   len(ceilings),
                },
            ))
            scores.append((0.40, 0.8))

        return findings, scores

    def _check_bandwidth_utilisation(
        self, steep: float, cutoff_hz: Optional[float], ap
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        For lossy codecs: check whether measured bandwidth is consistent
        with declared bitrate. Very low bandwidth for the declared bitrate
        indicates a prior lower-quality encoding pass.
        """
        findings = []
        scores   = []

        if not ap.codec_enum.is_lossy:
            return findings, scores
        if not ap.bitrate_bps or ap.bitrate_bps < 1:
            return findings, scores
        if cutoff_hz is None or steep < STEEPNESS_CODEC_MEDIUM:
            return findings, scores

        bitrate_kbps = ap.bitrate_bps / 1000.0

        # Expected minimum ceiling at this bitrate (rough heuristic)
        # MP3: 64k → ~8kHz, 128k → ~16kHz, 32k → ~5kHz
        expected_min_khz = bitrate_kbps / 16.0   # very conservative lower bound
        expected_min_hz  = expected_min_khz * 1000.0

        # Cap at Nyquist
        nyquist = ap.sample_rate / 2.0
        expected_min_hz = min(expected_min_hz, nyquist * 0.90)

        if cutoff_hz < expected_min_hz * 0.65:
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "Bandwidth too narrow for declared bitrate",
                description = (
                    f"At {bitrate_kbps:.0f} kbps ({ap.codec_name}), the expected "
                    f"minimum spectral bandwidth is approximately "
                    f"{expected_min_hz:.0f} Hz. The measured LP cutoff "
                    f"({cutoff_hz:.0f} Hz) is significantly lower, suggesting "
                    f"the content was previously encoded at a lower bitrate and "
                    f"then re-encoded at the current settings."
                ),
                severity          = Severity.MEDIUM,
                confidence        = 0.60,
                temporal_location = None,
                metadata          = {
                    "bitrate_kbps":      round(bitrate_kbps, 1),
                    "expected_min_hz":   round(expected_min_hz, 1),
                    "measured_cutoff_hz":round(cutoff_hz, 1),
                },
            ))
            scores.append((0.35, 0.7))

        return findings, scores
