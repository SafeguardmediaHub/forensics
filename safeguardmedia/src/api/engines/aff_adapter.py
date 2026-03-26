"""AFF adapter — wraps AudioForensics Framework for the unified API."""
import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def analyze(file_path: Path) -> dict[str, Any]:
    from api.engines.engine_registry import (
        AFF_Pipeline, AFF_MetadataModule, AFF_ENFModule,
        AFF_NoiseModule, AFF_CompressionModule, AFF_ReverberationModule,
        AFF_VoiceModule, AFF_FusionEngine, aff_available,
    )
    if not aff_available:
        raise RuntimeError("AFF engine is not available")

    tmp_dir = tempfile.mkdtemp(prefix="aff_")
    pipeline = AFF_Pipeline(temp_dir=tmp_dir)
    case = pipeline.ingest(file_path)

    for Module in [
        AFF_MetadataModule, AFF_ENFModule, AFF_NoiseModule,
        AFF_CompressionModule, AFF_ReverberationModule, AFF_VoiceModule,
    ]:
        Module()._safe_run(case)

    result = AFF_FusionEngine().fuse(case)
    return _serialize(result, case)


def _serialize(result: Any, case: Any) -> dict[str, Any]:
    findings = []
    for f in result.all_findings:
        finding = {
            "module":      f.module,
            "title":       f.title,
            "severity":    f.severity.value if hasattr(f.severity, "value") else str(f.severity),
            "confidence":  round(float(f.confidence), 4),
            "description": f.description,
        }
        if hasattr(f, "temporal_location") and f.temporal_location:
            tl = f.temporal_location
            finding["temporal_location"] = {
                "start_s": round(float(tl.start_seconds), 3),
                "end_s":   round(float(tl.end_seconds), 3),
            }
        findings.append(finding)

    audio_info = {}
    if hasattr(case, "audio_profile") and case.audio_profile:
        ap = case.audio_profile
        audio_info = {
            "codec":       getattr(ap, "codec", None),
            "duration_s":  getattr(ap, "duration_s", None),
            "sample_rate": getattr(ap, "sample_rate", None),
            "bitrate_bps": getattr(ap, "bitrate_bps", None),
            "channels":    getattr(ap, "channels", None),
            "container":   getattr(ap, "container", None),
        }

    module_scores = {}
    skipped_modules = []
    if hasattr(result, "module_scores"):
        for k, ms in result.module_scores.items():
            score_val = ms.score if hasattr(ms, "score") else float(ms)
            module_scores[k] = round(float(score_val), 4)
            if hasattr(ms, "skipped") and ms.skipped:
                skipped_modules.append(k)

    return {
        "case_id":              getattr(case, "case_id", None),
        "verdict":              result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict),
        "verdict_label":        getattr(result, "verdict_label", str(result.verdict)),
        "fused_probability":    round(float(result.fused_probability), 4),
        "confidence":           round(float(result.confidence), 4),
        "elevated_modules":     list(getattr(result, "elevated_modules", [])),
        "skipped_modules":      skipped_modules,
        "corroboration_factor": round(float(getattr(result, "corroboration_factor", 1.0)), 4),
        "has_conflict":         bool(getattr(result, "has_conflict", False)),
        "conflict_description": getattr(result, "conflict_description", None),
        "module_scores":        module_scores,
        "total_findings":       len(findings),
        "findings":             findings,
        "audio":                audio_info,
        "calibration_note":     "Pre-calibration thresholds in use.",
    }
