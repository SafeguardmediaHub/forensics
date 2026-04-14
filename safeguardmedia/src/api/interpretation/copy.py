"""Plain-language copy generation.

Every string produced here must describe **what the detectors measured**,
not **what the file is**. No verdicts. No judgments. No conclusions.
A banned-words test in the test suite locks this in.
"""

from __future__ import annotations

from typing import Any

from .bands import RiskBand

# Words that imply a conclusion about the file itself. If any of these
# slip into generated copy, the lint test fails. Keep this list aligned
# with the assertion in tests/test_interpretation.py.
BANNED_WORDS = (
    "manipulated",
    "authentic",
    "verified",
    "inconclusive",
    "tampered",
    "fake",
    "genuine",
    "real",
    "deepfake",
)

# ── Detector display info ────────────────────────────────────────────────────
# Maps internal detector names to (display_name, observation_template).
# observation_template is a short phrase describing what the detector measured.
# Used by build_summary() when detector_details are provided.

_DETECTOR_DISPLAY: dict[str, tuple[str, str]] = {
    "ela": (
        "Error Level Analysis",
        "measured compression differences across regions",
    ),
    "noise": (
        "Noise Analysis",
        "measured inconsistent noise patterns",
    ),
    "copy_move": (
        "Copy-Move Detection",
        "measured repeated pixel patterns in different regions",
    ),
    "jpeg_compression": (
        "JPEG Compression",
        "measured signs of multiple compression cycles",
    ),
    "exif_metadata": (
        "Metadata Analysis",
        "found limited or missing camera information",
    ),
    "temporal": (
        "Temporal Analysis",
        "measured timing or motion irregularities between frames",
    ),
    "spatial": (
        "Spatial Analysis",
        "measured per-frame visual anomalies",
    ),
    "crop_detection": (
        "Crop Detection",
        "measured signs the image was cropped from a larger original",
    ),
    "screenshot_detection": (
        "Screenshot Detection",
        "measured signals consistent with screen capture rather than camera origin",
    ),
}

# ── Detector context for what_this_means ─────────────────────────────────────
# Each entry: (what_it_measures, what_elevation_could_indicate).
# Appended to the framing paragraph when detector_details are provided.

_DETECTOR_CONTEXT: dict[str, tuple[str, str]] = {
    "ela": (
        "Error Level Analysis measures whether all regions were compressed "
        "at the same quality level.",
        "Elevation can indicate that part of the image was inserted from "
        "a different source or re-saved selectively.",
    ),
    "noise": (
        "Noise Analysis measures whether noise grain is consistent across "
        "the image.",
        "Elevation can indicate blending of content from different cameras "
        "or processing pipelines.",
    ),
    "copy_move": (
        "Copy-Move Detection looks for repeated pixel patterns in different "
        "locations.",
        "Elevation can indicate that a region was duplicated to cover or "
        "replicate content.",
    ),
    "jpeg_compression": (
        "JPEG Compression Analysis checks for signs of multiple save cycles.",
        "Elevation can indicate the image was opened and re-exported, which "
        "is common in editing workflows.",
    ),
    "exif_metadata": (
        "Metadata Analysis checks for camera information typically embedded "
        "at capture time.",
        "Missing metadata can indicate the file was re-exported, processed "
        "through software, or captured via screenshot.",
    ),
    "temporal": (
        "Temporal Analysis checks frame timing, motion flow, and scene "
        "transitions for irregularities.",
        "Elevation can indicate cuts, splices, or inserted segments within "
        "the video timeline.",
    ),
    "spatial": (
        "Spatial Analysis examines individual frames for per-pixel anomalies.",
        "Elevation can indicate localised editing within specific frames.",
    ),
    "crop_detection": (
        "Crop Detection checks whether the image boundary aligns with "
        "the original compression grid and stored dimensions.",
        "Elevation can indicate the image was trimmed from a larger original.",
    ),
    "screenshot_detection": (
        "Screenshot Detection checks for metadata, resolution, and pixel "
        "patterns characteristic of screen capture.",
        "Elevation can indicate the file originated from a screen recording "
        "rather than a camera or export.",
    ),
}


def _pluralise(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _describe_detector(name: str, detail: dict[str, Any] | None) -> str:
    """Build a one-sentence observation for a single elevated detector."""
    display_name, observation = _DETECTOR_DISPLAY.get(
        name, (name.replace("_", " ").title(), "produced an elevated signal")
    )
    score = detail.get("score") if detail else None
    if score is not None:
        return f"{display_name} {observation} (score {score:.1f})."
    return f"{display_name} {observation}."


def build_summary(
    elevated_detectors: list[str],
    total_detectors: int,
    n_findings: int,
    *,
    detector_details: list[dict[str, Any]] | None = None,
) -> str:
    """Description of what the detectors observed.

    When *detector_details* is provided, includes a plain-language sentence
    for each elevated detector. Falls back to the original count-based
    format when details are absent.
    """
    n_elevated = len(elevated_detectors)

    if total_detectors <= 0:
        return "No detectors completed on this file."

    if n_elevated == 0:
        finding_word = _pluralise(n_findings, "finding", "findings")
        return (
            f"No detectors elevated. "
            f"{n_findings} {finding_word} recorded across {total_detectors} checks."
        )

    # Build the header line.
    detector_word = _pluralise(n_elevated, "detector", "detectors")
    header = f"{n_elevated} of {total_detectors} {detector_word} elevated."

    # Build per-detector observations when details available.
    if detector_details:
        details_by_name = {d["name"]: d for d in detector_details}
        observations = [
            _describe_detector(name, details_by_name.get(name))
            for name in elevated_detectors
        ]
        finding_word = _pluralise(n_findings, "finding", "findings")
        return (
            f"{header} "
            + " ".join(observations)
            + f" {n_findings} {finding_word} recorded."
        )

    # Fallback: name list only.
    detector_list = ", ".join(elevated_detectors)
    return (
        f"{header} "
        f"Elevated: {detector_list}. "
        f"{n_findings} total "
        f"{_pluralise(n_findings, 'finding', 'findings')} recorded."
    )


def build_what_this_means(
    risk_band: RiskBand,
    calibration_status: str,
    *,
    detector_details: list[dict[str, Any]] | None = None,
) -> str:
    """Framing paragraph that tells the user how to read the result.

    Describes the measurement, not the file. When *detector_details* is
    provided, appends context sentences explaining what the elevated
    detectors measure and what their elevation could indicate.
    """
    if risk_band == "high":
        body = (
            "Several independent detectors produced strong signals. "
            "This describes what the tools measured, not a conclusion about "
            "the file. A human reviewer should verify before trusting or "
            "sharing it."
        )
    elif risk_band == "elevated":
        body = (
            "One or more detectors produced signals above their normal range. "
            "This describes what the tools measured, not a conclusion about "
            "the file. The result should be reviewed alongside the source "
            "and context of the file."
        )
    else:
        body = (
            "Detectors did not produce strong signals on this file. "
            "This describes what the tools measured — it is not a statement "
            "that the file is trustworthy. Source and context still matter."
        )

    if not detector_details:
        return body

    # Append per-detector context (cap at 3 to avoid wall of text).
    elevated_names = [d["name"] for d in detector_details if d["name"] in _DETECTOR_CONTEXT]
    context_lines: list[str] = []
    for name in elevated_names[:3]:
        measures, indicates = _DETECTOR_CONTEXT[name]
        context_lines.append(f"{measures} {indicates}")

    if len(elevated_names) > 3:
        extras = ", ".join(
            _DETECTOR_DISPLAY.get(n, (n,))[0] for n in elevated_names[3:]
        )
        context_lines.append(f"Additional signals were recorded by {extras}.")

    if context_lines:
        body += " " + " ".join(context_lines)

    return body
