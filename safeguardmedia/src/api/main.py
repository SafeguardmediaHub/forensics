import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import settings
from api.exceptions import SafeguardMediaException, safeguard_exception_handler
from api.logging_config import setup_logging
from api.routers import analysis, audio, frames, health, image, jobs, video
from api.services.file_service import ensure_runtime_dirs

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    setup_logging()

    # Ensure runtime directories exist
    ensure_runtime_dirs()

    logger.info("SafeguardMedia API starting up")
    logger.info(f"Environment : {settings.env}")
    logger.info(f"Upload dir  : {settings.upload_dir}")
    logger.info(f"Redis URL   : {settings.redis_url}")

    logger.info("Runner-based engine orchestration enabled")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("SafeguardMedia API shutting down")


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="SafeguardMedia API",
    description=(
        "Unified media forensics platform. "
        "Analyse video, audio, images, and video frames for tampering."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Exception handlers ────────────────────────────────────────────────────────

app.add_exception_handler(SafeguardMediaException, safeguard_exception_handler)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "INTERNAL_ERROR"},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router)

app.include_router(analysis.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(video.router,  prefix="/api/v1")
app.include_router(audio.router,  prefix="/api/v1")
app.include_router(image.router,  prefix="/api/v1")
app.include_router(frames.router, prefix="/api/v1")
