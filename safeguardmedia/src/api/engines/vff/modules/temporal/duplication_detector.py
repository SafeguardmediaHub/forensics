"""
VideoForensics — Frame Duplication Detector
Detects frozen frames, duplicated frames, and looping sequences.

Forensic relevance:
    Frozen frames — a segment of video where frames are identical
    indicates: video was paused/frozen during editing, frames were
    dropped and replaced with copies, or the video was artificially
    extended by repeating a single frame.

    Near-duplicate frames — frames that are very similar but not
    identical may indicate slow-motion effect applied after the fact,
    or a very low-motion clip inserted to pad duration.

    Periodic repetition — the same sequence of frames appearing
    multiple times indicates looping, which is a common manipulation
    to make a short clip appear longer.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("vf.temporal.duplication")


@dataclass
class DuplicationResult:
    """Results of frame duplication analysis."""
    total_frames_analyzed: int          = 0

    # Frozen frames (MAD ≈ 0)
    frozen_frame_count: int             = 0
    frozen_frame_rate: float            = 0.0
    frozen_segments: List[Tuple[float,float]] = field(default_factory=list)  # (start_s, end_s)

    # Near-duplicate frames (MAD very low but > 0)
    near_duplicate_count: int           = 0
    near_duplicate_rate: float          = 0.0

    # Exact hash duplicates (same frame appearing non-consecutively)
    hash_duplicate_count: int           = 0
    hash_duplicate_positions: List[Tuple[int,int]] = field(default_factory=list)

    # Overall
    duplication_score: float            = 0.0
    findings: List[str]                 = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class FrameDuplicationDetector:
    """
    Detects frozen, duplicated, and looped frames using:
    1. Mean Absolute Difference (MAD) between consecutive frames
    2. Perceptual hash comparison for non-consecutive duplicates
    """

    # MAD below this = frames are visually identical (frozen)
    FROZEN_THRESHOLD = 0.30

    # MAD below this = frames are very similar (near-duplicate)
    NEAR_DUPLICATE_THRESHOLD = 0.80

    # Minimum frozen segment length (frames) worth reporting
    MIN_FROZEN_SEGMENT = 3

    def analyze(
        self,
        frames: List[np.ndarray],
        timestamps: List[float],
        fps: float = 30.0,
    ) -> DuplicationResult:
        """
        Analyze a sequence of frames for duplication patterns.

        Args:
            frames:     List of BGR numpy frame arrays
            timestamps: Timestamp in seconds for each frame
            fps:        Video frame rate (for duration calculations)
        """
        result = DuplicationResult()
        result.total_frames_analyzed = len(frames)

        if len(frames) < 3:
            result.add_finding("Insufficient frames for duplication analysis")
            return result

        # ── Convert to grayscale for analysis ─────────────────────────────
        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]

        # ── 1. MAD-based consecutive analysis ─────────────────────────────
        mads = self._compute_mads(grays)
        self._analyze_frozen_frames(mads, timestamps, result)
        self._analyze_near_duplicates(mads, result)

        # ── 2. Hash-based non-consecutive duplicate detection ──────────────
        self._detect_hash_duplicates(grays, timestamps, result)

        # ── 3. Overall score ───────────────────────────────────────────────
        result.duplication_score = self._compute_score(result)

        # ── 4. Findings ────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"Duplication analysis — "
            f"frozen={result.frozen_frame_count} ({result.frozen_frame_rate:.1%}) "
            f"near_dup={result.near_duplicate_count} "
            f"hash_dup={result.hash_duplicate_count} "
            f"score={result.duplication_score:.3f}"
        )
        return result

    def _compute_mads(self, grays: List[np.ndarray]) -> np.ndarray:
        """Compute Mean Absolute Difference between consecutive frames."""
        mads = []
        for i in range(1, len(grays)):
            mad = float(cv2.absdiff(grays[i-1], grays[i]).mean())
            mads.append(mad)
        return np.array(mads)

    def _analyze_frozen_frames(
        self,
        mads: np.ndarray,
        timestamps: List[float],
        result: DuplicationResult,
    ) -> None:
        """Find consecutive frozen frames (MAD ≈ 0)."""
        frozen_mask = mads < self.FROZEN_THRESHOLD
        result.frozen_frame_count = int(np.sum(frozen_mask))
        result.frozen_frame_rate  = result.frozen_frame_count / max(len(mads), 1)

        # Find frozen segments (runs of consecutive frozen frames)
        in_segment = False
        seg_start  = 0
        for i, frozen in enumerate(frozen_mask):
            if frozen and not in_segment:
                in_segment = True
                seg_start  = i
            elif not frozen and in_segment:
                in_segment = False
                length = i - seg_start
                if length >= self.MIN_FROZEN_SEGMENT:
                    ts_start = timestamps[seg_start] if seg_start < len(timestamps) else 0.0
                    ts_end   = timestamps[i] if i < len(timestamps) else 0.0
                    result.frozen_segments.append((ts_start, ts_end))

        if in_segment:
            length = len(frozen_mask) - seg_start
            if length >= self.MIN_FROZEN_SEGMENT:
                ts_start = timestamps[seg_start] if seg_start < len(timestamps) else 0.0
                ts_end   = timestamps[-1] if timestamps else 0.0
                result.frozen_segments.append((ts_start, ts_end))

    def _analyze_near_duplicates(
        self,
        mads: np.ndarray,
        result: DuplicationResult,
    ) -> None:
        """Find near-duplicate frames (very similar but not identical)."""
        near_dup_mask = (mads >= self.FROZEN_THRESHOLD) & \
                        (mads < self.NEAR_DUPLICATE_THRESHOLD)
        result.near_duplicate_count = int(np.sum(near_dup_mask))
        result.near_duplicate_rate  = result.near_duplicate_count / max(len(mads), 1)

    def _detect_hash_duplicates(
        self,
        grays: List[np.ndarray],
        timestamps: List[float],
        result: DuplicationResult,
    ) -> None:
        """
        Compute perceptual hashes and find frames that appear
        multiple times non-consecutively — indicating looping or
        frame reuse from a different part of the video.
        """
        HASH_SIZE = 16
        hashes    = {}

        for i, gray in enumerate(grays):
            small = cv2.resize(gray, (HASH_SIZE, HASH_SIZE))
            h     = (small > small.mean()).tobytes()

            if h in hashes:
                prev_i = hashes[h]
                gap    = i - prev_i
                # Only flag non-consecutive duplicates (gap > 2 frames)
                if gap > 2:
                    result.hash_duplicate_count += 1
                    ts_a = timestamps[prev_i] if prev_i < len(timestamps) else 0.0
                    ts_b = timestamps[i] if i < len(timestamps) else 0.0
                    result.hash_duplicate_positions.append((prev_i, i))
                    logger.debug(
                        f"Hash duplicate: frame {prev_i} ({ts_a:.2f}s) "
                        f"≈ frame {i} ({ts_b:.2f}s)"
                    )
            else:
                hashes[h] = i

    def _compute_score(self, result: DuplicationResult) -> float:
        """Aggregate duplication signals into 0–1 score."""
        score = 0.0

        # Frozen frame contribution — heavily weighted
        if result.frozen_frame_rate > 0.30:
            score = max(score, 0.80)
        elif result.frozen_frame_rate > 0.10:
            score = max(score, 0.60)
        elif result.frozen_frame_rate > 0.03:
            score = max(score, 0.40)
        elif result.frozen_frame_count > 0:
            score = max(score, 0.20)

        # Multiple frozen segments suggest deliberate insertion
        if len(result.frozen_segments) > 1:
            score = max(score, 0.55)

        # Non-consecutive hash duplicates
        if result.hash_duplicate_count > 5:
            score = max(score, 0.65)
        elif result.hash_duplicate_count > 2:
            score = max(score, 0.45)

        return min(1.0, score)

    def _generate_findings(self, result: DuplicationResult) -> None:
        """Generate human-readable findings."""
        if result.frozen_frame_count > 0:
            seg_desc = ""
            if result.frozen_segments:
                segs = [f"{s:.2f}s–{e:.2f}s" for s, e in result.frozen_segments[:4]]
                seg_desc = f" Segments: {', '.join(segs)}."

            result.add_finding(
                f"{result.frozen_frame_count} frozen frame(s) detected "
                f"({result.frozen_frame_rate:.1%} of analyzed frames).{seg_desc} "
                f"Frozen frames — where consecutive frames are visually identical — "
                f"indicate video was paused, frames were replaced with copies, "
                f"or content was artificially extended."
            )

        if result.near_duplicate_count > 0 and result.near_duplicate_rate > 0.10:
            result.add_finding(
                f"{result.near_duplicate_count} near-duplicate frame pairs "
                f"({result.near_duplicate_rate:.1%} of frames). "
                f"Consecutive frames with very low but non-zero differences "
                f"may indicate artificial slow-motion or repeated low-motion content."
            )

        if result.hash_duplicate_count > 2:
            result.add_finding(
                f"{result.hash_duplicate_count} non-consecutive duplicate frames "
                f"detected by perceptual hash. Frames appearing more than once "
                f"at non-adjacent positions indicate looping or frame reuse."
            )
