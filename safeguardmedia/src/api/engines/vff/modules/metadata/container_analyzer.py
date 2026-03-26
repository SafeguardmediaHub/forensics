"""
VideoForensics — Container Structure Analyzer
Performs deep inspection of the video container structure using ffprobe.
Checks packet-level and frame-level data for structural anomalies that
indicate tampering, splicing, or re-encoding.

This runs separately from the metadata tag analysis — it looks at the
binary structure of the container rather than the embedded text metadata.
"""

from __future__ import annotations
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vf.metadata.container")


@dataclass
class PacketInfo:
    """Information about a single encoded packet in the video stream."""
    pts: int            # Presentation timestamp (in stream timebase units)
    pts_time: float     # Presentation timestamp in seconds
    dts: int            # Decode timestamp
    dts_time: float     # Decode timestamp in seconds
    duration: int       # Packet duration in timebase units
    size: int           # Packet size in bytes
    is_keyframe: bool   # Whether this is a keyframe (I-frame)
    position: int       # Byte offset in file


@dataclass
class ContainerAnalysisResult:
    """Results of container structure inspection."""
    # GOP structure
    keyframe_count: int                 = 0
    keyframe_positions: List[float]     = field(default_factory=list)  # seconds
    keyframe_intervals: List[float]     = field(default_factory=list)  # seconds between keyframes
    avg_keyframe_interval: float        = 0.0
    keyframe_interval_std: float        = 0.0
    irregular_gop_detected: bool        = False
    irregular_gop_positions: List[float] = field(default_factory=list)

    # Timestamp integrity
    pts_gaps: List[Tuple[float, float]] = field(default_factory=list)  # (position, gap_size)
    pts_reversals: List[float]          = field(default_factory=list)  # positions of reversals
    dts_pts_inversions: int             = 0                            # count of DTS > PTS
    timestamp_anomalies: List[str]      = field(default_factory=list)

    # Bitrate profile
    packet_sizes: List[int]             = field(default_factory=list)
    bitrate_anomaly_positions: List[float] = field(default_factory=list)

    # Container integrity
    container_format: str               = ""
    declared_duration: float            = 0.0
    computed_duration: float            = 0.0
    duration_mismatch: bool             = False
    has_multiple_video_streams: bool    = False
    stream_count: int                   = 0

    # Overall
    anomaly_count: int                  = 0
    findings: List[str]                 = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)
        self.anomaly_count += 1


class ContainerAnalyzer:
    """
    Analyzes the structural integrity of a video container.

    Key checks:
    - GOP (Group of Pictures) regularity — irregular I-frame spacing suggests editing
    - PTS/DTS timestamp continuity — gaps or reversals indicate splicing
    - Packet size profile — sudden size anomalies suggest content change
    - Container duration vs computed duration — declared vs actual length mismatch
    - Multiple video stream detection — unusual and worth flagging
    """

    # Maximum acceptable keyframe interval variation (coefficient of variation)
    GOP_IRREGULARITY_CV_THRESHOLD = 0.35

    # Minimum PTS gap to be considered anomalous (seconds)
    PTS_GAP_THRESHOLD = 0.5

    # How many packets to analyze for full analysis
    # For long videos, we sample rather than process every packet
    MAX_PACKETS_FULL  = 5000
    SAMPLE_INTERVAL   = 10     # Analyze every 10th packet beyond the limit

    def analyze(self, video_path: Path) -> ContainerAnalysisResult:
        """
        Run full container structure analysis on the video file.
        Returns ContainerAnalysisResult with all findings.
        """
        result = ContainerAnalysisResult()
        logger.info(f"Container analysis: {video_path.name}")

        # ── Get stream count and format info ──────────────────────────────
        format_data = self._get_format_info(video_path)
        if format_data:
            result.container_format  = format_data.get("format_name", "")
            result.declared_duration = self._safe_float(format_data.get("duration"))
            result.stream_count      = self._safe_int(format_data.get("nb_streams"))

        # ── Get all video packets for structure analysis ───────────────────
        packets = self._get_video_packets(video_path)
        if not packets:
            result.add_finding("No video packets accessible for container analysis")
            return result

        logger.debug(f"Analyzing {len(packets)} video packets")

        # ── GOP analysis ──────────────────────────────────────────────────
        self._analyze_gop(packets, result)

        # ── Timestamp continuity ──────────────────────────────────────────
        self._analyze_timestamps(packets, result)

        # ── Bitrate / packet size profile ─────────────────────────────────
        self._analyze_packet_sizes(packets, result)

        # ── Duration consistency ──────────────────────────────────────────
        self._check_duration_consistency(packets, result)

        logger.info(
            f"Container analysis complete — "
            f"keyframes={result.keyframe_count}  "
            f"irregular_gop={result.irregular_gop_detected}  "
            f"anomalies={result.anomaly_count}"
        )
        return result

    # ── ffprobe Data Retrieval ────────────────────────────────────────────────

    def _get_format_info(self, video_path: Path) -> Optional[Dict]:
        """Get container format information."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(video_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            data = json.loads(r.stdout)
            return data.get("format", {})
        except Exception as e:
            logger.error(f"Format info failed: {e}")
            return None

    def _get_video_packets(self, video_path: Path) -> List[PacketInfo]:
        """
        Get packet-level information for the video stream.
        For very long videos, retrieves a representative sample.
        """
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_packets",
            "-select_streams", "v:0",
            str(video_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                logger.error(f"Packet retrieval failed: {r.stderr[:100]}")
                return []

            data = json.loads(r.stdout)
            raw_packets = data.get("packets", [])

            packets = []
            for p in raw_packets:
                try:
                    packets.append(PacketInfo(
                        pts=self._safe_int(p.get("pts", 0)),
                        pts_time=self._safe_float(p.get("pts_time", 0)),
                        dts=self._safe_int(p.get("dts", 0)),
                        dts_time=self._safe_float(p.get("dts_time", 0)),
                        duration=self._safe_int(p.get("duration", 0)),
                        size=self._safe_int(p.get("size", 0)),
                        is_keyframe="K" in str(p.get("flags", "")),
                        position=self._safe_int(p.get("pos", 0)),
                    ))
                except Exception:
                    continue

            return packets

        except subprocess.TimeoutExpired:
            logger.error("Packet analysis timed out")
            return []
        except Exception as e:
            logger.error(f"Packet analysis failed: {e}")
            return []

    # ── Analysis Methods ──────────────────────────────────────────────────────

    def _analyze_gop(self, packets: List[PacketInfo], result: ContainerAnalysisResult) -> None:
        """
        Analyze GOP (Group of Pictures) structure.
        Irregular I-frame intervals are a strong indicator of video editing/splicing.
        """
        keyframes = [p for p in packets if p.is_keyframe]
        result.keyframe_count = len(keyframes)

        if len(keyframes) < 2:
            result.add_finding("Insufficient keyframes for GOP analysis")
            return

        result.keyframe_positions = [k.pts_time for k in keyframes]

        # Compute inter-keyframe intervals
        intervals = []
        for i in range(1, len(keyframes)):
            interval = keyframes[i].pts_time - keyframes[i-1].pts_time
            if interval > 0:
                intervals.append(interval)

        result.keyframe_intervals = intervals

        if not intervals:
            return

        import statistics
        result.avg_keyframe_interval = statistics.mean(intervals)

        if len(intervals) > 1:
            result.keyframe_interval_std = statistics.stdev(intervals)

            # Coefficient of variation — normalized measure of irregularity
            cv = result.keyframe_interval_std / result.avg_keyframe_interval
            if cv > self.GOP_IRREGULARITY_CV_THRESHOLD:
                result.irregular_gop_detected = True
                result.add_finding(
                    f"Irregular GOP structure detected — "
                    f"interval CV={cv:.3f} (threshold: {self.GOP_IRREGULARITY_CV_THRESHOLD}). "
                    f"Mean interval: {result.avg_keyframe_interval:.2f}s ± "
                    f"{result.keyframe_interval_std:.2f}s. "
                    f"This pattern is consistent with video editing or splicing."
                )

            # Find specific positions of irregular intervals
            threshold = result.avg_keyframe_interval + 2 * result.keyframe_interval_std
            for i, interval in enumerate(intervals):
                if interval > threshold:
                    pos = keyframes[i].pts_time
                    result.irregular_gop_positions.append(pos)
                    logger.debug(f"Irregular GOP interval at {pos:.2f}s: {interval:.2f}s")

        logger.debug(
            f"GOP: {len(keyframes)} keyframes, "
            f"avg_interval={result.avg_keyframe_interval:.2f}s, "
            f"irregular={result.irregular_gop_detected}"
        )

    def _analyze_timestamps(
        self, packets: List[PacketInfo], result: ContainerAnalysisResult
    ) -> None:
        """
        Analyze PTS/DTS timestamp continuity.
        Gaps, reversals, and DTS>PTS inversions all indicate potential tampering.
        """
        if len(packets) < 2:
            return

        prev_pts = packets[0].pts_time
        max_expected_gap = 0.5   # seconds — normal frame gaps are < 1/fps

        for i in range(1, len(packets)):
            curr_pts = packets[i].pts_time

            # PTS reversal — only flag SIGNIFICANT reversals
            # Small reversals (< 1 second) are normal B-frame reordering in H264/H265
            # Large reversals indicate genuine splicing or re-ordering
            reversal = prev_pts - curr_pts
            if reversal > 1.0:   # Only flag if reversal > 1 full second
                result.pts_reversals.append(curr_pts)
                result.add_finding(
                    f"Significant PTS reversal of {reversal:.3f}s at position {curr_pts:.3f}s "
                    f"(previous PTS was {prev_pts:.3f}s). "
                    f"Large timestamp reversals beyond normal B-frame reordering "
                    f"indicate potential frame re-ordering or splicing."
                )

            # PTS gap — unexpected jump forward in time
            gap = curr_pts - prev_pts
            if gap > max_expected_gap:
                result.pts_gaps.append((curr_pts, gap))
                if gap > self.PTS_GAP_THRESHOLD:
                    result.add_finding(
                        f"PTS gap of {gap:.3f}s at position {curr_pts:.3f}s. "
                        f"Large timestamp discontinuities suggest frames were deleted "
                        f"or content was inserted from a different recording."
                    )

            # DTS > PTS inversion (should not happen in well-formed streams)
            if packets[i].dts_time > packets[i].pts_time + 0.1:
                result.dts_pts_inversions += 1

            prev_pts = curr_pts

        if result.dts_pts_inversions > 5:
            result.add_finding(
                f"{result.dts_pts_inversions} DTS/PTS inversions detected. "
                f"Excessive DTS>PTS relationships indicate stream repackaging "
                f"or container manipulation."
            )

    def _analyze_packet_sizes(
        self, packets: List[PacketInfo], result: ContainerAnalysisResult
    ) -> None:
        """
        Analyze packet size distribution for anomalies.
        Sudden changes in packet size at specific timestamps can indicate
        content was inserted from a different encoding session.
        """
        if len(packets) < 10:
            return

        import statistics
        sizes = [p.size for p in packets if p.size > 0]
        if not sizes:
            return

        result.packet_sizes = sizes

        mean_size = statistics.mean(sizes)
        if len(sizes) > 1:
            std_size  = statistics.stdev(sizes)
        else:
            return

        if mean_size == 0:
            return

        # Look for significant local deviations using a sliding window
        window = 20
        for i in range(window, len(packets) - window):
            local_sizes = [packets[j].size for j in range(i-window, i+window)]
            local_mean  = statistics.mean(local_sizes)
            global_dev  = abs(local_mean - mean_size) / (std_size + 1)
            if global_dev > 3.0:   # More than 3 std deviations from global mean
                pos = packets[i].pts_time
                if pos not in result.bitrate_anomaly_positions:
                    result.bitrate_anomaly_positions.append(pos)

        if result.bitrate_anomaly_positions:
            result.add_finding(
                f"Packet size anomalies detected at "
                f"{len(result.bitrate_anomaly_positions)} position(s): "
                f"{[f'{p:.1f}s' for p in result.bitrate_anomaly_positions[:5]]}. "
                f"Sudden bitrate changes may indicate content spliced from "
                f"a different encoding session."
            )

    def _check_duration_consistency(
        self, packets: List[PacketInfo], result: ContainerAnalysisResult
    ) -> None:
        """
        Compare declared container duration against the duration
        computed from the actual packet timestamps.
        A significant mismatch suggests the container header was manipulated.
        """
        if not packets or result.declared_duration == 0:
            return

        last_pts = max(p.pts_time for p in packets)
        result.computed_duration = last_pts

        if result.declared_duration > 0:
            discrepancy = abs(result.declared_duration - result.computed_duration)
            relative_discrepancy = discrepancy / result.declared_duration

            if relative_discrepancy > 0.05:   # More than 5% discrepancy
                result.duration_mismatch = True
                result.add_finding(
                    f"Duration mismatch: container declares {result.declared_duration:.2f}s "
                    f"but packet timestamps span {result.computed_duration:.2f}s "
                    f"(discrepancy: {discrepancy:.2f}s, {relative_discrepancy*100:.1f}%). "
                    f"The container header may have been modified."
                )

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_float(val: Any, default: float = 0.0) -> float:
        try:
            return float(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_int(val: Any, default: int = 0) -> int:
        try:
            return int(val) if val is not None else default
        except (ValueError, TypeError):
            return default
