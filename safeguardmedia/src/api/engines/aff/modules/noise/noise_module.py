"""
AudioForensics — Noise Floor Consistency Module (Phase 4)

Background noise is the acoustic fingerprint of a recording environment.
Different rooms, outdoor spaces, and recording devices have distinct noise
profiles — characteristic spectral shapes, levels, and frequency balances.

A genuine recording has a consistent noise floor throughout. A spliced
recording will show an abrupt change in noise characteristics at the
edit boundary — even when the content (speech or music) masks the splice.

Detection signals:

  1. Spectral tilt track
     The ratio of low-frequency energy (50–500 Hz) to mid-band energy
     (500–4000 Hz) characterises the "colour" of the noise floor.
     Pink noise (indoor hum, HVAC) has high tilt (~4-5).
     White noise (outdoor wind, fan noise) has low tilt (~1).
     Speech and music vary, but the AVERAGE tilt over 0.5s windows
     reflects the background environment more than the content.

  2. Coefficient of variation (CV) of spectral tilt
     A stable recording has low tilt CV (< 0.01).
     A spliced recording has high tilt CV (> 0.02).
     A room-change has very high tilt CV (> 0.10).

  3. Z-score change-point detection
     Compare each window to the mean and std of the first 3 seconds
     (reference period). When z-score exceeds 4.0 for 3+ consecutive
     windows, a change-point is declared and its timestamp recorded.

  4. High-frequency energy ratio (HFE) track
     The ratio of energy above 4 kHz to mid-band energy is sensitive to
     microphone and room changes. Indoor recordings typically suppress
     HFE via walls and absorption; outdoor recordings have more HFE.

  5. RMS level consistency
     While level varies with content, sudden large level changes that
     persist across multiple windows suggest different recording sessions
     with different gain settings. Used as secondary corroboration only.

Threshold summary (empirically derived from synthetic test data):
    tilt_cv > 0.015  →  MEDIUM finding
    tilt_cv > 0.080  →  HIGH finding
    max_z > 2.5, persistent → change-point finding with timestamp
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

from core.models import (
    AudioCase, BaseAudioModule, Finding,
    ModuleScore, Severity, TemporalLocation,
)

log = logging.getLogger("af.modules.noise")

MODULE_NAME    = "noise"
DEFAULT_WEIGHT = 0.22

# ── Analysis parameters ───────────────────────────────────────────────────────
WINDOW_S    = 0.5      # Window length in seconds
HOP_S       = 0.25     # Hop size (50% overlap)
NPERSEG     = 1024     # Welch PSD segments within each window
REF_SECS    = 3.0      # Reference period: first N seconds
MIN_WINDOWS = 8        # Minimum windows required for analysis
MIN_SECS    = 5.0      # Minimum recording length for this module

# Frequency bands (Hz)
TILT_LOW_LO  =   50.0
TILT_LOW_HI  =  500.0
TILT_MID_LO  =  500.0
TILT_MID_HI  = 4000.0
HFE_LO       = 4000.0
HFE_HI       = 7500.0

# Detection thresholds
CV_MEDIUM         = 0.040   # tilt CV above this → MEDIUM  (raised from 0.015 for real recordings)
CV_HIGH           = 0.150   # tilt CV above this → HIGH  (raised from 0.080 for real recordings)
ZCHANGE_THRESHOLD = 4.0     # z-score for change-point
ZCHANGE_PERSIST   = 3       # consecutive windows required  (raised from 2 to reduce false positives)
RMS_CV_MEDIUM     = 0.25    # RMS coefficient of variation
HFE_CV_MEDIUM     = 0.15    # HFE CV


@dataclass
class NoiseTrack:
    """Per-window noise analysis results."""
    times:      np.ndarray    # Window centre times (s)
    tilts:      np.ndarray    # Spectral tilt per window
    hfe:        np.ndarray    # High-frequency energy ratio per window
    rms:        np.ndarray    # RMS level per window
    tilt_cv:    float         # Coefficient of variation of tilt
    hfe_cv:     float         # CV of HFE
    rms_cv:     float         # CV of RMS
    change_time: Optional[float]  # Detected change-point time (s), or None
    max_z:      float         # Maximum z-score relative to reference
    n_windows:  int


class NoiseModule(BaseAudioModule):
    """
    Noise Floor Consistency analysis module.

    Detects environment changes via spectral tilt tracking and
    z-score change-point detection.
    """

    MODULE_NAME    = MODULE_NAME
    DEFAULT_WEIGHT = DEFAULT_WEIGHT

    def run(self, case: AudioCase) -> ModuleScore:
        t0 = time.time()
        ap = case.audio_profile

        if not ap.wav_path_mono:
            return self._skipped("No extracted WAV available")

        wav_path = Path(ap.wav_path_mono)
        if not wav_path.exists():
            return self._skipped(f"WAV file not found: {wav_path}")

        try:
            sr, raw = wavfile.read(str(wav_path))
            audio   = raw.astype(np.float64) / 32767.0
        except Exception as e:
            return self._skipped(f"Could not read WAV: {e}")

        if ap.duration_seconds < MIN_SECS:
            return self._skipped(
                f"Recording too short ({ap.duration_seconds:.1f}s < {MIN_SECS}s) "
                f"for noise floor analysis"
            )

        # ── Extract noise track ────────────────────────────────────────────
        try:
            track = self._extract_noise_track(audio, sr)
        except Exception as e:
            return self._skipped(f"Noise track extraction failed: {e}")

        if track.n_windows < MIN_WINDOWS:
            return self._skipped(
                f"Insufficient windows ({track.n_windows}) for noise analysis"
            )

        # ── Build findings ─────────────────────────────────────────────────
        findings:        List[Finding]              = []
        score_components: List[Tuple[float, float]] = []

        # Tilt/HFE/change-point checks are unreliable on lossy-encoded files
        # (AAC, MP3, Opus, AMR) because speech-silence variation dominates
        # the spectral tilt signal and produces false positives regardless
        # of threshold. Only reliable on PCM/FLAC with continuous signals.
        codec_is_lossy = ap.codec_enum.is_lossy if ap.codec_enum else False

        f, s = self._check_tilt_consistency(track, ap.duration_seconds, codec_is_lossy)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_change_point(track, ap.duration_seconds, codec_is_lossy)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_hfe_consistency(track, codec_is_lossy)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_rms_consistency(track)
        findings.extend(f); score_components.extend(s)

        # ── Aggregate score ────────────────────────────────────────────────
        if not score_components:
            final_score = 0.0
            confidence  = 0.50
        else:
            weights     = [w for _, w in score_components]
            scores_     = [s for s, _ in score_components]
            total_w     = sum(weights)
            final_score = sum(sc * w for sc, w in zip(scores_, weights)) / total_w
            confidence  = min(0.88, 0.45 + 0.08 * len(score_components))

        # HIGH findings escalate score
        high_count = sum(1 for f in findings if f.severity == Severity.HIGH)
        if high_count >= 1:
            final_score = min(1.0, final_score + 0.10)

        return ModuleScore(
            module     = MODULE_NAME,
            score      = final_score,
            confidence = confidence,
            findings   = findings,
            weight     = DEFAULT_WEIGHT,
            elapsed_s  = round(time.time() - t0, 3),
            metadata   = {
                "n_windows":    track.n_windows,
                "tilt_cv":      round(track.tilt_cv, 5),
                "hfe_cv":       round(track.hfe_cv, 5),
                "rms_cv":       round(track.rms_cv, 5),
                "max_z":        round(track.max_z, 3),
                "change_time_s": round(track.change_time, 2) if track.change_time else None,
            },
        )

    # ── Noise track extraction ────────────────────────────────────────────────

    def _extract_noise_track(self, audio: np.ndarray, sr: int) -> NoiseTrack:
        win_n  = int(WINDOW_S * sr)
        hop_n  = int(HOP_S * sr)
        nperseg = min(NPERSEG, win_n)

        tilts, hfe_vals, rms_vals, times = [], [], [], []

        for start in range(0, len(audio) - win_n, hop_n):
            seg = audio[start:start + win_n]
            f_ax, psd = signal.welch(seg, fs=sr, nperseg=nperseg)

            low_e  = float(psd[(f_ax >= TILT_LOW_LO) & (f_ax <  TILT_LOW_HI)].sum())
            mid_e  = float(psd[(f_ax >= TILT_MID_LO) & (f_ax <  TILT_MID_HI)].sum())
            high_e = float(psd[(f_ax >= HFE_LO)       & (f_ax <= HFE_HI)].sum())

            denom  = mid_e + 1e-20
            tilts.append(low_e / denom)
            hfe_vals.append(high_e / denom)
            rms_vals.append(float(np.sqrt(np.mean(seg ** 2))))
            times.append((start + win_n / 2) / sr)

        tilts    = np.array(tilts,    dtype=np.float64)
        hfe_vals = np.array(hfe_vals, dtype=np.float64)
        rms_vals = np.array(rms_vals, dtype=np.float64)
        times    = np.array(times,    dtype=np.float64)

        def safe_cv(arr: np.ndarray) -> float:
            mean = arr.mean()
            return float(arr.std() / mean) if mean > 1e-12 else 0.0

        tilt_cv = safe_cv(tilts)
        hfe_cv  = safe_cv(hfe_vals)
        rms_cv  = safe_cv(rms_vals)

        # Change-point detection: z-score vs reference period
        ref_mask = times <= REF_SECS
        if ref_mask.sum() < 3:
            ref_mask[:min(3, len(ref_mask))] = True

        ref_mean = float(tilts[ref_mask].mean())
        ref_std  = float(tilts[ref_mask].std()) + 1e-6
        z_scores = np.abs(tilts - ref_mean) / ref_std
        max_z    = float(z_scores.max())

        # Find earliest persistent change-point
        change_time: Optional[float] = None
        for i in range(len(z_scores) - ZCHANGE_PERSIST + 1):
            if all(z_scores[i:i + ZCHANGE_PERSIST] > ZCHANGE_THRESHOLD):
                # Exclude reference period itself
                if times[i] > REF_SECS + WINDOW_S:
                    change_time = float(times[i])
                    break

        return NoiseTrack(
            times       = times,
            tilts       = tilts,
            hfe         = hfe_vals,
            rms         = rms_vals,
            tilt_cv     = tilt_cv,
            hfe_cv      = hfe_cv,
            rms_cv      = rms_cv,
            change_time = change_time,
            max_z       = max_z,
            n_windows   = len(tilts),
        )

    # ── Detection methods ─────────────────────────────────────────────────────

    def _check_tilt_consistency(
        self, track: NoiseTrack, duration_s: float, codec_is_lossy: bool = False
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect significant changes in spectral tilt over the recording.
        High tilt CV = the noise floor character changed substantially.
        Skipped for lossy-encoded files where speech-silence variation
        dominates the tilt signal and produces false positives.
        """
        if codec_is_lossy:
            return [], []  # unreliable on AAC/MP3/Opus/AMR recordings
        findings = []
        scores   = []
        cv = track.tilt_cv

        if cv >= CV_HIGH:
            severity  = Severity.HIGH
            score_val = min(0.85, 0.55 + (cv - CV_HIGH) * 0.5)
            conf      = 0.80
            desc = (
                f"The spectral tilt of the background noise changed dramatically "
                f"throughout the recording (CV: {cv:.3f}, threshold: {CV_HIGH:.3f}). "
                f"This is strong evidence of an environment change — the recording "
                f"appears to have been captured in two significantly different "
                f"acoustic spaces. Indoor recordings have pink-biased noise (high "
                f"tilt); outdoor recordings have flatter noise (low tilt)."
            )
        elif cv >= CV_MEDIUM:
            severity  = Severity.MEDIUM
            score_val = 0.35 + (cv - CV_MEDIUM) * 1.5
            conf      = 0.65
            desc = (
                f"The spectral tilt of the background noise is moderately "
                f"inconsistent (CV: {cv:.3f}). This may indicate a change in "
                f"recording environment, microphone position, or the presence "
                f"of different noise sources at different points in the recording."
            )
        else:
            return findings, scores

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Noise floor spectral inconsistency",
            description = desc,
            severity    = severity,
            confidence  = conf,
            temporal_location = None,   # global finding; change_point adds location
            metadata    = {
                "tilt_cv":    round(cv, 5),
                "tilt_mean":  round(float(track.tilts.mean()), 4),
                "tilt_range": [round(float(track.tilts.min()), 4),
                               round(float(track.tilts.max()), 4)],
            },
        ))
        scores.append((score_val, 1.5))
        return findings, scores

    def _check_change_point(
        self, track: NoiseTrack, duration_s: float, codec_is_lossy: bool = False
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Localise a detected change-point in the noise floor.
        Only raised when a persistent z-score exceedance is found.
        Skipped for lossy-encoded files where the tilt-based signal
        is not a reliable change-point detector.
        """
        if codec_is_lossy:
            return [], []  # tilt change-point unreliable on AAC/MP3/Opus/AMR
        findings = []
        scores   = []

        if track.change_time is None:
            return findings, scores
        if track.max_z < ZCHANGE_THRESHOLD:
            return findings, scores

        ct   = track.change_time
        mz   = track.max_z

        # Score based on how extreme the change is
        score_val = min(0.80, 0.40 + min(mz, 20.0) / 40.0)
        severity  = Severity.HIGH if mz > 5.0 else Severity.MEDIUM
        conf      = min(0.85, 0.60 + min(mz, 10.0) / 50.0)

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Noise floor change-point detected",
            description = (
                f"An abrupt change in the background noise floor was detected at "
                f"{ct:.1f}s (z-score: {mz:.1f}σ relative to the first "
                f"{REF_SECS:.0f}s). The noise spectrum after this point is "
                f"statistically inconsistent with the noise before it. This is "
                f"the acoustic signature of a recording splice — two sessions "
                f"captured in different environments joined at this point."
            ),
            severity          = severity,
            confidence        = conf,
            temporal_location = TemporalLocation(
                max(0.0, ct - WINDOW_S),
                min(duration_s, ct + WINDOW_S),
            ),
            metadata = {
                "change_time_s": round(ct, 2),
                "max_z_score":   round(mz, 3),
                "ref_period_s":  REF_SECS,
            },
        ))
        scores.append((score_val, 2.0))   # Primary signal — higher weight
        return findings, scores

    def _check_hfe_consistency(
        self, track: NoiseTrack, codec_is_lossy: bool = False
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        # Skip HFE check for lossy-encoded recordings — unreliable
        if codec_is_lossy:
            return [], []
        """
        Check high-frequency energy ratio consistency.
        HFE is sensitive to microphone and room-absorption changes.
        """
        findings = []
        scores   = []
        cv = track.hfe_cv

        if cv >= HFE_CV_MEDIUM:
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "High-frequency noise energy inconsistency",
                description = (
                    f"The ratio of high-frequency energy (> 4 kHz) to mid-band "
                    f"energy varies inconsistently (CV: {cv:.3f}). Changes in "
                    f"this ratio indicate different microphones, rooms, or "
                    f"recording conditions at different points in the file."
                ),
                severity          = Severity.LOW,
                confidence        = 0.55,
                temporal_location = None,
                metadata          = {"hfe_cv": round(cv, 5)},
            ))
            scores.append((0.20, 0.5))

        return findings, scores

    def _check_rms_consistency(
        self, track: NoiseTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Check for persistent RMS level shifts (different gain settings).
        Natural level variation from speech content is expected — we only
        flag when the variation is extreme and session-length in scale.
        """
        findings = []
        scores   = []
        cv = track.rms_cv

        if cv >= RMS_CV_MEDIUM:
            # Extra check: is this variation due to speech/silence?
            # Divide into halves — if one half is persistently louder, flag it
            n  = len(track.rms)
            h1 = float(track.rms[:n // 2].mean())
            h2 = float(track.rms[n // 2:].mean())
            ratio = max(h1, h2) / (min(h1, h2) + 1e-10)

            if ratio > 1.5:   # One half is > 50% louder
                findings.append(Finding(
                    module      = MODULE_NAME,
                    title       = "Recording level shift",
                    description = (
                        f"A persistent change in recording level was detected: "
                        f"the first half averages {h1:.4f} RMS vs {h2:.4f} RMS "
                        f"in the second half (ratio: {ratio:.2f}×). "
                        f"This may indicate different gain settings, microphone "
                        f"distances, or recording devices in different parts of "
                        f"the file."
                    ),
                    severity          = Severity.LOW,
                    confidence        = 0.50,
                    temporal_location = None,
                    metadata          = {
                        "h1_rms":    round(h1, 5),
                        "h2_rms":    round(h2, 5),
                        "rms_ratio": round(ratio, 3),
                        "rms_cv":    round(cv, 5),
                    },
                ))
                scores.append((0.20, 0.4))

        return findings, scores
