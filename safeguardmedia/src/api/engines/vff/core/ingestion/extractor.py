"""
VideoForensics — Metadata Extractor
Extracts the complete technical profile of a video file using ffprobe.
Produces a fully populated VideoProfile that all downstream modules use
to calibrate their analysis.

This is NOT the forensic metadata analysis module (that is Phase 2).
This is the infrastructure that reads the raw data at ingestion time.
"""

from __future__ import annotations
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.models import VideoProfile
from config.settings import (
    MIN_FRAMES_FOR_ANALYSIS,
    MIN_DURATION_SECONDS,
)

logger = logging.getLogger("vf.ingestion.extractor")


class MetadataExtractor:
    """
    Extracts complete technical metadata from a video file.
    Uses ffprobe for container/stream data.
    Populates and returns a VideoProfile.
    """

    def extract(self, file_path: Path, sha256: str = "", md5: str = "") -> VideoProfile:
        """
        Main entry point. Returns a fully populated VideoProfile.
        Never raises — logs errors and returns partial profile on failure.
        """
        logger.info(f"Extracting metadata: {file_path.name}")

        profile = VideoProfile(
            original_filename=file_path.name,
            file_size_bytes=file_path.stat().st_size if file_path.exists() else 0,
            sha256_hash=sha256,
            md5_hash=md5,
        )

        raw = self._run_ffprobe_full(file_path)
        if raw is None:
            logger.error("ffprobe returned no data — profile will be incomplete")
            return profile

        profile.raw_metadata = raw
        self._extract_format(raw.get("format", {}), profile)
        self._extract_streams(raw.get("streams", []), profile)
        self._set_quality_flags(profile)

        logger.info(
            f"Profile complete — {profile.codec} {profile.resolution_label} "
            f"@ {profile.frame_rate:.2f}fps  "
            f"audio={'yes' if profile.has_audio else 'no'}  "
            f"frames={profile.total_frames}"
        )
        return profile

    # ── ffprobe Execution ──────────────────────────────────────────────────────

    def _run_ffprobe_full(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Run ffprobe with all data extraction flags.
        Returns parsed JSON dict or None on failure.
        """
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(file_path),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                logger.error(f"ffprobe failed: {result.stderr.strip()[:200]}")
                return None
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            logger.error("ffprobe timed out")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"ffprobe JSON parse error: {e}")
            return None
        except Exception as e:
            logger.error(f"ffprobe exception: {e}")
            return None

    # ── Format-Level Extraction ────────────────────────────────────────────────

    def _extract_format(self, fmt: Dict[str, Any], profile: VideoProfile) -> None:
        """Extract container-level information."""
        profile.container_format  = self._clean(fmt.get("format_name", ""))
        profile.container_duration = self._safe_float(fmt.get("duration"))
        profile.avg_bitrate_kbps  = self._safe_float(fmt.get("bit_rate", 0)) / 1000

        # Encoder/software tag — critical for forensic metadata analysis
        tags = fmt.get("tags", {})
        profile.encoder_software  = self._find_tag(tags, ["encoder", "software", "writing_application"])
        profile.creation_time     = self._find_tag(tags, ["creation_time", "date"])
        profile.device_make       = self._find_tag(tags, ["make", "com.apple.quicktime.make"])
        profile.device_model      = self._find_tag(tags, ["model", "com.apple.quicktime.model"])

    # ── Stream-Level Extraction ────────────────────────────────────────────────

    def _extract_streams(self, streams: List[Dict], profile: VideoProfile) -> None:
        """Extract video and audio stream information."""
        video_stream = None
        audio_stream = None

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            if codec_type == "video" and video_stream is None:
                video_stream = stream
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = stream

        if video_stream:
            self._parse_video_stream(video_stream, profile)
        else:
            profile.has_video = False
            logger.warning("No video stream found")

        if audio_stream:
            self._parse_audio_stream(audio_stream, profile)
        else:
            profile.has_audio = False

    def _parse_video_stream(self, s: Dict, profile: VideoProfile) -> None:
        """Parse a video stream dict into the profile."""
        profile.codec       = self._clean(s.get("codec_name", ""))
        profile.width       = self._safe_int(s.get("width", 0))
        profile.height      = self._safe_int(s.get("height", 0))
        profile.bit_depth   = self._safe_int(s.get("bits_per_raw_sample", 8))
        profile.color_space = self._clean(s.get("pix_fmt", ""))

        # Frame rate — stored as fraction string e.g. "30000/1001"
        profile.frame_rate  = self._parse_frame_rate(
            s.get("r_frame_rate") or s.get("avg_frame_rate", "0/1")
        )

        # Total frames — prefer direct count, fall back to duration × fps
        if s.get("nb_frames"):
            profile.total_frames = self._safe_int(s["nb_frames"])
        elif profile.container_duration and profile.frame_rate:
            profile.total_frames = int(profile.container_duration * profile.frame_rate)

        # Stream-level bitrate (more precise than container-level)
        if s.get("bit_rate"):
            profile.avg_bitrate_kbps = self._safe_float(s["bit_rate"]) / 1000

        # GPS from stream tags
        tags = s.get("tags", {})
        self._extract_gps(tags, profile)

        # Encoder from stream tags (overrides format-level if present)
        enc = self._find_tag(tags, ["encoder", "handler_name"])
        if enc:
            profile.encoder_software = enc

    def _parse_audio_stream(self, s: Dict, profile: VideoProfile) -> None:
        """Parse an audio stream dict into the profile."""
        profile.has_audio         = True
        profile.audio_codec       = self._clean(s.get("codec_name", ""))
        profile.audio_sample_rate = self._safe_int(s.get("sample_rate", 0))
        profile.audio_channels    = self._safe_int(s.get("channels", 0))
        profile.audio_bitrate_kbps = self._safe_float(s.get("bit_rate", 0)) / 1000

    # ── GPS Extraction ─────────────────────────────────────────────────────────

    def _extract_gps(self, tags: Dict, profile: VideoProfile) -> None:
        """
        Extract GPS coordinates from stream or format tags.
        GPS can be stored in many tag formats depending on device/platform.
        """
        # Standard location tag (Android, many cameras)
        location = self._find_tag(tags, ["location", "com.apple.quicktime.location.ISO6709"])
        if location:
            lat, lon = self._parse_iso6709(location)
            if lat is not None:
                profile.gps_latitude  = lat
                profile.gps_longitude = lon
                return

        # Direct lat/lon tags
        lat_tag = self._find_tag(tags, ["gps_latitude", "latitude"])
        lon_tag = self._find_tag(tags, ["gps_longitude", "longitude"])
        if lat_tag and lon_tag:
            try:
                profile.gps_latitude  = float(lat_tag)
                profile.gps_longitude = float(lon_tag)
            except ValueError:
                pass

    def _parse_iso6709(self, location: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Parse ISO 6709 location string e.g. '+37.3861-122.0839+25.000/'
        Returns (latitude, longitude) or (None, None) on failure.
        """
        import re
        pattern = r'([+-]\d+\.?\d*)([+-]\d+\.?\d*)'
        match = re.search(pattern, location)
        if match:
            try:
                return float(match.group(1)), float(match.group(2))
            except ValueError:
                pass
        return None, None

    # ── Quality Flags ──────────────────────────────────────────────────────────

    def _set_quality_flags(self, profile: VideoProfile) -> None:
        """
        Set flags that downstream modules use to calibrate reliability.
        Low resolution, very short, or frame-sparse videos get flagged.
        """
        profile.is_low_resolution = (
            profile.height > 0 and profile.height < 480
        )
        profile.is_very_short = (
            profile.container_duration < MIN_DURATION_SECONDS
        )
        profile.has_sufficient_frames = (
            profile.total_frames >= MIN_FRAMES_FOR_ANALYSIS
        )

        if profile.is_low_resolution:
            logger.warning(f"Low resolution: {profile.height}p — some analyses less reliable")
        if not profile.has_sufficient_frames:
            logger.warning(
                f"Insufficient frames: {profile.total_frames} "
                f"(need {MIN_FRAMES_FOR_ANALYSIS}) — some analyses will be skipped"
            )

    # ── Parsing Utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_frame_rate(rate_str: str) -> float:
        """Parse '30000/1001' or '25' style frame rate strings."""
        try:
            if "/" in rate_str:
                num, den = rate_str.split("/")
                den = int(den)
                return float(num) / den if den != 0 else 0.0
            return float(rate_str)
        except (ValueError, ZeroDivisionError):
            return 0.0

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

    @staticmethod
    def _clean(val: Any) -> str:
        return str(val).strip() if val is not None else ""

    @staticmethod
    def _find_tag(tags: Dict, keys: List[str]) -> Optional[str]:
        """Search for any of the given keys in a tags dict (case-insensitive)."""
        lower_tags = {k.lower(): v for k, v in tags.items()}
        for key in keys:
            val = lower_tags.get(key.lower())
            if val:
                return str(val).strip()
        return None
