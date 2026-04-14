"""Next-steps catalog.

Static per-media-type lists for v1. Each entry is either a `manual` step
(something the human does themselves) or a `platform_feature` step that
points at another capability in the wider platform via a stable `feature`
string. The backend maps those feature strings to its own endpoint IDs
when rendering — this project never calls those features itself and
never claims to know their result.

Signal-conditional steps are inserted between the universal manual steps
and the platform-feature steps based on which detectors elevated.
"""

from __future__ import annotations

from typing import Any

MediaType = str  # "image" | "video" | "audio" | "frames"

# Universal manual steps — prepended to every media type.
_UNIVERSAL_MANUAL: list[dict[str, Any]] = [
    {
        "action": "verify_source",
        "label": "Verify where the file came from",
        "type": "manual",
    },
    {
        "action": "request_original",
        "label": "Request the original file from its source",
        "type": "manual",
    },
    {
        "action": "compare_versions",
        "label": "Compare against an earlier or trusted version",
        "type": "manual",
    },
]

# Universal trailing step — appended to every media type.
_UNIVERSAL_CAUTION: dict[str, Any] = {
    "action": "caution_before_share",
    "label": "Use caution before sharing or publishing",
    "type": "manual",
}

# ── Signal-conditional steps ─────────────────────────────────────────────────
# Inserted when specific detectors are elevated. Each detector maps to one
# or more manual steps that guide the user toward the most relevant follow-up
# for that particular signal.

_SIGNAL_CONDITIONAL_STEPS: dict[str, list[dict[str, Any]]] = {
    "ela": [
        {
            "action": "inspect_ela_regions",
            "label": (
                "Examine regions with compression differences "
                "— look for areas that stand out in the ELA heatmap"
            ),
            "type": "manual",
        },
    ],
    "noise": [
        {
            "action": "inspect_noise_regions",
            "label": (
                "Examine regions with different noise grain "
                "— these may come from a different source"
            ),
            "type": "manual",
        },
    ],
    "copy_move": [
        {
            "action": "inspect_clone_regions",
            "label": (
                "Review matched keypoint pairs "
                "— these regions share repeated pixel patterns"
            ),
            "type": "manual",
        },
    ],
    "jpeg_compression": [
        {
            "action": "inspect_compression",
            "label": (
                "Look for visible block artifacts at 8x8 pixel boundaries "
                "— signs of re-saving"
            ),
            "type": "manual",
        },
    ],
    "exif_metadata": [
        {
            "action": "investigate_metadata",
            "label": (
                "Investigate why camera metadata is missing "
                "— common in screenshots, re-exports, or processed files"
            ),
            "type": "manual",
        },
    ],
    "temporal": [
        {
            "action": "review_temporal_segments",
            "label": (
                "Review flagged time ranges for cuts, freezes, "
                "or timing irregularities"
            ),
            "type": "manual",
        },
    ],
    "spatial": [
        {
            "action": "review_flagged_frames",
            "label": "Examine individual frames flagged by per-frame analysis",
            "type": "manual",
        },
    ],
    "crop_detection": [
        {
            "action": "request_uncropped",
            "label": (
                "Request the uncropped original "
                "— the image may have been trimmed from a larger file"
            ),
            "type": "manual",
        },
    ],
    "screenshot_detection": [
        {
            "action": "request_pre_screenshot",
            "label": (
                "Request the original file before screen capture "
                "— screenshots lose metadata and fidelity"
            ),
            "type": "manual",
        },
    ],
}

# Platform-feature suggestions per media type. `feature` strings are
# placeholder IDs that the backend will map to its own endpoints.
_PLATFORM_FEATURES: dict[MediaType, list[dict[str, Any]]] = {
    "image": [
        {
            "action": "check_c2pa",
            "label": "Check for a C2PA provenance manifest",
            "type": "platform_feature",
            "feature": "authenticity.c2pa",
        },
        {
            "action": "check_metadata_authenticity",
            "label": "Verify metadata-based authenticity (geolocation, timeline)",
            "type": "platform_feature",
            "feature": "authenticity.metadata",
        },
        {
            "action": "reverse_image_lookup",
            "label": "Run a reverse image search on this file",
            "type": "platform_feature",
            "feature": "content_verification.reverse_lookup",
        },
        {
            "action": "ai_detection_image",
            "label": "Run AI-generation detection",
            "type": "platform_feature",
            "feature": "ai_detection.image",
        },
        {
            "action": "ocr_extract",
            "label": "Extract on-image text to check claims",
            "type": "platform_feature",
            "feature": "ocr.image",
        },
    ],
    "video": [
        {
            "action": "check_c2pa",
            "label": "Check for a C2PA provenance manifest",
            "type": "platform_feature",
            "feature": "authenticity.c2pa",
        },
        {
            "action": "check_metadata_authenticity",
            "label": "Verify metadata-based authenticity (geolocation, timeline)",
            "type": "platform_feature",
            "feature": "authenticity.metadata",
        },
        {
            "action": "keyframe_reverse_lookup",
            "label": "Extract keyframes and run reverse image search",
            "type": "platform_feature",
            "feature": "content_verification.keyframe_extraction",
        },
        {
            "action": "ai_detection_video",
            "label": "Run AI-generation detection",
            "type": "platform_feature",
            "feature": "ai_detection.video",
        },
    ],
    "audio": [
        {
            "action": "check_c2pa",
            "label": "Check for a C2PA provenance manifest",
            "type": "platform_feature",
            "feature": "authenticity.c2pa",
        },
        {
            "action": "check_metadata_authenticity",
            "label": "Verify metadata-based authenticity (geolocation, timeline)",
            "type": "platform_feature",
            "feature": "authenticity.metadata",
        },
        {
            "action": "ai_detection_audio",
            "label": "Run AI-generation detection",
            "type": "platform_feature",
            "feature": "ai_detection.audio",
        },
    ],
}

# Frames behaves like video — same platform features apply.
_PLATFORM_FEATURES["frames"] = _PLATFORM_FEATURES["video"]


def next_steps_for(
    media_type: MediaType,
    elevated_detectors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the next-steps list for a media type.

    When *elevated_detectors* is provided, signal-conditional steps are
    inserted between the universal manual steps and platform features.
    Unknown media types fall back to the universal manual steps only,
    so an upstream typo can't empty the list.
    """
    # Build conditional steps from elevated detectors.
    conditional: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for det in elevated_detectors or []:
        for step in _SIGNAL_CONDITIONAL_STEPS.get(det, []):
            if step["action"] not in seen_actions:
                conditional.append(step)
                seen_actions.add(step["action"])

    features = _PLATFORM_FEATURES.get(media_type, [])
    return [*_UNIVERSAL_MANUAL, *conditional, *features, _UNIVERSAL_CAUTION]
