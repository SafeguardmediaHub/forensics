"""Next-steps catalog.

Static per-media-type lists for v1. Each entry is either a `manual` step
(something the human does themselves) or a `platform_feature` step that
points at another capability in the wider platform via a stable `feature`
string. The backend maps those feature strings to its own endpoint IDs
when rendering — this project never calls those features itself and
never claims to know their result.

Signal-conditional steps (layer 3 from the plan — rules based on *which*
detectors elevated) are deliberately deferred. Add them here once real
usage tells us which rules matter.
"""

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


def next_steps_for(media_type: MediaType) -> list[dict[str, Any]]:
    """Return the v1 static next-steps list for a media type.

    Unknown media types fall back to the universal manual steps only,
    so an upstream typo can't empty the list.
    """
    features = _PLATFORM_FEATURES.get(media_type, [])
    return [*_UNIVERSAL_MANUAL, *features, _UNIVERSAL_CAUTION]
