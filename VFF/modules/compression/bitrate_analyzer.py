"""
VideoForensics — Bitrate Analyzer
Analyzes the per-frame bitrate profile of a video for anomalies.

Natural video bitrate varies smoothly with scene complexity.
Abrupt spikes or drops at specific timestamps indicate:
    - Content spliced in from a different encoding session
    - Frames inserted or deleted
    - Local re-encoding of specific segments

Also analyzes the I/P/B frame type distribution for GOP irregularities
that the metadata module's packet analysis may have missed.
"""

from __future__ import annotations
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats, signal

logger = logging.getLogger("vf.compression.bitrate")


@dataclass
class FrameRecord:
    """Data for a single video frame."""
    index: int
    pts_seconds: float
    size_bytes: int
    frame_type: str       # 'I', 'P', 'B', or '?'
    is_keyframe: bool


@dataclass
class BitrateAnalysisResult:
    """Complete results of bitrate profile analysis."""
    total_frames: int                  = 0
    frame_records: List[FrameRecord]   = field(default_factory=list)

    # Bitrate statistics
    mean_frame_size: float             = 0.0
    std_frame_size: float              = 0.0
    cv_frame_size: float               = 0.0   # Coefficient of variation

    # Anomaly detection
    anomaly_timestamps: List[float]    = field(default_factory=list)
    anomaly_descriptions: List[str]    = field(default_factory=list)
    anomaly_score: float               = 0.0

    # GOP / frame type analysis
    i_frame_count: int                 = 0
    p_frame_count: int                 = 0
    b_frame_count: int                 = 0
    i_frame_positions: List[float]     = field(default_factory=list)  # seconds
    gop_sizes: List[int]               = field(default_factory=list)
    irregular_gop_score: float         = 0.0
    unexpected_i_frames: List[float]   = field(default_factory=list)

    # Overall
    tampering_probability: float       = 0.0
    findings: List[str]                = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class BitrateAnalyzer:
    """
    Analyzes per-frame size (proxy for bitrate) and frame type distribution
    to detect editing artifacts and compression anomalies.
    """

    # Standard deviations above mean to consider anomalous
    ANOMALY_THRESHOLD_STD = 3.5

    # Minimum I-frame interval variation to flag as irregular (coefficient of variation)
    GOP_IRREGULARITY_CV = 0.40

    # Minimum frames for reliable analysis
    MIN_FRAMES = 30

    def analyze(self, video_path: Path) -> BitrateAnalysisResult:
        """
        Run full bitrate and frame type analysis.
        Returns BitrateAnalysisResult with all findings.
        """
        result = BitrateAnalysisResult()
        logger.info(f"Bitrate analysis: {video_path.name}")

        # ── Get frame data from ffprobe ────────────────────────────────────
        records = self._get_frame_records(video_path)
        if len(records) < self.MIN_FRAMES:
            result.add_finding(
                f"Insufficient frames for reliable bitrate analysis "
                f"({len(records)} frames, need {self.MIN_FRAMES})"
            )
            return result

        result.frame_records = records
        result.total_frames  = len(records)

        # ── Bitrate statistics ─────────────────────────────────────────────
        sizes = np.array([r.size_bytes for r in records], dtype=np.float64)
        result.mean_frame_size = float(np.mean(sizes))
        result.std_frame_size  = float(np.std(sizes))
        result.cv_frame_size   = float(result.std_frame_size / (result.mean_frame_size + 1))

        # ── Bitrate anomaly detection ──────────────────────────────────────
        self._detect_bitrate_anomalies(records, sizes, result)

        # ── Frame type distribution & GOP ──────────────────────────────────
        self._analyze_frame_types(records, result)

        # ── Overall probability ────────────────────────────────────────────
        result.tampering_probability = self._compute_probability(result)

        # ── Findings ──────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"Bitrate analysis complete — "
            f"frames={result.total_frames} "
            f"anomalies={len(result.anomaly_timestamps)} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    # ── ffprobe Data Retrieval ────────────────────────────────────────────────

    def _get_frame_records(self, video_path: Path) -> List[FrameRecord]:
        """Retrieve per-frame size and type data via ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_frames",
            "-select_streams", "v:0",
            str(video_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                logger.error(f"ffprobe frames failed: {r.stderr[:100]}")
                return []

            frames_data = json.loads(r.stdout).get("frames", [])
            records = []
            for i, f in enumerate(frames_data):
                try:
                    records.append(FrameRecord(
                        index=i,
                        pts_seconds=float(f.get("pts_time") or f.get("best_effort_timestamp_time") or 0),
                        size_bytes=int(f.get("pkt_size", 0)),
                        frame_type=f.get("pict_type", "?"),
                        is_keyframe=bool(f.get("key_frame", False)),
                    ))
                except (ValueError, TypeError):
                    continue
            return records

        except subprocess.TimeoutExpired:
            logger.error("ffprobe frame query timed out")
            return []
        except Exception as e:
            logger.error(f"Frame record retrieval failed: {e}")
            return []

    # ── Analysis Methods ──────────────────────────────────────────────────────

    def _detect_bitrate_anomalies(
        self,
        records: List[FrameRecord],
        sizes: np.ndarray,
        result: BitrateAnalysisResult,
    ) -> None:
        """
        Detect anomalous bitrate positions using a sliding window approach.
        A local region whose mean size deviates strongly from the global mean
        is a candidate for content from a different encoding session.
        """
        if len(sizes) < 20:
            return

        # Use a rolling window of ~1 second of frames
        # Assume ~30fps, so window = 30 frames
        window = min(30, len(sizes) // 5)
        threshold = self.ANOMALY_THRESHOLD_STD * result.std_frame_size
        global_mean = result.mean_frame_size

        flagged_positions = set()

        for i in range(window, len(records) - window):
            local_window = sizes[i-window:i+window]
            local_mean = float(np.mean(local_window))
            deviation  = abs(local_mean - global_mean)

            if deviation > threshold:
                pos = records[i].pts_seconds
                # Deduplicate nearby positions (within 1 second)
                nearby = any(abs(pos - p) < 1.0 for p in flagged_positions)
                if not nearby:
                    flagged_positions.add(pos)
                    result.anomaly_timestamps.append(pos)
                    result.anomaly_descriptions.append(
                        f"Bitrate anomaly at {pos:.2f}s — local mean "
                        f"{local_mean:.0f}B vs global mean {global_mean:.0f}B "
                        f"({deviation/result.std_frame_size:.1f}σ deviation)"
                    )

        if flagged_positions:
            result.anomaly_score = min(
                1.0,
                len(flagged_positions) / max(result.total_frames / 100, 1)
            )

    def _analyze_frame_types(
        self,
        records: List[FrameRecord],
        result: BitrateAnalysisResult,
    ) -> None:
        """
        Analyze I/P/B frame type distribution and GOP structure.
        Unexpected I-frames mid-GOP are a strong indicator of splicing —
        encoders insert I-frames at scene cuts, and abrupt mid-stream I-frames
        often mark where content was inserted.
        """
        i_frames = [r for r in records if r.frame_type == 'I']
        result.i_frame_count = len(i_frames)
        result.p_frame_count = sum(1 for r in records if r.frame_type == 'P')
        result.b_frame_count = sum(1 for r in records if r.frame_type == 'B')
        result.i_frame_positions = [r.pts_seconds for r in i_frames]

        if len(i_frames) < 2:
            return

        # Compute GOP sizes (frames between I-frames)
        for j in range(1, len(i_frames)):
            gop_size = i_frames[j].index - i_frames[j-1].index
            result.gop_sizes.append(gop_size)

        if not result.gop_sizes:
            return

        gop_array = np.array(result.gop_sizes, dtype=np.float64)
        mean_gop  = float(np.mean(gop_array))
        std_gop   = float(np.std(gop_array)) if len(gop_array) > 1 else 0.0

        if mean_gop > 0:
            cv = std_gop / mean_gop
            result.irregular_gop_score = min(1.0, max(0.0, (cv - 0.15) / 0.5))

        # Flag unexpected I-frames: those that deviate > 2σ from the expected position
        if std_gop > 0 and mean_gop > 0:
            for j, gop_size in enumerate(result.gop_sizes):
                if abs(gop_size - mean_gop) > 2.5 * std_gop:
                    pos = i_frames[j+1].pts_seconds
                    result.unexpected_i_frames.append(pos)

    def _compute_probability(self, result: BitrateAnalysisResult) -> float:
        """Aggregate bitrate anomaly scores into overall tampering probability."""
        score = (
            0.50 * result.anomaly_score +
            0.35 * result.irregular_gop_score +
            0.15 * min(1.0, len(result.unexpected_i_frames) / 3)
        )
        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: BitrateAnalysisResult) -> None:
        """Generate human-readable findings."""

        if result.anomaly_timestamps:
            positions = [f"{t:.2f}s" for t in result.anomaly_timestamps[:5]]
            result.add_finding(
                f"Bitrate anomalies detected at "
                f"{len(result.anomaly_timestamps)} position(s): {', '.join(positions)}. "
                f"Sudden local bitrate deviations ({self.ANOMALY_THRESHOLD_STD}σ from mean) "
                f"suggest content may have been inserted from a different encoding session "
                f"or the video was re-encoded in sections."
            )

        if result.irregular_gop_score > 0.45:
            result.add_finding(
                f"Irregular GOP structure — I-frame intervals vary significantly "
                f"(mean GOP={np.mean(result.gop_sizes):.1f} frames, "
                f"std={np.std(result.gop_sizes):.1f}). "
                f"Consistent GOP spacing is a property of continuous recordings. "
                f"Irregular spacing indicates editing, splicing, or partial re-encoding."
            )

        if result.unexpected_i_frames:
            positions = [f"{p:.2f}s" for p in result.unexpected_i_frames[:5]]
            result.add_finding(
                f"Unexpected I-frames detected at {len(result.unexpected_i_frames)} "
                f"position(s) outside normal GOP boundaries: {', '.join(positions)}. "
                f"Abrupt I-frames mid-GOP are a strong indicator of content splicing, "
                f"as encoders insert I-frames at scene boundaries during editing."
            )
