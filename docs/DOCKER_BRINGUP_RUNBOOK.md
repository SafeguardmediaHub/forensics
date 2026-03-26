# Docker Bring-Up Runbook

Exact first-pass sequence for bringing up the current `safeguardmedia` stack
and testing real requests end-to-end.

This runbook assumes:
- you are in the repo root
- Docker and Docker Compose are installed
- port `8000` and `6379` are free

Current stack shape:
- `safeguardmedia-api`
- `safeguardmedia-worker`
- `safeguardmedia-beat`
- `redis`

The current container baseline uses one shared image containing:
- `safeguardmedia/`
- `AFF/`
- `VFF/`
- `work/`

---

## 1. Build And Start

```bash
docker compose up --build
```

If you want detached mode:

```bash
docker compose up --build -d
```

Expected:
- Redis starts cleanly
- API starts on `0.0.0.0:8000`
- worker connects to Redis
- beat starts without import errors

If anything fails here, check:

```bash
docker compose logs safeguardmedia-api
docker compose logs safeguardmedia-worker
docker compose logs safeguardmedia-beat
docker compose logs redis
```

---

## 2. Smoke Check

```bash
curl http://localhost:8000/health
```

Expected:
- HTTP `200`
- JSON contains:
  - `status: "ok"`
  - `version`
  - `timestamp`
  - `env`

---

## 3. Sync Image Test

Use the approved local image sample:

`safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/ca16e742-84d7-434b-8dc0-7231a11b8e1e/frames/frame_000000_00000000.png`

Run:

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "media_type=image" \
  -F "file=@safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/ca16e742-84d7-434b-8dc0-7231a11b8e1e/frames/frame_000000_00000000.png"
```

Expected:
- HTTP `200`
- response fields include:
  - `"media_type": "image"`
  - `"engine": "work"`
  - `"verdict": "likely_authentic"`
  - probability around `0.17`

---

## 4. Sync Audio Test

Use the approved local audio sample:

`AFF/AUD-20230414-WA0012.m4a`

Run:

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "media_type=audio" \
  -F "file=@AFF/AUD-20230414-WA0012.m4a"
```

Expected:
- HTTP `200`
- response fields include:
  - `"media_type": "audio"`
  - `"engine": "aff"`
  - `"verdict": "likely_authentic"`

---

## 5. Async Video Test

Use the approved local video sample:

`safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4`

Submit:

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "media_type=video" \
  -F "file=@safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4"
```

Expected initial response:
- HTTP `202`
- JSON includes:
  - `job_id`
  - `"status": "pending"`
  - `"media_type": "video"`

Save that `job_id`, then poll:

```bash
curl http://localhost:8000/api/v1/jobs/<job_id>
```

Poll until:
- `"status": "completed"`
or
- `"status": "failed"`

When completed:

```bash
curl http://localhost:8000/api/v1/jobs/<job_id>/result
```

Expected completed result:
- `"engine": "vff"`
- `"media_type": "video"`
- `"verdict": "likely_authentic"`

---

## 6. Async Frames Test

Use the same approved local video sample:

`safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4`

Submit:

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "media_type=frames" \
  -F "file=@safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4"
```

Expected initial response:
- HTTP `202`
- JSON includes:
  - `job_id`
  - `"status": "pending"`
  - `"media_type": "frames"`

Poll:

```bash
curl http://localhost:8000/api/v1/jobs/<job_id>
```

When completed:

```bash
curl http://localhost:8000/api/v1/jobs/<job_id>/result
```

Expected:
- `"engine": "work"`
- `"media_type": "frames"`
- response includes `summary`, `findings`, and `engine_detail`

Note:
- frame-analysis parity is still the least stable path
- if it fails, treat that as a container/runtime debugging task, not as proof that the API wiring is wrong

---

## 7. Failure Checks

Test unsupported media type:

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "media_type=document" \
  -F "file=@README.md"
```

Expected:
- HTTP `422`

Test unsupported file extension on image:

```bash
cp README.md /tmp/not_an_image.txt
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "media_type=image" \
  -F "file=@/tmp/not_an_image.txt"
```

Expected:
- HTTP `422`

---

## 8. What To Check If Something Breaks

If API request fails immediately:
- inspect `docker compose logs safeguardmedia-api`
- look for import errors, missing binaries, or invalid JSON from runner scripts

If async jobs stay `pending`:
- inspect `docker compose logs safeguardmedia-worker`
- confirm Redis connectivity
- confirm the worker imported `api.workers.tasks`

If stale files accumulate:
- inspect `docker compose logs safeguardmedia-beat`
- confirm `cleanup_runtime_files` is being scheduled

If video/image processing fails:
- check missing native packages first:
  - `ffmpeg`
  - OpenCV runtime libs
  - `libsndfile`

---

## 9. Shut Down

Stop containers:

```bash
docker compose down
```

Stop containers and remove the runtime volume:

```bash
docker compose down -v
```

Use `-v` only if you want to discard uploads/outputs and Redis state.
