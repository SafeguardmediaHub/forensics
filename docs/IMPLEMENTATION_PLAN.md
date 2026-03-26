# SafeguardMedia — Unified Platform Implementation Plan

A living checklist. Update status as work progresses.
Status markers: [ ] not started | [x] done | [~] in progress | [-] skipped/deferred

---

## Understanding: What We Are Building

One unified Python API that wraps all four existing forensic engines:

| Engine | Location | What it does |
|---|---|---|
| VFF | `VFF/` | Video tamper detection (6 modules: noise/PRNU, temporal, compression, metadata, lighting, audio-sync) |
| AFF | `AFF/` | Audio tamper detection (6 modules: ENF, noise floor, compression, reverberation, voice, metadata) |
| Visual Forensics | `work/visual_forens.py` | Image manipulation detection (ELA, noise, clone detection, EXIF) |
| Frame Analysis | `work/frame_analysis.py` | Frame-by-frame video analysis (spatial + temporal findings) |

We are NOT reimplementing any engine logic. We copy the code in and wire it up
behind a clean, unified API. The engines stay structurally intact.

---

## Phase 0 — Project Setup

> Goal: a running FastAPI app with one working endpoint, proper project structure,
> and all tooling configured. Nothing forensics-related yet.

### 0.1 Project Scaffold
- [x] Decide project name and location — `safeguardmedia/` sibling to `VFF/`, `AFF/`, `work/`
- [x] Create root directory structure
- [x] `pyproject.toml` — setuptools backend, deps, black/ruff/pytest config
- [x] `.python-version` — pinned to 3.10 (system Python)
- [x] `.env.example` — all required environment variables documented
- [x] `.gitignore` — Python standard + project-specific ignores
- [x] `Makefile` — install, run, worker, test, lint, format, check, clean
- [x] `README.md` — project overview and quickstart

### 0.2 Dependency Management
- [x] Virtual environment created (`.venv/`)
- [x] Core deps installed: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `python-dotenv`
- [x] Async task deps installed: `celery[redis]`, `redis`
- [x] Dev deps installed: `pytest`, `pytest-asyncio`, `httpx`, `black`, `ruff`

### 0.3 Application Foundation
- [x] `src/api/main.py` — FastAPI app factory with lifespan, CORS, global exception handler
- [x] `src/api/config.py` — pydantic-settings `Settings` (reads `.env`, validates on startup)
- [x] `src/api/logging_config.py` — structured logging setup
- [x] `src/api/exceptions.py` — `SafeguardMediaException`, `UnsupportedFileTypeError`, `FileTooLargeError`, `EngineError` + handlers

### 0.4 Hello World Endpoint
- [x] `src/api/routers/health.py` — `GET /health` returns `{status, version, env, timestamp}`
- [x] Router registered in `main.py`
- [x] Manually verified: `curl http://localhost:8000/health` returns 200 ✓

### 0.5 Testing Infrastructure
- [x] `tests/conftest.py` — async test client fixture via `httpx.AsyncClient`
- [x] `tests/test_health.py` — 3 tests for `/health` endpoint
- [x] `pytest` runs clean: **3/3 passed** ✓

### 0.6 Celery Worker Foundation
- [x] `src/api/workers/celery_app.py` — Celery app configured against Redis
- [x] `src/api/workers/tasks.py` — placeholder with documented task patterns
- [x] `Makefile` target: `make worker`
- [-] Worker live-test deferred — requires Redis running (Phase 2)

---

## Phase 1 — Engine Integration

> Goal: each engine is importable from inside the unified project without
> breaking its internal structure. One endpoint per engine, returning raw
> engine output (no schema unification yet).

### 1.1 Engine Copy & Path Setup
- [x] Copy `VFF/` → `src/api/engines/vff/` (config, core, modules, database)
- [x] Copy `AFF/` → `src/api/engines/aff/` (core, modules)
- [x] Copy `work/` engines → `src/api/engines/work/` (frame_analysis, visual_forens, video_forensics, forensic_primitives, forensic_config, pdfgneration)
- [x] `src/api/engines/engine_registry.py` — sys.path isolation, loads all three engines at startup
- [x] VFF=OK, AFF=OK, Work=OK confirmed at server startup ✓

### 1.2 Video Forensics Endpoint (VFF)
- [x] `src/api/engines/vff_adapter.py` — wraps VFF IngestionPipeline + FusionModule
- [x] `src/api/routers/video.py`
- [x] `POST /api/v1/video/analyze` — accepts video file upload ✓
- [ ] Tested with a real video file

### 1.3 Audio Forensics Endpoint (AFF)
- [x] `src/api/engines/aff_adapter.py` — wraps AFF pipeline + all 6 modules + FusionEngine
- [x] `src/api/routers/audio.py`
- [x] `POST /api/v1/audio/analyze` — accepts audio file upload ✓
- [ ] Tested with a real audio file

### 1.4 Image Forensics Endpoint
- [x] `src/api/engines/image_adapter.py` — wraps visual_forens.generate_forensic_report()
- [x] `src/api/routers/image.py`
- [x] `POST /api/v1/image/analyze` — accepts image file upload ✓
- [ ] Tested with a real image

### 1.5 Frame Analysis Endpoint
- [x] `src/api/engines/frames_adapter.py` — wraps FrameAnalysisEngine
- [x] `src/api/routers/frames.py`
- [x] `POST /api/v1/frames/analyze` — accepts video file upload ✓
- [ ] Tested with a real video file

---

## Phase 2 — Async Job Infrastructure

> Goal: long-running analyses (video, frame analysis) run in background workers.
> Short analyses (image, audio) can remain synchronous for now.

- [ ] Celery task per engine: `analyze_video_task`, `analyze_audio_task`, `analyze_image_task`, `analyze_frames_task`
- [ ] Job status model: `pending | running | completed | failed`
- [ ] `GET /api/v1/jobs/{job_id}` — poll job status and result
- [ ] Redis-backed result storage with TTL
- [ ] Celery Beat: scheduled cleanup task for old uploads/results
- [ ] File upload temp directory lifecycle managed (created on ingest, cleaned after TTL)

---

## Phase 3 — Unified Response Schema

> Goal: all four endpoints return responses with the same top-level shape.
> Engine-specific detail is nested, not flattened.

- [ ] `src/api/schemas/responses.py` — define shared Pydantic models:
  - `ForensicVerdict` — `likely_authentic | inconclusive | likely_tampered | tampered`
  - `ForensicResult` — `verdict`, `confidence`, `probability`, `findings[]`, `engine_detail`
  - `Finding` — `title`, `module`, `severity`, `confidence`, `description`, `timestamp_s?`
  - `JobResponse` — `job_id`, `status`, `submitted_at`, `result?`
- [ ] VFF router maps VFF output → `ForensicResult`
- [ ] AFF router maps AFF output → `ForensicResult`
- [ ] Image router maps visual_forens output → `ForensicResult`
- [ ] Frames router maps frame_analysis output → `ForensicResult`
- [ ] All four endpoints return identical top-level structure

---

## Phase 4 — File Handling & Validation

- [ ] Centralised upload handler: file size limit, format validation per endpoint
- [ ] Secure filename handling (no path traversal)
- [ ] Temp file lifecycle: written on upload, cleaned on job completion or TTL
- [ ] Max file sizes configured in `.env`:
  - Video: 500 MB
  - Audio: 200 MB
  - Image: 50 MB

---

## Phase 5 — PDF Report Generation

- [ ] `POST /api/v1/video/report` — returns PDF from VFF report builder
- [ ] `POST /api/v1/audio/report` — returns PDF (AFF Phase 9 when built)
- [ ] `POST /api/v1/image/report` — returns PDF from pdfgneration.py
- [ ] `GET /api/v1/reports/{report_id}` — download previously generated report

---

## Phase 6 — Frontend Integration (fvaui)

- [ ] Point `fvaui/.streamlit/secrets.toml` `API_URL` at unified server
- [ ] Verify all existing Streamlit UI flows work against new endpoints
- [ ] Update Streamlit UI to support all four analysis types if needed

---

## Phase 7 — Hardening

- [ ] Rate limiting per endpoint (reuse pattern from `work/app.py`)
- [ ] Request ID propagation (header → logs → response)
- [ ] Structured JSON logging in production mode
- [ ] Health check includes Redis + Celery worker connectivity
- [ ] Environment validation at startup (missing required vars = hard fail, not silent)
- [ ] CORS configured properly (not `*` in production)

---

## Phase 8 — Testing

- [ ] Unit tests for each engine adapter (mock engine, test schema mapping)
- [ ] Integration tests for each endpoint (real engine, real files)
- [ ] Celery task tests
- [ ] Schema validation tests
- [ ] CI-ready: `make test` runs full suite

---

## Proposed Project Layout

```
safeguardmedia/                    <- new project root
├── src/
│   └── api/
│       ├── __init__.py
│       ├── main.py                <- FastAPI app factory, lifespan, middleware
│       ├── config.py              <- pydantic-settings Settings class
│       ├── logging_config.py
│       ├── exceptions.py
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── health.py          <- GET /health
│       │   ├── video.py           <- POST /api/v1/video/analyze
│       │   ├── audio.py           <- POST /api/v1/audio/analyze
│       │   ├── image.py           <- POST /api/v1/image/analyze
│       │   └── frames.py          <- POST /api/v1/frames/analyze
│       ├── schemas/
│       │   ├── __init__.py
│       │   └── responses.py       <- ForensicResult, Finding, JobResponse, etc.
│       ├── engines/               <- copied engine code, not reimplemented
│       │   ├── vff/               <- copy of VFF/
│       │   ├── aff/               <- copy of AFF/
│       │   ├── forensic_primitives.py
│       │   ├── forensic_config.py
│       │   ├── frame_analysis.py
│       │   ├── visual_forens.py
│       │   ├── video_forensics.py
│       │   └── audio_forensics/
│       └── workers/
│           ├── __init__.py
│           ├── celery_app.py
│           └── tasks.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_health.py
│   └── engines/                   <- one smoke test per engine
├── docs/
│   └── IMPLEMENTATION_PLAN.md     <- this file
├── pyproject.toml
├── .env.example
├── .gitignore
├── Makefile
└── README.md
```

---

## Key Technical Decisions (Agreed)

| Decision | Choice | Reason |
|---|---|---|
| API framework | FastAPI | Async, automatic OpenAPI docs, industry standard |
| Config management | pydantic-settings | Type-safe, reads from env, validates at startup |
| Async tasks | Celery + Redis | Already used in `work/`, proven for this workload |
| Engine integration | Copy, not symlink | Keeps the new project self-contained and deployable |
| Linting | ruff | Fast, replaces flake8 + isort |
| Formatting | black | Non-negotiable, consistent |
| Testing | pytest + httpx | Standard for FastAPI projects |
| Python version | 3.11 | Stable, supported by all engine deps |

---

## Environment Variables (Minimum Required)

```
# Server
PORT=8000
ENV=development

# Redis / Celery
REDIS_URL=redis://localhost:6379

# File storage
UPLOAD_DIR=/tmp/safeguardmedia/uploads
OUTPUT_DIR=/tmp/safeguardmedia/outputs

# Cleanup
CLEANUP_MAX_AGE_HOURS=24
CLEANUP_INTERVAL_SECONDS=3600

# Engine thresholds (inherited from forensic_config.py — all optional, have defaults)
# FA_TAMPERED_CONFIDENCE, VF_TAMPERED_THRESHOLD, VFO_TAMPERED_CONFIDENCE, etc.
```

---

_Last updated: Phase 1 wiring complete. All 5 endpoints live. Pending: real-file tests per endpoint._
