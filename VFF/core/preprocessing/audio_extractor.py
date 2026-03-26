"""
VideoForensics — Audio Extractor
Extracts the audio track from a video file for forensic analysis.
Always extracts to lossless WAV — no lossy re-encoding that would
corrupt the audio forensic signal.
"""

from __future__ import annotations
import logging
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("vf.preprocessing.audio")


@dataclass
class ExtractedAudio:
    """
    The result of audio extraction.
    Holds the audio as a numpy array and its technical parameters.
    """
    file_path: Path
    sample_rate: int
    channels: int
    duration_seconds: float
    samples: Optional[np.ndarray] = None    # Shape: (samples, channels)
    success: bool = True
    error: str = ""

    @property
    def num_samples(self) -> int:
        return len(self.samples) if self.samples is not None else 0

    @property
    def is_mono(self) -> bool:
        return self.channels == 1

    def to_mono(self) -> np.ndarray:
        """Return mono version by averaging channels."""
        if self.samples is None:
            return np.array([])
        if self.channels == 1:
            return self.samples.flatten()
        return self.samples.mean(axis=1)

    def get_channel(self, channel_index: int) -> np.ndarray:
        """Return a single audio channel."""
        if self.samples is None:
            return np.array([])
        if self.channels == 1:
            return self.samples.flatten()
        return self.samples[:, channel_index]


class AudioExtractor:
    """
    Extracts the audio track from a video file.

    Always extracts to 16-bit PCM WAV at the original sample rate.
    This preserves the forensic integrity of the audio signal.
    Lossy re-encoding (MP3, AAC) is never used for the working copy.
    """

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def extract(
        self,
        video_path: Path,
        case_id: str,
        load_samples: bool = True,
    ) -> ExtractedAudio:
        """
        Extract audio from the video to a lossless WAV file.

        Args:
            video_path:   Path to the source video
            case_id:      Case identifier for file organization
            load_samples: If True, load the audio into memory as numpy array.
                         Set False for very long videos to save memory.

        Returns:
            ExtractedAudio with file path and optionally loaded samples.
        """
        audio_dir = self.working_dir / case_id / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        output_path = audio_dir / "audio_track.wav"

        logger.info(f"Extracting audio from {video_path.name}")

        # ── Extract to WAV via ffmpeg ──────────────────────────────────────
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vn",                          # No video
            "-acodec", "pcm_s16le",         # 16-bit PCM — lossless
            "-ar", "44100",                 # Standardize to 44.1kHz
            # Note: we standardize sample rate here for consistency across
            # modules. The original sample rate is preserved in the
            # VideoProfile. Forensic analysis uses this standardized version.
            str(output_path),
            "-y",
            "-loglevel", "error",
        ]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            return ExtractedAudio(
                file_path=output_path,
                sample_rate=0, channels=0, duration_seconds=0,
                success=False, error="ffmpeg timed out"
            )
        except Exception as e:
            return ExtractedAudio(
                file_path=output_path,
                sample_rate=0, channels=0, duration_seconds=0,
                success=False, error=str(e)
            )

        if proc.returncode != 0:
            error = proc.stderr.strip()[:300]
            # Check if it's a "no audio stream" error — this is expected
            # for videos without audio, not a system failure
            if "no audio" in error.lower() or "does not contain" in error.lower():
                logger.info("Video has no audio track")
                return ExtractedAudio(
                    file_path=output_path,
                    sample_rate=0, channels=0, duration_seconds=0,
                    success=False, error="No audio stream in video"
                )
            logger.error(f"Audio extraction failed: {error}")
            return ExtractedAudio(
                file_path=output_path,
                sample_rate=0, channels=0, duration_seconds=0,
                success=False, error=error
            )

        # ── Read WAV metadata and optionally load samples ──────────────────
        return self._read_wav(output_path, load_samples)

    def _read_wav(self, wav_path: Path, load_samples: bool) -> ExtractedAudio:
        """
        Read WAV file metadata and optionally load audio samples.
        Uses stdlib wave module — no external dependencies needed.
        """
        try:
            with wave.open(str(wav_path), 'rb') as wf:
                sample_rate    = wf.getframerate()
                channels       = wf.getnchannels()
                n_frames       = wf.getnframes()
                sampwidth      = wf.getsampwidth()
                duration       = n_frames / sample_rate if sample_rate > 0 else 0.0

                samples = None
                if load_samples:
                    raw = wf.readframes(n_frames)
                    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sampwidth, np.int16)
                    flat = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                    # Normalize to [-1.0, 1.0]
                    flat = flat / float(np.iinfo(dtype).max)
                    if channels > 1:
                        samples = flat.reshape(-1, channels)
                    else:
                        samples = flat.reshape(-1, 1)

            logger.info(
                f"Audio extracted — {sample_rate}Hz {channels}ch "
                f"{duration:.1f}s  samples={'loaded' if load_samples else 'not loaded'}"
            )

            return ExtractedAudio(
                file_path=wav_path,
                sample_rate=sample_rate,
                channels=channels,
                duration_seconds=duration,
                samples=samples,
                success=True,
            )

        except Exception as e:
            logger.error(f"WAV read failed: {e}")
            return ExtractedAudio(
                file_path=wav_path,
                sample_rate=0, channels=0, duration_seconds=0,
                success=False, error=str(e)
            )

    def extract_segment(
        self,
        audio: ExtractedAudio,
        start_seconds: float,
        end_seconds: float,
    ) -> np.ndarray:
        """
        Extract a time segment from loaded audio samples.
        Returns mono float32 array normalized to [-1, 1].
        """
        if audio.samples is None:
            return np.array([])
        sr = audio.sample_rate
        start_idx = int(start_seconds * sr)
        end_idx   = int(end_seconds * sr)
        segment   = audio.samples[start_idx:end_idx]
        return audio.to_mono() if audio.channels > 1 else segment.flatten()
