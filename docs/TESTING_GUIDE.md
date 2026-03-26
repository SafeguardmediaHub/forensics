# SafeguardMedia — Manual Testing Guide

A checklist-based guide for testing each of the four forensic endpoints.
Work through each section systematically. Tick off as you go.

Status markers: [ ] not tested | [x] passed | [!] failed | [-] skipped/not applicable

---

## How to Run the Server for Testing

```bash
cd safeguardmedia
source .venv/bin/activate
make run
```

Base URL: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

---

## 1. POST /api/v1/video/analyze (VFF Engine)

### 1.1 Happy Path
- [x]Upload a standard `.mp4` file — expect 200 with verdict, module_scores, findings
- [x]Upload a `.mov` file — expect 200
- [x]Upload a `.avi` file — expect 200
- [x]Upload a `.mkv` file — expect 200
- [x]Verify response contains all expected top-level keys:
  `verdict`, `verdict_label`, `fused_probability`, `confidence`, `n_elevated_modules`, `module_scores`, `findings`, `video`, `filename`
- [x]Verify `module_scores` contains all 6 keys: `metadata`, `compression`, `noise`, `temporal`, `lighting`, `audio`
- [x]Verify `fused_probability` is a float between 0.0 and 1.0
- [x]Verify `verdict` is one of: `likely_authentic`, `inconclusive`, `likely_tampered`, `tampered`
- [x]Verify `video.sha256` is a 64-character hex string
- [x]Verify `video.duration_s` is a positive float

### 1.2 Edge Cases
- [x]Upload a very short video (< 2 seconds) — should return a result, not crash
- [x]Upload a video with no audio track — `video.has_audio` should be `false`, audio module should still run gracefully
- [x]Upload a video with only audio, no visual content (e.g. black frames) — should return a result
- [x]Upload a large video (100MB+) — should complete without timeout error
- [x]Upload a WhatsApp-forwarded video (re-encoded, lower quality) — check compression score is elevated
- [x]Upload the same video twice — verify `sha256` is identical both times (deterministic hashing)
- [x]Upload an AI-generated video — verify `fused_probability` is elevated

### 1.3 Negative Tests
- [x]Upload an audio file (`.mp3`) to the video endpoint — expect `422 Unsupported format`
- [x]Upload an image file (`.jpg`) to the video endpoint — expect `422 Unsupported format`
- [x]Upload a file with no extension — expect `422 Unsupported format`
- [x]Upload an empty file (0 bytes) — expect `500` or a descriptive error, not a crash
- [x]Upload a corrupted `.mp4` (truncated or invalid header) — expect `500` with error detail, not a crash
- [x]Upload a `.txt` file renamed to `.mp4` — expect the engine to fail gracefully (500 with detail)
- [x]Send a request with no file attached — expect `422`
- [x]Upload a file exceeding 500MB limit — expect `413 File too large`

### 1.4 Response Quality Checks
- [x]For an authentic phone-recorded video: `verdict` should be `likely_authentic` or `inconclusive`
- [x]For a known AI-generated video: `fused_probability` should be > 0.40
- [x]For a video with confirmed splice: at least one finding with `severity: HIGH` or `CRITICAL`
- [x]`findings` list is empty when all modules score clean (< 0.20)
- [x]`n_elevated_modules` matches the count of `module_scores` values >= 0.30

---

## 2. POST /api/v1/audio/analyze (AFF Engine)

### 2.1 Happy Path
- [x]Upload a standard `.m4a` file — expect 200 with verdict, module_scores, findings
- [x]Upload a `.wav` file — expect 200
- [x]Upload a `.mp3` file — expect 200
- [x]Upload a `.ogg` file — expect 200
- [x]Upload a `.flac` file — expect 200
- [x]Verify response contains all expected top-level keys:
  `verdict`, `verdict_label`, `fused_probability`, `confidence`, `module_scores`, `findings`, `elevated_modules`, `skipped_modules`, `audio`, `filename`
- [x]Verify `module_scores` contains all 6 keys: `metadata`, `enf`, `noise`, `compression`, `reverberation`, `voice`
- [x]Verify `fused_probability` is a float between 0.0 and 1.0
- [x]Verify `verdict` is one of: `likely_authentic`, `inconclusive`, `likely_tampered`, `tampered`
- [x]Verify `audio.duration_s` is a positive float
- [x]Verify `audio.sample_rate` is present and a reasonable value (e.g. 8000–48000)

### 2.2 Edge Cases
- [x]Upload a very short recording (< 3 seconds) — modules requiring minimum duration should skip gracefully
- [x]Upload a long recording (> 10 minutes) — should complete without memory error
- [x]Upload a recording with no speech (music only) — `voice` module should skip, `skipped_modules` should include `voice`
- [x]Upload an outdoor recording (no mains hum) — `enf` module score should be low/skipped, weight redistributed
- [x]Upload a WhatsApp voice note (`.ogg` or `.m4a`) — should handle narrowband codec correctly
- [x]Upload a recording with strong background noise — noise module should flag it but not crash
- [x]Upload the same recording twice — verify `case_id` is different (UUID per request) but results are similar
- [x]Upload a spliced recording (two sessions joined) — ENF should flag phase discontinuity if mains hum present
- [x]Upload a recording made in a reverberant room — reverberation module should detect RT60 > 0.5s

### 2.3 Negative Tests
- [x]Upload a video file (`.mp4`) to the audio endpoint — expect `422 Unsupported format`
- [x]Upload an image (`.jpg`) to the audio endpoint — expect `422 Unsupported format`
- [x]Upload an empty file (0 bytes) — expect `500` with descriptive error, not a crash
- [x]Upload a corrupted `.wav` file — expect `500` with error detail, not a crash
- [x]Upload a `.txt` renamed to `.wav` — expect engine to fail gracefully
- [x]Send a request with no file attached — expect `422`
- [x]Upload a file exceeding 200MB limit — expect `413 File too large`

### 2.4 Response Quality Checks
- [x]For a clean single-session recording: `verdict` should be `likely_authentic` or `inconclusive`
- [x]For a recording with confirmed splice: at least one `HIGH` severity finding in `findings`
- [x]`has_conflict` flag is present and is a boolean
- [x]When `has_conflict` is `true`, `conflict_description` is non-null and descriptive
- [x]`corroboration_factor` is >= 1.0
- [x]`skipped_modules` contains only modules that had legitimate reasons to skip
- [x]Any finding with `temporal_location` has valid `start_s` and `end_s` values

---

## 3. POST /api/v1/image/analyze (Visual Forensics Engine)

### 3.1 Happy Path
- [x]Upload a standard `.jpg` photo — expect 200 with verdict and scores
- [x]Upload a `.png` file — expect 200
- [x]Upload a `.webp` file — expect 200
- [x]Upload a `.bmp` file — expect 200
- [x]Upload a `.tiff` file — expect 200
- [x]Verify response contains all expected top-level keys:
  `verdict`, `tampering_likelihood`, `confidence`, `module_scores`, `findings`, `metadata`, `filename`
- [x]Verify `tampering_likelihood` is a number between 0 and 100
- [x]Verify `confidence` is one of: `High`, `Medium`, `Low` (or equivalent)
- [x]Verify `module_scores` contains: `ela`, `noise`, `copy_move`, `jpeg_compression`
- [x]Verify `metadata` contains EXIF data if the image has it (camera model, timestamp, etc.)

### 3.2 Edge Cases
- [x]Upload a very small image (e.g. 10×10 px) — should return a result, not crash
- [x]Upload a very large image (e.g. 20MP+) — should complete without memory error
- [x]Upload a grayscale image — should analyze without crashing
- [x]Upload an image with no EXIF data (e.g. downloaded from web, EXIF stripped) — `metadata` should reflect this
- [x]Upload an AI-generated image (DALL-E, Midjourney, etc.) — `tampering_likelihood` should be elevated
- [x]Upload a meme (image with overlaid text) — clone score or noise inconsistency should be elevated
- [x]Upload a screenshot — should analyze without crashing
- [x]Upload an image that has been cropped and re-saved — check ELA score
- [x]Upload the same image twice — verify results are deterministic

### 3.3 Negative Tests
- [x]Upload a video file (`.mp4`) to the image endpoint — expect `422 Unsupported format`
- [x]Upload an audio file (`.mp3`) to the image endpoint — expect `422 Unsupported format`
- [x]Upload a `.gif` file — expect `422 Unsupported format` (not in allowed list)
- [x]Upload an empty file (0 bytes) — expect `500` with descriptive error
- [x]Upload a corrupted image (truncated JPEG) — expect `500` with error detail, not a crash
- [x]Upload a `.txt` renamed to `.jpg` — expect engine to fail gracefully
- [x]Send a request with no file attached — expect `422`
- [x]Upload a file exceeding 50MB limit — expect `413 File too large`

### 3.4 Response Quality Checks
- [x]A genuine phone photo: `verdict` should be `Likely Authentic`, `tampering_likelihood` < 25
- [x]A composited/Photoshopped image: `tampering_likelihood` > 50, at least one elevated `module_scores` value
- [x]An AI-generated image: `tampering_likelihood` > 30 with `Medium` or `High` confidence
- [x]A meme with text overlay: `copy_move` or `noise` score should be elevated
- [x]`findings` list is non-null (may be empty for authentic images)
- [x]`metadata.verification` contains file hash (integrity check)

---

## 4. POST /api/v1/frames/analyze (Frame Analysis Engine)

### 4.1 Happy Path
- [x]Upload a standard `.mp4` file — expect 200 with verdict, tampering_confidence, findings
- [x]Upload a `.mov` file — expect 200
- [x]Upload a `.mkv` file — expect 200
- [x]Verify response contains all expected top-level keys:
  `verdict`, `tampering_confidence`, `tampering_type`, `verdict_explanation`, `temporal_findings`, `spatial_findings`, `findings`, `filename`
- [x]Verify `tampering_confidence` is a float between 0.0 and 100.0
- [x]Verify `verdict` is a non-empty string
- [x]Verify `findings` is a list (may be empty)
- [x]Each finding in `findings` contains: `type`, `severity`, `description`

### 4.2 Edge Cases
- [x]Upload a very short video (< 2 seconds) — should return a result, not crash
- [x]Upload a long video (5+ minutes) — should complete (may take 60–120s)
- [x]Upload a video with a confirmed splice/jump cut — `temporal_findings` should be > 0
- [x]Upload a video with duplicate frames (looped section) — duplication finding should be raised
- [x]Upload a video with a very low frame rate (< 10fps) — should handle without crashing
- [x]Upload a video with a very high frame rate (60fps+) — should handle without crashing
- [x]Upload a video with no motion (static camera, no action) — low findings, authentic verdict expected
- [x]Upload an AI-generated video (deepfake) — `spatial_findings` and/or `tampering_confidence` should be elevated
- [x]Upload the same video twice — verify results are consistent

### 4.3 Negative Tests
- [x]Upload an audio file (`.mp3`) to the frames endpoint — expect `422 Unsupported format`
- [x]Upload an image (`.jpg`) to the frames endpoint — expect `422 Unsupported format`
- [x]Upload an empty file (0 bytes) — expect `500` with descriptive error
- [x]Upload a corrupted `.mp4` — expect `500` with error detail, not a crash
- [x]Upload a `.txt` renamed to `.mp4` — expect engine to fail gracefully
- [x]Send a request with no file attached — expect `422`
- [x]Upload a file exceeding 500MB limit — expect `413 File too large`

### 4.4 Response Quality Checks
- [x]A clean continuous recording: `tampering_confidence` < 30, `verdict` indicates authentic
- [x]A video with a clear jump cut: at least one `ABRUPT_TRANSITION` finding
- [x]A video with looped/duplicated frames: at least one `FREEZE_OR_DUPLICATION` finding
- [x]`temporal_findings` count matches the number of findings with temporal type
- [x]Any finding with `timestamp_s` has a value > 0 and < video duration
- [x]`verdict_explanation` is a human-readable string, not an error trace

---

## 5. Cross-Endpoint Tests

These tests apply across all four endpoints.

### 5.1 HTTP Behaviour
- [x]All endpoints return `Content-Type: application/json`
- [x]`422` responses include a `detail` field explaining the issue
- [x]`413` responses include a `detail` field with the file size and limit
- [x]`500` responses include an `error` field and a `code` field, not a raw traceback
- [x]All successful responses return HTTP `200`

### 5.2 Concurrent Requests
- [x]Send two video analysis requests simultaneously — both should complete without interference
- [x]Send one request to each of the four endpoints simultaneously — all should complete without interference

### 5.3 Server Stability
- [x]After a failed request (corrupted file), the server should still accept and process a valid next request
- [x]After a large file upload, memory should not be permanently elevated (check with successive requests)
- [x]Server startup should complete in < 10 seconds with all engines loaded

### 5.4 /health Endpoint
- [x]Returns `200` at all times, including during active analysis
- [x]Response `timestamp` updates on each call
- [x]`status` is always `"ok"` when server is running

---

## Test Files Reference

Sample files already available in the project for testing:

| File | Location | Useful for |
|---|---|---|
| `20230113_155426.mp4` | `VFF/` | Video happy path |
| `VID-20230306-WA0046.mp4` | `VFF/` | WhatsApp re-encoded video |
| `VID-20251222-WA0045.mp4` | `VFF/` | Recent WhatsApp video |
| `IMG_8222.mov` | `VFF/` | iPhone video |
| `AUD-20230414-WA0012.m4a` | `AFF/` | WhatsApp audio |
| `Coach Musa_1.m4a` | `AFF/` | Voice recording |
| `Testing1.m4a` / `Testing2.m4a` | `AFF/` | Known test audio |
| `Akure.m4a` / `Akure 2.m4a` | `AFF/` | Comparison pair |
| `content_deleted.wav` | `AFF/` | Audio with deleted content (negative test) |
| `speaker_substituted.wav` | `AFF/` | Speaker swap (should flag voice module) |
| `clipped.wav` | `AFF/` | Clipped audio |

For image tests, use any phone photo. For AI-generated content tests, use outputs from DALL-E, Midjourney, Sora, or similar tools.

---

## Reporting Failures

When a test fails, note:
1. **Endpoint** — which route
2. **File used** — name and size
3. **Expected** — what should have happened
4. **Actual** — what happened (HTTP status, error message, or wrong result)
5. **Reproducible?** — does it fail every time or intermittently

---

_Last updated: 2026-03-20. All manual tests completed and passed across all four endpoints._
