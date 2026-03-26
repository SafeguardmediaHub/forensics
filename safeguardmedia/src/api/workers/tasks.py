from __future__ import annotations

from pathlib import Path
from typing import Any

from api.services.analysis_service import analyze_saved_file
from api.services.file_service import cleanup_expired_runtime_files, cleanup_runtime_paths
from api.services.job_service import _utcnow
from api.services.job_store import get_job_store
from api.workers.celery_app import celery_app


@celery_app.task(bind=True, name="analyze_media")
def analyze_media_task(
    self,
    media_type: str,
    file_path: str,
    original_filename: str,
    output_dir: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = get_job_store()
    job_id = self.request.id
    payload = store.get(job_id) or {}
    payload.update(
        {
            "job_id": job_id,
            "status": "running",
            "media_type": media_type,
            "started_at": _utcnow(),
            "error": None,
        }
    )
    store.save(job_id, payload)

    try:
        result = analyze_saved_file(
            media_type=media_type,
            file_path=Path(file_path),
            filename=original_filename,
            output_dir=Path(output_dir) if output_dir else None,
            options=options or {},
            cleanup=True,
        )
    except Exception as exc:
        payload["status"] = "failed"
        payload["completed_at"] = _utcnow()
        payload["error"] = {
            "code": "ENGINE_ERROR",
            "message": str(exc),
        }
        store.save(job_id, payload)
        cleanup_runtime_paths(
            upload_path=Path(file_path),
            output_dir=Path(output_dir) if output_dir else None,
            remove_output_dir=False,
        )
        raise

    payload["status"] = "completed"
    payload["completed_at"] = _utcnow()
    payload["result"] = result
    payload["error"] = None
    store.save(job_id, payload)
    return result


@celery_app.task(name="cleanup_runtime_files")
def cleanup_runtime_files_task() -> dict[str, int]:
    return cleanup_expired_runtime_files()
