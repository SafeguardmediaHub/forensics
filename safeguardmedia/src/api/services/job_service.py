from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, UploadFile

from api.services import analysis_service
from api.services.job_store import get_job_store
from api.workers.celery_app import celery_app

ASYNC_MEDIA_TYPES = {"video", "frames"}


async def submit_analysis(
    media_type: str,
    file: UploadFile,
    options: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    options = options or {}
    if media_type in ASYNC_MEDIA_TYPES:
        job = await enqueue_analysis(media_type=media_type, file=file, options=options)
        return job, 202

    result = await analysis_service.analyze_upload(media_type=media_type, file=file, options=options)
    return result, 200


async def enqueue_analysis(
    media_type: str,
    file: UploadFile,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = options or {}
    saved = await analysis_service.save_upload(media_type=media_type, file=file)

    job_id = uuid.uuid4().hex
    submitted_at = _utcnow()
    payload = {
        "job_id": job_id,
        "status": "pending",
        "media_type": media_type,
        "submitted_at": submitted_at,
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error": None,
    }
    get_job_store().save(job_id, payload)

    celery_app.send_task(
        "analyze_media",
        kwargs={
            "media_type": media_type,
            "file_path": str(saved["upload_path"]),
            "original_filename": saved["filename"],
            "output_dir": str(saved["output_dir"]) if saved["output_dir"] else None,
            "options": options,
        },
        task_id=job_id,
    )
    return payload


def get_job(job_id: str) -> dict[str, Any]:
    payload = get_job_store().get(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    async_result = celery_app.AsyncResult(job_id)
    state = async_result.state
    if state in {"STARTED", "RECEIVED", "RETRY", "PROGRESS"} and payload["status"] == "pending":
        payload["status"] = "running"
    elif state == "FAILURE" and payload["status"] not in {"failed", "completed"}:
        payload["status"] = "failed"
        payload["error"] = {
            "code": "ENGINE_ERROR",
            "message": str(async_result.result),
        }
    elif state == "SUCCESS" and payload["status"] != "completed":
        payload["status"] = "completed"
        if isinstance(async_result.result, dict):
            payload["result"] = async_result.result

    return payload


def get_job_result(job_id: str) -> dict[str, Any]:
    payload = get_job(job_id)
    if payload["status"] == "failed":
        raise HTTPException(status_code=409, detail=payload["error"])
    if payload["status"] != "completed" or not payload["result"]:
        raise HTTPException(status_code=409, detail=f"Job '{job_id}' is not completed")
    return payload["result"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
