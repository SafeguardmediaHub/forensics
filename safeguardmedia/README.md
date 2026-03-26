# SafeguardMedia API

Unified media forensics platform. One API, four forensic engines.

| Endpoint | Engine | What it analyses |
|---|---|---|
| `POST /api/v1/analyze` | Orchestrator | Unified entrypoint for all media types |
| `GET /api/v1/jobs/{job_id}` | Orchestrator | Poll async job state |
| `GET /api/v1/jobs/{job_id}/result` | Orchestrator | Fetch completed async result |
| `POST /api/v1/video/analyze` | VFF | Video tamper detection |
| `POST /api/v1/audio/analyze` | AFF | Audio tamper detection |
| `POST /api/v1/image/analyze` | Visual Forensics | Image manipulation detection |
| `POST /api/v1/frames/analyze` | Frame Analysis | Frame-by-frame video analysis |

## Quickstart

```bash
# 1. Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install API + engine dependencies
make install

# 3. Copy and configure environment
cp .env.example .env

# 4. Start the API server
make run

# 5. In a separate terminal, start Redis
make redis

# 6. In another terminal, start the Celery worker
make worker

# 7. In another terminal, start Celery beat
make beat

# 8. Quick in-process smoke test
make smoke-app

# 9. Real HTTP smoke test against the running server
make smoke-http

# API docs available at:
# http://localhost:8000/docs
# http://localhost:8000/redoc
```

## Docker

Current container baseline:
- one shared image containing `safeguardmedia`, `AFF`, `VFF`, and `work`
- one API container
- one Celery worker container
- one Redis container
- one shared runtime volume for uploads and outputs

```bash
docker compose up --build
```

Services:
- API: `http://localhost:8000`
- Redis: `redis://localhost:6379`

Notes:
- this Docker baseline matches the current runner-based integration path
- `video` and `frames` are queued through Celery and should be polled via `/api/v1/jobs/{job_id}`
- `audio` and `image` currently return synchronously
- it does not yet split `engine-vff`, `engine-aff`, and `engine-work` into separate containers
- live container verification still needs to be done on a machine with Docker
- use [docs/DOCKER_BRINGUP_RUNBOOK.md](/home/finzyphinzy/Downloads/safeguardmedia-forensics/docs/DOCKER_BRINGUP_RUNBOOK.md) for the exact first bring-up/test sequence

## Requirements

- Python 3.11+
- Redis (for Celery task queue)
- ffmpeg (for video/audio processing)

## Development

```bash
make test       # run test suite
make smoke-app  # in-process smoke test (health + image + audio)
make smoke-http # real HTTP smoke test (health + image + audio + video + frames)
make lint       # ruff linting
make format     # black formatting
make check      # lint + format check (CI-safe)
```

## Project Structure

```
src/api/
├── main.py           # FastAPI app factory
├── config.py         # Settings (pydantic-settings, reads from .env)
├── logging_config.py # Logging setup
├── exceptions.py     # Custom exceptions and handlers
├── routers/          # One router per analysis type + health
├── schemas/          # Shared Pydantic response models
├── engines/          # Forensic engine code (copied, not reimplemented)
└── workers/          # Celery app and task definitions
```
