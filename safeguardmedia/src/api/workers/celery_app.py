from celery import Celery

from api.config import settings

celery_app = Celery(
    "safeguardmedia",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["api.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=86400,  # 24 hours
    worker_prefetch_multiplier=1,  # one task at a time per worker (forensic tasks are heavy)
    beat_schedule={
        "cleanup-runtime-files": {
            "task": "cleanup_runtime_files",
            "schedule": settings.cleanup_interval_seconds,
        },
    },
)
