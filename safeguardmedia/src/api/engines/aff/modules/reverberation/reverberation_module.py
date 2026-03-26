"""
AudioForensics — Reverberation & Room Analysis Module (Phase 6)

Every room has an acoustic fingerprint: a characteristic pattern of early
reflections and reverberation that shapes the sound of any recording made
in that space. When a recording is assembled from sessions captured in
different rooms — or the same room with significant acoustic treatment
changes — the reverberation character changes at the edit point.

This module detects those acoustic environment changes by tracking the
Spectral Flatness Measure (SFM) of the recording over time.

Why SFM works:
  Reverberation diffuses sound energy across frequencies and time.
  A dry, anechoic recording preserves the tonal character of the source —
  peaks at harmonics, troughs between them — producing a LOW (negative dB)
  spectral flatness. A reverberant recording smears energy more evenly
  across the spectrum, producing a HIGHER (less negative) spectral flatness.

  When the room changes mid-recording, the SFM shifts — typically abruptly
  at the edit boundary. This shift is detectable even through speech content,
  because the spectral flatness of the COMBINATION of source + room noise
  reflects both.

Detection signals:

  1. SFM z-score change-point
     Compare each 1-second window to the mean and std of the first 3 seconds.
     When z-score exceeds a threshold for 2+ consecutive windows after the
     reference period, a change-point is declared.

  2. SFM coefficient of variation
     Low CV = stable acoustic environment.
     High CV = multiple acoustic environments present.

  3. SFM half-difference
     Compares mean SFM in the first half to the second half.
     A large difference indicates a sustained change, not a transient event.

  4. Sub-bass energy ratio (50–200 Hz)
     Room resonances and HVAC noise create characteristic sub-bass patterns.
     When this ratio differs substantially between the recording halves,
     it corroborates an acoustic environment change.

Thresholds (empirically derived from synthetic test data):
    max_z > 4.0          → MEDIUM finding
    max_z > 8.0          → HIGH finding
    half_diff > 0.30 dB  → corroborating evidence
    sub_bass_ratio > 3.0 → additional corroboration
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

from core.models import (
    AudioCase, BaseAudioModule, Finding,
    ModuleScore, Severity, TemporalLocation,
)

log = logging.getLogger("af.modules.reverberation")

MODULE_NAME    = "reverberation"
DEFAULT_WEIGHT = 0.12

# ── Analysis parameters ───────────────────────────────────────────────────────
WINDOW_S       = 1.0    # Analysis window length
HOP_S          = 0.5    # Hop size (50% overlap)
REF_SECS       = 3.0    # Reference period at start of recording
NPERSEG        = 512    # Welch PSD segments per window
MIN_WINDOWS    = 6      # Minimum usable windows
MIN_DURATION_S = 5.0    # Minimum recording length

# SFM change-point thresholds
ZCHANGE_MEDIUM    = 4.0    # z-score for MEDIUM finding
ZCHANGE_HIGH      = 8.0    # z-score for HIGH finding
ZCHANGE_PERSIST   = 2      # consecutive windows required
HALF_DIFF_MEDIUM  = 0.30   # SFM half-difference (dB) for corroboration
CV_MEDIUM         = 0.015  # SFM CV threshold  (raised from 0.006 for real recordings)

# Sub-bass ratio
SUBBASS_RATIO_MEDIUM = 3.0   # >3x change between halves
SUBBASS_LO = 50.0
SUBBASS_HI = 200.0
MID_LO     = 200.0
MID_HI     = 2000.0


@dataclass
class ReverbTrack:
    """Per-window reverberation analysis results."""
    times:       np.ndarray    # Window centre times (s)
    sfm:         np.ndarray    # Spectral flatness measure per window (dB)
    sfm_cv:      float         # Coefficient of variation of SFM
    sfm_half_diff: float       # |first_half_mean - second_half_mean| (dB)
    max_z:       float         # Max z-score vs reference period
    change_time: Optional[float]  # Detected change-point time (s)
    sub_bass_ratio: float      # Sub-bass energy ratio between halves
    n_windows:   int


class ReverberationModule(BaseAudioModule):
    """
    Reverberation & Room Analysis module.

    Detects acoustic environment changes via Spectral Flatness Measure
    tracking and z-score change-point detection.
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

        # ── Extract reverberation track ────────────────────────────────────
        try:
            track = self._extract_reverb_track(audio, sr)
        except Exception as e:
            return self._skipped(f"Reverb track extraction failed: {e}")

        if track.n_windows < MIN_WINDOWS:
            return self._skipped(
                f"Too few analysis windows ({track.n_windows}) for reverb analysis"
            )

        # ── Build findings ─────────────────────────────────────────────────
        findings: List[Finding]               = []
        score_components: List[Tuple[float, float]] = []

        f, s = self._check_sfm_change_point(track, ap.duration_seconds)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_sfm_consistency(track)
        findings.extend(f); score_components.extend(s)

        f, s = self._check_sub_bass(track)
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
            confidence  = min(0.85, 0.40 + 0.09 * len(score_components))

        return ModuleScore(
            module     = MODULE_NAME,
            score      = final_score,
            confidence = confidence,
            findings   = findings,
            weight     = DEFAULT_WEIGHT,
            elapsed_s  = round(time.time() - t0, 3),
            metadata   = {
                "n_windows":       track.n_windows,
                "sfm_cv":          round(track.sfm_cv, 6),
                "sfm_half_diff":   round(track.sfm_half_diff, 4),
                "max_z":           round(track.max_z, 3),
                "change_time_s":   round(track.change_time, 2) if track.change_time else None,
                "sub_bass_ratio":  round(track.sub_bass_ratio, 3),
            },
        )

    # ── Track extraction ──────────────────────────────────────────────────────

    def _extract_reverb_track(self, audio: np.ndarray, sr: int) -> ReverbTrack:
        win_n  = int(WINDOW_S * sr)
        hop_n  = int(HOP_S * sr)
        nperseg = min(NPERSEG, win_n)

        sfms  = []
        times = []

        for start in range(0, len(audio) - win_n, hop_n):
            seg  = audio[start:start + win_n]
            f_ax, psd = signal.welch(seg, fs=sr, nperseg=nperseg)
            psd  = psd + 1e-20

            # Spectral Flatness Measure: geometric mean / arithmetic mean (in dB)
            log_mean = float(np.mean(np.log(psd)))
            arith_mean = float(np.mean(psd))
            sfm  = 10.0 * np.log10(np.exp(log_mean) / arith_mean)

            sfms.append(sfm)
            times.append((start + win_n / 2) / sr)

        sfms  = np.array(sfms,  dtype=np.float64)
        times = np.array(times, dtype=np.float64)

        # Reference period z-scores
        ref_mask = times <= REF_SECS
        if ref_mask.sum() < 3:
            ref_mask[:min(3, len(sfms))] = True

        ref_mean = float(sfms[ref_mask].mean())
        ref_std  = float(sfms[ref_mask].std()) + 1e-6
        z_scores = np.abs(sfms - ref_mean) / ref_std
        max_z    = float(z_scores.max())

        # Change-point: first persistent exceedance after reference period
        change_time: Optional[float] = None
        for i in range(len(z_scores) - ZCHANGE_PERSIST + 1):
            if all(z_scores[i:i + ZCHANGE_PERSIST] > ZCHANGE_MEDIUM):
                if times[i] > REF_SECS + WINDOW_S:
                    change_time = float(times[i])
                    break

        # Global stats
        mean_sfm = float(sfms.mean())
        sfm_cv   = float(sfms.std() / abs(mean_sfm)) if abs(mean_sfm) > 1e-10 else 0.0
        n        = len(sfms)
        half_diff = abs(float(sfms[:n // 2].mean()) - float(sfms[n // 2:].mean()))

        # Sub-bass energy ratio between halves
        sub_bass_ratio = self._compute_sub_bass_ratio(audio, sr)

        return ReverbTrack(
            times          = times,
            sfm            = sfms,
            sfm_cv         = sfm_cv,
            sfm_half_diff  = half_diff,
            max_z          = max_z,
            change_time    = change_time,
            sub_bass_ratio = sub_bass_ratio,
            n_windows      = len(sfms),
        )

    @staticmethod
    def _compute_sub_bass_ratio(audio: np.ndarray, sr: int) -> float:
        """
        Compare sub-bass energy (50–200 Hz) vs mid energy (200–2000 Hz)
        in first half vs second half of the recording.
        Returns the ratio of the two halves' sub-bass/mid ratios.
        Large ratio = sub-bass character changed between halves.
        """
        n = len(audio)
        half_a = audio[:n // 2]
        half_b = audio[n // 2:]

        def sbr(seg: np.ndarray) -> float:
            f_ax, psd = signal.welch(seg, fs=sr, nperseg=min(2048, len(seg) // 2))
            low_e = float(psd[(f_ax >= SUBBASS_LO) & (f_ax < SUBBASS_HI)].sum())
            mid_e = float(psd[(f_ax >= MID_LO)     & (f_ax < MID_HI)].sum())
            return low_e / (mid_e + 1e-20)

        r_a = sbr(half_a)
        r_b = sbr(half_b)
        return float(max(r_a, r_b) / (min(r_a, r_b) + 1e-10))

    # ── Detection methods ─────────────────────────────────────────────────────

    def _check_sfm_change_point(
        self, track: ReverbTrack, duration_s: float
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Detect a statistically significant shift in spectral flatness.
        The primary signal for acoustic environment changes.
        """
        findings = []
        scores   = []

        mz = track.max_z
        if mz < ZCHANGE_MEDIUM:
            return findings, scores

        ct = track.change_time
        hd = track.sfm_half_diff

        # Corroboration: half-difference confirms sustained change
        corroborated = hd >= HALF_DIFF_MEDIUM

        if mz >= ZCHANGE_HIGH:
            severity  = Severity.HIGH
            score_val = min(0.80, 0.55 + min(mz - ZCHANGE_HIGH, 20.0) / 60.0)
            conf      = 0.82 if corroborated else 0.68
        else:
            severity  = Severity.MEDIUM
            score_val = 0.35 + (mz - ZCHANGE_MEDIUM) / 20.0
            conf      = 0.65 if corroborated else 0.52

        if ct is not None:
            loc = TemporalLocation(
                max(0.0, ct - WINDOW_S),
                min(duration_s, ct + WINDOW_S),
            )
            location_str = f" at {ct:.1f}s"
        else:
            loc = None
            location_str = ""

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Acoustic environment change detected",
            description = (
                f"The spectral flatness of the recording changed significantly"
                f"{location_str} "
                f"(z-score: {mz:.1f}σ relative to the first {REF_SECS:.0f}s). "
                f"Spectral flatness reflects how diffuse vs tonal the acoustic "
                f"environment is — reverberant rooms produce flatter spectra than "
                f"dry or anechoic spaces. A sustained shift in this measure "
                f"indicates the recording was captured in two different acoustic "
                f"environments"
                + (f" (SFM half-difference: {hd:.3f} dB corroborates a sustained change)." if corroborated else ".")
            ),
            severity          = severity,
            confidence        = conf,
            temporal_location = loc,
            metadata          = {
                "max_z_score":     round(mz, 3),
                "change_time_s":   round(ct, 2) if ct else None,
                "sfm_half_diff":   round(hd, 4),
                "ref_period_s":    REF_SECS,
                "corroborated":    corroborated,
            },
        ))
        scores.append((score_val, 2.0))   # Primary signal
        return findings, scores

    def _check_sfm_consistency(
        self, track: ReverbTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Global SFM coefficient of variation check.
        Stable recordings have consistently low CV; mixed-environment
        recordings have elevated CV.
        """
        findings = []
        scores   = []
        cv = track.sfm_cv

        if cv < CV_MEDIUM:
            return findings, scores

        # Only raise if NOT already explained by a strong change-point finding
        # (avoid double-counting)
        if track.max_z >= ZCHANGE_HIGH:
            return findings, scores

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Spectral flatness inconsistency across recording",
            description = (
                f"The spectral flatness varies more than expected across the "
                f"recording (CV: {cv:.5f}). Authentic single-session recordings "
                f"in a consistent acoustic environment have stable spectral "
                f"flatness. Elevated variability may indicate subtle acoustic "
                f"environment changes."
            ),
            severity          = Severity.LOW,
            confidence        = 0.50,
            temporal_location = None,
            metadata          = {"sfm_cv": round(cv, 6)},
        ))
        scores.append((0.18, 0.5))
        return findings, scores

    def _check_sub_bass(
        self, track: ReverbTrack
    ) -> Tuple[List[Finding], List[Tuple[float, float]]]:
        """
        Sub-bass energy ratio change between recording halves.
        Room resonances, HVAC, and outdoor noise create characteristic
        sub-bass signatures. Large change = different acoustic environment.
        Only raised as corroborating evidence alongside other findings.
        """
        findings = []
        scores   = []
        sbr = track.sub_bass_ratio

        if sbr < SUBBASS_RATIO_MEDIUM:
            return findings, scores

        # Only corroborate — don't raise alone
        has_primary = track.max_z >= ZCHANGE_MEDIUM
        if not has_primary:
            return findings, scores

        findings.append(Finding(
            module      = MODULE_NAME,
            title       = "Sub-bass acoustic character change",
            description = (
                f"The sub-bass frequency content (50–200 Hz) differs by "
                f"{sbr:.1f}× between the first and second halves of the "
                f"recording. This band carries room resonances and environmental "
                f"noise signatures. A large change corroborates an acoustic "
                f"environment change already detected in the spectral flatness."
            ),
            severity          = Severity.LOW,
            confidence        = 0.55,
            temporal_location = None,
            metadata          = {
                "sub_bass_ratio": round(sbr, 3),
                "threshold":      SUBBASS_RATIO_MEDIUM,
            },
        ))
        scores.append((0.20, 0.4))
        return findings, scores
