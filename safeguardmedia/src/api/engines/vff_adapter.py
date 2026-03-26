"""VFF adapter — wraps VideoForensics Framework for the unified API."""
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def analyze(file_path: Path) -> dict[str, Any]:
    from api.engines.engine_registry import VFF_Pipeline, VFF_Fusion, vff_available
    if not vff_available:
        raise RuntimeError("VFF engine is not available")

    pipeline = VFF_Pipeline(extract_frames=True, extract_audio=True)
    case = pipeline.ingest(file_path)
    result = VFF_Fusion().run(case)
    return _serialize(result, case)


def _serialize(result: Any, case: Any) -> dict[str, Any]:
    m = result.metadata
    vp = case.video_profile

    findings = []
    for f in result.findings:
        findings.append({
            "module": f.module,
            "title": f.title,
            "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
            "confidence": round(float(f.confidence), 4),
            "description": f.description,
        })

    video_info = {}
    if vp:
        duration = (vp.total_frames / vp.frame_rate) if (vp.frame_rate and vp.total_frames) else 0
        video_info = {
            "codec":           vp.codec,
            "width":           vp.width,
            "height":          vp.height,
            "frame_rate":      vp.frame_rate,
            "total_frames":    vp.total_frames,
            "duration_s":      round(float(duration), 2),
            "has_audio":       vp.has_audio,
            "audio_codec":     vp.audio_codec,
            "file_size_bytes": vp.file_size_bytes,
            "sha256":          vp.sha256_hash,
            "encoder":         vp.encoder_software,
        }

    return {
        "verdict":            m.get("verdict"),
        "verdict_label":      m.get("verdict_label"),
        "fused_probability":  m.get("fused_probability"),
        "confidence":         m.get("confidence"),
        "n_elevated_modules": m.get("n_elevated_modules"),
        "module_scores":      {k: round(float(v), 4) for k, v in m.get("module_scores", {}).items()},
        "findings":           findings,
        "video":              video_info,
        "calibration_note":   m.get("calibration_note", "Pre-calibration thresholds in use."),
    }
