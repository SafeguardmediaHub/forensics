"""Thin JSON runner for image and frame-analysis paths in `work/`.

This is the first wrapper boundary for the legacy `work` engine. It preserves
the existing direct execution paths while emitting machine-readable JSON for
future internal service/container work.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

# Must be set before importing frame_analysis/librosa dependencies.
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

from frame_analysis import AnalysisMode, Finding, FrameAnalysisEngine, SamplingMode
from visual_forens import generate_forensic_report


def analyze_image_to_dict(
    file_path: str | Path,
    output_dir: str | Path | None = None,
    is_video_frame: bool = False,
) -> dict[str, Any]:
    """Run the image forensics path and return a JSON-serializable dict."""
    path = Path(file_path)
    out_dir = Path(output_dir) if output_dir else Path(
        tempfile.mkdtemp(prefix="work_image_runner_")
    )

    report = generate_forensic_report(
        str(path),
        str(out_dir),
        ai_detector=None,
        is_video_frame=is_video_frame,
    )

    assessment = report.get("overall_assessment", {})
    metadata = report.get("metadata", {})
    verification = report.get("verification", {})

    return {
        "engine": "work",
        "mode": "image",
        "filename": path.name,
        "output_dir": str(out_dir),
        "verdict": assessment.get("verdict"),
        "tampering_likelihood": assessment.get("tampering_likelihood"),
        "confidence": assessment.get("confidence"),
        "note": assessment.get("note"),
        "file": {
            "sha256": verification.get("sha256") or metadata.get("sha256"),
            "md5": verification.get("md5") or metadata.get("md5"),
            "dimensions": metadata.get("dimensions"),
            "format": metadata.get("format"),
            "file_size_bytes": metadata.get("file_size_bytes"),
        },
        "report": report,
    }


def analyze_frames_to_dict(
    file_path: str | Path,
    mode: str = "standard",
    sampling_mode: str = "sampled",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the frame-analysis path and return a JSON-serializable dict."""
    path = Path(file_path)
    job_id = f"frames-{uuid.uuid4()}"
    out_dir = Path(output_dir) if output_dir else Path(
        tempfile.mkdtemp(prefix="work_frames_runner_")
    )

    result = FrameAnalysisEngine(job_id=job_id, output_dir=str(out_dir)).analyze_video(
        str(path),
        AnalysisMode(mode),
        SamplingMode(sampling_mode),
    )

    return {
        "engine": "work",
        "mode": "frames",
        "filename": path.name,
        "job_id": result.job_id,
        "output_dir": str(out_dir),
        "media_type": result.media_type.value,
        "media_info": result.media_info,
        "verdict": result.verdict,
        "tampering_confidence": round(float(result.tampering_confidence), 4),
        "tampering_type": result.tampering_type,
        "verdict_explanation": result.verdict_explanation,
        "parameters": result.parameters,
        "created_at": result.created_at,
        "completed_at": result.completed_at,
        "temporal_findings_count": len(result.findings),
        "spatial_findings_count": len(result.spatial_findings),
        "findings": [_serialize_finding(item) for item in result.findings],
        "spatial_findings": [
            _serialize_finding(item) for item in result.spatial_findings
        ],
    }


def _serialize_finding(finding: Finding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "type": finding.type.value,
        "severity": finding.severity.value,
        "location": finding.location,
        "explanation": finding.explanation,
        "metrics": finding.metrics,
        "evidence_artifacts": list(finding.evidence_artifacts),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run work engine paths and emit JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_parser = subparsers.add_parser("image", help="Run image forensics")
    image_parser.add_argument("file_path", help="Path to the image file to analyse")
    image_parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Optional output directory for generated artefacts",
    )
    image_parser.add_argument(
        "--video-frame",
        action="store_true",
        help="Treat the input image as an extracted video frame",
    )

    frames_parser = subparsers.add_parser("frames", help="Run frame analysis")
    frames_parser.add_argument("file_path", help="Path to the video file to analyse")
    frames_parser.add_argument(
        "--mode",
        default="standard",
        choices=[item.value for item in AnalysisMode],
        help="Frame-analysis mode",
    )
    frames_parser.add_argument(
        "--sampling-mode",
        default="sampled",
        choices=[item.value for item in SamplingMode],
        help="Frame sampling mode",
    )
    frames_parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Optional output directory for generated artefacts",
    )

    args = parser.parse_args()

    if args.command == "image":
        output = analyze_image_to_dict(
            args.file_path,
            output_dir=args.output_dir,
            is_video_frame=args.video_frame,
        )
    else:
        output = analyze_frames_to_dict(
            args.file_path,
            mode=args.mode,
            sampling_mode=args.sampling_mode,
            output_dir=args.output_dir,
        )

    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
