"""
AudioForensics — ffmpeg Extractor (Phase 1)

Converts any supported audio format to lossless WAV for analysis.

Two outputs per file:
    wav_mono_16k  — 16kHz mono int16 WAV (analysis standard for all modules)
    wav_full      — Original sample rate, original channels, PCM int16
                    (used by compression module for spectral analysis)

The original file is NEVER modified.
Both WAV files are written to a temp directory and cleaned up after analysis.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("af.ingestion.extractor")


class ExtractionError(Exception):
    pass


class AudioExtractor:
    """
    Extracts lossless WAV files from any supported audio format using ffmpeg.
    """

    ANALYSIS_SAMPLE_RATE = 16000   # Hz — forensic speech analysis standard
    ANALYSIS_CHANNELS    = 1       # Mono

    def __init__(self, temp_dir: Optional[Path] = None):
        """
        Args:
            temp_dir: Directory for extracted WAV files.
                      If None, uses system temp directory.
                      Caller is responsible for cleanup.
        """
        if temp_dir is not None:
            self.temp_dir = Path(temp_dir)
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            self._owns_temp = False
        else:
            self._tmpdir    = tempfile.mkdtemp(prefix="af_extract_")
            self.temp_dir   = Path(self._tmpdir)
            self._owns_temp = True

    def extract(self, file_path: Path) -> Tuple[Path, Path]:
        """
        Extract two WAV files from file_path.

        Returns:
            (wav_mono_16k, wav_full) — both as Path objects.

        Raises:
            ExtractionError on any ffmpeg failure.
        """
        file_path = Path(file_path)
        stem      = file_path.stem

        wav_mono = self.temp_dir / f"{stem}_mono16k.wav"
        wav_full = self.temp_dir / f"{stem}_full.wav"

        self._extract_mono_16k(file_path, wav_mono)
        self._extract_full(file_path, wav_full)

        return wav_mono, wav_full

    def extract_mono_16k(self, file_path: Path) -> Path:
        """Extract only the 16kHz mono WAV (faster, for quick analysis)."""
        file_path = Path(file_path)
        wav_mono  = self.temp_dir / f"{file_path.stem}_mono16k.wav"
        self._extract_mono_16k(file_path, wav_mono)
        return wav_mono

    def cleanup(self):
        """Remove temp directory if we created it."""
        if self._owns_temp:
            import shutil
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── Private extraction methods ────────────────────────────────────────────

    def _extract_mono_16k(self, src: Path, dst: Path) -> None:
        """
        16kHz mono int16 PCM WAV.
        This is the standard for all analysis modules:
        - ENF: needs ≥ 16kHz to capture 50Hz and harmonics up to 100Hz
        - Noise: Welch PSD at 16kHz gives 8Hz resolution per bin at 4096-point FFT
        - Voice: F0 and formants typically < 8kHz
        - Reverberation: decay analysis works well at 16kHz
        """
        cmd = [
            "ffmpeg",
            "-y",                           # overwrite without asking
            "-i", str(src),                 # input file
            "-vn",                          # no video
            "-ar", str(self.ANALYSIS_SAMPLE_RATE),  # resample to 16kHz
            "-ac", str(self.ANALYSIS_CHANNELS),      # downmix to mono
            "-acodec", "pcm_s16le",         # 16-bit signed little-endian
            "-f", "wav",                    # WAV container
            str(dst),
        ]
        self._run(cmd, dst)

    def _extract_full(self, src: Path, dst: Path) -> None:
        """
        Original sample rate, original channel count, int16 PCM WAV.
        Used by the compression module for spectral ceiling analysis —
        we need the original Nyquist frequency to see where the codec cut off.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(src),
            "-vn",
            "-acodec", "pcm_s16le",         # lossless PCM, preserve everything else
            "-f", "wav",
            str(dst),
        ]
        self._run(cmd, dst)

    @staticmethod
    def _run(cmd: list, expected_output: Path) -> None:
        """Run an ffmpeg command and verify the output was created."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise ExtractionError(f"ffmpeg timed out extracting: {expected_output}")
        except FileNotFoundError:
            raise ExtractionError("ffmpeg not found — install ffmpeg")

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()[-300:]
            raise ExtractionError(
                f"ffmpeg failed (code {result.returncode}):\n{stderr}"
            )

        if not expected_output.exists() or expected_output.stat().st_size == 0:
            raise ExtractionError(
                f"ffmpeg produced no output at: {expected_output}"
            )

        log.debug(
            f"Extracted: {expected_output.name} "
            f"({expected_output.stat().st_size // 1024} KB)"
        )
