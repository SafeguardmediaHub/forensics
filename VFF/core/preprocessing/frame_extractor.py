"""
VideoForensics — Frame Extractor
Extracts frames from a video file for use by all analysis modules.
Frames are stored losslessly in the working directory.
Every module that needs frame-level analysis reads from here.

Design principle: extract once, analyze many times.
No module should be running its own frame extraction.
"""

from __future__ import annotations
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import cv2
import numpy as np

from core.models import VideoProfile

logger = logging.getLogger("vf.preprocessing.frames")


@dataclass
class ExtractedFrame:
    """A single extracted video frame with its metadata."""
    frame_index: int                    # 0-based index in the video
    timestamp_seconds: float            # Position in video
    data: np.ndarray                    # BGR image array (OpenCV format)
    file_path: Optional[Path] = None   # On-disk path if saved

    @property
    def shape(self) -> Tuple[int, int, int]:
        return self.data.shape   # (height, width, channels)

    @property
    def height(self) -> int:
        return self.data.shape[0]

    @property
    def width(self) -> int:
        return self.data.shape[1]

    def to_grayscale(self) -> np.ndarray:
        """Return grayscale version of the frame."""
        return cv2.cvtColor(self.data, cv2.COLOR_BGR2GRAY)

    def to_float(self) -> np.ndarray:
        """Return float32 normalized [0,1] version."""
        return self.data.astype(np.float32) / 255.0


@dataclass
class FrameExtractionResult:
    """Summary of a frame extraction operation."""
    frames_extracted: int       = 0
    frames_requested: int       = 0
    output_directory: Optional[Path] = None
    frame_paths: List[Path]     = field(default_factory=list)
    success: bool               = True
    error: str                  = ""

    @property
    def extraction_rate(self) -> float:
        if self.frames_requested == 0:
            return 0.0
        return self.frames_extracted / self.frames_requested


class FrameExtractor:
    """
    Extracts frames from a video file.

    Two operating modes:
      1. In-memory streaming  — yields ExtractedFrame objects one at a time.
         Used when modules process frames sequentially without needing all frames.

      2. Disk extraction — saves all frames as lossless PNG files.
         Used when multiple modules need access to the same frames.

    For forensic analysis, lossless PNG is the only acceptable format.
    JPEG is never used — it would introduce compression artifacts that
    would pollute noise and compression analysis.
    """

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.working_dir.mkdir(parents=True, exist_ok=True)

    # ── In-Memory Streaming ────────────────────────────────────────────────────

    def stream_frames(
        self,
        video_path: Path,
        profile: VideoProfile,
        max_frames: Optional[int] = None,
        step: int = 1,
        start_second: float = 0.0,
        end_second: Optional[float] = None,
    ) -> Iterator[ExtractedFrame]:
        """
        Stream frames from the video without saving to disk.
        Memory efficient for sequential processing.

        Args:
            video_path:   Path to the video file
            profile:      VideoProfile with frame rate and total frames
            max_frames:   Stop after this many frames (None = all)
            step:         Extract every Nth frame (1 = all, 2 = every other, etc.)
            start_second: Start extraction from this timestamp
            end_second:   Stop extraction at this timestamp (None = end of video)
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error(f"Cannot open video: {video_path}")
            return

        fps = profile.frame_rate or cap.get(cv2.CAP_PROP_FPS) or 25.0
        end_second = end_second or profile.container_duration

        # Seek to start position
        if start_second > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, start_second * 1000)

        frame_index = int(start_second * fps)
        yielded = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                current_ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

                if end_second and current_ts > end_second:
                    break

                if (frame_index % step) == 0:
                    yield ExtractedFrame(
                        frame_index=frame_index,
                        timestamp_seconds=current_ts,
                        data=frame.copy(),
                    )
                    yielded += 1

                    if max_frames and yielded >= max_frames:
                        break

                frame_index += 1
        finally:
            cap.release()

        logger.debug(f"Streamed {yielded} frames from {video_path.name}")

    # ── Disk Extraction ────────────────────────────────────────────────────────

    def extract_to_disk(
        self,
        video_path: Path,
        profile: VideoProfile,
        case_id: str,
        step: int = 1,
        max_frames: Optional[int] = None,
    ) -> FrameExtractionResult:
        """
        Extract frames to disk as lossless PNG files.
        Returns a FrameExtractionResult with paths to all saved frames.

        Frame files are named: frame_{index:06d}_{timestamp_ms:08d}.png
        This naming scheme makes them sortable and timestamp-queryable.
        """
        frame_dir = self.working_dir / case_id / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)

        result = FrameExtractionResult(output_directory=frame_dir)
        total_frames = profile.total_frames or 0
        result.frames_requested = (
            (total_frames // step) if step > 1 else total_frames
        )
        if max_frames:
            result.frames_requested = min(result.frames_requested, max_frames)

        logger.info(
            f"Extracting frames to disk — "
            f"step={step} max={max_frames or 'all'} "
            f"dir={frame_dir}"
        )

        try:
            for frame in self.stream_frames(
                video_path, profile, max_frames=max_frames, step=step
            ):
                ts_ms = int(frame.timestamp_seconds * 1000)
                filename = f"frame_{frame.frame_index:06d}_{ts_ms:08d}.png"
                file_path = frame_dir / filename

                # PNG with maximum compression — lossless, smallest file
                cv2.imwrite(
                    str(file_path),
                    frame.data,
                    [cv2.IMWRITE_PNG_COMPRESSION, 9]
                )

                result.frame_paths.append(file_path)
                result.frames_extracted += 1

            result.success = True
            logger.info(f"Extracted {result.frames_extracted} frames to {frame_dir}")

        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error(f"Frame extraction failed: {e}", exc_info=True)

        return result

    # ── Keyframe Extraction ────────────────────────────────────────────────────

    def extract_keyframes(
        self,
        video_path: Path,
        case_id: str,
    ) -> FrameExtractionResult:
        """
        Extract only keyframes (I-frames) using ffmpeg.
        Keyframes are critical for compression analysis (GOP structure).
        Uses ffmpeg directly because OpenCV doesn't expose keyframe detection.
        """
        keyframe_dir = self.working_dir / case_id / "keyframes"
        keyframe_dir.mkdir(parents=True, exist_ok=True)

        result = FrameExtractionResult(output_directory=keyframe_dir)

        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vf", "select=eq(pict_type\\,I)",   # Select only I-frames
            "-vsync", "vfr",
            "-frame_pts", "1",
            "-f", "image2",
            str(keyframe_dir / "keyframe_%06d.png"),
            "-y",
            "-loglevel", "error",
        ]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if proc.returncode != 0:
                result.success = False
                result.error = proc.stderr.strip()[:200]
                logger.error(f"Keyframe extraction failed: {result.error}")
                return result

            result.frame_paths = sorted(keyframe_dir.glob("keyframe_*.png"))
            result.frames_extracted = len(result.frame_paths)
            result.success = True
            logger.info(f"Extracted {result.frames_extracted} keyframes")

        except subprocess.TimeoutExpired:
            result.success = False
            result.error = "ffmpeg timed out during keyframe extraction"
        except Exception as e:
            result.success = False
            result.error = str(e)

        return result

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def load_frame(path: Path) -> Optional[ExtractedFrame]:
        """Load a previously saved frame from disk."""
        try:
            data = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if data is None:
                return None
            # Recover index and timestamp from filename
            parts = path.stem.split("_")
            idx = int(parts[1]) if len(parts) > 1 else 0
            ts_ms = int(parts[2]) if len(parts) > 2 else 0
            return ExtractedFrame(
                frame_index=idx,
                timestamp_seconds=ts_ms / 1000.0,
                data=data,
                file_path=path,
            )
        except Exception as e:
            logger.error(f"Failed to load frame {path}: {e}")
            return None

    @staticmethod
    def sample_frames(
        all_frames: List[Path],
        n: int,
    ) -> List[Path]:
        """
        Select N evenly spaced frames from a list of frame paths.
        Used when a module needs a representative sample rather than all frames.
        """
        if len(all_frames) <= n:
            return all_frames
        step = len(all_frames) / n
        return [all_frames[int(i * step)] for i in range(n)]
