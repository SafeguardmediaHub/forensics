# Media Forensics Platform

A **multi-modal media forensics platform** that analyses video, image, and audio files for signs of digital manipulation — cuts, splices, inserted content, duplicated frames, deepfakes, AI-generated imagery, metadata tampering, and more.

All analysis is pure mathematics: signal processing, statistical comparisons, and pattern detection applied to the raw bytes of a file. There is no "watching" or "listening" in any human sense.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Modules](#modules)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Verdict Interpretation](#verdict-interpretation)
- [Architecture](#architecture)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

---

## How It Works

```
User uploads file
       ↓
  Flask (app.py) — validates the file, creates a job
       ↓
  Celery task (tasks.py) — picks up the job asynchronously
       ↓
  Analysis engines run (frame_analysis.py, visual_forens.py,
                        video_forensics.py, audio_forensics/)
       ↓
  Verdict computed — confidence score + findings list
       ↓
  Results stored, returned via job-status endpoint
```

**Redis** acts as the message broker between Flask and Celery — Flask enqueues the job, Celery workers pull from it. Analysis runs without blocking the web server.

| Input type | Modules that run                                                                         |
| ---------- | ---------------------------------------------------------------------------------------- |
| Video      | Frame Analysis + Image Forensics (on sampled frames) + Video Forensics + Audio Forensics |
| Image      | Image / Visual Forensics                                                                 |
| Audio      | Audio Forensics                                                                          |

---

## Modules

### Module 1 — Video Frame Analysis (`frame_analysis.py`)

Treats a video as a sequence of images over time and looks for anything that breaks natural temporal flow. It runs a 12-step pipeline:

| Detection                  | What it catches                                                |
| -------------------------- | -------------------------------------------------------------- |
| SSIM + histogram distance  | Abrupt cuts and splice points                                  |
| Adaptive scene thresholds  | Abnormal transitions calibrated to each video's own statistics |
| GOP / I-frame distribution | Re-encoded segments (uneven keyframe spacing)                  |
| Optical flow vectors       | Composited regions with inconsistent motion                    |
| Perceptual hash clustering | Freeze frames and duplicated/looped sections                   |
| Audio-video sync           | Deleted content from one track only; stream duration mismatch  |

**Finding types:** `ABRUPT_TRANSITION`, `FREEZE_OR_DUPLICATION`, `QUALITY_DRIFT`, `TIMING_IRREGULARITY`, `MOTION_ANOMALY`, `IRREGULAR_GOP`, `MOTION_DISCONTINUITY`, `AV_SYNC_ANOMALY`

---

### Module 2 — Image / Visual Forensics (`visual_forens.py`, `forensic_primitives.py`)

Analyses the **internal consistency** of a single image — whether different regions show signs of having come from different sources or having been processed differently.

| Technique                           | What it catches                                                                |
| ----------------------------------- | ------------------------------------------------------------------------------ |
| Error Level Analysis (ELA)          | Regions with different JPEG compression history (copy-paste, Photoshop edits)  |
| Noise Analysis (PRNU)               | Regions with different camera sensor noise patterns                            |
| Copy-Move Detection (SIFT + DCT)    | Cloned/stamp-tool regions within the same image                                |
| JPEG Compression Analysis           | Double-compression artifacts from re-saving after editing                      |
| AI Generation Detection (heuristic) | Frequency domain, texture, and edge density signatures of GAN-generated images |
| EXIF / Metadata Analysis            | Missing, stripped, or self-contradictory EXIF metadata                         |

---

### Module 3 — Video Forensics (`video_forensics.py`)

Goes beneath frame content to examine the **encoded file structure**: codec layer, metadata chain, motion mathematics, and physics-based electrical frequency signals.

| Sub-detector                       | What it catches                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| Benford's Law (DCT coefficients)   | Double-encoding — video decoded and re-encoded after editing                         |
| Bitrate segment profiling          | Bitrate spikes at splice boundaries                                                  |
| Encoder tag consistency            | Mismatched encoder metadata (software that altered the file)                         |
| Metadata chain audit               | `creation_time` / `encoded_date` / file `mtime` discrepancies                        |
| `moov` atom position               | Edited/re-muxed MP4 containers                                                       |
| Deepfake detection (ResNet-SSD)    | Face sharpness/background inconsistency; GAN spectral peaks                          |
| Motion vector forensics            | Composited foreground objects moving independently of camera                         |
| ENF (Electrical Network Frequency) | Phase jumps in mains-frequency signal indicating splice points (high-fps video only) |

---

### Module 4 — Audio Forensics (`audio_forensics/`)

Analyses speech and voice recordings for five categories of manipulation.

| Sub-module                 | What it catches                                                  |
| -------------------------- | ---------------------------------------------------------------- |
| Integrity check            | Clipping, DC offset, anomalous silence gaps (adaptive threshold) |
| Speaker consistency (MFCC) | Multiple voices/sources stitched together                        |
| ENF analysis               | Phase discontinuities in 50/60 Hz mains signal                   |
| Splice / event detection   | Abrupt spectral flux spikes at cut points                        |
| Duplicate detection        | Repeated audio segments that shouldn't repeat                    |

Audio type is classified first (`speech`, `music`, `phone_speech`) to adjust sensitivity and skip modules that would produce false positives (e.g., speaker-change detection is skipped for music).

---

## Project Structure

```
.
├── app.py                    # Flask API — all endpoints
├── tasks.py                  # Celery tasks — all async jobs
├── frame_analysis.py         # Module 1: Video frame analysis engine
├── visual_forens.py          # Module 2: Image forensics engine
├── forensic_primitives.py    # Module 2: ELA, noise, copy-move primitives
├── video_forensics.py        # Module 3: Bitstream / deepfake / ENF engine
├── forensic_config.py        # Shared configuration constants
├── audio_forensics/          # Module 4: Audio forensics package
├── pdfgneration.py           # PDF report generation
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

---

## Requirements

### System dependencies

```bash
# Ubuntu / Debian
sudo apt-get install ffmpeg redis-server

# macOS
brew install ffmpeg redis
```

Optional (for video forensics container metadata):

```bash
sudo apt-get install mediainfo   # enables pymediainfo
```

Optional (for deepfake detection): download the OpenCV ResNet-SSD face model files into `./models/`:

- `deploy.prototxt`
- `res10_300x300_ssd_iter_140000.caffemodel`

### Python dependencies

```bash
pip install -r requirements.txt
```

**Python 3.8+** required.

Key libraries: Flask, Celery, Redis, OpenCV, scikit-image, librosa, PySceneDetect, ffmpeg-python, imagehash, resemblyzer, PyAV, reportlab.

---

## Quick Start

### 1. Start Redis

```bash
redis-server
```

### 2. Start the Celery worker

```bash
celery -A tasks worker --loglevel=info
```

### 3. Start the Flask API

```bash
python app.py
# or
PORT=5000 python app.py
```

The API starts on `http://0.0.0.0:5000` by default.

---

## API Reference

### Health check

```
GET /api/health
GET /health
```

---

### Frame Analysis

**Submit a video for temporal frame analysis**

```
POST /api/analyze/video
Content-Type: multipart/form-data
```

| Parameter       | Type   | Default    | Description                                     |
| --------------- | ------ | ---------- | ----------------------------------------------- |
| `video`         | file   | required   | Video file (mp4, avi, mov, mkv, webm)           |
| `mode`          | string | `standard` | `standard` \| `high_sensitivity` \| `deep_scan` |
| `sampling_mode` | string | `sampled`  | `sampled` (~2 fps) \| `full` (every frame)      |

Response `202`:

```json
{ "job_id": "uuid", "status": "queued", "type": "video_analysis" }
```

---

### Image / Visual Forensics

**Submit an image for manipulation detection**

```
POST /api/forensics/analyze
POST /api/analyze              (alias)
Content-Type: multipart/form-data
```

| Parameter | Type | Default  | Description                                  |
| --------- | ---- | -------- | -------------------------------------------- |
| `file`    | file | required | Image file (jpg, jpeg, png, bmp, tiff, webp) |
| `async`   | bool | `false`  | Run asynchronously via Celery                |
| `pdf`     | bool | `true`   | Generate a PDF report                        |

Sync response `200`:

```json
{
  "status": "success",
  "verdict": "Likely Tampered",
  "tampering_likelihood": 75,
  "confidence": "High",
  "report": { ... },
  "output_directory": "/tmp/frame_analysis/<id>"
}
```

Async response `202`:

```json
{ "status": "processing", "task_id": "uuid", "type": "forensic_analysis" }
```

---

### Video Forensics (Bitstream / Deepfake / ENF)

**Submit a video for codec-level and structural forensics**

```
POST /api/forensics/video/analyze
Content-Type: multipart/form-data
```

| Parameter | Type   | Default    | Description                                     |
| --------- | ------ | ---------- | ----------------------------------------------- |
| `file`    | file   | required   | Video file                                      |
| `mode`    | string | `standard` | `standard` \| `high_sensitivity` \| `deep_scan` |
| `async`   | bool   | `false`    | Run asynchronously via Celery                   |

**Check status of async job:**

```
GET /api/forensics/video/status/<task_id>
```

---

### Audio Forensics

**Submit an audio file for speech/recording analysis**

```
POST /api/forensics/audio/analyze
Content-Type: multipart/form-data
```

| Parameter  | Type   | Default        | Description                                        |
| ---------- | ------ | -------------- | -------------------------------------------------- |
| `file`     | file   | required       | Audio file (wav, mp3, flac, ogg, m4a) — max 15 min |
| `case_id`  | string | auto           | Case identifier                                    |
| `examiner` | string | `API Examiner` | Examiner name embedded in report                   |
| `async`    | bool   | `false`        | Run asynchronously                                 |

Sync response `200`:

```json
{
  "status": "success",
  "case_id": "CASE_...",
  "evidence_id": "AUDIO_...",
  "confidence_score": 0.91,
  "results": { ... }
}
```

**Check status of async job:**

```
GET /api/forensics/audio/status/<task_id>
```

---

### Job Status (universal)

```
GET /api/job/<job_id>
GET /api/status/<job_id>   (alias)
```

Response states: `pending` → `processing` → `completed` | `failed`

```json
{ "status": "processing", "stage": "Running audio forensics", "progress": 60 }
```

---

### Results & Downloads

| Endpoint                       | Description                                          |
| ------------------------------ | ---------------------------------------------------- |
| `GET /api/result/<job_id>`     | Full JSON result with SHA-256 integrity verification |
| `GET /api/export/pdf/<job_id>` | Download PDF forensic report                         |
| `GET /api/download/<path>`     | Download generated artefact (heatmaps, etc.)         |
| `POST /api/admin/cleanup`      | Trigger deletion of uploads older than TTL           |

---

## Configuration

Set via environment variables:

| Variable                      | Default                  | Description                                       |
| ----------------------------- | ------------------------ | ------------------------------------------------- |
| `PORT`                        | `5000`                   | Flask listen port                                 |
| `REDIS_URL`                   | `redis://localhost:6379` | Redis broker and result backend                   |
| `UPLOAD_FOLDER`               | `/tmp/uploads`           | Temporary upload directory                        |
| `OUTPUT_FOLDER`               | `/tmp/frame_analysis`    | Analysis output directory                         |
| `ALLOWED_ORIGINS`             | `*`                      | CORS allowed origins                              |
| `CLEANUP_MAX_AGE_HOURS`       | `24`                     | Age threshold for cleanup endpoint                |
| `MAX_DUPLICATE_GAP_FRAMES`    | `90`                     | Window for perceptual hash duplicate search       |
| `IMAGEHASH_HAMMING_THRESHOLD` | `5`                      | Max Hamming distance to consider frames duplicate |

---

## Verdict Interpretation

### Frame / Video analysis

| Verdict              | Meaning                                                                     |
| -------------------- | --------------------------------------------------------------------------- |
| `LIKELY_AUTHENTIC`   | No significant anomalies surfaced                                           |
| `REVIEW_RECOMMENDED` | One or more moderate indicators — inspect evidence images before concluding |
| `LIKELY_TAMPERED`    | Multiple high-severity findings across independent detector groups          |

Confidence is computed by blending capped temporal scores (40%) and spatial scores (60%).

### Image forensics scoring

| Score   | Verdict           |
| ------- | ----------------- |
| 0 – 29  | Likely Authentic  |
| 30 – 59 | Possibly Tampered |
| 60+     | Likely Tampered   |

### Video forensics scoring

| Score    | Verdict            |
| -------- | ------------------ |
| 0 – 34%  | LIKELY_AUTHENTIC   |
| 35 – 74% | REVIEW_RECOMMENDED |
| 75%+     | LIKELY_TAMPERED    |

Each finding contributes: HIGH = 25 pts, MEDIUM = 12 pts, LOW = 4 pts (capped at 100%).

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Flask API  (app.py)                  │
│  POST /api/analyze/video          POST /api/forensics/*  │
│  POST /api/forensics/video/analyze  POST /api/forensics/ │
│  POST /api/forensics/audio/analyze                       │
└────────────────────────┬─────────────────────────────────┘
                         │  Redis queue
                         ▼
┌──────────────────────────────────────────────────────────┐
│                  Celery Workers  (tasks.py)               │
│  analyze_video_task   analyze_video_forensics_task        │
│  async_forensic_analysis   analyze_audio_task             │
└────┬──────────────┬──────────────┬────────────────┬───────┘
     ▼              ▼              ▼                ▼
frame_analysis  visual_forens  video_forensics  audio_forensics/
(temporal)      (image pixels) (codec/deepfake) (speech/splice)
```

Results are written to `OUTPUT_FOLDER/<job_id>/result.json` alongside a SHA-256 sidecar file for chain-of-custody verification.

---

## Security

- File upload size limited to **500 MB**
- File extension and magic-byte validation
- Path traversal protection on all download endpoints
- CORS configurable via `ALLOWED_ORIGINS`
- Rate limiting: **100 requests/hour** per IP on analysis endpoints (Redis-backed, shared across workers)
- Audio files limited to **15 minutes** duration

---

## Troubleshooting

**Tasks stuck in `pending`**

- Verify Redis is running: `redis-cli ping` → should return `PONG`
- Verify a Celery worker is running: check your worker terminal for errors
- Check `REDIS_URL` matches the address Redis is actually listening on

**Import errors on startup**

- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Check Python version: `python --version` (3.8+ required)

**FFmpeg / ffprobe errors**

- Install FFmpeg: `apt-get install ffmpeg` or `brew install ffmpeg`
- Ensure `ffprobe` is on your `$PATH`: `ffprobe -version`

**Deepfake detector skipped**

- Download the OpenCV ResNet-SSD model files into `./models/` (see [Requirements](#requirements))

**PDF not generated**

- A warning is logged but analysis still completes — check `reportlab` is installed: `pip install reportlab`

**ENF analysis skipped on video**

- ENF requires high-fps video (>100 fps). Standard 24–30 fps recordings cannot resolve the 50/60 Hz mains signal (Nyquist limit). This is expected behaviour.

---

## License

[Your License Here]
