"""
Video Forensics Module
----------------------
Analyses the compressed bitstream, codec metadata, deepfake indicators,
motion field consistency, and ENF signal of video files.

Detection classes
-----------------
CodecBitstreamForensics   — double-encoding (DCT Benford), bitrate anomalies,
                            codec inconsistency (ffprobe, no extra deps)
MetadataChainForensics    — container structure, timestamp discrepancies,
                            encoder fingerprinting (ffprobe + optional pymediainfo)
DeepfakeDetector          — face detection (OpenCV DNN), temporal consistency,
                            frequency artifacts (2-D FFT on face crops)
MotionVectorForensics     — Farneback dense optical flow, composite region detection
ENFVideoForensics         — luminance ENF extraction, phase-jump discontinuity

Orchestrator
------------
VideoForensicsEngine.analyze_video() → VideoForensicResult
"""

import os
import json
import uuid
import subprocess
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np
from scipy.signal import butter, sosfiltfilt
from scipy.stats import zscore as scipy_zscore

from frame_analysis import Severity
from forensic_config import (
    FA_SEV_HIGH_PTS, FA_SEV_MEDIUM_PTS, FA_SEV_LOW_PTS,
    VFO_TAMPERED_CONFIDENCE, VFO_REVIEW_CONFIDENCE,
    VFO_BITRATE_ZSCORE_THRESHOLD, VFO_DOUBLE_ENC_BENFORD_THRESH,
    VFO_FACE_CONSISTENCY_ZSCORE, VFO_ENF_PHASE_ZSCORE,
    VFO_MOTION_DIVERGENCE_RATIO, VFO_TIMESTAMP_DELTA_HOURS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency: pymediainfo
# ---------------------------------------------------------------------------
try:
    from pymediainfo import MediaInfo as _PyMediaInfo
    _HAS_PYMEDIAINFO = True
except ImportError:
    _HAS_PYMEDIAINFO = False
    logger.info("pymediainfo not installed — falling back to ffprobe for container metadata")

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VideoForensicFindingType(Enum):
    DOUBLE_ENCODING             = "DOUBLE_ENCODING"
    CODEC_INCONSISTENCY         = "CODEC_INCONSISTENCY"
    BITRATE_ANOMALY             = "BITRATE_ANOMALY"
    METADATA_TAMPERING          = "METADATA_TAMPERING"
    ENCODER_MISMATCH            = "ENCODER_MISMATCH"
    CONTAINER_ANOMALY           = "CONTAINER_ANOMALY"
    DEEPFAKE_INDICATORS         = "DEEPFAKE_INDICATORS"
    FACE_TEMPORAL_INCONSISTENCY = "FACE_TEMPORAL_INCONSISTENCY"
    FREQUENCY_ARTIFACTS         = "FREQUENCY_ARTIFACTS"
    MOTION_VECTOR_ANOMALY       = "MOTION_VECTOR_ANOMALY"
    COMPOSITE_REGION            = "COMPOSITE_REGION"
    ENF_DISCONTINUITY           = "ENF_DISCONTINUITY"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class VideoForensicFinding:
    id: str
    type: VideoForensicFindingType
    severity: Severity
    location: Dict[str, Any]        # {'timestamp': float} or {'start': float, 'end': float}
    explanation: str
    metrics: Dict[str, float]
    evidence_artifacts: List[str]   # paths to heatmaps / screenshots


@dataclass
class VideoForensicResult:
    job_id: str
    media_info: Dict[str, Any]
    findings: List[VideoForensicFinding]
    verdict: str                    # LIKELY_AUTHENTIC | REVIEW_RECOMMENDED | LIKELY_TAMPERED
    confidence: float               # 0.0 – 100.0
    tampering_types: List[str]      # which finding categories fired
    verdict_explanation: str
    analysis_modules: Dict[str, Any]  # per-module raw results
    created_at: str
    completed_at: str

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_ffprobe(args: List[str]) -> Optional[Dict]:
    """Run ffprobe with the given arguments and return parsed JSON, or None on error."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning(f"ffprobe non-zero exit: {result.stderr[:200]}")
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning(f"ffprobe error: {e}")
        return None


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _make_finding(
    ftype: VideoForensicFindingType,
    severity: Severity,
    location: Dict[str, Any],
    explanation: str,
    metrics: Dict[str, float],
    artifacts: Optional[List[str]] = None,
) -> VideoForensicFinding:
    return VideoForensicFinding(
        id=str(uuid.uuid4()),
        type=ftype,
        severity=severity,
        location=location,
        explanation=explanation,
        metrics=metrics,
        evidence_artifacts=artifacts or [],
    )

# ---------------------------------------------------------------------------
# 1. CodecBitstreamForensics
# ---------------------------------------------------------------------------

class CodecBitstreamForensics:
    """
    Analyses codec-level signals using ffprobe only (no additional deps).
    """

    # Expected first-digit probabilities under Benford's law
    _BENFORD = np.array([
        0.301, 0.176, 0.125, 0.097, 0.079,
        0.067, 0.058, 0.051, 0.046
    ])

    def __init__(self, video_path: str):
        self.video_path = video_path

    def extract_codec_parameters(self) -> Dict[str, Any]:
        """Return base codec / format info via ffprobe."""
        data = _run_ffprobe(["-show_streams", "-show_format", self.video_path])
        if not data:
            return {}
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {}
        )
        fmt = data.get("format", {})
        return {
            "codec_name":    video_stream.get("codec_name"),
            "profile":       video_stream.get("profile"),
            "level":         video_stream.get("level"),
            "width":         video_stream.get("width"),
            "height":        video_stream.get("height"),
            "avg_frame_rate": video_stream.get("avg_frame_rate"),
            "bit_rate":      _safe_float(fmt.get("bit_rate")),
            "duration":      _safe_float(fmt.get("duration")),
            "encoder_tag":   video_stream.get("tags", {}).get("encoder", ""),
            "creation_time": fmt.get("tags", {}).get("creation_time", ""),
            "format_name":   fmt.get("format_name", ""),
            "streams_raw":   data.get("streams", []),
        }

    # ------------------------------------------------------------------
    def detect_double_encoding(self, sampled_frames: List[Tuple[float, np.ndarray]]) -> Optional[VideoForensicFinding]:
        """
        Sample up to 8 key frames, extract 8×8 DCT blocks on the Y channel,
        build a first-digit histogram of non-zero AC coefficients, and test
        against Benford's law.  A poor fit suggests re-encoding artifacts.
        """
        if not sampled_frames:
            return None

        step = max(1, len(sampled_frames) // 8)
        selected = [sampled_frames[i] for i in range(0, len(sampled_frames), step)][:8]

        digit_counts = np.zeros(9, dtype=np.float64)

        for _ts, frame in selected:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            h, w = gray.shape
            # Iterate over 8×8 blocks
            for row in range(0, h - 7, 8):
                for col in range(0, w - 7, 8):
                    block = gray[row:row + 8, col:col + 8]
                    dct_block = cv2.dct(block)
                    # Skip DC (0,0); gather AC magnitudes
                    ac = np.abs(dct_block.flatten()[1:])
                    ac = ac[ac >= 1.0]   # ignore near-zero
                    for val in ac:
                        d = int(str(int(val))[0])
                        if 1 <= d <= 9:
                            digit_counts[d - 1] += 1

        total = digit_counts.sum()
        if total < 100:
            return None  # not enough data

        observed = digit_counts / total
        # Mean absolute deviation from Benford's law
        mad = float(np.mean(np.abs(observed - self._BENFORD)))

        if mad >= VFO_DOUBLE_ENC_BENFORD_THRESH:
            severity = Severity.HIGH if mad >= VFO_DOUBLE_ENC_BENFORD_THRESH * 1.5 else Severity.MEDIUM
            return _make_finding(
                VideoForensicFindingType.DOUBLE_ENCODING,
                severity,
                {"start": 0.0, "end": -1.0},
                (
                    f"DCT coefficient first-digit distribution deviates from Benford's law "
                    f"(MAD={mad:.4f}, threshold={VFO_DOUBLE_ENC_BENFORD_THRESH}). "
                    "This is consistent with lossy re-encoding of a previously compressed video."
                ),
                {"benford_mad": mad, "samples_blocks": int(total)},
            )
        return None

    # ------------------------------------------------------------------
    def profile_bitrate_segments(self) -> List[VideoForensicFinding]:
        """
        Use ffprobe show_packets to compute per-second bitrate.
        Flag z-score outliers above VFO_BITRATE_ZSCORE_THRESHOLD.
        """
        data = _run_ffprobe([
            "-show_packets", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,size",
            self.video_path,
        ])
        if not data:
            return []

        packets = data.get("packets", [])
        if len(packets) < 10:
            return []

        # Group packet sizes into 1-second buckets
        bucket: Dict[int, float] = {}
        for pkt in packets:
            pts = _safe_float(pkt.get("pts_time", -1))
            size = _safe_float(pkt.get("size", 0))
            if pts < 0:
                continue
            sec = int(pts)
            bucket[sec] = bucket.get(sec, 0.0) + size * 8  # bits

        if len(bucket) < 4:
            return []

        secs = sorted(bucket)
        rates = np.array([bucket[s] for s in secs], dtype=np.float64)

        if rates.std() == 0:
            return []

        z = scipy_zscore(rates)
        findings: List[VideoForensicFinding] = []

        for idx, (sec, zval) in enumerate(zip(secs, z)):
            if abs(zval) >= VFO_BITRATE_ZSCORE_THRESHOLD:
                severity = Severity.HIGH if abs(zval) >= VFO_BITRATE_ZSCORE_THRESHOLD * 1.5 else Severity.MEDIUM
                findings.append(_make_finding(
                    VideoForensicFindingType.BITRATE_ANOMALY,
                    severity,
                    {"timestamp": float(sec)},
                    (
                        f"Bitrate spike at t={sec}s (z={zval:.2f}): "
                        f"{rates[idx] / 1e6:.2f} Mbps vs mean {rates.mean() / 1e6:.2f} Mbps. "
                        "An abrupt bitrate jump can indicate a splice point."
                    ),
                    {"z_score": float(zval), "bitrate_bps": float(rates[idx]), "mean_bitrate_bps": float(rates.mean())},
                ))

        return findings

    # ------------------------------------------------------------------
    def detect_encoder_inconsistency(self) -> Optional[VideoForensicFinding]:
        """
        Compare stream-level encoder tag, profile, level, and SAR between
        the first/last 20 % of stream packets as reported by ffprobe.
        A mismatch suggests the video was assembled from segments with different
        encoding parameters.
        """
        data = _run_ffprobe(["-show_streams", self.video_path])
        if not data:
            return None

        streams = data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]

        if len(video_streams) < 2:
            # Only one video stream — check for self-inconsistency in tags
            if not video_streams:
                return None
            vs = video_streams[0]
            tags = vs.get("tags", {})
            # Check if claimed codec name disagrees with detected codec_long_name
            encoder = tags.get("encoder", "").lower()
            codec   = vs.get("codec_name", "").lower()
            if encoder and codec and codec not in encoder and encoder not in codec:
                # e.g. encoder tag says "Final Cut" but codec is h265
                return _make_finding(
                    VideoForensicFindingType.CODEC_INCONSISTENCY,
                    Severity.LOW,
                    {"start": 0.0, "end": -1.0},
                    (
                        f"Encoder tag ('{tags.get('encoder')}') is inconsistent with "
                        f"the detected codec ('{vs.get('codec_name')}'). "
                        "This may indicate metadata manipulation."
                    ),
                    {"encoder_tag": encoder, "detected_codec": codec},
                )
            return None

        # Multiple video streams — compare profiles / levels across them
        profiles = [s.get("profile", "") for s in video_streams]
        levels   = [s.get("level", -1) for s in video_streams]

        mismatches = []
        if len(set(profiles)) > 1:
            mismatches.append(f"profile mismatch: {profiles}")
        if len(set(levels)) > 1:
            mismatches.append(f"level mismatch: {levels}")

        if mismatches:
            return _make_finding(
                VideoForensicFindingType.CODEC_INCONSISTENCY,
                Severity.MEDIUM,
                {"start": 0.0, "end": -1.0},
                (
                    "Multiple video streams with inconsistent codec parameters detected: "
                    + "; ".join(mismatches) + ". "
                    "This may indicate the video was assembled from disparate source clips."
                ),
                {"profiles": str(profiles), "levels": str(levels)},
            )
        return None


# ---------------------------------------------------------------------------
# 2. MetadataChainForensics
# ---------------------------------------------------------------------------

class MetadataChainForensics:
    """
    Analyses container metadata, timestamps, and encoder fingerprints.
    Uses ffprobe; if pymediainfo is available it also checks moov atom position.
    """

    def __init__(self, video_path: str):
        self.video_path = video_path

    def audit_container_metadata(self) -> Optional[VideoForensicFinding]:
        """
        Checks for unusual container structure issues such as moov atom at the
        end of the file (makes streaming impossible and is unusual for edited
        files), or unusual private boxes.
        """
        issues: List[str] = []

        if _HAS_PYMEDIAINFO:
            try:
                mi = _PyMediaInfo.parse(self.video_path)
                for track in mi.tracks:
                    if hasattr(track, "isstreamable") and track.isstreamable == "No":
                        issues.append("moov atom is at the end of the file (not streamable)")
            except Exception as e:
                logger.debug(f"pymediainfo audit failed: {e}")

        # Fallback: use ffprobe format tags for hints
        data = _run_ffprobe(["-show_format", self.video_path])
        if data:
            fmt_name = data.get("format", {}).get("format_name", "")
            # Unusual: raw video formats used as container
            suspicious_formats = {"rawvideo", "avi", "asf"}
            for sf in suspicious_formats:
                if sf in fmt_name.lower() and "mp4" not in fmt_name.lower():
                    issues.append(f"unusual container format detected: {fmt_name}")

        if issues:
            return _make_finding(
                VideoForensicFindingType.CONTAINER_ANOMALY,
                Severity.LOW,
                {"start": 0.0, "end": -1.0},
                "Container structure issues: " + "; ".join(issues),
                {"issues_count": len(issues)},
            )
        return None

    # ------------------------------------------------------------------
    def detect_timestamp_anomalies(self) -> Optional[VideoForensicFinding]:
        """
        Compare creation_time tag vs encoded_date vs file mtime.
        Flag discrepancies larger than VFO_TIMESTAMP_DELTA_HOURS.
        """
        data = _run_ffprobe(["-show_format", "-show_streams", self.video_path])
        if not data:
            return None

        fmt_tags   = data.get("format", {}).get("tags", {})
        all_tags: Dict[str, str] = dict(fmt_tags)
        for stream in data.get("streams", []):
            all_tags.update(stream.get("tags", {}))

        time_keys = ["creation_time", "encoded_date", "date"]
        parsed_times: List[Tuple[str, datetime]] = []

        for key in time_keys:
            val = all_tags.get(key, "")
            if not val:
                continue
            for fmt_str in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
            ):
                try:
                    parsed_times.append((key, datetime.strptime(val[:26], fmt_str)))
                    break
                except ValueError:
                    continue

        # Add file mtime
        try:
            mtime = datetime.utcfromtimestamp(os.path.getmtime(self.video_path))
            parsed_times.append(("file_mtime", mtime))
        except OSError:
            pass

        if len(parsed_times) < 2:
            return None

        max_delta_h = 0.0
        earliest_key = latest_key = ""
        earliest = latest = parsed_times[0][1]

        for key, dt in parsed_times:
            if dt < earliest:
                earliest, earliest_key = dt, key
            if dt > latest:
                latest, latest_key = dt, key

        delta_h = (latest - earliest).total_seconds() / 3600.0
        max_delta_h = delta_h

        if max_delta_h > VFO_TIMESTAMP_DELTA_HOURS:
            severity = Severity.HIGH if max_delta_h > VFO_TIMESTAMP_DELTA_HOURS * 5 else Severity.MEDIUM
            return _make_finding(
                VideoForensicFindingType.METADATA_TAMPERING,
                severity,
                {"start": 0.0, "end": -1.0},
                (
                    f"Timestamp discrepancy of {max_delta_h:.1f} h between "
                    f"'{earliest_key}' ({earliest.isoformat()}) and "
                    f"'{latest_key}' ({latest.isoformat()}). "
                    f"Expected delta < {VFO_TIMESTAMP_DELTA_HOURS} h."
                ),
                {"delta_hours": round(max_delta_h, 2), "earliest_key": earliest_key, "latest_key": latest_key},
            )
        return None

    # ------------------------------------------------------------------
    def fingerprint_encoding_software(self) -> Optional[VideoForensicFinding]:
        """
        Extract the 'encoder' metadata tag and cross-check it against
        the detected codec.  Flag obvious mismatches.
        """
        data = _run_ffprobe(["-show_format", "-show_streams", self.video_path])
        if not data:
            return None

        fmt_tags = data.get("format", {}).get("tags", {})
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        stream_tags = video_stream.get("tags", {})

        encoder_tag = (fmt_tags.get("encoder") or stream_tags.get("encoder") or "").strip()
        codec_name  = video_stream.get("codec_name", "").lower()

        if not encoder_tag or not codec_name:
            return None

        encoder_lower = encoder_tag.lower()

        # Known encoder → expected codec mappings
        _ENCODER_CODEC_MAP = {
            "apple": ["h264", "hevc", "aac"],
            "final cut": ["h264", "hevc", "prores"],
            "adobe premiere": ["h264", "hevc", "dnxhd"],
            "handbrake": ["h264", "hevc", "vp9"],
            "ffmpeg": ["h264", "hevc", "vp8", "vp9", "av1"],
            "x264": ["h264"],
            "x265": ["hevc"],
            "libvpx": ["vp8", "vp9"],
        }

        mismatch_detail = None
        for software_kw, expected_codecs in _ENCODER_CODEC_MAP.items():
            if software_kw in encoder_lower:
                if codec_name not in expected_codecs:
                    mismatch_detail = (
                        f"encoder tag '{encoder_tag}' is associated with "
                        f"{expected_codecs} but detected codec is '{codec_name}'"
                    )
                break

        if mismatch_detail:
            return _make_finding(
                VideoForensicFindingType.ENCODER_MISMATCH,
                Severity.MEDIUM,
                {"start": 0.0, "end": -1.0},
                f"Encoder fingerprint mismatch: {mismatch_detail}. "
                "The metadata may have been altered or the file re-encoded.",
                {"encoder_tag": encoder_tag, "detected_codec": codec_name},
            )
        return None


# ---------------------------------------------------------------------------
# 3. DeepfakeDetector
# ---------------------------------------------------------------------------

class DeepfakeDetector:
    """
    Uses OpenCV's DNN-based face detector (res10_300x300_ssd_iter_140000.caffemodel).
    Falls back gracefully if the model files are not present.
    """

    # Standard locations searched for the Caffe face model
    _MODEL_DIRS = [
        "/usr/share/opencv4/haarcascades",
        "/usr/local/share/opencv4",
        os.path.join(os.path.dirname(__file__), "models"),
    ]
    _PROTO = "deploy.prototxt"
    _MODEL = "res10_300x300_ssd_iter_140000.caffemodel"

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self._net = self._load_net()

    def _load_net(self):
        for d in self._MODEL_DIRS:
            proto = os.path.join(d, self._PROTO)
            model = os.path.join(d, self._MODEL)
            if os.path.isfile(proto) and os.path.isfile(model):
                try:
                    return cv2.dnn.readNetFromCaffe(proto, model)
                except Exception as e:
                    logger.warning(f"Failed to load DNN face model from {d}: {e}")
        logger.warning(
            "OpenCV DNN face model not found — deepfake detector disabled. "
            "To enable, place deploy.prototxt and "
            "res10_300x300_ssd_iter_140000.caffemodel in ./models/"
        )
        return None

    # ------------------------------------------------------------------
    def detect_and_track_faces(
        self, sampled_frames: List[Tuple[float, np.ndarray]], conf_thresh: float = 0.5
    ) -> List[Tuple[float, List[Tuple[int, int, int, int]]]]:
        """
        Detect faces in sampled frames.

        Returns a list of (timestamp, [bboxes]) where each bbox is (x, y, w, h).
        Returns empty list if DNN model is unavailable.
        """
        if self._net is None:
            return []

        detections: List[Tuple[float, List[Tuple[int, int, int, int]]]] = []
        for ts, frame in sampled_frames:
            h, w = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(
                cv2.resize(frame, (300, 300)), 1.0, (300, 300),
                (104.0, 177.0, 123.0),
            )
            self._net.setInput(blob)
            out = self._net.forward()
            bboxes = []
            for i in range(out.shape[2]):
                conf = float(out[0, 0, i, 2])
                if conf < conf_thresh:
                    continue
                x1 = int(out[0, 0, i, 3] * w)
                y1 = int(out[0, 0, i, 4] * h)
                x2 = int(out[0, 0, i, 5] * w)
                y2 = int(out[0, 0, i, 6] * h)
                bboxes.append((
                    max(0, x1), max(0, y1),
                    max(0, x2 - x1), max(0, y2 - y1),
                ))
            detections.append((ts, bboxes))
        return detections

    # ------------------------------------------------------------------
    def analyze_facial_consistency(
        self,
        sampled_frames: List[Tuple[float, np.ndarray]],
        face_detections: List[Tuple[float, List[Tuple[int, int, int, int]]]],
    ) -> Optional[VideoForensicFinding]:
        """
        Compute Laplacian sharpness + color histogram of face crops per frame.
        A face region that varies significantly while the background is stable
        indicates a composite (deepfake) insertion.
        """
        if not face_detections:
            return None

        frame_map = {ts: frame for ts, frame in sampled_frames}
        face_scores: List[float] = []
        bg_scores: List[float] = []

        for ts, bboxes in face_detections:
            frame = frame_map.get(ts)
            if frame is None or not bboxes:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            x, y, w, h = bboxes[0]
            if w < 16 or h < 16:
                continue
            face_crop = gray[y:y + h, x:x + w]
            lap_var = float(cv2.Laplacian(face_crop, cv2.CV_64F).var())
            face_scores.append(lap_var)

            # Background: centre strip excluding face
            bg_mask = np.ones(gray.shape, dtype=bool)
            bg_mask[y:y + h, x:x + w] = False
            bg_region = gray[bg_mask]
            if bg_region.size > 0:
                bg_lap = float(np.var(
                    cv2.Laplacian(gray, cv2.CV_64F)[bg_mask]
                ))
                bg_scores.append(bg_lap)

        if len(face_scores) < 4:
            return None

        face_arr = np.array(face_scores)
        face_z = scipy_zscore(face_arr)
        max_face_z = float(np.max(np.abs(face_z)))

        bg_arr = np.array(bg_scores) if bg_scores else np.zeros(1)
        bg_std = float(bg_arr.std()) if len(bg_arr) > 1 else 0.0
        face_std = float(face_arr.std())

        # High ratio: face varies much more than background
        ratio = (face_std / (bg_std + 1e-6))

        if max_face_z >= VFO_FACE_CONSISTENCY_ZSCORE and ratio >= 2.0:
            severity = Severity.HIGH if max_face_z >= VFO_FACE_CONSISTENCY_ZSCORE * 1.5 else Severity.MEDIUM
            return _make_finding(
                VideoForensicFindingType.FACE_TEMPORAL_INCONSISTENCY,
                severity,
                {"start": 0.0, "end": -1.0},
                (
                    f"Facial region sharpness variance is {ratio:.1f}× higher than background "
                    f"(face z-max={max_face_z:.2f}). "
                    "This pattern is consistent with a composited face insertion."
                ),
                {"face_z_max": round(max_face_z, 3), "face_bg_ratio": round(ratio, 3)},
            )
        return None

    # ------------------------------------------------------------------
    def analyze_frequency_artifacts(
        self,
        sampled_frames: List[Tuple[float, np.ndarray]],
        face_detections: List[Tuple[float, List[Tuple[int, int, int, int]]]],
    ) -> Optional[VideoForensicFinding]:
        """
        2-D FFT on face crop grayscale.
        GAN outputs often have periodic high-frequency components.
        Compute spectral flatness across face crops; anomalous flatness = GAN artifact.
        """
        if not face_detections:
            return None

        frame_map = {ts: frame for ts, frame in sampled_frames}
        flatness_vals: List[float] = []

        for ts, bboxes in face_detections:
            frame = frame_map.get(ts)
            if frame is None or not bboxes:
                continue
            x, y, w, h = bboxes[0]
            if w < 32 or h < 32:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            crop = gray[y:y + h, x:x + w]
            crop_resized = cv2.resize(crop, (128, 128))
            f = np.fft.fft2(crop_resized)
            fshift = np.fft.fftshift(f)
            mag = np.abs(fshift) + 1e-8
            # Spectral flatness: geometric mean / arithmetic mean
            log_mean = np.mean(np.log(mag))
            arith_mean = np.mean(mag)
            sf = np.exp(log_mean) / (arith_mean + 1e-8)
            flatness_vals.append(float(sf))

        if len(flatness_vals) < 3:
            return None

        arr = np.array(flatness_vals)
        mean_sf = float(arr.mean())

        # GAN fingerprints tend to produce unusually high spectral flatness
        # (more uniform frequency distribution than natural face imagery)
        HIGH_FLATNESS_THRESH = 0.18
        MED_FLATNESS_THRESH  = 0.12

        if mean_sf >= HIGH_FLATNESS_THRESH:
            severity = Severity.HIGH
        elif mean_sf >= MED_FLATNESS_THRESH:
            severity = Severity.MEDIUM
        else:
            return None

        return _make_finding(
            VideoForensicFindingType.FREQUENCY_ARTIFACTS,
            severity,
            {"start": 0.0, "end": -1.0},
            (
                f"Face crop spectral flatness ({mean_sf:.4f}) exceeds natural-image baseline. "
                "Periodic high-frequency components in face regions are a known GAN fingerprint."
            ),
            {"mean_spectral_flatness": round(mean_sf, 4), "face_crops_analysed": len(flatness_vals)},
        )


# ---------------------------------------------------------------------------
# 4. MotionVectorForensics
# ---------------------------------------------------------------------------

class MotionVectorForensics:
    """
    Dense Farneback optical flow to detect unnatural motion field patterns
    and composite regions.
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    def analyze_motion_consistency(
        self, sampled_frames: List[Tuple[float, np.ndarray]]
    ) -> Optional[VideoForensicFinding]:
        """
        Compute local vs global motion ratio per 16×16 block across consecutive
        frame pairs.  Regions with divergent local motion are flagged.
        """
        if len(sampled_frames) < 3:
            return None

        grays = [
            (ts, cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
            for ts, f in sampled_frames
        ]

        divergence_scores: List[float] = []

        for i in range(len(grays) - 1):
            ts_a, ga = grays[i]
            _ts_b, gb = grays[i + 1]

            # Resize for performance
            scale = min(1.0, 320 / max(ga.shape))
            if scale < 1.0:
                ga = cv2.resize(ga, None, fx=scale, fy=scale)
                gb = cv2.resize(gb, None, fx=scale, fy=scale)

            flow = cv2.calcOpticalFlowFarneback(
                ga, gb, None, 0.5, 3, 15, 3, 5, 1.2, 0
            )

            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            global_mag = float(mag.mean())
            if global_mag < 0.1:
                continue  # static scene — skip

            # 16×16 block local magnitudes
            h, w = mag.shape
            local_mags = []
            for r in range(0, h - 15, 16):
                for c in range(0, w - 15, 16):
                    block_mag = float(mag[r:r + 16, c:c + 16].mean())
                    local_mags.append(block_mag)

            if not local_mags:
                continue

            local_arr = np.array(local_mags)
            # Fraction of blocks with motion ratio far from global
            ratio_arr = local_arr / (global_mag + 1e-6)
            divergent = float(np.mean(ratio_arr > VFO_MOTION_DIVERGENCE_RATIO))
            divergence_scores.append(divergent)

        if not divergence_scores:
            return None

        mean_div = float(np.mean(divergence_scores))
        HIGH_DIV = 0.25
        MED_DIV  = 0.10

        if mean_div >= HIGH_DIV:
            severity = Severity.HIGH
        elif mean_div >= MED_DIV:
            severity = Severity.MEDIUM
        else:
            return None

        return _make_finding(
            VideoForensicFindingType.MOTION_VECTOR_ANOMALY,
            severity,
            {"start": 0.0, "end": -1.0},
            (
                f"{mean_div * 100:.1f}% of motion blocks diverge from the global motion field "
                f"(threshold ratio >{VFO_MOTION_DIVERGENCE_RATIO}×). "
                "Unnatural local motion patterns suggest possible object insertion or removal."
            ),
            {"mean_divergent_fraction": round(mean_div, 4), "frame_pairs_analysed": len(divergence_scores)},
        )

    # ------------------------------------------------------------------
    def detect_composite_regions(
        self, sampled_frames: List[Tuple[float, np.ndarray]]
    ) -> Optional[VideoForensicFinding]:
        """
        Compare motion field gradient between foreground objects and the
        background homography estimation.  Large residuals after homography
        alignment indicate pasted-in foreground elements.
        """
        if len(sampled_frames) < 4:
            return None

        high_residual_pairs = 0
        analysed = 0

        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in sampled_frames]

        for i in range(len(grays) - 1):
            ga = grays[i]
            gb = grays[i + 1]

            scale = min(1.0, 480 / max(ga.shape))
            if scale < 1.0:
                ga = cv2.resize(ga, None, fx=scale, fy=scale)
                gb = cv2.resize(gb, None, fx=scale, fy=scale)

            # ORB keypoints for homography
            orb = cv2.ORB_create(500)
            kp1, des1 = orb.detectAndCompute(ga, None)
            kp2, des2 = orb.detectAndCompute(gb, None)

            if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
                continue

            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = bf.match(des1, des2)
            matches = sorted(matches, key=lambda x: x.distance)[:50]

            if len(matches) < 8:
                continue

            src_pts = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)

            H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            if H is None:
                continue

            analysed += 1
            inlier_ratio = float(mask.sum()) / len(mask) if mask is not None else 1.0
            # Low inlier ratio → many points deviate from the global camera motion model
            if inlier_ratio < 0.4:
                high_residual_pairs += 1

        if analysed == 0:
            return None

        residual_rate = high_residual_pairs / analysed

        if residual_rate >= 0.5:
            severity = Severity.HIGH
        elif residual_rate >= 0.25:
            severity = Severity.MEDIUM
        else:
            return None

        return _make_finding(
            VideoForensicFindingType.COMPOSITE_REGION,
            severity,
            {"start": 0.0, "end": -1.0},
            (
                f"{residual_rate * 100:.0f}% of frame pairs show significant motion field "
                "inconsistency with the estimated global camera homography. "
                "This is a strong indicator of pasted-in foreground regions."
            ),
            {"residual_rate": round(residual_rate, 3), "high_residual_pairs": high_residual_pairs, "analysed_pairs": analysed},
        )


# ---------------------------------------------------------------------------
# 5. ENFVideoForensics
# ---------------------------------------------------------------------------

class ENFVideoForensics:
    """
    Electrical Network Frequency (ENF) analysis on the luminance channel.
    Phase discontinuities indicate potential video splices.
    """

    ENF_BANDS = [
        (49.0, 51.0),   # 50 Hz mains (Europe/Asia)
        (59.0, 61.0),   # 60 Hz mains (Americas)
    ]

    def __init__(self, fps: float):
        self.fps = fps

    # ------------------------------------------------------------------
    def extract_luminance_enf(
        self, sampled_frames: List[Tuple[float, np.ndarray]]
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Sample mean luminance of the central horizontal strip per frame.
        FFT → bandpass filter around 50/60 Hz → return (timestamps, phase_signal, enf_freq).
        """
        if len(sampled_frames) < 10:
            return np.array([]), np.array([]), 0.0

        timestamps = np.array([ts for ts, _ in sampled_frames])
        lum_vals: List[float] = []
        for _, frame in sampled_frames:
            h = frame.shape[0]
            strip = frame[h // 3: 2 * h // 3, :, :]
            gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
            lum_vals.append(float(gray.mean()))

        lum = np.array(lum_vals, dtype=np.float64)
        lum -= lum.mean()  # remove DC

        # The ENF at mains frequency is captured if the sampling rate > 2×ENF.
        # At 2 fps sampling we cannot resolve 50/60 Hz; this detector works only
        # when the video was sampled at full frame rate (i.e. fps > 120).
        # For typical 24–60 fps content we instead look at flicker harmonics that
        # may alias down into the detectable range. We skip analysis if fps < 100.
        if self.fps < 100:
            return timestamps, lum, 0.0

        # Bandpass filter around the dominant ENF band
        best_band = None
        best_power = -1.0
        N = len(lum)
        freqs = np.fft.rfftfreq(N, d=1.0 / self.fps)
        fft_mag = np.abs(np.fft.rfft(lum))

        for low, high in self.ENF_BANDS:
            mask = (freqs >= low) & (freqs <= high)
            if mask.any():
                band_power = float(fft_mag[mask].max())
                if band_power > best_power:
                    best_power = band_power
                    best_band = (low, high)

        if best_band is None:
            return timestamps, lum, 0.0

        low, high = best_band
        nyq = self.fps / 2.0
        sos = butter(4, [low / nyq, high / nyq], btype="band", output="sos")
        enf_signal = sosfiltfilt(sos, lum)

        # Instantaneous phase via Hilbert transform analytic signal
        analytic = np.fft.ifft(
            np.fft.fft(enf_signal) * (np.arange(len(enf_signal)) >= 0) * 2
        )
        phase = np.unwrap(np.angle(analytic))
        return timestamps, phase, (low + high) / 2.0

    # ------------------------------------------------------------------
    def detect_enf_discontinuities(
        self, timestamps: np.ndarray, phase: np.ndarray
    ) -> List[VideoForensicFinding]:
        """
        Compute first derivative of the unwrapped phase; z-score the jumps.
        Jumps > VFO_ENF_PHASE_ZSCORE σ are flagged as potential splice points.
        """
        if len(phase) < 10:
            return []

        dphase = np.diff(phase)
        if dphase.std() == 0:
            return []

        z = scipy_zscore(dphase)
        findings: List[VideoForensicFinding] = []

        for i, zval in enumerate(z):
            if abs(zval) >= VFO_ENF_PHASE_ZSCORE:
                ts = float(timestamps[i]) if i < len(timestamps) else float(i)
                severity = Severity.HIGH if abs(zval) >= VFO_ENF_PHASE_ZSCORE * 1.5 else Severity.MEDIUM
                findings.append(_make_finding(
                    VideoForensicFindingType.ENF_DISCONTINUITY,
                    severity,
                    {"timestamp": ts},
                    (
                        f"ENF phase jump of z={zval:.2f}σ at t≈{ts:.2f}s. "
                        "Abrupt phase discontinuities in the electrical network frequency "
                        "signal are a strong indicator of a video splice point."
                    ),
                    {"z_score": round(float(zval), 3), "phase_jump_rad": round(float(dphase[i]), 4)},
                ))

        return findings


# ---------------------------------------------------------------------------
# 6. VideoForensicsEngine (orchestrator)
# ---------------------------------------------------------------------------

class VideoForensicsEngine:
    """
    Orchestrates all sub-detectors and synthesises a final verdict.
    """

    _SEV_POINTS = {
        Severity.HIGH:   FA_SEV_HIGH_PTS,
        Severity.MEDIUM: FA_SEV_MEDIUM_PTS,
        Severity.LOW:    FA_SEV_LOW_PTS,
    }

    def __init__(self, job_id: str, output_dir: str):
        self.job_id = job_id
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def _extract_media_info(self, video_path: str) -> Dict[str, Any]:
        data = _run_ffprobe(["-show_streams", "-show_format", video_path])
        if not data:
            return {}
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        fmt = data.get("format", {})
        fps_str = video_stream.get("avg_frame_rate", "0/1")
        try:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0

        return {
            "duration":    _safe_float(fmt.get("duration")),
            "fps":         fps,
            "width":       video_stream.get("width", 0),
            "height":      video_stream.get("height", 0),
            "codec":       video_stream.get("codec_name", "unknown"),
            "format":      fmt.get("format_name", "unknown"),
            "bit_rate":    _safe_float(fmt.get("bit_rate")),
            "file_size":   _safe_float(fmt.get("size")),
        }

    # ------------------------------------------------------------------
    def _sample_frames(
        self,
        video_path: str,
        target_fps: float = 2.0,
        max_frames: int = 60,
    ) -> Tuple[List[Tuple[float, np.ndarray]], float]:
        """
        Sample frames at approximately *target_fps*.
        Returns (frame_list, actual_video_fps).
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if video_fps <= 0:
            video_fps = 25.0  # safe default

        frame_interval = max(1, int(video_fps / target_fps))
        frames: List[Tuple[float, np.ndarray]] = []
        frame_idx = 0

        while len(frames) < max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % frame_interval == 0:
                ts = frame_idx / video_fps
                frames.append((ts, frame))
            frame_idx += 1

        cap.release()
        return frames, video_fps

    # ------------------------------------------------------------------
    def _synthesize_verdict(
        self, findings: List[VideoForensicFinding]
    ) -> Tuple[str, float, List[str], str]:
        """
        Compute (verdict, confidence, tampering_types, explanation)
        using the same scoring model as frame_analysis.py.
        """
        total_score = 0.0
        type_set: set = set()

        for f in findings:
            total_score += self._SEV_POINTS.get(f.severity, 0.0)
            type_set.add(f.type.value)

        confidence = min(100.0, total_score)
        tampering_types = sorted(type_set)

        if confidence >= VFO_TAMPERED_CONFIDENCE:
            verdict = "LIKELY_TAMPERED"
            explanation = (
                f"High forensic confidence ({confidence:.1f}%) based on "
                f"{len(findings)} finding(s) across: {', '.join(tampering_types)}."
            )
        elif confidence >= VFO_REVIEW_CONFIDENCE:
            verdict = "REVIEW_RECOMMENDED"
            explanation = (
                f"Moderate forensic confidence ({confidence:.1f}%). "
                f"Found {len(findings)} indicator(s): {', '.join(tampering_types)}. "
                "Manual review recommended."
            )
        else:
            verdict = "LIKELY_AUTHENTIC"
            explanation = (
                f"Low forensic confidence ({confidence:.1f}%); "
                f"no significant indicators detected."
                + (f" Minor signals: {', '.join(tampering_types)}." if tampering_types else "")
            )

        return verdict, confidence, tampering_types, explanation

    # ------------------------------------------------------------------
    def analyze_video(
        self, video_path: str, mode: str = "standard"
    ) -> VideoForensicResult:
        """
        Main entry point.  Runs all sub-detectors and returns a VideoForensicResult.

        Non-fatal: each sub-module is wrapped in try-except; partial results
        are returned even if individual modules fail.
        """
        created_at = datetime.utcnow().isoformat()
        all_findings: List[VideoForensicFinding] = []
        analysis_modules: Dict[str, Any] = {}

        # ── 1. Media info ──────────────────────────────────────────────
        media_info = self._extract_media_info(video_path)
        duration = media_info.get("duration", 0.0)
        fps_val  = media_info.get("fps", 25.0)

        # ── 2. Sample frames ───────────────────────────────────────────
        sampled_frames: List[Tuple[float, np.ndarray]] = []
        actual_fps = fps_val or 25.0
        try:
            sampled_frames, actual_fps = self._sample_frames(video_path)
            analysis_modules["sampling"] = {
                "frames_sampled": len(sampled_frames),
                "actual_fps": actual_fps,
            }
        except Exception as e:
            logger.error(f"Frame sampling failed: {e}")
            analysis_modules["sampling"] = {"error": str(e)}

        # ── 3. Codec / bitstream analysis ─────────────────────────────
        codec_results: Dict[str, Any] = {}
        try:
            cbc = CodecBitstreamForensics(video_path)
            codec_params = cbc.extract_codec_parameters()
            codec_results["codec_params"] = codec_params

            de_finding = cbc.detect_double_encoding(sampled_frames)
            if de_finding:
                all_findings.append(de_finding)
                codec_results["double_encoding"] = "detected"
            else:
                codec_results["double_encoding"] = "not detected"

            br_findings = cbc.profile_bitrate_segments()
            all_findings.extend(br_findings)
            codec_results["bitrate_anomalies"] = len(br_findings)

            ci_finding = cbc.detect_encoder_inconsistency()
            if ci_finding:
                all_findings.append(ci_finding)
                codec_results["codec_inconsistency"] = "detected"
            else:
                codec_results["codec_inconsistency"] = "not detected"

        except Exception as e:
            logger.error(f"Codec bitstream analysis failed: {e}")
            codec_results["error"] = str(e)

        analysis_modules["codec_bitstream"] = codec_results

        # ── 4. Metadata chain analysis ─────────────────────────────────
        meta_results: Dict[str, Any] = {}
        try:
            mcf = MetadataChainForensics(video_path)

            container_finding = mcf.audit_container_metadata()
            if container_finding:
                all_findings.append(container_finding)
                meta_results["container_anomaly"] = "detected"
            else:
                meta_results["container_anomaly"] = "not detected"

            ts_finding = mcf.detect_timestamp_anomalies()
            if ts_finding:
                all_findings.append(ts_finding)
                meta_results["timestamp_anomaly"] = "detected"
            else:
                meta_results["timestamp_anomaly"] = "not detected"

            enc_finding = mcf.fingerprint_encoding_software()
            if enc_finding:
                all_findings.append(enc_finding)
                meta_results["encoder_mismatch"] = "detected"
            else:
                meta_results["encoder_mismatch"] = "not detected"

        except Exception as e:
            logger.error(f"Metadata chain analysis failed: {e}")
            meta_results["error"] = str(e)

        analysis_modules["metadata_chain"] = meta_results

        # ── 5. ENF analysis (skip if video < 5 s) ─────────────────────
        enf_results: Dict[str, Any] = {}
        if duration >= 5.0:
            try:
                enf = ENFVideoForensics(fps=actual_fps)
                ts_arr, phase_arr, enf_freq = enf.extract_luminance_enf(sampled_frames)
                enf_results["enf_freq_hz"] = enf_freq
                if enf_freq > 0 and len(phase_arr) > 0:
                    enf_findings = enf.detect_enf_discontinuities(ts_arr, phase_arr)
                    all_findings.extend(enf_findings)
                    enf_results["discontinuities"] = len(enf_findings)
                else:
                    enf_results["note"] = "ENF extraction skipped (fps too low or no ENF band detected)"
            except Exception as e:
                logger.error(f"ENF analysis failed: {e}")
                enf_results["error"] = str(e)
        else:
            enf_results["skipped"] = "video shorter than 5 s"

        analysis_modules["enf"] = enf_results

        # ── 6. Motion vector analysis (skip if < 10 frames) ───────────
        motion_results: Dict[str, Any] = {}
        if len(sampled_frames) >= 10:
            try:
                mvf = MotionVectorForensics()
                mv_finding = mvf.analyze_motion_consistency(sampled_frames)
                if mv_finding:
                    all_findings.append(mv_finding)
                    motion_results["motion_vector_anomaly"] = "detected"
                else:
                    motion_results["motion_vector_anomaly"] = "not detected"

                comp_finding = mvf.detect_composite_regions(sampled_frames)
                if comp_finding:
                    all_findings.append(comp_finding)
                    motion_results["composite_region"] = "detected"
                else:
                    motion_results["composite_region"] = "not detected"

            except Exception as e:
                logger.error(f"Motion vector analysis failed: {e}")
                motion_results["error"] = str(e)
        else:
            motion_results["skipped"] = f"insufficient frames ({len(sampled_frames)} < 10)"

        analysis_modules["motion_vectors"] = motion_results

        # ── 7. Deepfake detection ──────────────────────────────────────
        deepfake_results: Dict[str, Any] = {}
        try:
            dd = DeepfakeDetector(output_dir=self.output_dir)
            if dd._net is None:
                deepfake_results["model_available"] = False
                deepfake_results["note"] = (
                    "Deepfake face model not loaded — place deploy.prototxt and "
                    "res10_300x300_ssd_iter_140000.caffemodel in ./models/ to enable"
                )
            face_detections = dd.detect_and_track_faces(sampled_frames)
            faces_found = sum(len(bbs) for _, bbs in face_detections)
            deepfake_results["faces_detected"] = faces_found

            if faces_found > 0:
                fc_finding = dd.analyze_facial_consistency(sampled_frames, face_detections)
                if fc_finding:
                    all_findings.append(fc_finding)
                    deepfake_results["face_temporal_inconsistency"] = "detected"
                else:
                    deepfake_results["face_temporal_inconsistency"] = "not detected"

                freq_finding = dd.analyze_frequency_artifacts(sampled_frames, face_detections)
                if freq_finding:
                    all_findings.append(freq_finding)
                    deepfake_results["frequency_artifacts"] = "detected"
                else:
                    deepfake_results["frequency_artifacts"] = "not detected"
            else:
                deepfake_results["note"] = "no faces detected — deepfake sub-checks skipped"

        except Exception as e:
            logger.error(f"Deepfake detection failed: {e}")
            deepfake_results["error"] = str(e)

        analysis_modules["deepfake"] = deepfake_results

        # ── 8. Verdict synthesis ───────────────────────────────────────
        verdict, confidence, tampering_types, explanation = self._synthesize_verdict(all_findings)
        completed_at = datetime.utcnow().isoformat()

        return VideoForensicResult(
            job_id=self.job_id,
            media_info=media_info,
            findings=all_findings,
            verdict=verdict,
            confidence=round(confidence, 2),
            tampering_types=tampering_types,
            verdict_explanation=explanation,
            analysis_modules=analysis_modules,
            created_at=created_at,
            completed_at=completed_at,
        )
