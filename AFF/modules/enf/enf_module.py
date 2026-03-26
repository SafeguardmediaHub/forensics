"""
AudioForensics — ENF (Electric Network Frequency) Analysis Module (Phase 3)

Electric mains hum is embedded in nearly all recordings made near electrical
equipment — phones, laptops, and indoor environments. The grid frequency
fluctuates slightly (typically within ±0.2 Hz of nominal) in a pattern that
is unique to each moment in time.

If a recording was assembled from two different sessions, the ENF phase will
be discontinuous at the cut point. This discontinuity is mathematically
detectable even when the splice is inaudible.

This is the most court-admissible audio forensics technique in use today.

Algorithm:
    1. Bandpass filter: 8th-order Butterworth, ±1.0 Hz around nominal (50/60 Hz)
    2. Overlapping 1-second windows with Hann taper
    3. Per-window frequency estimation: zero-padded DFT + parabolic interpolation
       for sub-bin accuracy (~0.002 Hz resolution with 8× zero-padding)
    4. Filter transient removal: drop first and last N frames
    5. Phase track: cumulative integral of frequency deviation from nominal
    6. Phase discontinuity detection: find frames where phase jumps abnormally
       relative to the typical drift rate of the recording
    7. Frequency variance analysis: secondary corroboration
    8. ENF presence scoring: SNR of narrow ENF band vs broad neighbourhood
    9. Harmonic analysis: check 2nd harmonic (100/120 Hz) for corroboration

Grid support:
    50 Hz — Nigeria, Europe, Africa, Asia, Australia (default)
    60 Hz — North America, parts of South America

Detection thresholds (empirically derived from synthetic test data):
    discontinuity_ratio > 4.0 → HIGH severity finding
    discontinuity_ratio > 2.5 → MEDIUM severity finding
    freq_std            > 0.05 → frequency variance anomaly
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

from core.models import (
    AudioCase, BaseAudioModule, ENFGrid, Finding,
    ModuleScore, Severity, TemporalLocation,
)

log = logging.getLogger("af.modules.enf")

MODULE_NAME    = "enf"
DEFAULT_WEIGHT = 0.30   # Highest weight — most forensically robust


# ── Detection thresholds ──────────────────────────────────────────────────────

# Phase discontinuity (ratio of max step to typical drift)
DISC_THRESHOLD_HIGH   = 4.0    # > 4× typical → HIGH severity
DISC_THRESHOLD_MEDIUM = 2.5    # > 2.5× typical → MEDIUM severity

# Frequency standard deviation (Hz) — natural ENF drift is ~0.02 Hz
FREQ_STD_SUSPICIOUS   = 0.05   # > 0.05 Hz std → suspicious
FREQ_STD_HIGH         = 0.10   # > 0.10 Hz std → HIGH severity

# ENF presence: SNR of 0.2Hz narrow band vs 20Hz wide band around nominal
SNR_PRESENT_DB        = -2.0   # SNR above this → ENF is present
SNR_CONFIDENT_DB      =  2.0   # SNR above this → high confidence ENF

# Analysis parameters
WINDOW_S     = 1.0     # 1-second analysis windows
HOP_S        = 0.25    # 0.25-second hop (75% overlap)
NFFT_MULT    = 8       # Zero-padding factor for frequency resolution
BUTTER_ORDER = 8       # Bandpass filter order
TRANSIENT_FRAMES = 2   # Frames to drop at start/end (filter transients)
MIN_FRAMES   = 6       # Minimum usable frames for analysis
BAND_WIDTH   = 1.0     # ±Hz around nominal for bandpass


@dataclass
class ENFTrack:
    """Full ENF analysis result for one recording."""
    times:           np.ndarray   # Frame centre times (seconds)
    frequencies:     np.ndarray   # Estimated frequency per frame (Hz)
    phase:           np.ndarray   # Cumulative phase deviation (radians)
    phase_steps:     np.ndarray   # Absolute per-frame phase increments
    snr_db:          float        # ENF presence SNR (dB)
    enf_present:     bool         # True if ENF reliably detected
    freq_std:        float        # Standard deviation of frequency track (Hz)
    discontinuity:   float        # Max phase step / typical phase step ratio
    jump_time_s:     float        # Time of maximum phase jump (seconds)
    jump_magnitude:  float        # Magnitude of maximum phase jump (radians)
    harmonic_snr_db: float        # 2nd harmonic SNR (corroboration)
    nominal_hz:      float        # Grid nominal frequency (50 or 60 Hz)
    n_frames:        int


class ENFModule(BaseAudioModule):
    """
    Electric Network Frequency (ENF) Analysis.

    Detects edit boundaries via phase discontinuity in the mains hum
    embedded in recordings made near electrical equipment.
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

        # Load audio
        try:
            sr, raw = wavfile.read(str(wav_path))
            audio   = raw.astype(np.float64) / 32767.0
        except Exception as e:
            return self._skipped(f"Could not read WAV: {e}")

        if len(audio) < sr * 3:
            return self._skipped("Recording too short (< 3s) for ENF analysis")

        nominal = ap.enf_grid.nominal   # 50.0 or 60.0

        # ── 1. Extract ENF track ───────────────────────────────────────────
        try:
            track = self._extract_enf_track(audio, sr, nominal)
        except Exception as e:
            return self._skipped(f"ENF extraction failed: {e}")

        if track.n_frames < MIN_FRAMES:
            return self._skipped(
                f"Too few ENF frames ({track.n_frames}) after transient removal"
            )

        # ── 2. Build findings ──────────────────────────────────────────────
        findings: List[Finding] = []
        score_components: List[Tuple[float, float]] = []

        # ENF presence assessment
        f_presence, s_presence = self._assess_presence(track)
        findings.extend(f_presence)
        score_components.extend(s_presence)

        # If ENF is not reliably present, analysis is low-confidence
        # but we still report what we found
        enf_confidence_multiplier = 1.0
        if not track.enf_present:
            enf_confidence_multiplier = 0.35

        # Phase discontinuity
        f_disc, s_disc = self._check_phase_discontinuity(track, ap.duration_seconds)
        findings.extend(f_disc)
        score_components.extend(s_disc)

        # Frequency variance
        f_var, s_var = self._check_frequency_variance(track)
        findings.extend(f_var)
        score_components.extend(s_var)

        # Harmonic corroboration
        f_harm, s_harm = self._check_harmonic(audio, sr, nominal)
        findings.extend(f_harm)
        score_components.extend(s_harm)

        # ── 3. Aggregate score ─────────────────────────────────────────────
        if not score_components:
            final_score = 0.0
            confidence  = 0.35 if not track.enf_present else 0.50
        else:
            weights     = [w for _, w in score_components]
            scores      = [s for s, _ in score_components]
            total_w     = sum(weights)
            final_score = sum(s * w for s, w in zip(scores, weights)) / total_w
            confidence  = min(0.92, 0.50 + 0.08 * len(score_components))
            confidence *= enf_confidence_multiplier

        return ModuleScore(
            module     = MODULE_NAME,
            score      = final_score,
            confidence = confidence,
            findings   = findings,
            weight     = DEFAULT_WEIGHT,
            elapsed_s  = round(time.time() - t0, 3),
            metadata   = {
                "nominal_hz":        nominal,
                "enf_present":       track.enf_present,
                "snr_db":            round(track.snr_db, 2),
                "freq_std_hz":       round(track.freq_std, 5),
                "discontinuity":     round(track.discontinuity, 3),
                "jump_time_s":       round(track.jump_time_s, 2),
                "jump_magnitude_rad":round(track.jump_magnitude, 5),
                "n_frames":          track.n_frames,
                "harmonic_snr_db":   round(track.harmonic_snr_db, 2),
                "grid":              ap.enf_grid.value,
            },
        )

    # ── ENF extraction ────────────────────────────────────────────────────────

    def _extract_enf_track(
        self, audio: np.ndarray, sr: int, nominal: float
    ) -> ENFTrack:
        """
        Extract instantaneous ENF frequency track from audio.

        Method: overlapping windowed DFT with parabolic interpolation.
        Returns ENFTrack with per-frame frequencies, phase, and statistics.
        """
        # Bandpass filter to isolate ENF band
        low  = max(1.0, nominal - BAND_WIDTH)
        high = min(sr / 2 - 1, nominal + BAND_WIDTH)
        sos  = signal.butter(BUTTER_ORDER, [low, high],
                             btype="bandpass", fs=sr, output="sos")
        filtered = signal.sosfilt(sos, audio)

        win_n  = int(WINDOW_S * sr)
        hop_n  = int(HOP_S * sr)
        nfft   = win_n * NFFT_MULT
        hann   = np.hanning(win_n)

        times  = []
        freqs  = []

        for start in range(0, len(filtered) - win_n, hop_n):
            frame    = filtered[start:start + win_n] * hann
            spectrum = np.abs(np.fft.rfft(frame, n=nfft))
            f_axis   = np.fft.rfftfreq(nfft, d=1.0 / sr)

            # Find peak within ENF band
            mask = (f_axis >= low) & (f_axis <= high)
            if not mask.any():
                continue

            sub_spec = spectrum[mask]
            sub_f    = f_axis[mask]
            pk_idx   = int(np.argmax(sub_spec))

            # Parabolic interpolation for sub-bin accuracy
            peak_f = self._parabolic_peak(sub_spec, sub_f, pk_idx)

            times.append((start + win_n / 2) / sr)
            freqs.append(peak_f)

        times = np.array(times)
        freqs = np.array(freqs)

        # Drop filter transient frames
        if len(freqs) > TRANSIENT_FRAMES * 2 + MIN_FRAMES:
            times = times[TRANSIENT_FRAMES:-TRANSIENT_FRAMES]
            freqs = freqs[TRANSIENT_FRAMES:-TRANSIENT_FRAMES]

        if len(freqs) < 2:
            return self._empty_track(nominal)

        # Phase track
        dt          = times[1] - times[0]
        phase       = np.cumsum(freqs - nominal) * dt * 2 * np.pi
        phase_steps = np.abs(np.diff(phase))

        # Discontinuity score
        typical      = float(np.percentile(phase_steps, 75)) if len(phase_steps) else 1e-6
        max_jump     = float(phase_steps.max()) if len(phase_steps) else 0.0
        discontinuity= max_jump / (typical + 1e-6)
        jump_idx     = int(np.argmax(phase_steps)) if len(phase_steps) else 0
        jump_time    = float(times[jump_idx + 1]) if jump_idx + 1 < len(times) else 0.0

        # ENF presence SNR
        snr_db       = self._compute_snr(audio, sr, nominal)
        enf_present  = snr_db > SNR_PRESENT_DB

        # Harmonic SNR
        harmonic_snr = self._compute_snr(audio, sr, nominal * 2)

        return ENFTrack(
            times          = times,
            frequencies    = freqs,
            phase          = phase,
            phase_steps    = phase_steps,
            snr_db         = snr_db,
            enf_present    = enf_present,
            freq_std       = float(freqs.std()),
            discontinuity  = discontinuity,
            jump_time_s    = jump_time,
            jump_magnitude = max_jump,
            harmonic_snr_db= harmonic_snr,
            nominal_hz     = nominal,
            n_frames       = len(freqs),
        )

    @staticmethod
    def _parabolic_peak(
        spectrum: np.ndarray, f_axis: np.ndarray, pk_idx: int
    ) -> float:
        """Sub-bin frequency estimation via parabolic interpolation."""
        if 0 < pk_idx < len(spectrum) - 1:
            a, b, g = spectrum[pk_idx - 1], spectrum[pk_idx], spectrum[pk_idx + 1]
            denom = a - 2 * b + g
            if abs(denom) > 1e-10:
                delta    = 0.5 * (a - g) / denom
                bin_width= f_axis[1] - f_axis[0] if len(f_axis) > 1 else 0.0
                return float(f_axis[pk_idx] + delta * bin_width)
        return float(f_axis[pk_idx])

    @staticmethod
    def _compute_snr(
        audio: np.ndarray, sr: int, nominal: float, band_narrow: float = 0.2
    ) -> float:
        """
        SNR of narrow ENF band vs broad neighbourhood.
        Positive = ENF signal present above noise floor.
        """
        try:
            # Narrow band around nominal
            nl, nh = nominal - band_narrow, nominal + band_narrow
            sos_n  = signal.butter(10, [max(1.0, nl), min(sr/2-1, nh)],
                                   btype="bandpass", fs=sr, output="sos")
            enf_n  = signal.sosfilt(sos_n, audio)

            # Broad neighbourhood (exclude narrow band — noise reference)
            bl, bh = max(1.0, nominal - 10.0), min(sr/2-1, nominal + 10.0)
            sos_b  = signal.butter(6, [bl, bh], btype="bandpass", fs=sr, output="sos")
            enf_b  = signal.sosfilt(sos_b, audio)

            power_narrow = float(np.mean(enf_n ** 2))
            power_broad  = float(np.mean(enf_b ** 2))

            if power_broad < 1e-20:
                return 0.0
            return float(10 * np.log10(power_narrow / power_broad))
        except Exception:
            return 0.0

    @staticmethod
    def _empty_track(nominal: float) -> ENFTrack:
        empty = np.array([])
        return ENFTrack(
            times=empty, frequencies=empty, phase=empty, phase_steps=empty,
            snr_db=0.0, enf_present=False, freq_std=0.0,
            discontinuity=0.0, jump_time_s=0.0, jump_magnitude=0.0,
            harmonic_snr_db=0.0, nominal_hz=nominal, n_frames=0,
        )

    # ── Detection methods ─────────────────────────────────────────────────────

    def _assess_presence(
        self, track: ENFTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """Report whether ENF is present and reliable."""
        findings = []
        scores   = []

        if not track.enf_present:
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "ENF signal absent or below detection threshold",
                description = (
                    f"No reliable Electric Network Frequency signal was detected "
                    f"near {track.nominal_hz:.0f} Hz (SNR: {track.snr_db:.1f} dB). "
                    f"This may indicate the recording was made outdoors, in a "
                    f"battery-powered environment, or that the microphone/recording "
                    f"chain attenuated low frequencies. Phase continuity analysis "
                    f"was performed but carries reduced confidence."
                ),
                severity          = Severity.LOW,
                confidence        = 0.90,
                temporal_location = None,
                metadata          = {
                    "snr_db":     round(track.snr_db, 2),
                    "nominal_hz": track.nominal_hz,
                },
            ))
            # No score contribution — absence is informational

        return findings, scores

    def _check_phase_discontinuity(
        self, track: ENFTrack, duration_s: float
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect abrupt phase jumps in the ENF track.
        A phase jump > DISC_THRESHOLD × typical drift indicates a
        recording assembled from two different sessions.
        """
        findings = []
        scores   = []

        if track.n_frames < MIN_FRAMES or len(track.phase_steps) == 0:
            return findings, scores

        disc = track.discontinuity

        if disc >= DISC_THRESHOLD_HIGH:
            severity  = Severity.HIGH
            score_val = min(0.90, 0.65 + (disc - DISC_THRESHOLD_HIGH) * 0.04)
            conf      = 0.82 if track.enf_present else 0.52
            desc = (
                f"An ENF phase discontinuity of {track.jump_magnitude:.4f} radians "
                f"was detected at {track.jump_time_s:.1f}s into the recording "
                f"({disc:.1f}× the typical drift rate). "
                f"This is strong evidence that the recording was assembled from "
                f"two or more sessions captured at different times. The ENF phase "
                f"is continuous within a single unedited session but will jump "
                f"abruptly at a splice point."
            )
        elif disc >= DISC_THRESHOLD_MEDIUM:
            severity  = Severity.MEDIUM
            score_val = 0.45 + (disc - DISC_THRESHOLD_MEDIUM) * 0.05
            conf      = 0.65 if track.enf_present else 0.38
            desc = (
                f"A moderate ENF phase discontinuity of {track.jump_magnitude:.4f} "
                f"radians was detected at {track.jump_time_s:.1f}s "
                f"({disc:.1f}× the typical drift rate). "
                f"This may indicate an edit point or recording session boundary. "
                f"Further analysis with higher-SNR ENF signal would increase confidence."
            )
        else:
            return findings, scores  # No anomaly

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "ENF phase discontinuity detected",
            description = desc,
            severity    = severity,
            confidence  = conf,
            temporal_location = TemporalLocation(
                max(0.0, track.jump_time_s - 1.0),
                min(duration_s, track.jump_time_s + 1.0),
            ),
            metadata = {
                "discontinuity_ratio": round(disc, 3),
                "jump_magnitude_rad":  round(track.jump_magnitude, 5),
                "jump_time_s":         round(track.jump_time_s, 2),
                "typical_drift_rad":   round(
                    float(np.percentile(track.phase_steps, 75)), 5
                ) if len(track.phase_steps) else 0.0,
            },
        ))
        scores.append((score_val, 2.0))   # Highest weight — primary ENF signal

        return findings, scores

    def _check_frequency_variance(
        self, track: ENFTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect abnormal frequency variance in the ENF track.
        Natural ENF drift is ~0.02 Hz std. Higher variance indicates
        either multiple sessions or environmental contamination.
        Secondary signal — lower weight than phase discontinuity.
        """
        findings = []
        scores   = []

        if track.n_frames < MIN_FRAMES or not track.enf_present:
            return findings, scores

        fstd = track.freq_std

        if fstd >= FREQ_STD_HIGH:
            severity  = Severity.MEDIUM
            score_val = min(0.60, 0.40 + (fstd - FREQ_STD_HIGH) * 2.0)
            desc = (
                f"The ENF frequency track has unusually high variance "
                f"({fstd:.4f} Hz std, expected ~0.02 Hz for genuine single-session "
                f"recordings). This may indicate multiple recording sessions or "
                f"significant environmental interference in the ENF band."
            )
        elif fstd >= FREQ_STD_SUSPICIOUS:
            severity  = Severity.LOW
            score_val = 0.25
            desc = (
                f"Mildly elevated ENF frequency variance detected "
                f"({fstd:.4f} Hz std). This is slightly above the typical "
                f"natural drift range but not conclusive on its own."
            )
        else:
            return findings, scores

        findings.append(Finding(
            module            = MODULE_NAME,
            title             = "ENF frequency variance anomaly",
            description       = desc,
            severity          = severity,
            confidence        = 0.55,
            temporal_location = None,
            metadata          = {
                "freq_std_hz":  round(fstd, 5),
                "expected_hz":  0.02,
            },
        ))
        scores.append((score_val, 0.6))   # Supporting signal

        return findings, scores

    def _check_harmonic(
        self, audio: np.ndarray, sr: int, nominal: float
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Check the 2nd harmonic (100/120 Hz) for corroborating ENF presence.
        When both fundamental and harmonic are present and consistent,
        confidence increases. When they disagree, flag the inconsistency.
        """
        findings = []
        scores   = []

        harmonic   = nominal * 2
        snr_fund   = self._compute_snr(audio, sr, nominal)
        snr_harm   = self._compute_snr(audio, sr, harmonic)

        # Both present and consistent — confidence booster (no tamper finding)
        # Harmonic present but fundamental absent — unusual, mild flag
        if snr_harm > SNR_PRESENT_DB and snr_fund <= SNR_PRESENT_DB:
            findings.append(Finding(
                module      = MODULE_NAME,
                title       = "ENF harmonic present without fundamental",
                description = (
                    f"The 2nd harmonic ({harmonic:.0f} Hz) of the mains frequency "
                    f"is detectable (SNR: {snr_harm:.1f} dB) but the fundamental "
                    f"({nominal:.0f} Hz) is not (SNR: {snr_fund:.1f} dB). "
                    f"This unusual pattern may indicate selective frequency "
                    f"processing or filtering of the original recording."
                ),
                severity          = Severity.LOW,
                confidence        = 0.45,
                temporal_location = None,
                metadata          = {
                    "fundamental_snr": round(snr_fund, 2),
                    "harmonic_snr":    round(snr_harm, 2),
                    "harmonic_hz":     harmonic,
                },
            ))
            scores.append((0.20, 0.3))

        return findings, scores
