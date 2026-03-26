from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from api.config import settings
from api.services.file_service import build_runtime_paths, cleanup_runtime_paths, ensure_runtime_dirs

REPO_ROOT = Path(__file__).resolve().parents[4]
CHUNK_SIZE = 1024 * 1024

MEDIA_CONFIG: dict[str, dict[str, Any]] = {
    "audio": {
        "prefix": "aff",
        "max_size_mb": settings.max_audio_size_mb,
        "allowed_exts": {
            ".wav", ".mp3", ".aac", ".m4a", ".ogg", ".flac", ".mp4", ".3gp", ".amr"
        },
        "script": REPO_ROOT / "AFF" / "engine_runner.py",
        "timeout_s": 300,
    },
    "video": {
        "prefix": "vff",
        "max_size_mb": settings.max_video_size_mb,
        "allowed_exts": {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v"},
        "script": REPO_ROOT / "VFF" / "engine_runner.py",
        "timeout_s": 900,
    },
    "image": {
        "prefix": "img",
        "max_size_mb": settings.max_image_size_mb,
        "allowed_exts": {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"},
        "script": REPO_ROOT / "work" / "engine_runner.py",
        "subcommand": ["image"],
        "timeout_s": 180,
        "uses_output_dir": True,
    },
    "frames": {
        "prefix": "frames",
        "max_size_mb": settings.max_video_size_mb,
        "allowed_exts": {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v"},
        "script": REPO_ROOT / "work" / "engine_runner.py",
        "subcommand": ["frames"],
        "timeout_s": 1200,
        "uses_output_dir": True,
    },
}


async def analyze_upload(
    media_type: str,
    file: UploadFile,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    saved = await save_upload(media_type=media_type, file=file)
    return analyze_saved_file(
        media_type=media_type,
        file_path=saved["upload_path"],
        filename=saved["filename"],
        output_dir=saved["output_dir"],
        options=options,
        cleanup=True,
    )


async def save_upload(
    media_type: str,
    file: UploadFile,
) -> dict[str, Any]:
    config = MEDIA_CONFIG.get(media_type)
    if config is None:
        raise HTTPException(status_code=422, detail=f"Unsupported media_type '{media_type}'")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in config["allowed_exts"]:
        accepted = ", ".join(sorted(config["allowed_exts"]))
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{suffix}'. Accepted: {accepted}",
        )

    runtime_paths = build_runtime_paths(
        media_type=media_type,
        suffix=suffix,
        job_id=f"{config['prefix']}_{uuid.uuid4().hex}",
        uses_output_dir=bool(config.get("uses_output_dir")),
    )

    size_bytes = 0
    try:
        with runtime_paths.upload_path.open("wb") as handle:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size_bytes += len(chunk)
                _validate_size(size_bytes, config["max_size_mb"])
                handle.write(chunk)
        return {
            "job_id": runtime_paths.job_id,
            "upload_path": runtime_paths.upload_path,
            "output_dir": runtime_paths.output_dir,
            "filename": file.filename or runtime_paths.upload_path.name,
        }
    finally:
        await file.close()


def analyze_saved_file(
    media_type: str,
    file_path: Path,
    filename: str,
    output_dir: Path | None,
    options: dict[str, Any] | None = None,
    cleanup: bool = False,
) -> dict[str, Any]:
    options = options or {}
    config = MEDIA_CONFIG.get(media_type)
    if config is None:
        raise HTTPException(status_code=422, detail=f"Unsupported media_type '{media_type}'")

    ensure_runtime_dirs()

    try:
        try:
            raw_result = _run_runner(
                media_type=media_type,
                file_path=file_path,
                output_dir=output_dir,
                options=options,
                timeout_s=config["timeout_s"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _normalize_result(
            media_type=media_type,
            filename=filename,
            raw_result=raw_result,
        )
    finally:
        if cleanup:
            cleanup_runtime_paths(
                upload_path=file_path,
                output_dir=output_dir,
                remove_output_dir=False,
            )


def _validate_size(size_bytes: int, max_size_mb: int) -> None:
    limit = max_size_mb * 1024 * 1024
    if size_bytes > limit:
        size_mb = size_bytes / 1024 / 1024
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f} MB). Limit: {max_size_mb} MB",
        )


def _run_runner(
    media_type: str,
    file_path: Path,
    output_dir: Path | None,
    options: dict[str, Any],
    timeout_s: int,
) -> dict[str, Any]:
    config = MEDIA_CONFIG[media_type]
    command = [sys.executable, str(config["script"])]
    command.extend(config.get("subcommand", []))
    command.append(str(file_path))

    if media_type == "frames":
        mode = str(options.get("mode", "standard"))
        sampling_mode = str(options.get("sampling_mode", "sampled"))
        command.extend(["--mode", mode, "--sampling-mode", sampling_mode])

    if output_dir is not None:
        command.extend(["--output-dir", str(output_dir)])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{media_type} runner timed out after {timeout_s}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"{media_type} runner failed: {detail}")

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(
            f"{media_type} runner returned invalid JSON. stderr: {stderr}"
        ) from exc


def _normalize_result(
    media_type: str,
    filename: str,
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    if media_type == "audio":
        probability = _clamp(raw_result.get("fused_probability"))
        confidence = _clamp(raw_result.get("confidence"))
        file_sha = raw_result.get("audio", {}).get("sha256")
        findings = _normalize_generic_findings(raw_result.get("findings", []))
        verdict = _normalize_verdict(raw_result.get("verdict"))
        return {
            "media_type": media_type,
            "engine": "aff",
            "verdict": verdict,
            "verdict_label": raw_result.get("verdict_label") or _label_from_verdict(verdict),
            "probability": probability,
            "confidence": confidence,
            "findings": findings,
            "summary": _first_non_empty(
                raw_result.get("conflict_description"),
                findings[0]["description"] if findings else None,
                raw_result.get("calibration_note"),
            ),
            "file": {"filename": filename, "sha256": file_sha},
            "engine_detail": raw_result,
        }

    if media_type == "video":
        probability = _clamp(raw_result.get("fused_probability"))
        confidence = _clamp(raw_result.get("confidence"))
        file_sha = raw_result.get("video", {}).get("sha256")
        findings = _normalize_generic_findings(raw_result.get("findings", []))
        verdict = _normalize_verdict(raw_result.get("verdict"))
        return {
            "media_type": media_type,
            "engine": "vff",
            "verdict": verdict,
            "verdict_label": raw_result.get("verdict_label") or _label_from_verdict(verdict),
            "probability": probability,
            "confidence": confidence,
            "findings": findings,
            "summary": _first_non_empty(
                findings[0]["description"] if findings else None,
                raw_result.get("calibration_note"),
            ),
            "file": {"filename": filename, "sha256": file_sha},
            "engine_detail": raw_result,
        }

    if media_type == "image":
        probability = _clamp(_as_float(raw_result.get("tampering_likelihood")) / 100.0)
        confidence = _confidence_from_string(raw_result.get("confidence"))
        verdict = _normalize_verdict(raw_result.get("verdict"))
        return {
            "media_type": media_type,
            "engine": "work",
            "verdict": verdict,
            "verdict_label": raw_result.get("verdict") or _label_from_verdict(verdict),
            "probability": probability,
            "confidence": confidence,
            "findings": [],
            "summary": raw_result.get("note"),
            "file": {
                "filename": filename,
                "sha256": raw_result.get("file", {}).get("sha256"),
            },
            "engine_detail": raw_result,
        }

    probability = _clamp(_as_float(raw_result.get("tampering_confidence")) / 100.0)
    confidence = probability
    verdict = _normalize_verdict(raw_result.get("verdict"))
    findings = _normalize_frame_findings(raw_result.get("findings", []))
    return {
        "media_type": media_type,
        "engine": "work",
        "verdict": verdict,
        "verdict_label": _label_from_verdict(verdict),
        "probability": probability,
        "confidence": confidence,
        "findings": findings,
        "summary": raw_result.get("verdict_explanation"),
        "file": {"filename": filename, "sha256": None},
        "engine_detail": raw_result,
    }


def _normalize_generic_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for finding in findings:
        temporal = finding.get("temporal_location") or {}
        normalized.append(
            {
                "title": finding.get("title") or "Untitled finding",
                "module": finding.get("module") or "unknown",
                "severity": str(finding.get("severity") or "low").lower(),
                "confidence": _clamp(finding.get("confidence")),
                "description": finding.get("description") or "",
                "timestamp_s": temporal.get("start_s"),
            }
        )
    return normalized


def _normalize_frame_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for finding in findings:
        location = finding.get("location") or {}
        title = str(finding.get("type") or "FRAME_ANALYSIS").replace("_", " ").title()
        normalized.append(
            {
                "title": title,
                "module": "frame_analysis",
                "severity": str(finding.get("severity") or "low").lower(),
                "confidence": _clamp(finding.get("metrics", {}).get("magnitude_ratio", 0.0)),
                "description": finding.get("explanation") or "",
                "timestamp_s": location.get("start"),
            }
        )
    return normalized


def _normalize_verdict(verdict: Any) -> str:
    value = str(verdict or "inconclusive").strip().lower()
    mapping = {
        "likely authentic": "likely_authentic",
        "likely_authentic": "likely_authentic",
        "possibly tampered": "inconclusive",
        "inconclusive": "inconclusive",
        "likely tampered": "likely_tampered",
        "likely_tampered": "likely_tampered",
        "tampered": "tampered",
    }
    return mapping.get(value, value.replace(" ", "_"))


def _label_from_verdict(verdict: str) -> str:
    return verdict.replace("_", " ").title()


def _confidence_from_string(value: Any) -> float:
    if isinstance(value, (int, float)):
        return _clamp(value)
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
    return mapping.get(str(value or "").strip().lower(), 0.5)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: Any) -> float:
    number = _as_float(value)
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return round(number, 4)


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None
