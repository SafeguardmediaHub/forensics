"""
VideoForensics — Encoder Signature Database
A structured reference of known encoder signatures and their expected contexts.
Used by the metadata module to detect encoder/source mismatches.

This database is built from published research and empirical observation.
It grows over time as new encoders and devices are documented.

Structure:
    ENCODER_SIGNATURES — maps encoder name patterns to source categories
    DEVICE_ENCODERS    — maps device types to their expected encoder patterns
    PLATFORM_ENCODERS  — encoders introduced by social media re-encoding
    SUSPICIOUS_PATTERNS — patterns that indicate post-processing tools
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class EncoderCategory(Enum):
    """Category of encoder — what kind of source it suggests."""
    MOBILE_CAMERA     = "mobile_camera"       # Smartphone native recording
    PROFESSIONAL_CAM  = "professional_cam"    # DSLR, mirrorless, broadcast
    SCREEN_RECORDER   = "screen_recorder"     # Screen capture software
    VIDEO_EDITOR      = "video_editor"        # Desktop editing software
    TRANSCODER        = "transcoder"          # General purpose transcoder
    PLATFORM_REENC    = "platform_reenc"      # Social media re-encoding
    STREAMING         = "streaming"           # Streaming/broadcast tools
    UNKNOWN           = "unknown"


@dataclass
class EncoderSignature:
    """A known encoder with its identifying patterns and expected contexts."""
    name: str
    patterns: List[str]              # Regex patterns that match this encoder
    category: EncoderCategory
    description: str
    expected_containers: List[str]   # Containers this encoder normally produces
    suspicious_if_claimed_source: List[str]  # Source types this is suspicious for
    notes: str = ""


# ─── Known Encoder Signatures ─────────────────────────────────────────────────
# Patterns are applied case-insensitively to the encoder string

ENCODER_SIGNATURES: List[EncoderSignature] = [

    # ── Mobile Cameras ────────────────────────────────────────────────────────
    EncoderSignature(
        name="Apple iPhone / iPad",
        patterns=[r"apple", r"com\.apple", r"iphone", r"ipad"],
        category=EncoderCategory.MOBILE_CAMERA,
        description="Apple iOS native camera encoder",
        expected_containers=["mov", "mp4"],
        suspicious_if_claimed_source=[],
        notes="Apple devices use QuickTime MOV natively; MP4 export also common"
    ),
    EncoderSignature(
        name="Samsung Camera",
        patterns=[r"samsung", r"sec\b"],
        category=EncoderCategory.MOBILE_CAMERA,
        description="Samsung Android native camera",
        expected_containers=["mp4"],
        suspicious_if_claimed_source=[],
    ),
    EncoderSignature(
        name="Android Generic",
        patterns=[r"android", r"google", r"com\.android"],
        category=EncoderCategory.MOBILE_CAMERA,
        description="Generic Android camera encoder",
        expected_containers=["mp4", "3gp"],
        suspicious_if_claimed_source=[],
    ),
    EncoderSignature(
        name="Huawei Camera",
        patterns=[r"huawei", r"kirin"],
        category=EncoderCategory.MOBILE_CAMERA,
        description="Huawei Android camera encoder",
        expected_containers=["mp4"],
        suspicious_if_claimed_source=[],
    ),

    # ── Professional Cameras ──────────────────────────────────────────────────
    EncoderSignature(
        name="Canon Camera",
        patterns=[r"canon", r"eos"],
        category=EncoderCategory.PROFESSIONAL_CAM,
        description="Canon DSLR/mirrorless camera",
        expected_containers=["mp4", "mov", "mts"],
        suspicious_if_claimed_source=[],
    ),
    EncoderSignature(
        name="Sony Camera",
        patterns=[r"sony", r"xavcs"],
        category=EncoderCategory.PROFESSIONAL_CAM,
        description="Sony camera encoder (XAVC-S or similar)",
        expected_containers=["mp4", "mts", "m2ts"],
        suspicious_if_claimed_source=[],
    ),
    EncoderSignature(
        name="GoPro",
        patterns=[r"gopro", r"hero"],
        category=EncoderCategory.PROFESSIONAL_CAM,
        description="GoPro action camera",
        expected_containers=["mp4"],
        suspicious_if_claimed_source=[],
    ),
    EncoderSignature(
        name="DJI Drone",
        patterns=[r"dji", r"djivideo"],
        category=EncoderCategory.PROFESSIONAL_CAM,
        description="DJI drone camera encoder",
        expected_containers=["mp4", "mov"],
        suspicious_if_claimed_source=[],
    ),

    # ── Video Editing Software ────────────────────────────────────────────────
    EncoderSignature(
        name="Adobe Premiere",
        patterns=[r"adobe premiere", r"premiere pro", r"mc_adobe"],
        category=EncoderCategory.VIDEO_EDITOR,
        description="Adobe Premiere Pro video editor",
        expected_containers=["mp4", "mov", "avi", "mkv"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
        notes="Presence strongly suggests post-processing"
    ),
    EncoderSignature(
        name="Adobe After Effects",
        patterns=[r"after effects", r"adobe ae"],
        category=EncoderCategory.VIDEO_EDITOR,
        description="Adobe After Effects compositing",
        expected_containers=["mp4", "mov", "avi"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
        notes="Indicates compositing / visual effects work"
    ),
    EncoderSignature(
        name="DaVinci Resolve",
        patterns=[r"davinci", r"blackmagic", r"resolve"],
        category=EncoderCategory.VIDEO_EDITOR,
        description="Blackmagic DaVinci Resolve",
        expected_containers=["mp4", "mov", "mkv"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
        notes="Professional color grading and editing tool"
    ),
    EncoderSignature(
        name="Final Cut Pro",
        patterns=[r"final cut", r"fcp", r"apple final"],
        category=EncoderCategory.VIDEO_EDITOR,
        description="Apple Final Cut Pro",
        expected_containers=["mov", "mp4"],
        suspicious_if_claimed_source=["professional_cam"],
        notes="Apple's professional editor — common in legitimate production"
    ),
    EncoderSignature(
        name="iMovie",
        patterns=[r"imovie"],
        category=EncoderCategory.VIDEO_EDITOR,
        description="Apple iMovie consumer editor",
        expected_containers=["mov", "mp4"],
        suspicious_if_claimed_source=[],
        notes="Consumer editor — not inherently suspicious"
    ),
    EncoderSignature(
        name="HandBrake",
        patterns=[r"handbrake"],
        category=EncoderCategory.TRANSCODER,
        description="HandBrake open source transcoder",
        expected_containers=["mp4", "mkv"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
        notes="Very commonly used for re-encoding — strong indicator of reprocessing"
    ),
    EncoderSignature(
        name="FFmpeg / Lavf / Lavc",
        patterns=[r"lavf", r"lavc", r"ffmpeg", r"libav"],
        category=EncoderCategory.TRANSCODER,
        description="FFmpeg / Libav transcoding framework",
        expected_containers=["mp4", "mkv", "avi", "mov", "webm"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
        notes="Most likely post-processing. Rare on raw device footage unless"
              " device explicitly uses FFmpeg internally (some drones/IP cameras do)"
    ),

    # ── Screen Recording ──────────────────────────────────────────────────────
    EncoderSignature(
        name="OBS Studio",
        patterns=[r"obs", r"obs-output"],
        category=EncoderCategory.SCREEN_RECORDER,
        description="OBS Studio screen/stream recorder",
        expected_containers=["mp4", "mkv", "flv"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
        notes="Screen recording — not consistent with claimed camera footage"
    ),
    EncoderSignature(
        name="Camtasia",
        patterns=[r"camtasia", r"techsmith"],
        category=EncoderCategory.SCREEN_RECORDER,
        description="TechSmith Camtasia screen recorder",
        expected_containers=["mp4", "avi"],
        suspicious_if_claimed_source=["mobile_camera", "professional_cam"],
    ),

    # ── Platform Re-encoding ──────────────────────────────────────────────────
    EncoderSignature(
        name="YouTube Processing",
        patterns=[r"youtube", r"ytdl"],
        category=EncoderCategory.PLATFORM_REENC,
        description="YouTube platform re-encoding",
        expected_containers=["mp4", "webm"],
        suspicious_if_claimed_source=[],
        notes="Expected if video was downloaded from YouTube"
    ),
    EncoderSignature(
        name="WhatsApp",
        patterns=[r"whatsapp"],
        category=EncoderCategory.PLATFORM_REENC,
        description="WhatsApp video compression",
        expected_containers=["mp4"],
        suspicious_if_claimed_source=[],
        notes="WhatsApp aggressively re-encodes video — metadata often stripped"
    ),
]


# ─── Lookup Functions ─────────────────────────────────────────────────────────

def identify_encoder(encoder_string: str) -> Optional[EncoderSignature]:
    """
    Match an encoder string against the known signature database.
    Returns the best matching EncoderSignature or None if unknown.
    """
    if not encoder_string:
        return None
    es = encoder_string.lower().strip()
    for sig in ENCODER_SIGNATURES:
        for pattern in sig.patterns:
            if re.search(pattern, es, re.IGNORECASE):
                return sig
    return None


def is_editing_software(encoder_string: str) -> bool:
    """Quick check: does this encoder string suggest editing/post-processing?"""
    sig = identify_encoder(encoder_string)
    if sig is None:
        return False
    return sig.category in [
        EncoderCategory.VIDEO_EDITOR,
        EncoderCategory.TRANSCODER,
        EncoderCategory.SCREEN_RECORDER,
    ]


def is_suspicious_for_raw_footage(encoder_string: str) -> bool:
    """
    Returns True if this encoder is suspicious for a video
    claimed to be unedited raw camera footage.
    """
    sig = identify_encoder(encoder_string)
    if sig is None:
        return False
    return bool(sig.suspicious_if_claimed_source)


# ─── Known Metadata Patterns That Indicate Tampering ─────────────────────────

SUSPICIOUS_METADATA_PATTERNS = {
    "missing_creation_time": {
        "description": "Creation time metadata absent",
        "severity": "medium",
        "note": "Native camera recordings almost always embed creation time. "
                "Absence suggests metadata was stripped.",
    },
    "future_timestamp": {
        "description": "Creation timestamp is in the future",
        "severity": "high",
        "note": "Timestamp post-dates the analysis date — physically impossible "
                "for a genuine recording.",
    },
    "modification_before_creation": {
        "description": "File modification date precedes creation date",
        "severity": "high",
        "note": "A file cannot be modified before it was created. "
                "Indicates timestamp manipulation.",
    },
    "encoder_device_mismatch": {
        "description": "Encoder software inconsistent with claimed device",
        "severity": "high",
        "note": "The encoder signature does not match what the claimed "
                "recording device would produce.",
    },
    "editing_software_detected": {
        "description": "Video editing or transcoding software signature detected",
        "severity": "medium",
        "note": "Encoder identifies as post-processing software. Video has been "
                "processed through editing tools after initial recording.",
    },
    "stripped_metadata": {
        "description": "Unusually sparse metadata for claimed source",
        "severity": "medium",
        "note": "Expected metadata fields are absent. May indicate deliberate "
                "metadata stripping to conceal origin.",
    },
    "gps_timezone_mismatch": {
        "description": "GPS location inconsistent with embedded timezone",
        "severity": "high",
        "note": "The GPS coordinates place the recording in a timezone "
                "that does not match the embedded timezone offset.",
    },
    "inconsistent_stream_timestamps": {
        "description": "Video and audio stream timestamps do not agree",
        "severity": "medium",
        "note": "Timestamps embedded in the video and audio streams differ, "
                "suggesting the streams were assembled from different recordings.",
    },
}
