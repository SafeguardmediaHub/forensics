from datetime import datetime, timezone

from fastapi import APIRouter

from api.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0",
        "env": settings.env,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
