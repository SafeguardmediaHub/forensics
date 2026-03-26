"""
AudioForensics — ffprobe Wrapper (Phase 1)

Runs ffprobe on any audio file and returns structured metadata.
Handles all supported formats: WAV, MP3, AAC, M4A, OGG, FLAC, MP4, 3GP, AMR.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("af.ingestion.ffprobe")


class FFprobeError(Exception):
    pass


class FFprobeResult:
    """Structured result from an ffprobe run."""

    def __init__(self, raw: Dict[str, Any]):
        self._raw     = raw
        self._format  = raw.get("format",  {})
        self._streams = raw.get("streams", [])
        self._audio   = self._find_audio_stream()

    def _find_audio_stream(self) -> Optional[Dict]:
        """Return the first audio stream (most files have exactly one)."""
        for s in self._streams:
            if s.get("codec_type") == "audio":
                return s
        return None

    # ── Format-level properties ───────────────────────────────────────────────

    @property
    def format_name(self) -> str:
        return self._format.get("format_name", "unknown")

    @property
    def format_long_name(self) -> str:
        return self._format.get("format_long_name", "")

    @property
    def duration_seconds(self) -> float:
        raw = self._format.get("duration") or (
            self._audio.get("duration") if self._audio else None
        )
        return float(raw) if raw else 0.0

    @property
    def file_size_bytes(self) -> int:
        return int(self._format.get("size", 0))

    @property
    def bitrate_bps(self) -> Optional[int]:
        raw = self._format.get("bit_rate")
        return int(raw) if raw else None

    @property
    def format_tags(self) -> Dict[str, str]:
        return self._format.get("tags", {})

    @property
    def creation_time(self) -> Optional[str]:
        """ISO timestamp from container metadata, or None."""
        tags = self.format_tags
        # Try common tag names
        for key in ("creation_time", "date", "DATE", "TDRC", "year"):
            if key in tags:
                return tags[key]
        # Also check stream tags
        if self._audio:
            for key in ("creation_time", "date"):
                val = self._audio.get("tags", {}).get(key)
                if val:
                    return val
        return None

    @property
    def encoder_string(self) -> Optional[str]:
        """Encoder software string (e.g. 'LAME3.100', 'iTunes 12.9')."""
        for key in ("encoder", "ENCODER", "Encoder", "software", "tool"):
            val = self.format_tags.get(key)
            if val:
                return val
        if self._audio:
            for key in ("encoder", "ENCODER"):
                val = self._audio.get("tags", {}).get(key)
                if val:
                    return val
        return None

    @property
    def has_id3_tags(self) -> bool:
        """True if ID3 tag structure is present (MP3 files)."""
        return "mp3" in self.format_name.lower() and bool(self.format_tags)

    @property
    def has_xmp_data(self) -> bool:
        """Heuristic: XMP often stored in certain container tag keys."""
        return any("xmp" in k.lower() for k in self.format_tags)

    # ── Audio stream properties ───────────────────────────────────────────────

    @property
    def has_audio(self) -> bool:
        return self._audio is not None

    @property
    def codec_name(self) -> str:
        if not self._audio:
            return "unknown"
        return self._audio.get("codec_name", "unknown")

    @property
    def codec_long_name(self) -> str:
        if not self._audio:
            return ""
        return self._audio.get("codec_long_name", "")

    @property
    def sample_rate(self) -> int:
        if not self._audio:
            return 0
        return int(self._audio.get("sample_rate", 0))

    @property
    def channels(self) -> int:
        if not self._audio:
            return 0
        return int(self._audio.get("channels", 1))

    @property
    def channel_layout(self) -> str:
        if not self._audio:
            return "unknown"
        return self._audio.get("channel_layout", "unknown")

    @property
    def bit_depth(self) -> Optional[int]:
        """Bits per sample — None for lossy codecs."""
        if not self._audio:
            return None
        bps = self._audio.get("bits_per_sample")
        if bps and int(bps) > 0:
            return int(bps)
        return None

    @property
    def stream_bitrate_bps(self) -> Optional[int]:
        """Stream-level bitrate (more accurate than format-level for lossy)."""
        if not self._audio:
            return None
        raw = self._audio.get("bit_rate")
        return int(raw) if raw else None

    @property
    def nb_samples(self) -> Optional[int]:
        """Total sample count if reported."""
        if not self._audio:
            return None
        raw = self._audio.get("nb_samples")
        return int(raw) if raw else None

    @property
    def stream_tags(self) -> Dict[str, str]:
        if not self._audio:
            return {}
        return self._audio.get("tags", {})

    @property
    def all_tags(self) -> Dict[str, str]:
        """Merged format + stream tags."""
        merged = dict(self.format_tags)
        merged.update(self.stream_tags)
        return merged

    # ── Raw access ────────────────────────────────────────────────────────────

    @property
    def raw(self) -> Dict[str, Any]:
        return self._raw

    @property
    def all_streams(self) -> List[Dict]:
        return self._streams


def run_ffprobe(file_path: Path) -> FFprobeResult:
    """
    Run ffprobe on file_path and return structured FFprobeResult.
    Raises FFprobeError if the file cannot be probed.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FFprobeError(f"File not found: {file_path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise FFprobeError(f"ffprobe timed out on: {file_path}")
    except FileNotFoundError:
        raise FFprobeError("ffprobe not found — install ffmpeg")

    if result.returncode != 0:
        stderr = result.stderr.strip()[:200]
        raise FFprobeError(
            f"ffprobe failed (code {result.returncode}): {stderr}"
        )

    if not result.stdout.strip():
        raise FFprobeError(f"ffprobe returned no output for: {file_path}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise FFprobeError(f"ffprobe output not valid JSON: {e}")

    probe = FFprobeResult(data)

    if not probe.has_audio:
        raise FFprobeError(f"No audio stream found in: {file_path}")

    log.debug(
        f"Probed {file_path.name}: "
        f"{probe.codec_name} {probe.sample_rate}Hz "
        f"{probe.channels}ch {probe.duration_seconds:.2f}s"
    )

    return probe
