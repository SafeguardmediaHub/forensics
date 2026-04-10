"""Plain-language copy generation.

Every string produced here must describe **what the detectors measured**,
not **what the file is**. No verdicts. No judgments. No conclusions.
A banned-words test in the test suite locks this in.
"""

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


def _pluralise(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def build_summary(
    elevated_detectors: list[str],
    total_detectors: int,
    n_findings: int,
) -> str:
    """One-sentence description of what the detectors observed.

    Deliberately observational: counts and names, no framing.
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

    detector_list = ", ".join(elevated_detectors)
    detector_word = _pluralise(n_elevated, "detector", "detectors")
    return (
        f"{n_elevated} of {total_detectors} {detector_word} elevated "
        f"({detector_list}). {n_findings} total "
        f"{_pluralise(n_findings, 'finding', 'findings')} recorded."
    )


def build_what_this_means(
    risk_band: RiskBand,
    calibration_status: str,
) -> str:
    """Framing paragraph that tells the user how to read the result.

    Describes the measurement, not the file. Appends a calibration note
    while the system is still in pre-calibration.
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

    if calibration_status == "pre_calibration":
        body += (
            " Risk bands are provisional pending calibration against "
            "live traffic."
        )

    return body
