"""
AudioForensics — Signal Analyser (Phase 1)

Computes signal-level properties directly from WAV samples.
These measurements describe what the audio actually IS,
as opposed to what the container metadata CLAIMS.

The gap between claimed and measured properties is often
where tampering evidence lives — handled in Phase 2 (Metadata module).

All measurements made on the 16kHz mono WAV extracted by AudioExtractor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

log = logging.getLogger("af.ingestion.signal")


@dataclass
class SignalMeasurements:
    """
    All signal-level properties computed from raw samples.
    Stored in AudioProfile after ingestion.
    """
    # Level measurements
    dc_offset:          float           # Mean sample value in [-1.0, 1.0]
    peak_amplitude:     float           # Max absolute sample value in [0.0, 1.0]
    rms_level:          float           # Root mean square amplitude
    crest_factor_db:    float           # Peak / RMS in dB (dynamic range proxy)

    # Clipping
    is_clipped:         bool            # Any sample at or near ±full-scale
    clipping_count:     int             # Number of clipped sample runs
    clipping_ratio:     float           # Fraction of samples that are clipped

    # Silence
    silence_ratio:      float           # Fraction of file below silence threshold
    silence_threshold:  float           # Threshold used (fraction of full scale)
    has_leading_silence: bool           # First 0.1s is silent
    has_trailing_silence:bool           # Last 0.1s is silent

    # Spectral
    effective_max_freq: Optional[float] # Measured bandwidth cutoff in Hz
    spectral_centroid:  float           # Weighted mean frequency in Hz
    sample_rate:        int             # Actual sample rate of the WAV read

    # Dynamic properties
    dynamic_range_db:   float           # Loud vs quiet sections
    is_mono_content:    bool            # True if stereo content is actually mono

    # Segment-level noise floor (for later modules)
    noise_floor_db:     float           # Estimated noise floor in dBFS
    snr_estimate_db:    float           # Rough signal-to-noise estimate


class SignalAnalyser:
    """
    Computes all signal-level measurements from a WAV file.
    Operates on the 16kHz mono WAV produced by AudioExtractor.
    """

    SILENCE_THRESHOLD     = 0.008    # ~-42dBFS — samples below this are silence
    CLIPPING_THRESHOLD    = 0.9998   # ±32766/32767 counts as clipped
    BANDWIDTH_DB_BELOW    = 40       # dB below peak to define effective bandwidth
    NOISE_FLOOR_PERCENTILE = 5       # Bottom 5% of energy = noise floor estimate
    WINDOW_S              = 0.5      # Window size for segment analysis (seconds)

    def analyse(self, wav_path: Path) -> SignalMeasurements:
        """
        Read wav_path and compute all signal measurements.
        Returns SignalMeasurements dataclass.
        """
        wav_path = Path(wav_path)
        sr, raw  = wavfile.read(str(wav_path))

        # Normalise to float64 in [-1.0, 1.0]
        if raw.dtype == np.int16:
            audio = raw.astype(np.float64) / 32767.0
        elif raw.dtype == np.int32:
            audio = raw.astype(np.float64) / 2147483647.0
        elif raw.dtype in (np.float32, np.float64):
            audio = raw.astype(np.float64)
        else:
            audio = raw.astype(np.float64)

        # Flatten to mono if needed
        if audio.ndim > 1:
            is_mono_content = np.allclose(audio[:, 0], audio[:, 1], atol=1e-4)
            audio = audio.mean(axis=1)
        else:
            is_mono_content = True

        n = len(audio)
        if n == 0:
            return self._empty(sr)

        # ── Level measurements ─────────────────────────────────────────────
        dc_offset      = float(audio.mean())
        peak_amplitude = float(np.max(np.abs(audio)))
        rms_level      = float(np.sqrt(np.mean(audio ** 2)))
        crest_db       = (
            20 * np.log10(peak_amplitude / (rms_level + 1e-12))
            if rms_level > 1e-12 else 0.0
        )

        # ── Clipping ───────────────────────────────────────────────────────
        clipped_mask   = np.abs(audio) >= self.CLIPPING_THRESHOLD
        clipping_ratio = float(clipped_mask.mean())
        # Count distinct clipping runs (not just total samples)
        clipping_count = int(np.sum(np.diff(clipped_mask.astype(int)) == 1))
        is_clipped     = clipping_count > 0

        # ── Silence ────────────────────────────────────────────────────────
        silence_mask   = np.abs(audio) < self.SILENCE_THRESHOLD
        silence_ratio  = float(silence_mask.mean())
        boundary       = min(int(0.1 * sr), n // 4)
        has_lead_sil   = bool(silence_mask[:boundary].mean() > 0.8) if boundary else False
        has_trail_sil  = bool(silence_mask[-boundary:].mean() > 0.8) if boundary else False

        # ── Spectral ───────────────────────────────────────────────────────
        effective_bw   = self._effective_bandwidth(audio, sr)
        spec_centroid  = self._spectral_centroid(audio, sr)

        # ── Dynamic range ──────────────────────────────────────────────────
        dynamic_db     = self._dynamic_range(audio, sr)

        # ── Noise floor ────────────────────────────────────────────────────
        noise_floor_db, snr_db = self._noise_floor_estimate(audio, sr)

        return SignalMeasurements(
            dc_offset           = dc_offset,
            peak_amplitude      = peak_amplitude,
            rms_level           = rms_level,
            crest_factor_db     = crest_db,
            is_clipped          = is_clipped,
            clipping_count      = clipping_count,
            clipping_ratio      = clipping_ratio,
            silence_ratio       = silence_ratio,
            silence_threshold   = self.SILENCE_THRESHOLD,
            has_leading_silence = has_lead_sil,
            has_trailing_silence= has_trail_sil,
            effective_max_freq  = effective_bw,
            spectral_centroid   = spec_centroid,
            sample_rate         = sr,
            dynamic_range_db    = dynamic_db,
            is_mono_content     = is_mono_content,
            noise_floor_db      = noise_floor_db,
            snr_estimate_db     = snr_db,
        )

    # ── Private DSP methods ───────────────────────────────────────────────────

    def _effective_bandwidth(self, audio: np.ndarray, sr: int) -> Optional[float]:
        """
        Find the highest frequency with significant energy.
        Uses Welch PSD — robust against transients.
        Returns frequency in Hz, or None if measurement fails.
        """
        try:
            nperseg = min(4096, len(audio) // 4)
            if nperseg < 64:
                return None
            f, psd   = signal.welch(audio, fs=sr, nperseg=nperseg)
            psd_db   = 10 * np.log10(psd + 1e-20)
            peak_db  = psd_db.max()
            active   = f[psd_db > peak_db - self.BANDWIDTH_DB_BELOW]
            if len(active) == 0:
                return None
            return float(active[-1])
        except Exception:
            return None

    def _spectral_centroid(self, audio: np.ndarray, sr: int) -> float:
        """
        Weighted mean frequency — characterises the 'brightness' of the audio.
        Low value = bass-heavy; High value = treble-heavy.
        """
        try:
            nperseg = min(2048, len(audio) // 4)
            if nperseg < 32:
                return 0.0
            f, psd   = signal.welch(audio, fs=sr, nperseg=nperseg)
            total    = psd.sum()
            if total < 1e-20:
                return 0.0
            return float(np.sum(f * psd) / total)
        except Exception:
            return 0.0

    def _dynamic_range(self, audio: np.ndarray, sr: int) -> float:
        """
        Estimate dynamic range by comparing loud vs quiet non-silent windows.
        Returns value in dB.
        """
        try:
            win  = int(self.WINDOW_S * sr)
            if win < 1 or len(audio) < win * 2:
                return 0.0
            n_wins = len(audio) // win
            rms_vals = np.array([
                np.sqrt(np.mean(audio[i*win:(i+1)*win]**2))
                for i in range(n_wins)
            ])
            # Only use non-silent windows
            active = rms_vals[rms_vals > self.SILENCE_THRESHOLD]
            if len(active) < 2:
                return 0.0
            loud  = np.percentile(active, 90)
            quiet = np.percentile(active, 10)
            if quiet < 1e-9:
                return 60.0
            return float(20 * np.log10(loud / quiet))
        except Exception:
            return 0.0

    def _noise_floor_estimate(
        self, audio: np.ndarray, sr: int
    ) -> Tuple[float, float]:
        """
        Estimate noise floor by looking at the quietest non-silent windows.
        Returns (noise_floor_dBFS, snr_estimate_dB).
        """
        try:
            win    = int(self.WINDOW_S * sr)
            if win < 1 or len(audio) < win * 2:
                return -60.0, 20.0
            n_wins = len(audio) // win
            rms_vals = np.array([
                np.sqrt(np.mean(audio[i*win:(i+1)*win]**2))
                for i in range(n_wins)
            ])
            # Noise floor = bottom percentile of all windows
            floor_rms = np.percentile(rms_vals, self.NOISE_FLOOR_PERCENTILE)
            if floor_rms < 1e-12:
                floor_rms = 1e-12
            noise_floor_db = float(20 * np.log10(floor_rms))

            # SNR: compare signal RMS to noise floor
            signal_rms = float(np.sqrt(np.mean(audio**2)))
            if signal_rms < 1e-12:
                return noise_floor_db, 0.0
            snr_db = float(20 * np.log10(signal_rms / floor_rms))
            return noise_floor_db, max(0.0, snr_db)
        except Exception:
            return -60.0, 20.0

    def _empty(self, sr: int) -> SignalMeasurements:
        """Return zeroed measurements for empty/unreadable files."""
        return SignalMeasurements(
            dc_offset=0.0, peak_amplitude=0.0, rms_level=0.0,
            crest_factor_db=0.0, is_clipped=False, clipping_count=0,
            clipping_ratio=0.0, silence_ratio=1.0,
            silence_threshold=self.SILENCE_THRESHOLD,
            has_leading_silence=True, has_trailing_silence=True,
            effective_max_freq=None, spectral_centroid=0.0,
            sample_rate=sr, dynamic_range_db=0.0, is_mono_content=True,
            noise_floor_db=-96.0, snr_estimate_db=0.0,
        )
