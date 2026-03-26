"""
VideoForensics — Noise Pattern Analyzer
Analyzes the statistical properties of image noise for forensic signals.

Checks performed:
    1. Local noise statistics consistency (mean, variance, skewness across regions)
    2. Frequency domain analysis (FFT — periodic patterns indicate GAN/synthesis)
    3. Cross-frame noise correlation (scene-independent camera noise)
    4. Noise floor consistency (background noise level should be uniform)

These checks complement PRNU analysis by catching cases where content
from different sources has been composited — the noise textures will be
statistically different even if the visual content looks seamless.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from scipy import ndimage, stats
from scipy.fftpack import fft2, fftshift

logger = logging.getLogger("vf.noise.pattern")


@dataclass
class NoisePatternResult:
    """Results of noise pattern analysis."""

    # Local noise statistics
    local_variance_consistency: float = 0.0   # 0=consistent, 1=highly inconsistent
    local_skewness_consistency: float = 0.0
    noise_floor_consistency: float    = 0.0
    regional_anomalies: int           = 0

    # Frequency domain
    frequency_periodicity_score: float = 0.0  # Periodic FFT peaks → synthetic content
    frequency_anomaly_positions: List[Tuple[float,float]] = field(default_factory=list)

    # Cross-frame noise analysis
    cross_frame_correlation: float    = 0.0   # How correlated is noise across frames
    scene_independent_noise: float    = 0.0   # Noise that doesn't depend on scene content

    # Overall
    tampering_probability: float      = 0.0
    findings: List[str]               = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class NoisePatternAnalyzer:
    """
    Analyzes noise patterns at multiple scales and dimensions.
    Detects compositing, synthesis artifacts, and mixed-source content.
    """

    # Grid divisions for local noise analysis
    GRID_SIZE = 8   # 8×8 = 64 analysis regions per frame

    # Number of frames to analyze
    SAMPLE_FRAMES = 25

    # Frequency periodicity detection threshold
    PERIODICITY_THRESHOLD = 0.35

    def analyze(
        self,
        frame_iterator,
        residual_iterator=None,
    ) -> NoisePatternResult:
        """
        Run full noise pattern analysis.

        Args:
            frame_iterator:   Iterator of (index, np.ndarray BGR) tuples
            residual_iterator: Pre-computed noise residuals (optional)

        Returns:
            NoisePatternResult with all findings
        """
        result = NoisePatternResult()

        frames    = []
        residuals = []

        for item in frame_iterator:
            idx, frame = item if isinstance(item, tuple) else (len(frames), item)
            if frame is None:
                continue
            frames.append(frame)

            # Compute residual for this frame
            gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float64)
            blurred  = ndimage.gaussian_filter(gray, sigma=1.5)
            residual = (gray - blurred).astype(np.float32)
            residuals.append(residual)

        if len(frames) < 3:
            result.add_finding("Insufficient frames for noise pattern analysis")
            return result

        logger.info(f"Noise pattern analysis — {len(frames)} frames")

        # ── 1. Local noise statistics ──────────────────────────────────────
        self._analyze_local_statistics(frames, residuals, result)

        # ── 2. Frequency domain ────────────────────────────────────────────
        self._analyze_frequency_domain(residuals, result)

        # ── 3. Cross-frame noise correlation ──────────────────────────────
        self._analyze_cross_frame_noise(residuals, result)

        # ── 4. Overall score ───────────────────────────────────────────────
        result.tampering_probability = self._compute_probability(result)

        # ── 5. Findings ────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"Noise pattern complete — "
            f"local_var={result.local_variance_consistency:.3f} "
            f"freq_period={result.frequency_periodicity_score:.3f} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    # ── Local Statistics Analysis ─────────────────────────────────────────────

    def _analyze_local_statistics(
        self,
        frames: List[np.ndarray],
        residuals: List[np.ndarray],
        result: NoisePatternResult,
    ) -> None:
        """
        Divide each frame into a grid and compute local noise statistics.
        Consistent statistics across regions indicate a single camera source.
        Significant regional variation suggests compositing.
        """
        all_region_variances  = []
        all_region_skewnesses = []

        for residual in residuals:
            h, w = residual.shape
            bh = h // self.GRID_SIZE
            bw = w // self.GRID_SIZE
            if bh < 8 or bw < 8:
                continue

            frame_variances  = []
            frame_skewnesses = []

            for i in range(self.GRID_SIZE):
                for j in range(self.GRID_SIZE):
                    block = residual[i*bh:(i+1)*bh, j*bw:(j+1)*bw].flatten()
                    if len(block) < 10:
                        continue
                    frame_variances.append(float(np.var(block)))
                    frame_skewnesses.append(float(stats.skew(block)))

            if frame_variances:
                all_region_variances.append(frame_variances)
            if frame_skewnesses:
                all_region_skewnesses.append(frame_skewnesses)

        if not all_region_variances:
            return

        mean_variances = np.mean(all_region_variances, axis=0)

        # Use robust coefficient of variation: only compare regions that have
        # non-trivial noise (variance above 5% of the median).
        # This prevents flat/zero-noise regions from inflating the CV.
        median_var = np.median(mean_variances)
        active_regions = mean_variances[mean_variances > 0.05 * (median_var + 1e-6)]

        if len(active_regions) < 4:
            return  # Not enough active regions for reliable comparison

        cv_variance = float(np.std(active_regions) / (np.mean(active_regions) + 1e-6))

        mean_skewnesses = np.mean(all_region_skewnesses, axis=0)
        cv_skewness     = float(np.std(mean_skewnesses) / (np.mean(np.abs(mean_skewnesses)) + 1e-6))

        # Map CV to 0–1 inconsistency score — raised lower bound to reduce false positives
        result.local_variance_consistency  = min(1.0, max(0.0, (cv_variance - 0.30) / 0.60))
        result.local_skewness_consistency  = min(1.0, max(0.0, (cv_skewness - 0.40) / 0.80))

        # Count significantly anomalous regions
        var_mean = np.mean(mean_variances)
        var_std  = np.std(mean_variances)
        if var_std > 0:
            result.regional_anomalies = int(np.sum(
                np.abs(mean_variances - var_mean) > 2.5 * var_std
            ))

        logger.debug(
            f"Local stats — cv_variance={cv_variance:.3f} "
            f"cv_skew={cv_skewness:.3f} "
            f"anomalous_regions={result.regional_anomalies}"
        )

    # ── Frequency Domain Analysis ─────────────────────────────────────────────

    def _analyze_frequency_domain(
        self,
        residuals: List[np.ndarray],
        result: NoisePatternResult,
    ) -> None:
        """
        Analyze the frequency domain of noise residuals.

        Genuine camera noise has a flat (white noise) power spectrum.
        GAN-generated or synthetically processed content often shows:
        - Periodic peaks at specific frequencies (generator architecture artifacts)
        - Unusual energy concentration in certain frequency bands
        - Grid-like patterns in 2D FFT magnitude (convolution artifacts)

        We compute the mean 2D FFT magnitude and analyze it for
        periodicity using autocorrelation.
        """
        if len(residuals) < 3:
            return

        # Average FFT magnitude across frames to suppress scene content
        fft_magnitudes = []
        for residual in residuals[:self.SAMPLE_FRAMES]:
            fft = fftshift(fft2(residual.astype(np.float64)))
            magnitude = np.log1p(np.abs(fft))
            fft_magnitudes.append(magnitude)

        mean_magnitude = np.mean(fft_magnitudes, axis=0)
        h, w = mean_magnitude.shape
        center_h, center_w = h // 2, w // 2

        # Compute radial power spectrum (1D profile)
        radial_profile = self._radial_profile(mean_magnitude, center_h, center_w)

        # Detect periodicity in the radial profile
        if len(radial_profile) > 20:
            periodicity = self._measure_periodicity(radial_profile)
            result.frequency_periodicity_score = min(1.0, max(0.0, periodicity))

        # Detect anomalous frequency peaks (outliers in the spectrum)
        if len(radial_profile) > 10:
            profile_mean = np.mean(radial_profile)
            profile_std  = np.std(radial_profile)
            if profile_std > 0:
                for i, val in enumerate(radial_profile):
                    if val > profile_mean + 3.5 * profile_std:
                        # Normalize frequency to [0, 1]
                        normalized_freq = i / len(radial_profile)
                        result.frequency_anomaly_positions.append(
                            (normalized_freq, float(val))
                        )

        logger.debug(
            f"Frequency domain — "
            f"periodicity={result.frequency_periodicity_score:.3f} "
            f"anomalous_peaks={len(result.frequency_anomaly_positions)}"
        )

    def _radial_profile(
        self,
        magnitude: np.ndarray,
        cy: int,
        cx: int,
    ) -> np.ndarray:
        """Compute radial average of 2D FFT magnitude from center."""
        y, x  = np.indices(magnitude.shape)
        r     = np.sqrt((x - cx)**2 + (y - cy)**2).astype(int)
        max_r = min(cy, cx)
        profile = np.array([
            magnitude[r == radius].mean() if np.any(r == radius) else 0.0
            for radius in range(max_r)
        ])
        return profile

    def _measure_periodicity(self, profile: np.ndarray) -> float:
        """
        Measure periodicity in a 1D signal using autocorrelation.
        Strong periodicity in the noise frequency spectrum indicates
        a synthetic or processed origin.
        """
        if len(profile) < 10:
            return 0.0

        # Normalize
        p = profile - profile.mean()
        if np.std(p) < 1e-8:
            return 0.0
        p = p / np.std(p)

        # Autocorrelation
        autocorr = np.correlate(p, p, mode='full')
        autocorr = autocorr[len(autocorr)//2:]
        autocorr = autocorr / (autocorr[0] + 1e-8)

        # Only flag LONG-period (low-frequency) periodicity — lags 15–40.
        # This is the GAN/synthesis artifact signature.
        # Short-period periodicity (lags 3–14) is common in CGI and
        # structured synthetic content and is not a reliable tampering signal.
        long_period_start = 15
        long_period_end   = min(41, len(autocorr))

        if long_period_end > long_period_start:
            periodicity = float(np.mean(np.abs(autocorr[long_period_start:long_period_end])))
        else:
            periodicity = 0.0

        return min(1.0, periodicity * 4.0)  # Scale to [0, 1]

    # ── Cross-Frame Noise Analysis ────────────────────────────────────────────

    def _analyze_cross_frame_noise(
        self,
        residuals: List[np.ndarray],
        result: NoisePatternResult,
    ) -> None:
        """
        Analyze how noise correlates across frames.
        The PRNU component of noise is scene-independent and fixed —
        it should produce consistent correlations between residuals
        from the same camera. Different cameras → different correlations.
        """
        if len(residuals) < 4:
            return

        # Compute pairwise correlations between residuals
        # Use a sample to keep computation reasonable
        sample_size = min(10, len(residuals))
        step = max(1, len(residuals) // sample_size)
        sampled = [residuals[i] for i in range(0, len(residuals), step)][:sample_size]

        correlations = []
        for i in range(len(sampled)):
            for j in range(i+1, len(sampled)):
                corr = self._ncc(sampled[i], sampled[j])
                correlations.append(corr)

        if correlations:
            result.cross_frame_correlation = float(np.mean(correlations))
            result.scene_independent_noise = max(0.0, result.cross_frame_correlation)

    # ── Probability & Findings ────────────────────────────────────────────────

    def _compute_probability(self, result: NoisePatternResult) -> float:
        """Aggregate noise pattern signals into tampering probability.
        
        Requires BOTH variance and frequency signals to be elevated before
        scoring high. Either signal alone is unreliable — synthetic/CGI content
        easily triggers variance inconsistency, and many natural video types
        produce non-flat frequency spectra.
        """
        var_score  = result.local_variance_consistency
        freq_score = result.frequency_periodicity_score

        # Only combine if both are meaningfully elevated (corroboration required)
        if var_score > 0.30 and freq_score > 0.30:
            combined = 0.50 * var_score + 0.50 * freq_score
        elif freq_score > 0.50:
            # Strong frequency signal alone — moderate weight
            combined = freq_score * 0.50
        elif var_score > 0.50:
            # Strong variance signal alone — low weight (easily triggered by CGI)
            combined = var_score * 0.25
        else:
            combined = 0.0

        score = (
            0.70 * combined +
            0.20 * result.local_skewness_consistency +
            0.10 * min(1.0, result.regional_anomalies / 5.0)
        )
        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: NoisePatternResult) -> None:
        """Generate human-readable findings."""

        if result.local_variance_consistency > 0.50:
            result.add_finding(
                f"High noise variance inconsistency across frame regions "
                f"(score={result.local_variance_consistency:.3f}). "
                f"Different frame regions show significantly different noise "
                f"variance profiles, suggesting content from different sources "
                f"was composited into the same frame."
            )
        elif result.local_variance_consistency > 0.25:
            result.add_finding(
                f"Moderate noise variance inconsistency "
                f"(score={result.local_variance_consistency:.3f}). "
                f"Some regional noise variance differences detected. "
                f"May indicate partial post-processing or compositing."
            )

        if result.frequency_periodicity_score > self.PERIODICITY_THRESHOLD:
            result.add_finding(
                f"Periodic structure detected in noise frequency spectrum "
                f"(score={result.frequency_periodicity_score:.3f}). "
                f"Natural camera noise has a flat (white) power spectrum. "
                f"Periodic frequency peaks indicate the noise may have been "
                f"generated or processed by software with a regular filter kernel — "
                f"characteristic of GAN-generated content or algorithmic processing."
            )

        if result.regional_anomalies > 3:
            result.add_finding(
                f"{result.regional_anomalies} frame regions show anomalous "
                f"noise statistics. Spatially localized noise anomalies are "
                f"consistent with region-level compositing or content replacement."
            )

        if len(result.frequency_anomaly_positions) > 2:
            freqs = [f"{f[0]:.3f}" for f in result.frequency_anomaly_positions[:3]]
            result.add_finding(
                f"Anomalous frequency peaks at normalized frequencies: "
                f"{', '.join(freqs)}. "
                f"Specific frequency band outliers suggest systematic "
                f"image processing that introduced structured artifacts."
            )

    @staticmethod
    def _ncc(a: np.ndarray, b: np.ndarray) -> float:
        """Normalized cross-correlation."""
        a = a.flatten().astype(np.float64) - a.mean()
        b = b.flatten().astype(np.float64) - b.mean()
        denom = np.sqrt(np.sum(a**2) * np.sum(b**2))
        return float(np.clip(np.sum(a * b) / denom, -1.0, 1.0)) if denom > 1e-12 else 0.0
