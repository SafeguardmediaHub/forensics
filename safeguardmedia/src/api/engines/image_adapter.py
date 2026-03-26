"""Image adapter — wraps visual_forens for the unified API."""
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def analyze(file_path: Path, output_dir: Path) -> dict[str, Any]:
    from api.engines.engine_registry import work_available
    if not work_available:
        raise RuntimeError("Work engines are not available")

    import visual_forens
    result = visual_forens.generate_forensic_report(
        image_path=str(file_path),
        output_dir=str(output_dir),
    )
    return _serialize(result)


def _serialize(result: dict[str, Any]) -> dict[str, Any]:
    assessment = result.get("overall_assessment", {})
    return {
        "verdict":             assessment.get("verdict", "unknown"),
        "tampering_likelihood": assessment.get("tampering_likelihood"),
        "confidence":          assessment.get("confidence"),
        "note":                assessment.get("note"),
        "module_scores": {
            "ela":              result.get("manipulation_detection", {}).get("ela", {}).get("score"),
            "noise":            result.get("manipulation_detection", {}).get("noise", {}).get("inconsistency_score"),
            "copy_move":        result.get("manipulation_detection", {}).get("copy_move", {}).get("clone_score"),
            "jpeg_compression": result.get("manipulation_detection", {}).get("jpeg_compression", {}).get("level"),
        },
        "findings":    result.get("user_friendly_summary", {}).get("findings", []),
        "metadata":    result.get("metadata", {}),
        "verification": result.get("verification", {}),
    }
