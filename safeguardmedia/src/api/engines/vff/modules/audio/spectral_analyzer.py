"""
VideoForensics — Audio Spectral Analyzer
Detects audio splice boundaries through spectral consistency analysis.

Forensic relevance:
    Every recording environment has a characteristic spectral fingerprint:
    room acoustics, background noise, microphone frequency response, and
    ambient sound sources all imprint on the audio spectrum. When clips
    from different environments are spliced together, the spectral profile
    changes abruptly at the boundary.

    We measure four signals:

    1. Spectral centroid abruptness — the "center of gravity" of the
       frequency spectrum is stable in a continuous recording. An abrupt
       jump indicates a change in audio source or environment.

    2. RMS energy consistency — overall loudness should be stable within
       a recording from the same environment. Sudden level jumps indicate
       splice boundaries or inserted segments.

    3. Spectral profile shift — compare the average spectral shape in the
       first half of the video to the second half. A large distance between
       the two profiles indicates two different acoustic environments.

    4. Onset flux abruptness — audio onset events (transients) follow a
       certain pattern within a recording. An extreme outlier onset event
       indicates an edit point.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
from scipy.signal import find_peaks

logger = logging.getLogger("vf.audio.spectral")


@dataclass
class SpectralResult:
    """Results of audio spectral consistency analysis."""

    # Spectral centroid
    centroid_mean: float                 = 0.0
    centroid_std: float                  = 0.0
    centroid_abruptness_ratio: float     = 0.0   # max_jump / mean_jump
    centroid_spike_timestamps: List[float] = field(default_factory=list)

    # RMS energy
    rms_mean: float                      = 0.0
    rms_std: float                       = 0.0
    rms_abruptness_ratio: float          = 0.0
    rms_spike_timestamps: List[float]    = field(default_factory=list)

    # Spectral profile shift (first vs second half)
    spectral_profile_distance: float     = 0.0   # 0=identical, 2=completely different

    # Onset flux
    onset_abruptness_ratio: float        = 0.0
    onset_spike_timestamps: List[float]  = field(default_factory=list)

    # Aggregated
    tampering_probability: float         = 0.0
    findings: List[str]                  = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class AudioSpectralAnalyzer:
    """
    Analyzes audio spectral properties for temporal consistency.
    All analysis is performed on pre-extracted numpy sample arrays.
    """

    # Window / hop sizes
    CENTROID_WINDOW_S = 0.50    # seconds per FFT window
    CENTROID_HOP_S    = 0.10    # hop between windows
    RMS_WINDOW_S      = 0.10
    RMS_HOP_S         = 0.05
    ONSET_HOP         = 512     # samples

    # Spike detection thresholds (standard deviations above mean)
    CENTROID_SPIKE_STD = 3.0
    RMS_SPIKE_STD      = 4.0
    ONSET_SPIKE_STD    = 5.0

    # Abruptness ratio thresholds for scoring
    CENTROID_CLEAN    = 10.0
    CENTROID_SUSPECT  = 30.0
    CENTROID_CERTAIN  = 60.0

    RMS_CLEAN         = 15.0
    RMS_SUSPECT       = 40.0
    RMS_CERTAIN       = 80.0

    PROFILE_CLEAN     = 0.60
    PROFILE_SUSPECT   = 0.85
    PROFILE_CERTAIN   = 1.10

    def analyze(self, samples: np.ndarray, sr: int) -> SpectralResult:
        """
        Analyze spectral consistency of an audio signal.

        Args:
            samples: Mono audio samples, float64, normalized to [-1, 1]
            sr:      Sample rate in Hz
        """
        result = SpectralResult()

        if len(samples) < sr:   # Need at least 1 second
            result.add_finding("Audio too short for spectral analysis")
            return result

        logger.info(f"Spectral analysis — {len(samples)/sr:.2f}s @ {sr}Hz")

        self._analyze_spectral_centroid(samples, sr, result)
        self._analyze_rms_energy(samples, sr, result)
        self._analyze_spectral_profile_shift(samples, sr, result)
        self._analyze_onset_flux(samples, sr, result)

        result.tampering_probability = self._compute_score(result)
        self._generate_findings(result)

        logger.info(
            f"Spectral analysis complete — "
            f"centroid_ratio={result.centroid_abruptness_ratio:.1f} "
            f"rms_ratio={result.rms_abruptness_ratio:.1f} "
            f"profile_dist={result.spectral_profile_distance:.3f} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    def _analyze_spectral_centroid(
        self, samples: np.ndarray, sr: int, result: SpectralResult
    ) -> None:
        """Track spectral centroid over time and detect abrupt jumps."""
        win   = int(self.CENTROID_WINDOW_S * sr)
        hop   = int(self.CENTROID_HOP_S * sr)
        freqs = np.fft.rfftfreq(win, 1 / sr)

        centroids = []
        times     = []

        for i in range(0, len(samples) - win, hop):
            chunk = samples[i:i + win] * np.hanning(win)
            fft   = np.abs(np.fft.rfft(chunk))
            total = fft.sum()
            if total < 1e-9:
                continue
            centroid = float(np.sum(freqs * fft) / total)
            centroids.append(centroid)
            times.append(i / sr)

        if len(centroids) < 4:
            return

        c_arr = np.array(centroids)
        result.centroid_mean = float(c_arr.mean())
        result.centroid_std  = float(c_arr.std())

        d1       = np.abs(np.diff(c_arr))
        mean_d1  = float(d1.mean())
        max_d1   = float(d1.max())

        if mean_d1 < 1e-6:
            return

        result.centroid_abruptness_ratio = max_d1 / mean_d1

        # Flag timestamps where centroid jumps significantly
        thresh = mean_d1 + self.CENTROID_SPIKE_STD * d1.std()
        for i, diff in enumerate(d1):
            if diff > thresh:
                result.centroid_spike_timestamps.append(times[i + 1])

    def _analyze_rms_energy(
        self, samples: np.ndarray, sr: int, result: SpectralResult
    ) -> None:
        """Track RMS energy over time and detect sudden level changes."""
        win = int(self.RMS_WINDOW_S * sr)
        hop = int(self.RMS_HOP_S * sr)

        rms_vals = []
        times    = []

        for i in range(0, len(samples) - win, hop):
            chunk = samples[i:i + win]
            rms   = float(np.sqrt(np.mean(chunk ** 2)))
            rms_vals.append(rms)
            times.append(i / sr)

        if len(rms_vals) < 4:
            return

        r_arr   = np.array(rms_vals)
        result.rms_mean = float(r_arr.mean())
        result.rms_std  = float(r_arr.std())

        d1      = np.abs(np.diff(r_arr))
        mean_d1 = float(d1.mean())

        if mean_d1 < 1e-9:
            return

        # Compute abruptness ratio on INTERIOR only (skip first/last 1.5s).
        # Startup and tail transients legitimately spike on any recording.
        interior_mask = np.array([
            (t > 1.5 and t < (times[-1] - 1.0))
            for t in times[1:]
        ])
        if interior_mask.sum() >= 4:
            interior_d1 = d1[interior_mask]
            interior_mean = float(interior_d1.mean())
            interior_max  = float(interior_d1.max())
            result.rms_abruptness_ratio = (
                interior_max / interior_mean if interior_mean > 1e-9 else 0.0
            )
        else:
            result.rms_abruptness_ratio = 0.0

        max_ts = times[-1] - 1.0 if times else 1e9
        thresh = mean_d1 + self.RMS_SPIKE_STD * d1.std()
        for i, diff in enumerate(d1):
            ts = times[i + 1]
            if diff > thresh and ts > 1.5 and ts < max_ts:
                result.rms_spike_timestamps.append(ts)

    def _analyze_spectral_profile_shift(
        self, samples: np.ndarray, sr: int, result: SpectralResult
    ) -> None:
        """
        Compare average spectral profile of the first vs second half of audio.
        A large distance indicates two different acoustic environments.
        """
        win = int(self.CENTROID_WINDOW_S * sr)
        n   = len(samples)
        mid = n // 2

        def mean_spectrum(segment: np.ndarray) -> np.ndarray:
            spectra = []
            for i in range(0, len(segment) - win, win):
                fft = np.abs(np.fft.rfft(segment[i:i+win] * np.hanning(win)))
                spectra.append(fft)
            if not spectra:
                return np.zeros(win // 2 + 1)
            return np.mean(spectra, axis=0)

        first  = mean_spectrum(samples[:mid])
        second = mean_spectrum(samples[mid:])

        # Normalize to probability distributions before comparing
        sum_f = first.sum()
        sum_s = second.sum()
        if sum_f < 1e-9 or sum_s < 1e-9:
            return

        norm_f = first  / sum_f
        norm_s = second / sum_s

        # L1 distance between normalized spectra
        result.spectral_profile_distance = float(np.sum(np.abs(norm_f - norm_s)))

    def _analyze_onset_flux(
        self, samples: np.ndarray, sr: int, result: SpectralResult
    ) -> None:
        """
        Compute spectral flux onset envelope and detect abrupt onset events.
        Normal audio has smooth onset flux. Splice boundaries produce
        extreme flux spikes where the audio character suddenly changes.
        """
        hop = self.ONSET_HOP
        win = hop * 4

        flux_vals = []
        times     = []
        prev_fft  = None

        for i in range(0, len(samples) - win, hop):
            chunk = samples[i:i + win]
            fft   = np.abs(np.fft.rfft(chunk))
            if prev_fft is not None:
                # Half-wave rectified spectral flux
                flux = float(np.sum(np.maximum(0, fft - prev_fft)))
                flux_vals.append(flux)
                times.append(i / sr)
            prev_fft = fft

        if len(flux_vals) < 4:
            return

        f_arr   = np.array(flux_vals)
        d1      = np.abs(np.diff(f_arr))
        mean_d1 = float(d1.mean())
        max_d1  = float(d1.max())

        if mean_d1 < 1e-9:
            return

        result.onset_abruptness_ratio = max_d1 / mean_d1

        thresh = mean_d1 + self.ONSET_SPIKE_STD * d1.std()
        for i, diff in enumerate(d1):
            ts = times[i + 1]
            if diff > thresh and ts > 0.3:
                if not any(abs(ts - s) < 0.5 for s in result.onset_spike_timestamps):
                    result.onset_spike_timestamps.append(ts)

    def _compute_score(self, result: SpectralResult) -> float:
        """Aggregate spectral signals into 0–1 tampering probability."""
        score = 0.0

        # Spectral centroid abruptness
        ratio = result.centroid_abruptness_ratio
        if ratio >= self.CENTROID_CERTAIN:
            c_score = 1.0
        elif ratio >= self.CENTROID_SUSPECT:
            c_score = 0.50 + 0.50 * (ratio - self.CENTROID_SUSPECT) / \
                      (self.CENTROID_CERTAIN - self.CENTROID_SUSPECT)
        elif ratio >= self.CENTROID_CLEAN:
            c_score = 0.20 + 0.30 * (ratio - self.CENTROID_CLEAN) / \
                      (self.CENTROID_SUSPECT - self.CENTROID_CLEAN)
        else:
            c_score = 0.0
        score = max(score, c_score)

        # RMS energy abruptness
        ratio = result.rms_abruptness_ratio
        if ratio >= self.RMS_CERTAIN:
            r_score = 0.80
        elif ratio >= self.RMS_SUSPECT:
            r_score = 0.40 + 0.40 * (ratio - self.RMS_SUSPECT) / \
                      (self.RMS_CERTAIN - self.RMS_SUSPECT)
        elif ratio >= self.RMS_CLEAN:
            r_score = 0.15
        else:
            r_score = 0.0
        score = max(score, r_score)

        # Spectral profile shift
        dist = result.spectral_profile_distance
        if dist >= self.PROFILE_CERTAIN:
            p_score = 0.70
        elif dist >= self.PROFILE_SUSPECT:
            p_score = 0.40 + 0.30 * (dist - self.PROFILE_SUSPECT) / \
                      (self.PROFILE_CERTAIN - self.PROFILE_SUSPECT)
        elif dist >= self.PROFILE_CLEAN:
            p_score = 0.20
        else:
            p_score = 0.0
        score = max(score, p_score * 0.80)   # Profile shift alone capped at 0.56

        # Corroboration bonus: multiple signals agree
        signals_elevated = sum([
            c_score > 0.30,
            r_score > 0.20,
            p_score > 0.25,
            result.onset_abruptness_ratio > 50,
        ])
        if signals_elevated >= 3:
            score = min(1.0, score + 0.10)

        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: SpectralResult) -> None:
        """Generate human-readable findings."""

        if result.centroid_abruptness_ratio >= self.CENTROID_SUSPECT:
            pos_str = ""
            if result.centroid_spike_timestamps:
                pts = [f"{t:.2f}s" for t in result.centroid_spike_timestamps[:4]]
                pos_str = f" at {', '.join(pts)}"
            result.add_finding(
                f"Abrupt spectral centroid shift detected "
                f"(abruptness ratio={result.centroid_abruptness_ratio:.1f}){pos_str}. "
                f"The spectral center of gravity of a continuous recording is stable. "
                f"A ratio above {self.CENTROID_SUSPECT} indicates a sudden change in "
                f"audio source or acoustic environment, consistent with a splice boundary."
            )

        if result.rms_abruptness_ratio >= self.RMS_SUSPECT:
            pos_str = ""
            if result.rms_spike_timestamps:
                pts = [f"{t:.2f}s" for t in result.rms_spike_timestamps[:4]]
                pos_str = f" at {', '.join(pts)}"
            result.add_finding(
                f"Abrupt RMS energy change detected "
                f"(abruptness ratio={result.rms_abruptness_ratio:.1f}){pos_str}. "
                f"Sudden jumps in audio level beyond the recording's own variance "
                f"indicate different source material was inserted."
            )

        if result.spectral_profile_distance >= self.PROFILE_SUSPECT:
            result.add_finding(
                f"Spectral profile mismatch between first and second half of audio "
                f"(distance={result.spectral_profile_distance:.3f}). "
                f"The frequency balance changed significantly across the recording, "
                f"indicating two different acoustic environments were joined. "
                f"Authentic single-environment recordings have distances below "
                f"{self.PROFILE_CLEAN:.2f}."
            )

        if result.onset_abruptness_ratio > 50 and result.onset_spike_timestamps:
            pts = [f"{t:.2f}s" for t in result.onset_spike_timestamps[:4]]
            result.add_finding(
                f"Anomalous audio onset event detected at {', '.join(pts)} "
                f"(flux abruptness ratio={result.onset_abruptness_ratio:.1f}). "
                f"An extreme outlier in onset flux indicates a sudden change in "
                f"audio character consistent with an edit boundary."
            )
