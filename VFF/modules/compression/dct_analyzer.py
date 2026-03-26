"""
VideoForensics — DCT Coefficient Analyzer
Detects double compression by analyzing DCT coefficient distributions.

The core forensic principle:
    Single compression → DCT coefficients follow a smooth Laplacian distribution
    Double compression → quantization grid from first encoding leaves
                        periodic artifacts (double quantization effect)
                        visible as statistical irregularities in the
                        coefficient histogram

This is one of the strongest signals for detecting re-encoded or
edited video that has been passed through an encoding process twice.

Reference: Luo et al. (2009), Vazquez-Padin et al. (2012)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from scipy.fftpack import dct
from scipy import stats

logger = logging.getLogger("vf.compression.dct")


@dataclass
class DCTAnalysisResult:
    """Results from DCT coefficient analysis across sampled frames."""
    frames_analyzed: int               = 0
    blocks_analyzed: int               = 0

    # AC coefficient statistics (DC removed — forensic signal is in AC)
    ac_coeff_mean: float               = 0.0
    ac_coeff_std: float                = 0.0
    ac_coeff_kurtosis: float           = 0.0
    ac_coeff_skewness: float           = 0.0

    # Double quantization detection
    double_quantization_score: float   = 0.0  # 0–1, higher = more likely double compressed
    histogram_periodicity: float       = 0.0  # Periodic peaks in coeff histogram
    blocking_artifact_score: float     = 0.0  # 8x8 block boundary artifacts

    # Spatial inconsistency (regions with different compression history)
    spatial_inconsistency_score: float = 0.0
    inconsistent_regions: List[Tuple[int,int]] = field(default_factory=list)

    # Overall
    double_compression_probability: float = 0.0
    findings: List[str]                = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class DCTAnalyzer:
    """
    Analyzes DCT coefficients across video frames to detect
    double compression and regional compression inconsistencies.

    Strategy:
    1. Sample frames evenly across the video
    2. For each frame, extract 8×8 luma blocks
    3. Compute DCT of each block
    4. Analyze AC coefficient distributions for double quantization
    5. Check histogram periodicity (the double-quantization fingerprint)
    6. Detect spatial inconsistencies between frame regions
    """

    BLOCK_SIZE = 8
    # Minimum blocks needed for reliable statistical analysis
    MIN_BLOCKS = 500
    # Number of frames to sample
    SAMPLE_FRAMES = 30
    # Subsample blocks per frame (performance)
    BLOCKS_PER_FRAME = 200

    def analyze_frames(
        self,
        frame_generator,
        total_frames: int,
    ) -> DCTAnalysisResult:
        """
        Analyze DCT coefficients across sampled video frames.

        Args:
            frame_generator: Iterator yielding (frame_index, np.ndarray) BGR frames
            total_frames: Total number of frames in the video

        Returns:
            DCTAnalysisResult with all statistics and findings
        """
        result = DCTAnalysisResult()
        all_ac_coeffs: List[np.ndarray] = []
        frame_stats: List[dict] = []

        logger.info(f"DCT analysis — sampling {self.SAMPLE_FRAMES} frames")

        for frame_idx, frame in frame_generator:
            gray = self._to_gray_float(frame)
            blocks = self._extract_blocks(gray)

            if len(blocks) == 0:
                continue

            # Subsample blocks for performance
            if len(blocks) > self.BLOCKS_PER_FRAME:
                indices = np.random.choice(len(blocks), self.BLOCKS_PER_FRAME, replace=False)
                blocks = [blocks[i] for i in sorted(indices)]

            # Compute DCT for each block
            dct_blocks = [self._compute_block_dct(b) for b in blocks]
            ac_coeffs  = np.array([self._extract_ac(d) for d in dct_blocks])

            all_ac_coeffs.append(ac_coeffs)
            frame_stats.append({
                "frame_idx": frame_idx,
                "mean": float(np.mean(ac_coeffs)),
                "std": float(np.std(ac_coeffs)),
                "kurtosis": float(stats.kurtosis(ac_coeffs.flatten())),
                "blocks": len(blocks),
            })
            result.frames_analyzed += 1
            result.blocks_analyzed += len(blocks)

        if not all_ac_coeffs:
            result.add_finding("Insufficient frame data for DCT analysis")
            return result

        # ── Global AC coefficient statistics ──────────────────────────────
        all_coeffs = np.concatenate([c.flatten() for c in all_ac_coeffs])
        result.ac_coeff_mean     = float(np.mean(all_coeffs))
        result.ac_coeff_std      = float(np.std(all_coeffs))
        result.ac_coeff_kurtosis = float(stats.kurtosis(all_coeffs))
        result.ac_coeff_skewness = float(stats.skew(all_coeffs))

        logger.debug(
            f"AC coefficients — mean={result.ac_coeff_mean:.2f} "
            f"std={result.ac_coeff_std:.2f} "
            f"kurtosis={result.ac_coeff_kurtosis:.2f}"
        )

        # ── Double quantization detection ─────────────────────────────────
        result.histogram_periodicity = self._detect_histogram_periodicity(all_coeffs)
        result.blocking_artifact_score = self._compute_blocking_score(frame_stats)
        result.double_quantization_score = self._compute_double_quant_score(
            result.histogram_periodicity,
            result.ac_coeff_kurtosis,
        )

        # ── Spatial consistency ────────────────────────────────────────────
        if len(frame_stats) >= 3:
            result.spatial_inconsistency_score = self._check_spatial_consistency(frame_stats)

        # ── Overall probability ────────────────────────────────────────────
        result.double_compression_probability = self._aggregate_probability(result)

        # ── Findings ──────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"DCT analysis complete — "
            f"frames={result.frames_analyzed} "
            f"blocks={result.blocks_analyzed} "
            f"double_compress_prob={result.double_compression_probability:.3f}"
        )
        return result

    # ── Core Signal Processing ─────────────────────────────────────────────────

    def _to_gray_float(self, frame: np.ndarray) -> np.ndarray:
        """Convert BGR frame to float32 grayscale [0, 255]."""
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        return gray.astype(np.float32)

    def _extract_blocks(self, gray: np.ndarray) -> List[np.ndarray]:
        """Extract all non-overlapping 8×8 blocks from a grayscale frame."""
        h, w = gray.shape
        blocks = []
        for y in range(0, h - self.BLOCK_SIZE + 1, self.BLOCK_SIZE):
            for x in range(0, w - self.BLOCK_SIZE + 1, self.BLOCK_SIZE):
                block = gray[y:y+self.BLOCK_SIZE, x:x+self.BLOCK_SIZE]
                if block.shape == (self.BLOCK_SIZE, self.BLOCK_SIZE):
                    blocks.append(block)
        return blocks

    def _compute_block_dct(self, block: np.ndarray) -> np.ndarray:
        """Compute 2D DCT of an 8×8 block using separable 1D DCTs."""
        # Level shift: center around zero (standard JPEG/H264 practice)
        shifted = block - 128.0
        # Apply DCT along rows then columns
        return dct(dct(shifted, axis=0, norm='ortho'), axis=1, norm='ortho')

    def _extract_ac(self, dct_block: np.ndarray) -> np.ndarray:
        """
        Extract AC coefficients (everything except DC at [0,0]).
        The forensic signal is entirely in AC — DC carries brightness,
        not compression history.
        """
        flat = dct_block.flatten()
        return flat[1:]   # Skip DC coefficient

    def _detect_histogram_periodicity(self, coeffs: np.ndarray) -> float:
        """
        Detect periodic peaks in the AC coefficient histogram.
        This is the signature of double quantization.

        When a signal is quantized twice (first encoding → decode → second encoding),
        the second quantization grid falls on a pre-quantized signal, creating
        regularly spaced gaps in the coefficient histogram — periodic valleys
        and peaks at multiples of the first quantization step size.

        Returns a periodicity score 0–1. Higher = more periodic = more likely
        double compressed.
        """
        if len(coeffs) < 100:
            return 0.0

        # Use only mid-range coefficients where the effect is strongest
        # Exclude extreme values that skew the histogram
        p5, p95 = np.percentile(coeffs, [5, 95])
        mid_coeffs = coeffs[(coeffs >= p5) & (coeffs <= p95)]

        if len(mid_coeffs) < 50:
            return 0.0

        # Build histogram
        hist, bin_edges = np.histogram(mid_coeffs, bins=64)
        hist_float = hist.astype(np.float64)

        if hist_float.max() == 0:
            return 0.0

        # Normalize
        hist_norm = hist_float / hist_float.max()

        # Look for periodicity using autocorrelation
        # Periodic histogram → periodic autocorrelation peaks
        from scipy.signal import correlate
        autocorr = correlate(hist_norm, hist_norm, mode='full')
        autocorr = autocorr[len(autocorr)//2:]  # Take positive lags only
        autocorr = autocorr / autocorr[0]        # Normalize to [0, 1]

        # Score is the mean of autocorrelation at lags 2–8
        # (typical quantization step sizes produce periodicity at these lags)
        if len(autocorr) > 8:
            periodicity = float(np.mean(np.abs(autocorr[2:9])))
        else:
            periodicity = 0.0

        return min(1.0, max(0.0, periodicity))

    def _compute_blocking_score(self, frame_stats: List[dict]) -> float:
        """
        Estimate blocking artifact severity from frame statistics.
        Double-compressed videos show more pronounced 8×8 block boundaries.
        Uses kurtosis as a proxy — higher kurtosis correlates with blockiness.
        """
        if not frame_stats:
            return 0.0

        kurtoses = [s["kurtosis"] for s in frame_stats if "kurtosis" in s]
        if not kurtoses:
            return 0.0

        mean_kurtosis = np.mean(kurtoses)

        # Typical single-compressed H264: kurtosis ~2–5
        # Double-compressed or heavily quantized: kurtosis >8
        if mean_kurtosis < 2:
            return 0.0
        elif mean_kurtosis < 5:
            return (mean_kurtosis - 2) / 6   # Linear scale 0→0.5
        elif mean_kurtosis < 10:
            return 0.5 + (mean_kurtosis - 5) / 10   # 0.5→1.0
        else:
            return 1.0

    def _compute_double_quant_score(
        self,
        periodicity: float,
        kurtosis: float,
    ) -> float:
        """
        Combine periodicity and kurtosis into a double quantization score.
        These two signals are complementary — both should be elevated
        in genuinely double-compressed video.
        """
        # Normalize kurtosis contribution
        # Single compress: kurtosis ~2–5. Double: often >6
        kurtosis_score = min(1.0, max(0.0, (kurtosis - 2) / 8))

        # Weighted combination
        score = 0.65 * periodicity + 0.35 * kurtosis_score
        return min(1.0, max(0.0, score))

    def _check_spatial_consistency(self, frame_stats: List[dict]) -> float:
        """
        Check whether DCT statistics are consistent across frames.
        High inter-frame variance in compression statistics suggests
        the video was assembled from clips with different compression histories.
        """
        if len(frame_stats) < 3:
            return 0.0

        stds  = [s["std"] for s in frame_stats]
        means = [s["mean"] for s in frame_stats]

        # Coefficient of variation of the per-frame std values
        cv_of_stds = np.std(stds) / (np.mean(stds) + 1e-6)

        # High CV suggests frames have different compression characteristics
        if cv_of_stds < 0.15:
            return 0.0
        elif cv_of_stds < 0.30:
            return (cv_of_stds - 0.15) / 0.15 * 0.4
        else:
            return min(1.0, 0.4 + (cv_of_stds - 0.30) * 2.0)

    def _aggregate_probability(self, result: DCTAnalysisResult) -> float:
        """
        Aggregate all DCT sub-scores into an overall double compression probability.
        """
        score = (
            0.50 * result.double_quantization_score +
            0.25 * result.histogram_periodicity +
            0.15 * result.blocking_artifact_score +
            0.10 * result.spatial_inconsistency_score
        )
        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: DCTAnalysisResult) -> None:
        """Generate human-readable findings from the analysis results."""
        if result.double_compression_probability > 0.70:
            result.add_finding(
                f"Strong double compression signature detected. "
                f"DCT histogram periodicity={result.histogram_periodicity:.3f}, "
                f"double quantization score={result.double_quantization_score:.3f}. "
                f"The video has almost certainly been decoded and re-encoded at least once, "
                f"which is the expected result of editing, splicing, or format conversion."
            )
        elif result.double_compression_probability > 0.45:
            result.add_finding(
                f"Moderate double compression indicators present. "
                f"Histogram periodicity={result.histogram_periodicity:.3f}, "
                f"blocking artifact score={result.blocking_artifact_score:.3f}. "
                f"The video may have been re-encoded or processed after initial recording."
            )

        if result.spatial_inconsistency_score > 0.40:
            result.add_finding(
                f"Spatial DCT inconsistency detected across frames "
                f"(score={result.spatial_inconsistency_score:.3f}). "
                f"Frames show significantly different compression characteristics, "
                f"suggesting the video may be composed of clips from different sources."
            )

        # Only flag kurtosis in the forensically meaningful range.
        # Synthetic, CGI, or screen-recorded content routinely produces
        # kurtosis > 100 due to large flat regions — this is not a
        # tampering indicator. We only flag values in the range 8–80
        # which is anomalous for natural camera footage but not for
        # synthetic content.
        if 8.0 < result.ac_coeff_kurtosis < 80.0:
            result.add_finding(
                f"Elevated AC coefficient kurtosis ({result.ac_coeff_kurtosis:.2f}). "
                f"Values in the 8–80 range for natural video indicate heavy quantization "
                f"or double compression artifacts."
            )
