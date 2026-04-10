"""Interpretation layer — turns raw engine output into risk bands, plain-language
summaries, and recommended next steps.

This package is deliberately pure: data in, data out, no I/O. Engines measure;
this layer interprets. No code in here should ever make a truth-claim about a
file — only statements about what the detectors measured and what a human
should consider doing next.
"""

from .bands import RISK_BAND_ELEVATED_MIN, RISK_BAND_HIGH_MIN, band_for
from .checks import resolve_unavailable
from .copy import build_summary, build_what_this_means
from .engine import build_interpretation
from .next_steps import next_steps_for

__all__ = [
    "RISK_BAND_ELEVATED_MIN",
    "RISK_BAND_HIGH_MIN",
    "band_for",
    "build_interpretation",
    "build_summary",
    "build_what_this_means",
    "next_steps_for",
    "resolve_unavailable",
]
