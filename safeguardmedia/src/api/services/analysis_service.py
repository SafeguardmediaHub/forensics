from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from api.config import settings
from api.interpretation import band_for, build_interpretation, resolve_unavailable
from api.services.file_service import (
    build_runtime_paths,
    cleanup_runtime_paths,
    ensure_runtime_dirs,
)

CALIBRATION_STATUS = "pre_calibration"

# Keys stripped from raw engine output before it leaves the API as
# ``engine_detail``.  The engines still emit verdicts internally — we
# just don't let them leak into the response so consumers can't read them
# instead of the top-level risk fields.
_VERDICT_KEYS_TOP = {
    "verdict",
    "verdict_label",
    "verdict_explanation",
    "tampering_likelihood",
    "tampering_confidence",
}
_VERDICT_KEYS_REPORT = {"overall_assessment", "user_friendly_summary"}

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


def _strip_engine_verdicts(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-ish copy of *raw* with verdict fields removed.

    Strips top-level verdict keys and, if a ``report`` dict exists, removes
    nested verdict blocks (``overall_assessment``, ``user_friendly_summary``).
    The rest of the engine output is preserved for debugging.
    """
    cleaned = {k: v for k, v in raw.items() if k not in _VERDICT_KEYS_TOP}
    if "report" in cleaned and isinstance(cleaned["report"], dict):
        cleaned["report"] = {
            k: v for k, v in cleaned["report"].items() if k not in _VERDICT_KEYS_REPORT
        }
    return cleaned


# ── Normalize result ─────────────────────────────────────────────────────────

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
        elevated_detectors = list(raw_result.get("elevated_modules", []) or [])
        total_detectors = len(raw_result.get("module_scores", {}) or {})
        risk_score = int(round(probability * 100))
        detector_details = _build_audio_detector_details(raw_result, elevated_detectors)
        return {
            "media_type": media_type,
            "engine": "aff",
            "risk_score": risk_score,
            "risk_band": band_for(risk_score),
            "measurement_confidence": confidence,
            "calibration_status": CALIBRATION_STATUS,
            "elevated_detectors": elevated_detectors,
            "checks_unavailable": resolve_unavailable(raw_result, "audio"),
            "interpretation": build_interpretation(
                media_type="audio",
                risk_score=risk_score,
                elevated_detectors=elevated_detectors,
                total_detectors=total_detectors,
                n_findings=len(findings),
                calibration_status=CALIBRATION_STATUS,
                detector_details=detector_details,
            ),
            "findings": findings,
            "file": {"filename": filename, "sha256": file_sha},
            "engine_detail": _strip_engine_verdicts(raw_result),
            # Deprecated — always null. Scheduled for removal next release.
            "verdict": None,
            "verdict_label": None,
            "probability": None,
            "tampering_likelihood": None,
        }

    if media_type == "video":
        probability = _clamp(raw_result.get("fused_probability"))
        confidence = _clamp(raw_result.get("confidence"))
        file_sha = raw_result.get("video", {}).get("sha256")
        findings = _normalize_generic_findings(raw_result.get("findings", []))
        elevated_detectors = _vff_elevated_detectors(raw_result)
        total_detectors = len(raw_result.get("module_scores", {}) or {})
        risk_score = int(round(probability * 100))
        detector_details = _build_video_detector_details(raw_result, elevated_detectors)
        return {
            "media_type": media_type,
            "engine": "vff",
            "risk_score": risk_score,
            "risk_band": band_for(risk_score),
            "measurement_confidence": confidence,
            "calibration_status": CALIBRATION_STATUS,
            "elevated_detectors": elevated_detectors,
            "checks_unavailable": resolve_unavailable(raw_result, "video"),
            "interpretation": build_interpretation(
                media_type="video",
                risk_score=risk_score,
                elevated_detectors=elevated_detectors,
                total_detectors=total_detectors,
                n_findings=len(findings),
                calibration_status=CALIBRATION_STATUS,
                detector_details=detector_details,
            ),
            "findings": findings,
            "file": {"filename": filename, "sha256": file_sha},
            "engine_detail": _strip_engine_verdicts(raw_result),
            # Deprecated — always null. Scheduled for removal next release.
            "verdict": None,
            "verdict_label": None,
            "probability": None,
            "tampering_likelihood": None,
        }

    if media_type == "image":
        probability = _clamp(_as_float(raw_result.get("tampering_likelihood")) / 100.0)
        confidence = _confidence_from_string(raw_result.get("confidence"))
        risk_score = int(round(probability * 100))
        elevated_detectors = _work_image_elevated_detectors(raw_result)
        total_detectors = _work_image_total_detectors(raw_result)
        findings = _normalize_image_findings(raw_result)
        detector_details = _build_image_detector_details(raw_result, elevated_detectors)
        return {
            "media_type": media_type,
            "engine": "work",
            "risk_score": risk_score,
            "risk_band": band_for(risk_score),
            "measurement_confidence": confidence,
            "calibration_status": CALIBRATION_STATUS,
            "elevated_detectors": elevated_detectors,
            "checks_unavailable": resolve_unavailable(raw_result, "image"),
            "interpretation": build_interpretation(
                media_type="image",
                risk_score=risk_score,
                elevated_detectors=elevated_detectors,
                total_detectors=total_detectors,
                n_findings=len(findings),
                calibration_status=CALIBRATION_STATUS,
                detector_details=detector_details,
            ),
            "findings": findings,
            "file": {
                "filename": filename,
                "sha256": raw_result.get("file", {}).get("sha256"),
            },
            "engine_detail": _strip_engine_verdicts(raw_result),
            # Deprecated — always null. Scheduled for removal next release.
            "verdict": None,
            "verdict_label": None,
            "probability": None,
            "tampering_likelihood": None,
        }

    # frames: the engine reports tampering_confidence as 0–1 already.
    raw_conf = _as_float(raw_result.get("tampering_confidence"))
    probability = _clamp(raw_conf if raw_conf <= 1.0 else raw_conf / 100.0)
    confidence = probability
    temporal_findings = _normalize_frame_findings(raw_result.get("findings", []))
    spatial_findings = _normalize_spatial_findings(raw_result.get("spatial_findings", []))
    findings = temporal_findings + spatial_findings
    elevated_detectors: list[str] = []
    if temporal_findings:
        elevated_detectors.append("temporal")
    if spatial_findings:
        elevated_detectors.append("spatial")
    risk_score = int(round(probability * 100))
    detector_details = _build_frame_detector_details(
        temporal_findings, spatial_findings, elevated_detectors,
    )
    return {
        "media_type": media_type,
        "engine": "work",
        "risk_score": risk_score,
        "risk_band": band_for(risk_score),
        "measurement_confidence": confidence,
        "calibration_status": CALIBRATION_STATUS,
        "elevated_detectors": elevated_detectors,
        "checks_unavailable": resolve_unavailable(raw_result, "frames"),
        "interpretation": build_interpretation(
            media_type="frames",
            risk_score=risk_score,
            elevated_detectors=elevated_detectors,
            total_detectors=2,  # temporal + spatial
            n_findings=len(findings),
            calibration_status=CALIBRATION_STATUS,
            detector_details=detector_details,
        ),
        "findings": findings,
        "file": {"filename": filename, "sha256": None},
        "engine_detail": _strip_engine_verdicts(raw_result),
        # Deprecated — always null. Scheduled for removal next release.
        "verdict": None,
        "verdict_label": None,
        "probability": None,
        "tampering_likelihood": None,
    }


# ── Detector details builders ────────────────────────────────────────────────
# Build the detector_details list that feeds the interpretation layer with
# per-detector context. Only elevated detectors are included.

def _build_audio_detector_details(
    raw_result: dict[str, Any],
    elevated_detectors: list[str],
) -> list[dict[str, Any]]:
    module_scores = raw_result.get("module_scores", {}) or {}
    return [
        {"name": name, "score": round(_as_float(module_scores.get(name)) * 100, 1)}
        for name in elevated_detectors
        if name in module_scores
    ]


def _build_video_detector_details(
    raw_result: dict[str, Any],
    elevated_detectors: list[str],
) -> list[dict[str, Any]]:
    module_scores = raw_result.get("module_scores", {}) or {}
    return [
        {"name": name, "score": round(_as_float(module_scores.get(name)) * 100, 1)}
        for name in elevated_detectors
        if name in module_scores
    ]


def _build_image_detector_details(
    raw_result: dict[str, Any],
    elevated_detectors: list[str],
) -> list[dict[str, Any]]:
    report = raw_result.get("report") or {}
    manipulation = report.get("manipulation_detection") or {}
    details: list[dict[str, Any]] = []

    score_map: dict[str, float] = {
        "ela": _as_float((manipulation.get("ela") or {}).get("ela_score")),
        "noise": _as_float(
            (manipulation.get("noise") or {}).get("noise_inconsistency_score")
        ),
        "copy_move": _as_float(
            (manipulation.get("copy_move") or {}).get("clone_score")
        ),
    }

    for name in elevated_detectors:
        if name in score_map:
            details.append({"name": name, "score": round(score_map[name], 1)})
        else:
            # Detectors without a numeric score (jpeg_compression, exif_metadata,
            # crop_detection, screenshot_detection).
            details.append({"name": name})

    return details


def _build_frame_detector_details(
    temporal_findings: list[dict[str, Any]],
    spatial_findings: list[dict[str, Any]],
    elevated_detectors: list[str],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    if "temporal" in elevated_detectors:
        details.append({
            "name": "temporal",
            "score": float(len(temporal_findings)),
        })
    if "spatial" in elevated_detectors:
        details.append({
            "name": "spatial",
            "score": float(len(spatial_findings)),
        })
    return details


# ── Image findings normalization ─────────────────────────────────────────────
# Extracts proper Finding objects from the image engine's manipulation_detection
# report. Previously this returned [] and all detail was buried in engine_detail.

# Thresholds mirror the engine's medium-band cutoffs. Only findings at or above
# the medium threshold are surfaced — below-threshold detectors stay quiet.
_IMAGE_FINDING_THRESHOLDS = {
    "ela": 8.0,
    "noise": 25.0,
    "copy_move": 2.0,
}


def _normalize_image_findings(raw_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract findings from the image engine's manipulation_detection report."""
    report = raw_result.get("report") or {}
    manipulation = report.get("manipulation_detection") or {}
    findings: list[dict[str, Any]] = []

    # ELA
    ela = manipulation.get("ela") or {}
    ela_score = _as_float(ela.get("ela_score"))
    if ela_score > _IMAGE_FINDING_THRESHOLDS["ela"]:
        severity = "high" if ela_score > 15.0 else "medium"
        findings.append({
            "title": "Compression inconsistency detected",
            "module": "ela",
            "severity": severity,
            "confidence": min(ela_score / 20.0, 1.0),
            "description": (
                f"Error Level Analysis measured a score of {ela_score:.1f}. "
                "This indicates that some regions of the image were saved at "
                "different compression levels, which can occur when content "
                "is composited from multiple sources."
            ),
            "timestamp_s": None,
        })

    # Noise
    noise = manipulation.get("noise") or {}
    noise_score = _as_float(noise.get("noise_inconsistency_score"))
    if noise_score > _IMAGE_FINDING_THRESHOLDS["noise"]:
        severity = "high" if noise_score > 50.0 else "medium"
        findings.append({
            "title": "Noise pattern inconsistency detected",
            "module": "noise",
            "severity": severity,
            "confidence": min(noise_score / 60.0, 1.0),
            "description": (
                f"Noise analysis measured an inconsistency score of "
                f"{noise_score:.1f}. This indicates the noise grain varies "
                "across regions, which can occur when parts of the image "
                "come from different cameras or processing pipelines."
            ),
            "timestamp_s": None,
        })

    # Copy-move
    clone = manipulation.get("copy_move") or {}
    clone_score = _as_float(clone.get("clone_score"))
    if clone_score > _IMAGE_FINDING_THRESHOLDS["copy_move"]:
        severity = "high" if clone_score > 5.0 else "medium"
        findings.append({
            "title": "Repeated pixel patterns detected",
            "module": "copy_move",
            "severity": severity,
            "confidence": min(clone_score / 8.0, 1.0),
            "description": (
                f"Copy-move detection measured a clone score of "
                f"{clone_score:.1f}%. This indicates that similar pixel "
                "patterns appear in multiple locations, which can occur "
                "when regions are duplicated."
            ),
            "timestamp_s": None,
        })

    # JPEG double compression
    jpeg = manipulation.get("jpeg_compression") or {}
    if jpeg.get("double_compression_likelihood") in ("High", "Medium"):
        likelihood = jpeg["double_compression_likelihood"]
        severity = "medium" if likelihood == "High" else "low"
        findings.append({
            "title": "Multiple compression cycles detected",
            "module": "jpeg_compression",
            "severity": severity,
            "confidence": 0.7 if likelihood == "High" else 0.4,
            "description": (
                "JPEG compression analysis detected signs of the image being "
                "saved multiple times. This can occur during editing workflows "
                "or when an image is re-exported from an editing tool."
            ),
            "timestamp_s": None,
        })

    # Sparse/missing EXIF
    metadata = report.get("metadata") or {}
    exif = metadata.get("exif")
    if not exif or (isinstance(exif, dict) and len(exif) < 3):
        findings.append({
            "title": "Camera metadata missing or sparse",
            "module": "exif_metadata",
            "severity": "low",
            "confidence": 0.5,
            "description": (
                "The image contains little or no EXIF metadata. Camera-produced "
                "images typically include make, model, and capture settings. "
                "Missing metadata can indicate the file was re-exported, "
                "processed through software, or captured via screenshot."
            ),
            "timestamp_s": None,
        })

    # Crop detection (populated by Phase 3 modules)
    crop = manipulation.get("crop_detection") or {}
    crop_score = _as_float(crop.get("crop_score"))
    if crop_score > 40:
        severity = "medium" if crop_score > 70 else "low"
        signals = crop.get("crop_signals") or []
        signal_names = ", ".join(s.get("check", "") for s in signals if s.get("check"))
        desc = (
            f"Crop detection measured a score of {crop_score:.0f}. "
            "This indicates the image may have been trimmed from a larger "
            "original."
        )
        if signal_names:
            desc += f" Signals: {signal_names}."
        findings.append({
            "title": "Signs of cropping detected",
            "module": "crop_detection",
            "severity": severity,
            "confidence": _clamp(crop.get("confidence", 0.5)),
            "description": desc,
            "timestamp_s": None,
        })

    # Screenshot detection (populated by Phase 3 modules)
    screenshot = manipulation.get("screenshot_detection") or {}
    screenshot_score = _as_float(screenshot.get("screenshot_score"))
    if screenshot_score > 40:
        severity = "medium" if screenshot_score > 70 else "low"
        signals = screenshot.get("screenshot_signals") or []
        signal_names = ", ".join(s.get("check", "") for s in signals if s.get("check"))
        desc = (
            f"Screenshot detection measured a score of {screenshot_score:.0f}. "
            "This indicates the file may have originated from a screen "
            "capture rather than a camera or direct export."
        )
        if signal_names:
            desc += f" Signals: {signal_names}."
        findings.append({
            "title": "Signs of screen capture detected",
            "module": "screenshot_detection",
            "severity": severity,
            "confidence": _clamp(screenshot.get("confidence", 0.5)),
            "description": desc,
            "timestamp_s": None,
        })

    return findings


# ── Spatial findings normalization ───────────────────────────────────────────
# Frame engine emits spatial_findings separately. Previously these were only
# counted for elevated_detectors but not included in the findings list.

def _normalize_spatial_findings(
    spatial_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize spatial (per-frame) findings from the frame analysis engine."""
    normalized: list[dict[str, Any]] = []
    for finding in spatial_findings or []:
        location = finding.get("location") or {}
        title = str(finding.get("type") or "SPATIAL_ANALYSIS").replace("_", " ").title()
        normalized.append({
            "title": title,
            "module": "spatial_analysis",
            "severity": str(finding.get("severity") or "low").lower(),
            "confidence": _clamp(
                finding.get("metrics", {}).get("magnitude_ratio", 0.0)
            ),
            "description": finding.get("explanation") or "",
            "timestamp_s": location.get("start"),
        })
    return normalized


# ── Elevated detectors ───────────────────────────────────────────────────────

# Canonical list of forensic checks the work-engine image pipeline performs.
# Used as the total_detectors denominator for image interpretation.
_WORK_IMAGE_DETECTORS = (
    "ela", "noise", "copy_move", "jpeg_compression", "exif_metadata",
    "crop_detection", "screenshot_detection",
)


def _work_image_total_detectors(raw_result: dict[str, Any]) -> int:
    return len(_WORK_IMAGE_DETECTORS)


def _work_image_elevated_detectors(raw_result: dict[str, Any]) -> list[str]:
    """Derive the list of elevated detectors from the work-engine image report.

    Display-only: risk_score is driven by the engine's own tampering_likelihood,
    so any inaccuracy here only affects the summary sentence, not the band.
    Thresholds mirror the engine's medium-band cutoffs loosely so the list
    matches intuition without importing engine constants.
    """
    report = raw_result.get("report") or {}
    manipulation = report.get("manipulation_detection") or {}
    elevated: list[str] = []

    ela_score = _as_float((manipulation.get("ela") or {}).get("ela_score"))
    if ela_score > 10.0:
        elevated.append("ela")

    noise_score = _as_float(
        (manipulation.get("noise") or {}).get("noise_inconsistency_score")
    )
    if noise_score > 5.0:
        elevated.append("noise")

    clone_score = _as_float(
        (manipulation.get("copy_move") or {}).get("clone_score")
    )
    if clone_score > 0.1:
        elevated.append("copy_move")

    jpeg = manipulation.get("jpeg_compression") or {}
    if jpeg.get("double_compression_likelihood") == "High":
        elevated.append("jpeg_compression")

    exif = (report.get("metadata") or {}).get("exif")
    if not exif or (isinstance(exif, dict) and len(exif) < 3):
        elevated.append("exif_metadata")

    # Crop detection
    crop = manipulation.get("crop_detection") or {}
    if _as_float(crop.get("crop_score")) > 40:
        elevated.append("crop_detection")

    # Screenshot detection
    screenshot = manipulation.get("screenshot_detection") or {}
    if _as_float(screenshot.get("screenshot_score")) > 40:
        elevated.append("screenshot_detection")

    return elevated


def _vff_elevated_detectors(raw_result: dict[str, Any]) -> list[str]:
    """VFF's runner emits module_scores but only a count of elevated modules,
    not the list. Approximate the list by flagging modules whose score is at
    or above 0.5. Display-only — risk_score still comes from fused_probability.
    """
    module_scores = raw_result.get("module_scores", {}) or {}
    return [name for name, score in module_scores.items() if float(score) >= 0.5]


# ── Generic findings normalization ───────────────────────────────────────────

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


# ── Helpers ──────────────────────────────────────────────────────────────────

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
