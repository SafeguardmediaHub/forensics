"""
VideoForensics — Audio-Video Sync Analyzer
Detects synchronization issues between audio and video streams.

Forensic relevance:
    In a genuine, single-take recording, audio and video are captured by the
    same device at the same time and remain perfectly synchronized. Editing
    operations that replace, trim, or reorder video segments often introduce
    subtle synchronization errors:

    1. Duration mismatch — audio and video streams of different lengths
       indicates one stream was replaced or the container was re-assembled.

    2. Stream start time offset — legitimate recordings have audio and
       video starting at the same PTS. An offset between them suggests
       independent stream manipulation.

    3. Bitrate signature mismatch — audio and video encoded at different
       times often show different bitrate signatures embedded in the codec
       headers, even when the container reports the same timestamp.

    4. Audio codec discontinuity — within a single recording the audio
       codec parameters (sample rate, channel count, bitrate) are fixed.
       A codec change mid-stream indicates re-encoding at a boundary.

Note on ENF (Electric Network Frequency):
    Mains hum (50/60 Hz) phase tracking is the gold standard for audio
    forensics but requires recordings with detectable mains hum. Synthetic
    audio and professionally recorded content rarely contains ENF.
    This analyzer implements a basic ENF check but treats the result
    with appropriate uncertainty when ENF amplitude is below threshold.
"""

from __future__ import annotations
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger("vf.audio.sync")


@dataclass
class AVSyncResult:
    """Results of audio-video synchronization analysis."""

    # Duration consistency
    video_duration: float              = 0.0
    audio_duration: float              = 0.0
    duration_mismatch_s: float         = 0.0
    duration_mismatch_score: float     = 0.0

    # Stream timing
    video_start_pts: float             = 0.0
    audio_start_pts: float             = 0.0
    start_offset_s: float              = 0.0
    start_offset_score: float          = 0.0

    # Audio codec parameters
    sample_rate: int                   = 0
    channels: int                      = 0
    audio_codec: str                   = ""
    audio_bitrate: int                 = 0

    # ENF check
    enf_detected: bool                 = False
    enf_amplitude: float               = 0.0
    enf_phase_discontinuities: int     = 0
    enf_score: float                   = 0.0

    # Overall
    tampering_probability: float       = 0.0
    findings: List[str]                = field(default_factory=list)

    def add_finding(self, msg: str) -> None:
        self.findings.append(msg)


class AVSyncAnalyzer:
    """
    Analyzes audio-video synchronization for forensic anomalies.
    Operates on the video container file directly via ffprobe.
    """

    # Duration mismatch thresholds (seconds)
    DURATION_CLEAN   = 0.10    # within 100ms = clean
    DURATION_SUSPECT = 0.33    # > 330ms = suspect
    DURATION_CERTAIN = 1.00    # > 1s = highly suspicious

    # PTS start offset thresholds
    OFFSET_CLEAN   = 0.05    # 50ms
    OFFSET_SUSPECT = 0.20    # 200ms
    OFFSET_CERTAIN = 0.50    # 500ms

    # ENF detection thresholds
    ENF_MIN_AMPLITUDE    = 1e-4    # Below this = no ENF present
    ENF_NOMINAL_HZ       = 60.0   # Use 60Hz (US); 50Hz also checked
    ENF_BAND_HZ          = 0.5
    ENF_PHASE_JUMP_RAD   = np.pi / 4   # 45 degrees = significant jump

    def analyze(
        self,
        video_path: Path,
        samples: Optional[np.ndarray] = None,
        sr: Optional[int] = None,
    ) -> AVSyncResult:
        """
        Analyze AV sync properties of a video file.

        Args:
            video_path: Path to the video file
            samples:    Pre-extracted mono audio samples (optional)
            sr:         Sample rate of provided samples (optional)
        """
        result = AVSyncResult()

        # ── 1. Container-level stream metadata ────────────────────────────
        self._analyze_container(video_path, result)

        # ── 2. ENF analysis (if audio samples provided) ───────────────────
        if samples is not None and sr is not None and len(samples) > sr:
            self._analyze_enf(samples, sr, result)

        # ── Overall score ──────────────────────────────────────────────────
        result.tampering_probability = self._compute_score(result)

        # ── Findings ──────────────────────────────────────────────────────
        self._generate_findings(result)

        logger.info(
            f"AV sync analysis — "
            f"dur_mismatch={result.duration_mismatch_s:.3f}s "
            f"start_offset={result.start_offset_s:.3f}s "
            f"enf={result.enf_detected} "
            f"prob={result.tampering_probability:.3f}"
        )
        return result

    def _analyze_container(self, video_path: Path, result: AVSyncResult) -> None:
        """Extract stream timing from ffprobe output."""
        try:
            proc = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams", "-show_format",
                    str(video_path),
                ],
                capture_output=True, text=True, timeout=30
            )
            data = json.loads(proc.stdout)
        except Exception as e:
            logger.warning(f"ffprobe failed: {e}")
            return

        streams = data.get("streams", [])
        fmt     = data.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream or not audio_stream:
            result.add_finding("Could not find both audio and video streams in container")
            return

        # Duration
        v_dur = self._safe_float(video_stream.get("duration"))
        a_dur = self._safe_float(audio_stream.get("duration"))

        # Fall back to format duration if stream duration unavailable
        if v_dur is None or a_dur is None:
            fmt_dur = self._safe_float(fmt.get("duration"))
            if fmt_dur:
                v_dur = v_dur or fmt_dur
                a_dur = a_dur or fmt_dur

        if v_dur is not None and a_dur is not None:
            result.video_duration     = v_dur
            result.audio_duration     = a_dur
            result.duration_mismatch_s = abs(v_dur - a_dur)
            result.duration_mismatch_score = self._threshold_score(
                result.duration_mismatch_s,
                self.DURATION_CLEAN, self.DURATION_SUSPECT, self.DURATION_CERTAIN
            )

        # Start PTS
        v_start = self._safe_float(video_stream.get("start_time"))
        a_start = self._safe_float(audio_stream.get("start_time"))
        if v_start is not None and a_start is not None:
            result.video_start_pts   = v_start
            result.audio_start_pts   = a_start
            result.start_offset_s    = abs(v_start - a_start)
            result.start_offset_score = self._threshold_score(
                result.start_offset_s,
                self.OFFSET_CLEAN, self.OFFSET_SUSPECT, self.OFFSET_CERTAIN
            )

        # Audio codec parameters
        result.audio_codec   = audio_stream.get("codec_name", "unknown")
        result.sample_rate   = int(audio_stream.get("sample_rate", 0) or 0)
        result.channels      = int(audio_stream.get("channels", 0) or 0)
        result.audio_bitrate = int(audio_stream.get("bit_rate", 0) or 0)

    def _analyze_enf(
        self, samples: np.ndarray, sr: int, result: AVSyncResult
    ) -> None:
        """
        Attempt to detect and analyze Electric Network Frequency (ENF).
        ENF is mains hum (50/60 Hz) imprinted on recordings by nearby
        electrical devices. Phase continuity of ENF proves temporal
        authenticity within a recording.
        """
        from scipy.signal import butter, filtfilt, hilbert

        for nominal_hz in [60.0, 50.0]:
            band = self.ENF_BAND_HZ
            nyq  = sr / 2
            low  = (nominal_hz - band) / nyq
            high = (nominal_hz + band) / nyq

            if low <= 0 or high >= 1:
                continue

            try:
                b, a     = butter(4, [low, high], btype='band')
                filtered = filtfilt(b, a, samples)
            except Exception:
                continue

            amplitude = float(np.sqrt(np.mean(filtered ** 2)))

            if amplitude < self.ENF_MIN_AMPLITUDE:
                continue   # No detectable ENF at this frequency

            # ENF present — analyze phase continuity
            result.enf_detected   = True
            result.enf_amplitude  = amplitude

            analytic = hilbert(filtered)
            phase    = np.unwrap(np.angle(analytic))

            # Expected phase rate: 2π * nominal_hz / sr
            expected_rate = 2 * np.pi * nominal_hz / sr
            phase_rate    = np.diff(phase)
            deviation     = np.abs(phase_rate - expected_rate)

            # Flag samples where phase jumps by more than threshold
            jumps = np.sum(deviation > self.ENF_PHASE_JUMP_RAD)
            result.enf_phase_discontinuities = int(jumps)

            jump_rate = jumps / len(deviation)
            if jump_rate > 0.01:   # More than 1% of samples have phase jumps
                result.enf_score = min(1.0, jump_rate * 20)

            logger.info(
                f"ENF detected at {nominal_hz}Hz — "
                f"amplitude={amplitude:.6f} "
                f"phase_jumps={jumps} ({jump_rate:.4f})"
            )
            break  # Analyze at first detected frequency only

    @staticmethod
    def _threshold_score(
        value: float,
        clean: float,
        suspect: float,
        certain: float,
    ) -> float:
        """Map a value to a 0–1 score using three threshold levels."""
        if value <= clean:
            return 0.0
        elif value >= certain:
            return 1.0
        elif value >= suspect:
            return 0.50 + 0.50 * (value - suspect) / (certain - suspect)
        else:
            return 0.20 * (value - clean) / (suspect - clean)

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        """Safely convert a value to float, returning None on failure."""
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _compute_score(self, result: AVSyncResult) -> float:
        """Aggregate AV sync signals into 0–1 tampering probability."""
        score = 0.0

        # Duration mismatch
        score = max(score, result.duration_mismatch_score * 0.70)

        # Start time offset
        score = max(score, result.start_offset_score * 0.60)

        # ENF phase discontinuities (strongest signal when ENF is present)
        if result.enf_detected and result.enf_score > 0:
            score = max(score, result.enf_score * 0.80)

        return min(1.0, max(0.0, score))

    def _generate_findings(self, result: AVSyncResult) -> None:
        """Generate human-readable findings."""

        if result.duration_mismatch_score > 0.20:
            result.add_finding(
                f"Audio/video duration mismatch of {result.duration_mismatch_s:.3f}s "
                f"(video={result.video_duration:.3f}s, audio={result.audio_duration:.3f}s). "
                f"In a genuine recording, audio and video streams have equal durations. "
                f"A mismatch suggests one stream was independently replaced, "
                f"trimmed, or re-encoded."
            )

        if result.start_offset_score > 0.20:
            result.add_finding(
                f"Audio/video start time offset of {result.start_offset_s:.3f}s "
                f"(video_pts={result.video_start_pts:.3f}s, "
                f"audio_pts={result.audio_start_pts:.3f}s). "
                f"A significant PTS offset between streams indicates they were "
                f"assembled from different source material."
            )

        if result.enf_detected and result.enf_score > 0.30:
            result.add_finding(
                f"ENF phase discontinuities detected "
                f"({result.enf_phase_discontinuities} samples, "
                f"score={result.enf_score:.3f}). "
                f"Electric Network Frequency (mains hum) phase should be continuous "
                f"within a genuine recording. Phase jumps indicate the recording "
                f"was assembled from segments recorded at different times."
            )
