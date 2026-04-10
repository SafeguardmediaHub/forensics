"""Resolver for `checks_unavailable`.

Scope is strictly engine-internal: a check belongs here only if one of
this project's own engines was supposed to run it and couldn't. Checks
that live outside this project (C2PA, reverse lookup, AI detection, OCR,
geolocation, timeline, fact-check, claim research) are *never* listed
here — they are surfaced as suggestions in `next_steps` instead.

For v1 the resolver reads signals the engines already emit:
  - AFF  : `skipped_modules` from the fusion result
  - VFF  : `skipped_modules` if present, plus `has_audio` on the profile
  - image: presence of an EXIF block in the work-engine report
  - frames: sampling-mode errors reported by the frame engine

Each engine adapter calls `resolve_unavailable` with whatever structured
dict it has in hand; the resolver knows how to read each shape.
"""

from typing import Any

MediaType = str  # "image" | "video" | "audio" | "frames"


def _unavailable(name: str, reason: str) -> dict[str, str]:
    return {"name": name, "reason": reason}


def _resolve_audio(engine_result: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for module_name in engine_result.get("skipped_modules", []) or []:
        items.append(_unavailable(str(module_name), "detector_skipped"))
    audio_info = engine_result.get("audio") or {}
    if not audio_info.get("codec"):
        items.append(_unavailable("audio_metadata", "unreadable_header"))
    return items


def _resolve_video(engine_result: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for module_name in engine_result.get("skipped_modules", []) or []:
        items.append(_unavailable(str(module_name), "detector_skipped"))
    video_info = engine_result.get("video") or {}
    if video_info and not video_info.get("has_audio"):
        items.append(_unavailable("embedded_audio", "no_audio_stream"))
    return items


def _resolve_image(engine_result: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    report = engine_result.get("report") or {}
    metadata = report.get("metadata") or {}
    exif = metadata.get("exif")
    if not exif:
        items.append(_unavailable("exif_metadata", "no_exif_block_in_file"))
    return items


def _resolve_frames(engine_result: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    media_info = engine_result.get("media_info") or {}
    if media_info.get("sampling_error"):
        items.append(
            _unavailable("frame_sampling", str(media_info["sampling_error"]))
        )
    return items


_RESOLVERS = {
    "audio": _resolve_audio,
    "video": _resolve_video,
    "image": _resolve_image,
    "frames": _resolve_frames,
}


def resolve_unavailable(
    engine_result: dict[str, Any],
    media_type: MediaType,
) -> list[dict[str, str]]:
    """Return the `checks_unavailable` list for one engine result."""
    resolver = _RESOLVERS.get(media_type)
    if resolver is None:
        return []
    return resolver(engine_result)
