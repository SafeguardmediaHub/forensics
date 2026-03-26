"""Frames adapter — wraps FrameAnalysisEngine for the unified API."""
import logging
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def analyze(file_path: Path, output_dir: Path) -> dict[str, Any]:
    from api.engines.engine_registry import work_available
    if not work_available:
        raise RuntimeError("Work engines are not available")

    import frame_analysis as fa

    engine = fa.FrameAnalysisEngine(
        job_id=uuid.uuid4().hex,
        output_dir=str(output_dir),
    )
    result = engine.analyze_video(
        video_path=str(file_path),
        mode=fa.AnalysisMode.FULL,
        sampling_mode=fa.SamplingMode.ADAPTIVE,
    )
    return _serialize(result)


def _serialize(result: Any) -> dict[str, Any]:
    findings = []
    for f in getattr(result, "findings", []):
        findings.append({
            "type":        f.finding_type.value if hasattr(f.finding_type, "value") else str(f.finding_type),
            "severity":    f.severity.value if hasattr(f.severity, "value") else str(f.severity),
            "description": f.description,
            "frame_range": getattr(f, "frame_range", None),
            "timestamp_s": getattr(f, "timestamp_s", None),
        })

    return {
        "verdict":              result.verdict,
        "tampering_confidence": round(float(result.tampering_confidence), 2),
        "tampering_type":       result.tampering_type,
        "verdict_explanation":  result.verdict_explanation,
        "temporal_findings":    getattr(result, "temporal_findings_count", len(findings)),
        "spatial_findings":     getattr(result, "spatial_findings_count", 0),
        "findings":             findings,
    }
