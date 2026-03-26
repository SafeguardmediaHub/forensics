"""
VideoForensics — PRNU Extractor
Photo Response Non-Uniformity (PRNU) camera fingerprint analysis.

Every camera sensor has microscopic manufacturing imperfections that cause
individual pixels to respond slightly differently to the same light level.
This produces a unique, stable, spatially fixed noise pattern called PRNU.
It is present in every image the camera captures, invisible to the eye,
but extractable through signal processing.

Forensic applications:
    1. Device verification   — does this video match a known camera?
    2. Internal consistency  — is the fingerprint consistent throughout the video?
       (inconsistency = content from different cameras was composited in)
    3. Regional consistency  — does the fingerprint match across spatial regions?
       (regional mismatch = specific areas were replaced/composited)

Reference: Lukas, Fridrich & Goljan (2006) — the foundational PRNU paper
           Goljan, Fridrich & Filler (2009) — video PRNU methods
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from scipy import ndimage

logger = logging.getLogger("vf.noise.prnu")

# Try skimage for better wavelet denoising — fall back to Gaussian if unavailable
try:
    from skimage.restoration import denoise_wavelet
    import pywt   # Test that PyWavelets is actually available
    _WAVELET_AVAILABLE = True
except (ImportError, Exception):
    _WAVELET_AVAILABLE = False
    # Gaussian high-pass filter fallback is equally valid for forensic PRNU


@dataclass
class PRNUResult:
    """Results of PRNU fingerprint analysis."""

    # Fingerprint quality
    fingerprint_computed: bool        = False
    frames_used: int                  = 0
    fingerprint_quality: float        = 0.0   # 0–1, higher = more reliable

    # Internal consistency (whole-video)
    internal_consistency_score: float = 0.0   # How consistent is fingerprint across video
    consistency_reliable: bool        = False  # Enough frames for reliable estimate

    # Temporal consistency (fingerprint over time)
    temporal_scores: List[float]      = field(default_factory=list)
    temporal_mean: float              = 0.0
    temporal_std: float               = 0.0
    temporal_drops: List[float]       = field(default_factory=list)  # timestamps of drops

    # Spatial consistency (fingerprint across frame regions)
    spatial_consistency_score: float  = 0.0
    spatial_block_scores: List[float] = field(default_factory=list)
    spatial_anomaly_regions: List[Tuple[int,int,int,int]] = field(default_factory=list)

    # Device fingerprint matching (requires reference DB)
    device_match_score: Optional[float] = None
    device_match_name: Optional[str]    = None

    # Overall
    tampering_probability: float      = 0.0
    findings: List[str]               = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class PRNUExtractor:
    """
    Extracts and analyzes the PRNU camera fingerprint from a video.

    Pipeline:
        1. Extract noise residuals from sampled frames using denoising filter
        2. Average residuals to estimate the PRNU fingerprint
        3. Compute temporal consistency — correlate each frame's residual with fingerprint
        4. Compute spatial consistency — correlate regional fingerprints
        5. Flag temporal drops and spatial mismatches as tampering indicators
    """

    # Minimum frames for a usable fingerprint estimate
    MIN_FRAMES_FOR_FINGERPRINT = 30

    # Minimum NCC for internal consistency to be considered clean
    CONSISTENCY_THRESHOLD = 0.08

    # NCC drop threshold — a frame whose correlation falls below this
    # relative to the mean is flagged as potentially inconsistent
    TEMPORAL_DROP_THRESHOLD_SIGMA = 2.5

    # Spatial analysis: divide frame into this many blocks per dimension
    SPATIAL_BLOCKS = 4   # 4×4 = 16 spatial regions

    def analyze(
        self,
        frame_iterator,
        frame_timestamps: Optional[List[float]] = None,
        reference_fingerprint: Optional[np.ndarray] = None,
    ) -> PRNUResult:
        """
        Full PRNU analysis.

        Args:
            frame_iterator:        Iterator of (index, np.ndarray BGR) frame tuples
            frame_timestamps:      Timestamps in seconds for each frame
            reference_fingerprint: Known device fingerprint for device verification
                                   (None = internal consistency only)

        Returns:
            PRNUResult with all consistency scores and findings
        """
        result = PRNUResult()

        # ── Step 1: Extract noise residuals ───────────────────────────────
        residuals, timestamps = self._extract_residuals(
            frame_iterator, frame_timestamps
        )

        result.frames_used = len(residuals)

        if result.frames_used < 5:
            result.add_finding(
                f"Insufficient frames for PRNU analysis "
                f"({result.frames_used} extracted, need {self.MIN_FRAMES_FOR_FINGERPRINT}+)"
            )
            result.consistency_reliable = False
            return result

        # ── Step 2: Estimate PRNU fingerprint ─────────────────────────────
        fingerprint = self._estimate_fingerprint(residuals)
        result.fingerprint_computed = True
        result.fingerprint_quality  = self._assess_fingerprint_quality(
            fingerprint, residuals
        )
        result.consistency_reliable = (
            result.frames_used >= self.MIN_FRAMES_FOR_FINGERPRINT
        )

        logger.debug(
            f"Fingerprint estimated from {result.frames_used} frames "
            f"quality={result.fingerprint_quality:.3f}"
        )

        # ── Step 3: Temporal consistency ──────────────────────────────────
        self._analyze_temporal_consistency(
            residuals, fingerprint, timestamps, result
        )

        # ── Step 4: Spatial consistency ───────────────────────────────────
        self._analyze_spatial_consistency(residuals, result)

        # ── Step 5: Device verification (if reference provided) ───────────
        if reference_fingerprint is not None:
            score = self._compare_to_reference(fingerprint, reference_fingerprint)
            result.device_match_score = score

        # ── Step 6: Overall score ──────────────────────────────────────────
        result.tampering_probability = self._compute_tampering_probability(result)

        # ── Step 7: Findings ───────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"PRNU analysis complete — "
            f"frames={result.frames_used} "
            f"internal_consistency={result.internal_consistency_score:.4f} "
            f"temporal_drops={len(result.temporal_drops)} "
            f"spatial_score={result.spatial_consistency_score:.4f} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    # ── Residual Extraction ───────────────────────────────────────────────────

    def _extract_residuals(
        self,
        frame_iterator,
        timestamps: Optional[List[float]],
    ) -> Tuple[List[np.ndarray], List[float]]:
        """
        Extract noise residuals from frames by removing scene content.
        The residual = original_frame - denoised_frame.
        What remains after removing the scene is primarily sensor noise
        including the PRNU pattern.
        """
        residuals  = []
        used_times = []

        for item in frame_iterator:
            if isinstance(item, tuple):
                idx, frame = item
                ts = timestamps[len(residuals)] if timestamps else float(idx)
            else:
                frame = item
                ts = float(len(residuals))

            if frame is None or frame.size == 0:
                continue

            residual = self._compute_residual(frame)
            if residual is not None:
                residuals.append(residual)
                used_times.append(ts)

        return residuals, used_times

    def _compute_residual(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """
        Compute noise residual for a single frame.
        Uses wavelet denoising if available (better PRNU extraction),
        falls back to Gaussian high-pass filter.
        """
        try:
            # Convert to float grayscale [0, 255]
            if len(frame.shape) == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float64)
            else:
                gray = frame.astype(np.float64)

            if _WAVELET_AVAILABLE:
                # Wavelet denoising: better separates noise from edges
                # sigma_range removes color noise; preserve scene structure
                gray_norm = gray / 255.0
                denoised_norm = denoise_wavelet(
                    gray_norm,
                    method='BayesShrink',
                    mode='soft',
                    wavelet_levels=4,
                    channel_axis=None,
                )
                denoised = denoised_norm * 255.0
            else:
                # Gaussian high-pass filter fallback
                # sigma=1.5 preserves sensor-frequency noise
                denoised = ndimage.gaussian_filter(gray, sigma=1.5)

            residual = gray - denoised
            return residual.astype(np.float32)

        except Exception as e:
            logger.debug(f"Residual extraction failed for frame: {e}")
            return None

    # ── Fingerprint Estimation ────────────────────────────────────────────────

    def _estimate_fingerprint(self, residuals: List[np.ndarray]) -> np.ndarray:
        """
        Estimate PRNU fingerprint by averaging residuals across frames.
        Scene content is random and averages to zero.
        PRNU is fixed and accumulates — it dominates after enough frames.
        Uses Wiener filter normalization for cleaner extraction.
        """
        stack       = np.array(residuals, dtype=np.float64)
        fingerprint = np.mean(stack, axis=0)

        # Wiener filter normalization — standard in PRNU literature
        # Reduces the contribution of textured/edge regions where
        # PRNU estimation is less reliable
        fingerprint = self._wiener_normalize(fingerprint)

        return fingerprint.astype(np.float32)

    def _wiener_normalize(
        self, fingerprint: np.ndarray, block_size: int = 8
    ) -> np.ndarray:
        """
        Wiener normalization: divide fingerprint by local variance estimate.
        This equalizes the fingerprint's amplitude across different image regions.
        Bright, textured regions have higher variance and the PRNU contribution
        needs to be normalized relative to local content.
        """
        fp = fingerprint.copy()
        h, w = fp.shape
        variance_map = np.zeros_like(fp)

        for y in range(0, h, block_size):
            for x in range(0, w, block_size):
                block = fp[y:y+block_size, x:x+block_size]
                local_var = np.var(block)
                variance_map[y:y+block_size, x:x+block_size] = local_var

        # Avoid division by zero
        variance_map = np.maximum(variance_map, 1e-6)
        # Normalize — suppress high-variance regions
        normalized = fp / (variance_map + variance_map.mean())
        return normalized

    def _assess_fingerprint_quality(
        self, fingerprint: np.ndarray, residuals: List[np.ndarray]
    ) -> float:
        """
        Assess how reliable this fingerprint estimate is.
        Quality increases with more frames and higher fingerprint SNR.
        """
        # Quality from frame count
        frame_quality = min(1.0, len(residuals) / self.MIN_FRAMES_FOR_FINGERPRINT)

        # Quality from fingerprint SNR
        fp_std = float(np.std(fingerprint))
        snr_quality = min(1.0, fp_std / 0.5) if fp_std > 0 else 0.0

        return 0.6 * frame_quality + 0.4 * snr_quality

    # ── Temporal Consistency ──────────────────────────────────────────────────

    def _analyze_temporal_consistency(
        self,
        residuals: List[np.ndarray],
        fingerprint: np.ndarray,
        timestamps: List[float],
        result: PRNUResult,
    ) -> None:
        """
        Compute the NCC between the estimated fingerprint and each
        individual frame's noise residual. Frames from the same camera
        should show stable, positive correlations. A sudden drop in NCC
        at a specific timestamp indicates content from a different source
        was inserted at that point.
        """
        scores = []
        for residual in residuals:
            score = self._ncc(fingerprint, residual)
            scores.append(score)

        result.temporal_scores = scores
        result.temporal_mean   = float(np.mean(scores))
        result.temporal_std    = float(np.std(scores)) if len(scores) > 1 else 0.0

        # Internal consistency: mean NCC is the primary measure
        result.internal_consistency_score = result.temporal_mean

        # Detect temporal drops using two complementary criteria:
        # 1. Relative: significantly below the video's own mean (catches within-video drops)
        # 2. Absolute: below a hard minimum — when a spliced segment pulls the mean down,
        #    the relative threshold becomes too lenient and misses the boundary frames
        if len(scores) > 5:
            relative_threshold = (
                result.temporal_mean -
                self.TEMPORAL_DROP_THRESHOLD_SIGMA * (result.temporal_std + 1e-6)
            )
            # Absolute floor: NCC below 30% of the video maximum is suspicious
            max_ncc = max(scores) if scores else 0
            absolute_threshold = max_ncc * 0.30

            effective_threshold = max(relative_threshold, absolute_threshold)

            for i, (score, ts) in enumerate(zip(scores, timestamps)):
                if score < effective_threshold:
                    result.temporal_drops.append(ts)
                    logger.debug(
                        f"PRNU temporal drop at {ts:.2f}s — "
                        f"NCC={score:.4f} (threshold={effective_threshold:.4f}, "
                        f"relative={relative_threshold:.4f}, absolute={absolute_threshold:.4f})"
                    )

        logger.debug(
            f"Temporal consistency — "
            f"mean_NCC={result.temporal_mean:.4f} "
            f"std={result.temporal_std:.4f} "
            f"drops={len(result.temporal_drops)}"
        )

    # ── Spatial Consistency ───────────────────────────────────────────────────

    def _analyze_spatial_consistency(
        self,
        residuals: List[np.ndarray],
        result: PRNUResult,
    ) -> None:
        """
        Divide the frame into a grid of spatial blocks and estimate
        the PRNU fingerprint independently for each block.
        Then measure the cross-correlation between block fingerprints.

        In an unmodified video, all blocks should show the same
        fingerprint (same camera = same PRNU everywhere).
        A block whose fingerprint does not correlate with the rest
        indicates that region was replaced with content from a different source.
        """
        if len(residuals) < 10:
            return

        h, w = residuals[0].shape
        bh   = h // self.SPATIAL_BLOCKS
        bw   = w // self.SPATIAL_BLOCKS

        if bh < 32 or bw < 32:
            return   # Blocks too small for reliable analysis

        # Estimate regional fingerprints
        block_fingerprints = {}
        for bi in range(self.SPATIAL_BLOCKS):
            for bj in range(self.SPATIAL_BLOCKS):
                y0, y1 = bi * bh, (bi+1) * bh
                x0, x1 = bj * bw, (bj+1) * bw

                block_residuals = [r[y0:y1, x0:x1] for r in residuals]
                block_fp = np.mean(np.array(block_residuals), axis=0)
                block_fingerprints[(bi, bj)] = block_fp

        # Compare each block fingerprint to the global fingerprint estimate
        # (reconstructed from block-level mean)
        all_blocks = list(block_fingerprints.values())
        global_fp  = np.mean([
            np.pad(b, ((0, h - b.shape[0]), (0, w - b.shape[1])))
            if b.shape != (h, w) else b
            for b in all_blocks
        ], axis=0)

        block_scores = []
        for (bi, bj), block_fp in block_fingerprints.items():
            # Compare block against average of all other blocks
            others = [
                fp for (i, j), fp in block_fingerprints.items()
                if (i, j) != (bi, bj)
            ]
            if not others:
                continue
            other_mean = np.mean(others, axis=0)

            # Resize for comparison if needed
            if block_fp.shape != other_mean.shape:
                other_mean = cv2.resize(
                    other_mean, (block_fp.shape[1], block_fp.shape[0])
                )

            score = self._ncc(block_fp, other_mean)
            block_scores.append(score)

        result.spatial_block_scores = block_scores

        if block_scores:
            result.spatial_consistency_score = float(np.mean(block_scores))

            # Flag blocks with very low consistency
            block_mean = np.mean(block_scores)
            block_std  = np.std(block_scores) if len(block_scores) > 1 else 0.0
            anomaly_threshold = block_mean - 2.0 * block_std

            for i, score in enumerate(block_scores):
                if score < anomaly_threshold and block_std > 0.001:
                    bi = i // self.SPATIAL_BLOCKS
                    bj = i  % self.SPATIAL_BLOCKS
                    region = (bi * bh, bj * bw, bh, bw)
                    result.spatial_anomaly_regions.append(region)

        logger.debug(
            f"Spatial consistency — "
            f"score={result.spatial_consistency_score:.4f} "
            f"anomaly_regions={len(result.spatial_anomaly_regions)}"
        )

    # ── Device Verification ───────────────────────────────────────────────────

    def _compare_to_reference(
        self,
        video_fingerprint: np.ndarray,
        reference_fingerprint: np.ndarray,
    ) -> float:
        """
        Compare a video's fingerprint against a reference device fingerprint.
        Returns NCC score: ~0.6+ = likely same device, <0.3 = likely different.

        Reference fingerprints would be stored in the device fingerprint
        database built up from verified authentic recordings.
        """
        # Resize reference if dimensions don't match
        if video_fingerprint.shape != reference_fingerprint.shape:
            reference_fingerprint = cv2.resize(
                reference_fingerprint.astype(np.float32),
                (video_fingerprint.shape[1], video_fingerprint.shape[0])
            )
        return self._ncc(video_fingerprint, reference_fingerprint)

    # ── Overall Probability ───────────────────────────────────────────────────

    def _compute_tampering_probability(self, result: PRNUResult) -> float:
        """
        Aggregate PRNU signals into an overall tampering probability.

        Key signals:
        - Low internal consistency → different cameras used in the video
        - Many temporal drops → specific frames from different source
        - Low spatial consistency → specific regions replaced
        """
        score = 0.0

        # Internal consistency contribution (most important signal)
        # NCC mean: genuine video ~0.05–0.30 (synthetic content: near 0)
        # The absolute scale depends on scene content and frame count
        # We use relative comparison: is consistency unusually low?
        if result.consistency_reliable:
            consistency = result.internal_consistency_score
            # Very low consistency (near or below zero) is suspicious
            if consistency < 0.0:
                score = max(score, 0.65)
            elif consistency < 0.02:
                score = max(score, 0.50)
            elif consistency < 0.05:
                score = max(score, 0.30)

        # Temporal drops contribution
        if result.temporal_drops and result.frames_used > 0:
            drop_rate = len(result.temporal_drops) / result.frames_used
            if drop_rate > 0.20:
                score = max(score, 0.70)
            elif drop_rate > 0.10:
                score = max(score, 0.55)
            elif drop_rate > 0.05:
                score = max(score, 0.40)

        # Spatial anomalies contribution
        if result.spatial_anomaly_regions:
            anomaly_rate = (
                len(result.spatial_anomaly_regions) /
                max(len(result.spatial_block_scores), 1)
            )
            if anomaly_rate > 0.30:
                score = max(score, 0.65)
            elif anomaly_rate > 0.15:
                score = max(score, 0.45)

        # High temporal variance (inconsistent NCC) is suspicious
        if result.temporal_std > 0.08 and result.frames_used >= 20:
            score = max(score, 0.45)

        # Device mismatch (if reference available)
        if result.device_match_score is not None:
            if result.device_match_score < 0.30:
                score = max(score, 0.75)
            elif result.device_match_score < 0.50:
                score = max(score, 0.50)

        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: PRNUResult) -> None:
        """Generate human-readable forensic findings."""

        if not result.fingerprint_computed:
            return

        if not result.consistency_reliable:
            result.add_finding(
                f"PRNU fingerprint estimated from only {result.frames_used} frames. "
                f"At least {self.MIN_FRAMES_FOR_FINGERPRINT} frames are needed for a "
                f"reliable fingerprint. Results should be interpreted with caution."
            )

        consistency = result.internal_consistency_score
        if consistency < 0.0:
            result.add_finding(
                f"Negative PRNU internal consistency (NCC={consistency:.4f}). "
                f"This indicates the noise pattern is actively inconsistent across frames, "
                f"strongly suggesting content from multiple different camera sources "
                f"was combined into this video."
            )
        elif consistency < 0.03:
            result.add_finding(
                f"Very low PRNU internal consistency (NCC={consistency:.4f}). "
                f"The camera sensor fingerprint is not stably detectable across frames. "
                f"This may indicate mixed-source content or heavy post-processing "
                f"that destroyed the noise signature."
            )

        if result.temporal_drops:
            positions = [f"{t:.2f}s" for t in result.temporal_drops[:8]]
            result.add_finding(
                f"PRNU temporal drops at {len(result.temporal_drops)} position(s): "
                f"{', '.join(positions)}. "
                f"These frames show noise characteristics inconsistent with the "
                f"rest of the video's sensor fingerprint, suggesting content from "
                f"a different camera was inserted at these timestamps."
            )

        if result.spatial_anomaly_regions:
            result.add_finding(
                f"{len(result.spatial_anomaly_regions)} spatial region(s) show "
                f"PRNU inconsistency with the rest of the frame "
                f"(overall spatial score={result.spatial_consistency_score:.4f}). "
                f"Regions whose sensor fingerprint differs from the rest of the "
                f"frame indicate compositing or content replacement in those areas."
            )

        if result.temporal_std > 0.08 and result.frames_used >= 20:
            result.add_finding(
                f"High temporal variance in PRNU correlation "
                f"(std={result.temporal_std:.4f}, mean={result.temporal_mean:.4f}). "
                f"Genuine single-source recordings show stable NCC over time. "
                f"High variance indicates inconsistent noise patterns across the timeline."
            )

        if result.device_match_score is not None and result.device_match_score < 0.35:
            result.add_finding(
                f"Device fingerprint mismatch — NCC with reference device: "
                f"{result.device_match_score:.4f} (threshold for match: 0.60). "
                f"The video's sensor fingerprint does not match the claimed source device."
            )

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def _ncc(a: np.ndarray, b: np.ndarray) -> float:
        """
        Normalized cross-correlation between two arrays.
        Returns value in [-1, 1]. Identical signals → 1.0.
        """
        a = a.flatten().astype(np.float64)
        b = b.flatten().astype(np.float64)
        a = a - a.mean()
        b = b - b.mean()
        denom = np.sqrt(np.sum(a**2) * np.sum(b**2))
        if denom < 1e-12:
            return 0.0
        return float(np.clip(np.sum(a * b) / denom, -1.0, 1.0))
