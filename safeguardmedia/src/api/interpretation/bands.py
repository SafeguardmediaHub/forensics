"""Risk-band cutoffs.

These thresholds describe *signal strength* — how loud the detectors got —
not a claim about whether the file is authentic. They are provisional
until calibration against real traffic is complete; change them here.
"""

from typing import Literal

RiskBand = Literal["low", "elevated", "high"]

# Inclusive lower bounds. A risk_score below ELEVATED_MIN is "low".
RISK_BAND_ELEVATED_MIN = 40
RISK_BAND_HIGH_MIN = 75


def band_for(risk_score: int) -> RiskBand:
    """Map a 0–100 risk score to a band label.

    Clamps out-of-range inputs rather than raising, so an upstream bug
    in score calculation never crashes the response pipeline.
    """
    score = max(0, min(100, int(risk_score)))
    if score >= RISK_BAND_HIGH_MIN:
        return "high"
    if score >= RISK_BAND_ELEVATED_MIN:
        return "elevated"
    return "low"
