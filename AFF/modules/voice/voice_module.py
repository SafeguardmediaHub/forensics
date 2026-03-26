"""
AudioForensics — Voice & Speaker Analysis Module (Phase 7)

Every speaker has a characteristic vocal fingerprint — a combination of
fundamental frequency (pitch), vocal tract resonances (formants), and
long-term spectral shape. When a recording is assembled from different
speakers, or when a speaker's segment is substituted, these characteristics
shift at the edit boundary.

This module detects those vocal profile changes through three independent
measurements that together provide high-confidence speaker discontinuity
detection.

Detection signals:

  1. Fundamental Frequency (F0) tracking  [PRIMARY]
     F0 is the rate of vocal fold vibration — it determines perceived pitch.
     Adult male voices: 85–180 Hz. Adult female voices: 165–255 Hz.
     Children: 250–400 Hz. A speaker's F0 varies during speech, but the
     median stays stable over a session (±15%). A step-change in median
     F0 at any point is strong evidence of a different speaker or a
     recording splice.

     Method: 50ms windows, autocorrelation-based F0 estimation with
     voiced/unvoiced discrimination, 1-second sliding median smoothing,
     z-score change-point detection vs 3-second reference period.

  2. Long-Term Average Spectrum (LTAS) distance  [PRIMARY]
     The LTAS is the average power spectral density over a long recording
     segment. It reflects the combined effect of the speaker's vocal tract,
     microphone, and acoustic environment. Comparing the LTAS of the first
     half to the second half detects any sustained change in vocal character.
     Authentic recordings have very low LTAS distance (< 1 dB LSD).
     Speaker changes, room changes, or level shifts produce > 3 dB LSD.

  3. F0 stability (coefficient of variation)  [SECONDARY]
     Within a genuine single-speaker recording, F0 varies but the
     coefficient of variation stays low (< 0.15). When two speakers with
     different pitches are concatenated, the CV of the F0 track rises
     substantially above this baseline.

Thresholds (empirically derived from synthetic test data):
    F0 ratio > 1.4       → HIGH severity (definite F0 step-change)
    F0 max_z > 4.0       → MEDIUM severity (significant F0 drift)
    LTAS LSD > 5.0 dB   → HIGH severity
    LTAS LSD > 3.0 dB   → MEDIUM severity
    F0 CV > 0.15        → supporting evidence
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

log = logging.getLogger("af.modules.voice")

MODULE_NAME    = "voice"
DEFAULT_WEIGHT = 0.10

# ── Analysis parameters ───────────────────────────────────────────────────────
F0_WIN_MS      = 50       # F0 analysis window (ms)
F0_HOP_MS      = 25       # F0 hop size (ms)
F0_MIN_HZ      = 70.0     # Minimum F0 (low bass voice)
F0_MAX_HZ      = 400.0    # Maximum F0 (child voice)
F0_CONF_THRESH = 0.35     # Minimum autocorrelation peak for voiced frame
F0_SMOOTH_S    = 1.0      # Smoothing window (seconds)
REF_SECS       = 3.0      # Reference period

MIN_VOICED_FRAMES = 20    # Minimum frames needed for analysis
MIN_DURATION_S    = 5.0   # Minimum recording length

# Detection thresholds
F0_RATIO_HIGH     = 1.60   # F0 half-ratio above this → HIGH severity  (raised from 1.40)
F0_Z_MEDIUM       = 6.0    # F0 max z-score above this → MEDIUM severity  (raised from 4.0)
F0_CV_MEDIUM      = 0.15   # F0 coefficient of variation → supporting signal
LTAS_LSD_HIGH     = 9.0    # LTAS log-spectral distance → HIGH  (raised from 5.0)
LTAS_LSD_MEDIUM   = 6.0    # LTAS log-spectral distance → MEDIUM  (raised from 3.0)

# LPC formant analysis
LPC_ORDER    = 12   # LPC analysis order (covers first 6 formants)
NPERSEG_LTAS = 2048 # Welch segments for LTAS


@dataclass
class VoiceTrack:
    """Per-window voice analysis results."""
    times:       np.ndarray    # Smoothed F0 window centre times (s)
    f0:          np.ndarray    # Smoothed F0 per window (Hz)
    f0_cv:       float         # F0 coefficient of variation
    f0_h1_median:float         # Median F0 in first half (Hz)
    f0_h2_median:float         # Median F0 in second half (Hz)
    f0_ratio:    float         # max(h1,h2) / min(h1,h2)
    f0_max_z:    float         # Max z-score vs reference period
    f0_change_t: Optional[float]  # Change-point time (s)
    ltas_lsd:    float         # LTAS log-spectral distance (dB)
    n_voiced:    int           # Number of voiced frames used


class VoiceModule(BaseAudioModule):
    """
    Voice & Speaker Analysis module.

    Detects speaker changes and vocal profile inconsistencies via
    F0 tracking, LTAS comparison, and F0 stability analysis.
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
            return self._skipped(f"WAV not found: {wav_path}")

        if ap.duration_seconds < MIN_DURATION_S:
            return self._skipped(
                f"Recording too short ({ap.duration_seconds:.1f}s < {MIN_DURATION_S}s)"
            )

        try:
            sr, raw = wavfile.read(str(wav_path))
            audio   = raw.astype(np.float64) / 32767.0
        except Exception as e:
            return self._skipped(f"Could not read WAV: {e}")

        # ── Extract voice track ────────────────────────────────────────────
        try:
            track = self._extract_voice_track(audio, sr)
        except Exception as e:
            return self._skipped(f"Voice track extraction failed: {e}")

        if track.n_voiced < MIN_VOICED_FRAMES:
            return self._skipped(
                f"Insufficient voiced frames ({track.n_voiced}) — "
                f"recording may be non-speech or too quiet"
            )

        # ── Build findings ─────────────────────────────────────────────────
        findings: List[Finding]               = []
        score_components: List[Tuple[float, float]] = []

        f, s = self._check_f0_change(track, ap.duration_seconds)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_ltas(track)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_f0_stability(track)
        findings.extend(f); score_components.extend(s)

        # ── Aggregate ─────────────────────────────────────────────────────
        if not score_components:
            final_score = 0.0
            confidence  = 0.40
        else:
            weights     = [w for _, w in score_components]
            scores_     = [s for s, _ in score_components]
            total_w     = sum(weights)
            final_score = sum(sc * w for sc, w in zip(scores_, weights)) / total_w
            confidence  = min(0.88, 0.42 + 0.09 * len(score_components))

        return ModuleScore(
            module    = MODULE_NAME,
            score     = final_score,
            confidence= confidence,
            findings  = findings,
            weight    = DEFAULT_WEIGHT,
            elapsed_s = round(time.time() - t0, 3),
            metadata  = {
                "n_voiced_frames":  track.n_voiced,
                "f0_h1_median_hz":  round(track.f0_h1_median, 2),
                "f0_h2_median_hz":  round(track.f0_h2_median, 2),
                "f0_ratio":         round(track.f0_ratio, 4),
                "f0_max_z":         round(track.f0_max_z, 3),
                "f0_change_time_s": round(track.f0_change_t, 2) if track.f0_change_t else None,
                "f0_cv":            round(track.f0_cv, 5),
                "ltas_lsd_db":      round(track.ltas_lsd, 3),
            },
        )

    # ── Voice track extraction ────────────────────────────────────────────────

    def _extract_voice_track(self, audio: np.ndarray, sr: int) -> VoiceTrack:
        """Extract F0 track and LTAS from the recording."""
        win_n   = max(1, int(F0_WIN_MS / 1000 * sr))
        hop_n   = max(1, int(F0_HOP_MS / 1000 * sr))
        lag_min = max(1, int(sr / F0_MAX_HZ))
        lag_max = int(sr / F0_MIN_HZ)

        raw_f0s = []
        raw_ts  = []

        for start in range(0, len(audio) - win_n, hop_n):
            frame    = audio[start:start + win_n]
            f0, conf = self._autocorr_f0(frame, lag_min, lag_max, sr)
            if conf > F0_CONF_THRESH and f0 > 0:
                raw_f0s.append(f0)
                raw_ts.append((start + win_n / 2) / sr)

        n_voiced = len(raw_f0s)

        if n_voiced < MIN_VOICED_FRAMES:
            return self._empty_track(n_voiced)

        raw_f0s = np.array(raw_f0s)
        raw_ts  = np.array(raw_ts)

        # Smooth: 1-second sliding median
        wins_per_smooth = max(2, int(F0_SMOOTH_S / (F0_HOP_MS / 1000)))
        sm_f0, sm_t = [], []
        step = max(1, wins_per_smooth // 2)
        for i in range(0, len(raw_f0s) - wins_per_smooth, step):
            sm_f0.append(float(np.median(raw_f0s[i:i + wins_per_smooth])))
            sm_t.append(float(np.mean(raw_ts[i:i + wins_per_smooth])))

        if len(sm_f0) < 4:
            return self._empty_track(n_voiced)

        sm_f0 = np.array(sm_f0)
        sm_t  = np.array(sm_t)

        # Reference z-score change-point
        ref_mask = sm_t <= REF_SECS
        if ref_mask.sum() < 2:
            ref_mask[:min(2, len(sm_f0))] = True

        ref_mean = float(sm_f0[ref_mask].mean())
        ref_std  = float(sm_f0[ref_mask].std()) + 1.0   # floor 1 Hz
        z_scores = np.abs(sm_f0 - ref_mean) / ref_std
        f0_max_z = float(z_scores.max())

        # Find first persistent change-point
        f0_change_t: Optional[float] = None
        for i in range(len(z_scores) - 1):
            if z_scores[i] > F0_Z_MEDIUM and z_scores[i + 1] > F0_Z_MEDIUM:
                if sm_t[i] > REF_SECS + F0_SMOOTH_S:
                    f0_change_t = float(sm_t[i])
                    break

        # Half statistics
        n     = len(sm_f0)
        h1_med = float(np.median(sm_f0[:n // 2]))
        h2_med = float(np.median(sm_f0[n // 2:]))
        f0_ratio = (max(h1_med, h2_med) / (min(h1_med, h2_med) + 1e-6))
        f0_cv    = float(sm_f0.std() / (sm_f0.mean() + 1e-6))

        # LTAS distance
        ltas_lsd = self._compute_ltas_lsd(audio, sr)

        return VoiceTrack(
            times        = sm_t,
            f0           = sm_f0,
            f0_cv        = f0_cv,
            f0_h1_median = h1_med,
            f0_h2_median = h2_med,
            f0_ratio     = f0_ratio,
            f0_max_z     = f0_max_z,
            f0_change_t  = f0_change_t,
            ltas_lsd     = ltas_lsd,
            n_voiced     = n_voiced,
        )

    @staticmethod
    def _autocorr_f0(
        frame: np.ndarray, lag_min: int, lag_max: int, sr: int
    ) -> Tuple[float, float]:
        """Autocorrelation-based F0 estimation with voiced/unvoiced confidence."""
        n = len(frame)
        if lag_min >= lag_max or n < lag_max:
            return 0.0, 0.0
        r = np.correlate(frame, frame, mode='full')
        r = r[n - 1:]                          # positive lags
        r0 = r[0] + 1e-10
        r = r / r0                             # normalise
        sub_r  = r[lag_min:lag_max]
        if not len(sub_r):
            return 0.0, 0.0
        pk_idx = int(np.argmax(sub_r))
        peak   = float(sub_r[pk_idx])
        f0     = float(sr / (pk_idx + lag_min))
        return f0, peak

    @staticmethod
    def _compute_ltas_lsd(audio: np.ndarray, sr: int) -> float:
        """Log-spectral distance between LTAS of first and second halves."""
        n   = len(audio)
        h1  = audio[:n // 2]
        h2  = audio[n // 2:]
        nperseg = min(NPERSEG_LTAS, len(h1) // 4)
        if nperseg < 32:
            return 0.0
        _, p1 = signal.welch(h1, fs=sr, nperseg=nperseg)
        _, p2 = signal.welch(h2, fs=sr, nperseg=nperseg)
        p1_db = 10 * np.log10(p1 + 1e-20)
        p2_db = 10 * np.log10(p2 + 1e-20)
        return float(np.sqrt(np.mean((p1_db - p2_db) ** 2)))

    @staticmethod
    def _empty_track(n_voiced: int) -> VoiceTrack:
        empty = np.array([])
        return VoiceTrack(
            times=empty, f0=empty, f0_cv=0.0,
            f0_h1_median=0.0, f0_h2_median=0.0,
            f0_ratio=1.0, f0_max_z=0.0, f0_change_t=None,
            ltas_lsd=0.0, n_voiced=n_voiced,
        )

    # ── Detection methods ─────────────────────────────────────────────────────

    def _check_f0_change(
        self, track: VoiceTrack, duration_s: float
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect a significant step-change in fundamental frequency.
        This is the primary speaker-change signal.
        """
        findings = []
        scores   = []

        ratio = track.f0_ratio
        mz    = track.f0_max_z
        ct    = track.f0_change_t

        # Check for either a large half-ratio OR a large z-score
        if ratio < F0_RATIO_HIGH and mz < F0_Z_MEDIUM:
            return findings, scores

        # Determine severity
        if ratio >= F0_RATIO_HIGH:
            severity  = Severity.HIGH
            score_val = min(0.85, 0.60 + (ratio - F0_RATIO_HIGH) * 0.25)
            conf      = 0.88
        else:
            severity  = Severity.MEDIUM
            score_val = 0.40 + min((mz - F0_Z_MEDIUM) / 40.0, 0.25)
            conf      = 0.70

        h1 = track.f0_h1_median
        h2 = track.f0_h2_median

        if ct is not None:
            loc = TemporalLocation(
                max(0.0, ct - 1.0),
                min(duration_s, ct + 1.0),
            )
            ct_str = f" at {ct:.1f}s"
        else:
            loc    = None
            ct_str = ""

        # Determine likely direction (pitch rise vs fall)
        if h2 > h1 * 1.1:
            direction = f"rising from {h1:.0f} Hz to {h2:.0f} Hz"
        elif h1 > h2 * 1.1:
            direction = f"falling from {h1:.0f} Hz to {h2:.0f} Hz"
        else:
            direction = f"shifting (first half: {h1:.0f} Hz, second half: {h2:.0f} Hz)"

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Fundamental frequency change detected",
            description = (
                f"A significant change in fundamental frequency (pitch) was "
                f"detected{ct_str}, {direction}. "
                f"The F0 ratio between recording halves is {ratio:.2f}× "
                f"(max z-score: {mz:.1f}σ). "
                f"A speaker's fundamental frequency is highly stable across a "
                f"single session — a step-change of this magnitude is characteristic "
                f"of a different speaker or a recording splice between two sessions."
            ),
            severity          = severity,
            confidence        = conf,
            temporal_location = loc,
            metadata          = {
                "f0_h1_hz":    round(h1, 2),
                "f0_h2_hz":    round(h2, 2),
                "f0_ratio":    round(ratio, 4),
                "f0_max_z":    round(mz, 3),
                "change_time": round(ct, 2) if ct else None,
            },
        ))
        scores.append((score_val, 2.0))   # Primary signal
        return findings, scores

    def _check_ltas(
        self, track: VoiceTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect a large change in the Long-Term Average Spectrum.
        Reflects changes in speaker vocal tract, microphone, or room.
        """
        findings = []
        scores   = []
        lsd = track.ltas_lsd

        if lsd >= LTAS_LSD_HIGH:
            severity  = Severity.HIGH
            score_val = min(0.75, 0.55 + (lsd - LTAS_LSD_HIGH) / 30.0)
            conf      = 0.75
        elif lsd >= LTAS_LSD_MEDIUM:
            severity  = Severity.MEDIUM
            score_val = 0.35 + (lsd - LTAS_LSD_MEDIUM) / 15.0
            conf      = 0.62
        else:
            return findings, scores

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Vocal spectral profile change detected",
            description = (
                f"The long-term average spectrum (LTAS) differs substantially "
                f"between the first and second halves of the recording "
                f"(log-spectral distance: {lsd:.2f} dB). "
                f"The LTAS captures the combined effect of vocal tract shape, "
                f"microphone response, and room acoustics. A genuine single-session "
                f"recording has very stable LTAS (typically < 1 dB LSD). "
                f"A distance of {lsd:.2f} dB indicates a different speaker, "
                f"microphone, or recording environment between the two halves."
            ),
            severity          = severity,
            confidence        = conf,
            temporal_location = None,
            metadata          = {
                "ltas_lsd_db": round(lsd, 3),
                "threshold_db": LTAS_LSD_MEDIUM,
            },
        ))
        scores.append((score_val, 1.5))
        return findings, scores

    def _check_f0_stability(
        self, track: VoiceTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Check overall F0 coefficient of variation.
        Only raised as a supporting signal when CV is very high
        and no primary finding already explains it.
        """
        findings = []
        scores   = []
        cv = track.f0_cv

        if cv < F0_CV_MEDIUM:
            return findings, scores

        # Suppress if the F0 change finding already covers this
        if track.f0_ratio >= F0_RATIO_HIGH:
            return findings, scores

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Unstable fundamental frequency across recording",
            description = (
                f"The fundamental frequency track shows higher than normal "
                f"variability across the recording (CV: {cv:.3f}, expected < "
                f"{F0_CV_MEDIUM:.2f} for a single speaker). This may indicate "
                f"multiple speakers, significant emotional variation, or "
                f"recording session inconsistencies."
            ),
            severity          = Severity.LOW,
            confidence        = 0.50,
            temporal_location = None,
            metadata          = {
                "f0_cv":       round(cv, 5),
                "f0_mean_hz":  round(float(track.f0.mean()) if len(track.f0) else 0, 2),
            },
        ))
        scores.append((0.18, 0.5))
        return findings, scores
