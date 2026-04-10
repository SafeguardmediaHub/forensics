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

## Response shape

All four analysis endpoints return a unified shape. Forensics is
probabilistic — the API deliberately does not emit categorical verdicts.
Instead it reports signal strength, what the detectors measured, and
recommended next steps.

Key fields:

| Field | Meaning |
|---|---|
| `risk_score` | 0–100. Signal strength from the detectors. Not a claim about the file. |
| `risk_band` | `low` (0–39) / `elevated` (40–74) / `high` (75–100). |
| `measurement_confidence` | 0.0–1.0. How trustworthy the measurement itself is, separate from `risk_score`. |
| `calibration_status` | `pre_calibration` / `calibrated` / `recalibrating`. Currently `pre_calibration` — thresholds are provisional. |
| `elevated_detectors` | Names of detectors whose score crossed their elevated threshold. |
| `checks_unavailable` | Engine-internal checks that were expected to run but couldn't (e.g. `exif_metadata` missing, `enf` detector skipped). Scoped to this project's own engines only. |
| `interpretation.summary` | One-sentence observational description of what the detectors found. |
| `interpretation.what_this_means` | Plain-language framing. Describes the measurement, not the file. |
| `interpretation.next_steps` | List of recommended follow-up actions. Each entry has `action`, `label`, `type` (`manual` or `platform_feature`), and optionally `feature` (a pointer to a capability in the wider platform such as `content_verification.reverse_lookup` or `ai_detection.image`). |

**Deprecated fields** (always `null`, scheduled for removal next release):
`verdict`, `verdict_label`, `probability`, `tampering_likelihood`. Do not
read these in new code — use `risk_score` / `risk_band` /
`measurement_confidence` instead.

### Next-steps catalog

`interpretation.next_steps` is a static per-media-type list for v1. It
always includes the universal manual steps (`verify_source`,
`request_original`, `compare_versions`, `caution_before_share`) plus
media-specific `platform_feature` suggestions:

- **image**: `check_c2pa`, `check_metadata_authenticity`, `reverse_image_lookup`, `ai_detection_image`, `ocr_extract`
- **video** / **frames**: `check_c2pa`, `check_metadata_authenticity`, `keyframe_reverse_lookup`, `ai_detection_video`
- **audio**: `check_c2pa`, `check_metadata_authenticity`, `ai_detection_audio`

The `feature` strings are stable IDs. This project never calls those
features itself and never claims to know their result — the backend
maps each `feature` string to its own endpoints when rendering.

Risk-band cutoffs live in
`src/api/interpretation/bands.py`. Copy lives in
`src/api/interpretation/copy.py` and is guarded by a banned-words test
that blocks verdict language (`manipulated`, `authentic`, `tampered`,
etc.) from ever appearing in generated summaries.

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
