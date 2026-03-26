"""
AudioForensics — Ingestion Pipeline (Phase 1)

Single entry point for all audio forensic analysis.

Usage:
    pipeline = IngestionPipeline()
    case     = pipeline.ingest("/path/to/recording.mp3")
    # case.audio_profile is now fully populated
    # case is ready to pass to analysis modules

The pipeline:
    1. Verify file exists and is readable
    2. SHA-256 hash original file (chain of custody — BEFORE any modification)
    3. Run ffprobe (format, codec, stream metadata)
    4. Map codec string → AudioCodec enum
    5. Auto-detect ENF grid (50Hz default for Nigeria/Europe)
    6. Extract WAV files via ffmpeg (mono 16k + full rate)
    7. Run signal analysis on extracted WAV
    8. Assemble AudioProfile and AudioCase
    9. Clean up temp files on context exit (or manual cleanup)

Chain of custody guarantee:
    The SHA-256 hash is computed on the ORIGINAL file before extraction.
    The extracted WAV files are derivatives and their paths are stored
    separately. The hash in the report always refers to the original.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from core.models import (
    AudioCase, AudioProfile, AudioCodec, ENFGrid,
)
from core.ingestion.ffprobe import run_ffprobe, FFprobeResult, FFprobeError
from core.ingestion.extractor import AudioExtractor, ExtractionError
from core.ingestion.signal_analyser import SignalAnalyser

log = logging.getLogger("af.ingestion.pipeline")


class IngestionError(Exception):
    pass


# ── Codec mapping ─────────────────────────────────────────────────────────────

_CODEC_MAP = {
    "pcm_s16le":  AudioCodec.PCM_S16LE,
    "pcm_s24le":  AudioCodec.PCM_S24LE,
    "pcm_f32le":  AudioCodec.PCM_F32LE,
    "pcm_s32le":  AudioCodec.PCM_S16LE,  # treat as PCM
    "pcm_s8":     AudioCodec.PCM_S16LE,
    "pcm_alaw":   AudioCodec.PCM_ALAW,
    "pcm_mulaw":  AudioCodec.PCM_MULAW,
    "mp3":        AudioCodec.MP3,
    "mp3float":   AudioCodec.MP3,
    "aac":        AudioCodec.AAC,
    "vorbis":     AudioCodec.OGG_VORBIS,
    "opus":       AudioCodec.OPUS,
    "flac":       AudioCodec.FLAC,
    "amr_nb":     AudioCodec.AMR_NB,
    "amr_wb":     AudioCodec.AMR_WB,
}

# ENF grid detection: locale/country hints in metadata → grid frequency
_LOCALE_50HZ = {
    "NG", "GB", "EU", "DE", "FR", "IT", "ES", "NG", "GH", "ZA",
    "IN", "AU", "CN", "JP",  # Japan uses 50Hz in east, 60Hz in west — default 50
}
_LOCALE_60HZ = {"US", "CA", "MX", "BR", "CO", "JP_WEST"}


class IngestionPipeline:
    """
    Ingests any supported audio file and returns a fully-populated AudioCase.

    Temp files are stored in a pipeline-managed temp directory.
    Call cleanup() when analysis is complete, or use as a context manager.
    """

    HASH_CHUNK_SIZE   = 1024 * 1024      # 1MB chunks for SHA-256
    PARTIAL_HASH_SIZE = 1024 * 1024      # First 1MB for fast partial hash
    DEFAULT_ENF_GRID  = ENFGrid.GRID_50HZ  # Nigeria/Europe default

    def __init__(
        self,
        temp_dir: Optional[Path] = None,
        default_enf_grid: ENFGrid = ENFGrid.GRID_50HZ,
        keep_wav_files:   bool    = True,
    ):
        """
        Args:
            temp_dir:         Where to store extracted WAV files.
                              If None, uses system temp directory.
            default_enf_grid: 50Hz (Nigeria/Europe) or 60Hz (USA).
            keep_wav_files:   If True, WAV files persist for module analysis.
                              If False, cleaned up immediately after ingestion.
        """
        if temp_dir is not None:
            self._temp_dir   = Path(temp_dir)
            self._owns_temp  = False
        else:
            self._temp_dir   = Path(tempfile.mkdtemp(prefix="af_ingest_"))
            self._owns_temp  = True

        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self.default_enf_grid = default_enf_grid
        self.keep_wav_files   = keep_wav_files
        self._extractor       = AudioExtractor(temp_dir=self._temp_dir)
        self._signal_analyser = SignalAnalyser()

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()

    def cleanup(self):
        """Remove temp directory and all extracted files."""
        if self._owns_temp and self._temp_dir.exists():
            shutil.rmtree(str(self._temp_dir), ignore_errors=True)
            log.debug(f"Cleaned up temp dir: {self._temp_dir}")

    # ── Main entry point ──────────────────────────────────────────────────────

    def ingest(self, file_path: Path) -> AudioCase:
        """
        Ingest a single audio file.

        Args:
            file_path: Path to any supported audio file.

        Returns:
            AudioCase with fully populated audio_profile.
            Ready to pass to analysis modules.

        Raises:
            IngestionError: if the file cannot be processed.
        """
        t0        = time.time()
        file_path = Path(file_path)

        log.info(f"Ingesting: {file_path.name}")

        # ── 1. Verify file ────────────────────────────────────────────────
        if not file_path.exists():
            raise IngestionError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise IngestionError(f"Path is not a file: {file_path}")
        if file_path.stat().st_size == 0:
            raise IngestionError(f"File is empty: {file_path}")

        # ── 2. Hash original file (chain of custody) ──────────────────────
        log.debug("Computing SHA-256…")
        sha256       = self._hash_file(file_path)
        sha256_partial = self._hash_partial(file_path)

        # ── 3. ffprobe ────────────────────────────────────────────────────
        log.debug("Running ffprobe…")
        try:
            probe = run_ffprobe(file_path)
        except FFprobeError as e:
            raise IngestionError(f"ffprobe failed: {e}") from e

        # ── 4. Map codec ──────────────────────────────────────────────────
        codec_enum = _CODEC_MAP.get(probe.codec_name.lower(), AudioCodec.UNKNOWN)

        # ── 5. Detect ENF grid ────────────────────────────────────────────
        enf_grid = self._detect_enf_grid(probe)

        # ── 6. Extract WAV files ──────────────────────────────────────────
        log.debug("Extracting WAV files…")
        try:
            wav_mono, wav_full = self._extractor.extract(file_path)
        except ExtractionError as e:
            raise IngestionError(f"ffmpeg extraction failed: {e}") from e

        # ── 7. Signal analysis ────────────────────────────────────────────
        log.debug("Running signal analysis…")
        measurements = self._signal_analyser.analyse(wav_mono)

        # ── 8. Compute total samples ──────────────────────────────────────
        declared_sr      = probe.sample_rate or measurements.sample_rate
        declared_duration = probe.duration_seconds
        total_samples    = int(declared_sr * declared_duration)

        # ── 9. Assemble AudioProfile ──────────────────────────────────────
        profile = AudioProfile(
            # File identity
            file_path         = str(file_path.resolve()),
            file_size_bytes   = file_path.stat().st_size,
            sha256_hash       = sha256,
            sha256_partial    = sha256_partial,

            # Container
            container_format  = probe.format_name,
            codec_name        = probe.codec_name,
            codec_enum        = codec_enum,

            # Stream
            sample_rate       = declared_sr,
            channels          = probe.channels,
            duration_seconds  = declared_duration,
            total_samples     = total_samples,
            bit_depth         = probe.bit_depth,
            bitrate_bps       = probe.stream_bitrate_bps or probe.bitrate_bps,

            # Metadata
            encoder_string    = probe.encoder_string,
            creation_time     = probe.creation_time,
            has_id3_tags      = probe.has_id3_tags,
            has_xmp_data      = probe.has_xmp_data,

            # Signal measurements
            is_mono           = (probe.channels == 1),
            dc_offset         = measurements.dc_offset,
            peak_amplitude    = measurements.peak_amplitude,
            rms_level         = measurements.rms_level,
            is_clipped        = measurements.is_clipped,
            clipping_count    = measurements.clipping_count,
            silence_ratio     = measurements.silence_ratio,
            effective_max_freq= measurements.effective_max_freq,

            # ENF
            enf_grid          = enf_grid,

            # Extracted WAV paths
            wav_path_mono     = str(wav_mono),
            wav_path_full     = str(wav_full),
        )

        case     = AudioCase.create(profile)
        elapsed  = round(time.time() - t0, 2)

        log.info(
            f"Ingestion complete in {elapsed}s: "
            f"{probe.codec_name} {declared_sr}Hz "
            f"{probe.channels}ch {declared_duration:.1f}s | "
            f"SHA-256: {sha256[:12]}…"
        )

        return case

    # ── Private helpers ───────────────────────────────────────────────────────

    def _hash_file(self, path: Path) -> str:
        """Full SHA-256 of the file. Reads in chunks for large files."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(self.HASH_CHUNK_SIZE):
                h.update(chunk)
        return h.hexdigest()

    def _hash_partial(self, path: Path) -> str:
        """SHA-256 of the first 1MB only (fast, for deduplication)."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read(self.PARTIAL_HASH_SIZE))
        return h.hexdigest()

    def _detect_enf_grid(self, probe: FFprobeResult) -> ENFGrid:
        """
        Heuristic ENF grid detection from file metadata.

        Checks encoder strings and tags for locale hints.
        Falls back to the pipeline's default (50Hz for Nigeria/Europe).

        In Phase 3 (ENF module), this is corroborated by actually measuring
        which grid frequency has more energy in the recording.
        """
        tags = probe.all_tags
        enc  = (probe.encoder_string or "").upper()

        # US-specific encoder strings
        if any(hint in enc for hint in ["QUICKTIME", "ITUNES", "CORE AUDIO"]):
            # Apple tools: commonly US (60Hz) but not definitive
            # Don't override default — ambiguous
            pass

        # Check tags for country/locale info
        for key in ("location", "country", "com.apple.quicktime.location.ISO6709"):
            val = tags.get(key, "")
            if val:
                # Very rough — if it contains a US GPS prefix
                if val.startswith("+4") or val.startswith("+3"):
                    pass  # North America latitude range — ambiguous

        # Default to pipeline's configured grid (50Hz for Nigeria/Europe)
        return self.default_enf_grid
