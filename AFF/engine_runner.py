"""Thin JSON runner for the AFF engine.

This file is the first wrapper boundary for AFF. It preserves the current
direct-engine execution path while providing a stable machine-readable output
for future internal service/container work.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from core.ingestion.pipeline import IngestionPipeline
from modules.compression.compression_module import CompressionModule
from modules.enf.enf_module import ENFModule
from modules.fusion.fusion_engine import FusionEngine
from modules.metadata.metadata_module import MetadataModule
from modules.noise.noise_module import NoiseModule
from modules.reverberation.reverberation_module import ReverberationModule
from modules.voice.voice_module import VoiceModule

logger = logging.getLogger(__name__)


def analyze_to_dict(file_path: str | Path) -> dict[str, Any]:
    """Run AFF on one file and return a structured JSON-serializable dict."""
    path = Path(file_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="aff_runner_"))

    case = IngestionPipeline(temp_dir=temp_dir).ingest(path)

    for module in (
        MetadataModule(),
        ENFModule(),
        NoiseModule(),
        CompressionModule(),
        ReverberationModule(),
        VoiceModule(),
    ):
        case.add_score(module._safe_run(case))

    result = FusionEngine().fuse(case)
    return _serialize(result, case, path)


def _serialize(result: Any, case: Any, path: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for finding in result.all_findings:
        item = {
            "module": finding.module,
            "title": finding.title,
            "severity": (
                finding.severity.value
                if hasattr(finding.severity, "value")
                else str(finding.severity)
            ),
            "confidence": round(float(finding.confidence), 4),
            "description": finding.description,
        }
        temporal_location = getattr(finding, "temporal_location", None)
        if temporal_location:
            item["temporal_location"] = {
                "start_s": round(float(temporal_location.start_seconds), 3),
                "end_s": round(float(temporal_location.end_seconds), 3),
            }
        findings.append(item)

    audio_info: dict[str, Any] = {}
    audio_profile = getattr(case, "audio_profile", None)
    if audio_profile:
        audio_info = {
            "codec": getattr(audio_profile, "codec_name", None),
            "duration_s": getattr(audio_profile, "duration_seconds", None),
            "sample_rate": getattr(audio_profile, "sample_rate", None),
            "bitrate_bps": getattr(audio_profile, "bitrate_bps", None),
            "channels": getattr(audio_profile, "channels", None),
            "container": getattr(audio_profile, "container_format", None),
            "sha256": getattr(audio_profile, "sha256_hash", None),
        }

    module_scores: dict[str, float] = {}
    skipped_modules: list[str] = []
    if hasattr(result, "module_scores"):
        for name, module_score in result.module_scores.items():
            score_value = (
                module_score.score
                if hasattr(module_score, "score")
                else float(module_score)
            )
            module_scores[name] = round(float(score_value), 4)
            if getattr(module_score, "skipped", False):
                skipped_modules.append(name)

    return {
        "engine": "aff",
        "filename": path.name,
        "case_id": getattr(case, "case_id", None),
        "verdict": (
            result.verdict.value
            if hasattr(result.verdict, "value")
            else str(result.verdict)
        ),
        "verdict_label": getattr(result, "verdict_label", str(result.verdict)),
        "fused_probability": round(float(result.fused_probability), 4),
        "confidence": round(float(result.confidence), 4),
        "elevated_modules": list(getattr(result, "elevated_modules", [])),
        "skipped_modules": skipped_modules,
        "corroboration_factor": round(
            float(getattr(result, "corroboration_factor", 1.0)),
            4,
        ),
        "has_conflict": bool(getattr(result, "has_conflict", False)),
        "conflict_description": getattr(result, "conflict_description", None),
        "module_scores": module_scores,
        "total_findings": len(findings),
        "findings": findings,
        "audio": audio_info,
        "calibration_note": getattr(
            result,
            "calibration_note",
            "Pre-calibration thresholds in use.",
        ),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run AFF and emit JSON.")
    parser.add_argument("file_path", help="Path to the audio file to analyse")
    args = parser.parse_args()

    output = analyze_to_dict(args.file_path)
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
