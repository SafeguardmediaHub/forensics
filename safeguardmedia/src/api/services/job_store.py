from __future__ import annotations

import json
from typing import Any

import redis

from api.config import settings

JOB_PREFIX = "sgm:job:"
DEFAULT_TTL_SECONDS = 24 * 60 * 60


class JobStore:
    def __init__(self) -> None:
        self._client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    def save(self, job_id: str, payload: dict[str, Any], ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._client.setex(f"{JOB_PREFIX}{job_id}", ttl_seconds, json.dumps(payload))

    def get(self, job_id: str) -> dict[str, Any] | None:
        raw = self._client.get(f"{JOB_PREFIX}{job_id}")
        if not raw:
            return None
        return json.loads(raw)


_job_store: JobStore | None = None


def get_job_store() -> JobStore:
    global _job_store
    if _job_store is None:
        _job_store = JobStore()
    return _job_store
