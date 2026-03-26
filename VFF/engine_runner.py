"""Thin JSON runner for the VFF engine.

This is the first wrapper boundary for VFF. It preserves the current direct
engine execution path while emitting stable machine-readable JSON for future
internal service/container work.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.ingestion.pipeline import IngestionPipeline
from modules.fusion.fusion_module import FusionModule

logger = logging.getLogger(__name__)


def analyze_to_dict(file_path: str | Path) -> dict[str, Any]:
    """Run VFF on one file and return a structured JSON-serializable dict."""
    path = Path(file_path)

    # Mirror the current direct-run baseline path from VFF/run_analysis.py.
    pipeline = IngestionPipeline(extract_frames=False, extract_audio=False)
    case = pipeline.ingest(path)
    result = FusionModule().run(case)

    return _serialize(result, case, path)


def _serialize(result: Any, case: Any, path: Path) -> dict[str, Any]:
    metadata = result.metadata
    video_profile = case.video_profile

    findings: list[dict[str, Any]] = []
    for finding in result.findings:
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

    video_info: dict[str, Any] = {}
    if video_profile:
        duration = (
            (video_profile.total_frames / video_profile.frame_rate)
            if (video_profile.frame_rate and video_profile.total_frames)
            else 0.0
        )
        video_info = {
            "codec": video_profile.codec,
            "width": video_profile.width,
            "height": video_profile.height,
            "frame_rate": video_profile.frame_rate,
            "total_frames": video_profile.total_frames,
            "duration_s": round(float(duration), 2),
            "has_audio": video_profile.has_audio,
            "audio_codec": video_profile.audio_codec,
            "file_size_bytes": video_profile.file_size_bytes,
            "sha256": video_profile.sha256_hash,
            "encoder": video_profile.encoder_software,
        }

    return {
        "engine": "vff",
        "filename": path.name,
        "case_id": getattr(case, "case_id", None),
        "verdict": metadata.get("verdict"),
        "verdict_label": metadata.get("verdict_label"),
        "fused_probability": metadata.get("fused_probability"),
        "confidence": metadata.get("confidence"),
        "base_score": metadata.get("base_score"),
        "corroboration_multiplier": metadata.get("corroboration_multiplier"),
        "n_elevated_modules": metadata.get("n_elevated_modules"),
        "module_scores": {
            name: round(float(score), 4)
            for name, score in metadata.get("module_scores", {}).items()
        },
        "adjusted_weights": {
            name: round(float(weight), 4)
            for name, weight in metadata.get("adjusted_weights", {}).items()
        },
        "has_conflict": metadata.get("has_conflict"),
        "conflict_description": metadata.get("conflict_description"),
        "total_findings": metadata.get("total_findings", len(findings)),
        "findings": findings,
        "video": video_info,
        "calibration_note": metadata.get(
            "calibration_note",
            "Pre-calibration thresholds in use.",
        ),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run VFF and emit JSON.")
    parser.add_argument("file_path", help="Path to the video file to analyse")
    args = parser.parse_args()

    output = analyze_to_dict(args.file_path)
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
