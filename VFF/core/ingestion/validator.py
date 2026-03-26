"""
VideoForensics — File Validator
The first gate every submitted video must pass before any analysis begins.
Checks format support, file integrity, and size/duration limits.
Nothing enters the system that doesn't pass here.
"""

from __future__ import annotations
import hashlib
import logging
import os
import subprocess
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from config.settings import (
    SUPPORTED_CONTAINERS,
    MAX_FILE_SIZE_GB,
    MAX_DURATION_MINUTES,
    MIN_DURATION_SECONDS,
    HASH_ALGORITHMS,
)

logger = logging.getLogger("vf.ingestion.validator")


@dataclass
class ValidationResult:
    """Outcome of file validation."""
    is_valid: bool
    errors: List[str]          = field(default_factory=list)
    warnings: List[str]        = field(default_factory=list)
    file_size_bytes: int       = 0
    detected_format: str       = ""
    duration_seconds: float    = 0.0
    sha256_hash: str           = ""
    md5_hash: str              = ""

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def summary(self) -> str:
        if self.is_valid:
            return (f"Valid — {self.detected_format.upper()} "
                    f"{self.duration_seconds:.1f}s "
                    f"{self.file_size_bytes / 1e6:.1f}MB")
        return f"Invalid — {'; '.join(self.errors)}"


class FileValidator:
    """
    Validates a video file before ingestion.
    Runs these checks in order:
        1. File exists and is readable
        2. File size within limits
        3. Extension is supported
        4. File is not corrupt (ffprobe can read it)
        5. Duration is within limits
        6. Compute cryptographic hashes
    """

    def validate(self, file_path: Path) -> ValidationResult:
        result = ValidationResult(is_valid=True)
        logger.info(f"Validating: {file_path.name}")

        # ── 1. Existence & Readability ─────────────────────────────────────
        if not file_path.exists():
            result.add_error(f"File not found: {file_path}")
            return result

        if not file_path.is_file():
            result.add_error(f"Path is not a file: {file_path}")
            return result

        if not os.access(file_path, os.R_OK):
            result.add_error(f"File is not readable: {file_path}")
            return result

        # ── 2. File Size ───────────────────────────────────────────────────
        result.file_size_bytes = file_path.stat().st_size

        if result.file_size_bytes == 0:
            result.add_error("File is empty (0 bytes)")
            return result

        max_bytes = MAX_FILE_SIZE_GB * 1024 ** 3
        if result.file_size_bytes > max_bytes:
            result.add_error(
                f"File too large: {result.file_size_bytes / 1e9:.2f}GB "
                f"(limit: {MAX_FILE_SIZE_GB}GB)"
            )
            return result

        # ── 3. Extension Check ─────────────────────────────────────────────
        suffix = file_path.suffix.lower()
        if suffix not in SUPPORTED_CONTAINERS:
            result.add_error(
                f"Unsupported format: '{suffix}'. "
                f"Supported: {', '.join(SUPPORTED_CONTAINERS)}"
            )
            # Don't return — still try ffprobe in case extension is wrong

        result.detected_format = suffix.lstrip(".")

        # ── 4. Structural Integrity via ffprobe ────────────────────────────
        probe_data, probe_error = self._run_ffprobe(file_path)

        if probe_data is None:
            result.add_error(f"File is unreadable or corrupt: {probe_error}")
            return result

        # Confirm at least one video stream exists
        streams = probe_data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams:
            result.add_error("No video stream found in file")
            return result

        # ── 5. Duration ────────────────────────────────────────────────────
        fmt = probe_data.get("format", {})
        try:
            result.duration_seconds = float(fmt.get("duration", 0))
        except (ValueError, TypeError):
            result.duration_seconds = 0.0

        if result.duration_seconds < MIN_DURATION_SECONDS:
            result.add_error(
                f"Video too short: {result.duration_seconds:.2f}s "
                f"(minimum: {MIN_DURATION_SECONDS}s)"
            )
            return result

        max_seconds = MAX_DURATION_MINUTES * 60
        if result.duration_seconds > max_seconds:
            result.add_warning(
                f"Video exceeds recommended length "
                f"({result.duration_seconds / 60:.1f} min > {MAX_DURATION_MINUTES} min). "
                f"Analysis may be slow."
            )

        # ── 6. Cryptographic Hashes ────────────────────────────────────────
        result.sha256_hash, result.md5_hash = self._compute_hashes(file_path)

        if not result.sha256_hash:
            result.add_error("Failed to compute file hashes")
            return result

        logger.info(
            f"Validation passed — {result.detected_format.upper()} "
            f"{result.duration_seconds:.1f}s  "
            f"SHA256={result.sha256_hash[:16]}..."
        )
        return result

    # ── Private Helpers ────────────────────────────────────────────────────────

    def _run_ffprobe(self, file_path: Path) -> Tuple[Optional[dict], str]:
        """
        Run ffprobe and return parsed JSON output.
        Returns (data, error_message). data is None on failure.
        """
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
                timeout=60,
            )
            if result.returncode != 0:
                return None, result.stderr.strip()
            data = json.loads(result.stdout)
            return data, ""
        except subprocess.TimeoutExpired:
            return None, "ffprobe timed out after 60s"
        except json.JSONDecodeError as e:
            return None, f"ffprobe output unparseable: {e}"
        except Exception as e:
            return None, str(e)

    def _compute_hashes(self, file_path: Path) -> Tuple[str, str]:
        """
        Compute SHA-256 and MD5 hashes of the file.
        Reads in chunks to handle large files without memory issues.
        Returns (sha256, md5). Returns ("", "") on failure.
        """
        sha256 = hashlib.sha256()
        md5    = hashlib.md5()
        chunk  = 8 * 1024 * 1024   # 8MB chunks

        try:
            with open(file_path, "rb") as f:
                while True:
                    data = f.read(chunk)
                    if not data:
                        break
                    sha256.update(data)
                    md5.update(data)
            return sha256.hexdigest(), md5.hexdigest()
        except Exception as e:
            logger.error(f"Hash computation failed: {e}")
            return "", ""
