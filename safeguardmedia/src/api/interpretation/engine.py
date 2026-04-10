"""Single public entry point for the interpretation layer.

Adapters call `build_interpretation(...)` with structured data pulled
from their engine's output and get back the `interpretation` sub-object
to drop into the response.
"""

from typing import Any

from .bands import band_for
from .copy import build_summary, build_what_this_means
from .next_steps import next_steps_for


def build_interpretation(
    *,
    media_type: str,
    risk_score: int,
    elevated_detectors: list[str],
    total_detectors: int,
    n_findings: int,
    calibration_status: str,
) -> dict[str, Any]:
    """Build the interpretation sub-object for a forensic response."""
    risk_band = band_for(risk_score)
    return {
        "summary": build_summary(
            elevated_detectors=elevated_detectors,
            total_detectors=total_detectors,
            n_findings=n_findings,
        ),
        "what_this_means": build_what_this_means(
            risk_band=risk_band,
            calibration_status=calibration_status,
        ),
        "next_steps": next_steps_for(media_type),
    }
