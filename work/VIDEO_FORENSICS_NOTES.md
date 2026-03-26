# Video Forensics — Implementation Notes

## ENF Detector Limitation

The ENF (Electrical Network Frequency) detector only activates when the source video's frame rate is **≥ 100 fps**.

For normal 24 / 30 / 60 fps content the 50/60 Hz mains signal aliases below the Nyquist limit and cannot be resolved from sampled frames. The module skips gracefully in this case and records a note in `analysis_modules["enf"]`:

```
"note": "ENF extraction skipped (fps too low or no ENF band detected)"
```

To use ENF analysis in practice you would need either:
- A high-frame-rate source (≥ 100 fps), or
- Native-fps decoding of the full bitstream (rather than sampled frames)

The threshold is defined in `ENFVideoForensics` and can be lowered if you have a specific use case, but expect false negatives on standard broadcast content.

---

## DeepfakeDetector — Model Files Required

The face detection sub-module uses OpenCV's DNN Caffe model. Without the model files it skips all face-based checks gracefully (`analysis_modules["deepfake"]["faces_detected"]` will be 0).

**Files needed:**
- `deploy.prototxt`
- `res10_300x300_ssd_iter_140000.caffemodel`

**Where to place them:** `./models/` (next to `video_forensics.py`)

**Where to get them:** the `opencv_extra` repository on GitHub (under `testdata/dnn/`).

---

## What Works Without Extra Setup

The following detectors require only `ffprobe` on `PATH` and the standard Python deps:

| Detector | Dependency |
|---|---|
| Double-encoding (DCT Benford) | OpenCV (already required) |
| Bitrate anomaly | ffprobe |
| Codec inconsistency | ffprobe |
| Container anomaly | ffprobe (pymediainfo optional) |
| Timestamp discrepancy | ffprobe |
| Encoder fingerprint | ffprobe |
| Motion vector anomaly | OpenCV |
| Composite region detection | OpenCV |

---

## Tunable Thresholds (env vars)

| Variable | Default | Effect |
|---|---|---|
| `VFO_TAMPERED_CONFIDENCE` | 75.0 | Score ≥ this → LIKELY_TAMPERED |
| `VFO_REVIEW_CONFIDENCE` | 35.0 | Score ≥ this → REVIEW_RECOMMENDED |
| `VFO_BITRATE_ZSCORE_THRESHOLD` | 2.5 | Bitrate spike sensitivity |
| `VFO_DOUBLE_ENC_BENFORD_THRESH` | 0.15 | Benford MAD sensitivity |
| `VFO_FACE_CONSISTENCY_ZSCORE` | 2.5 | Face sharpness variance sensitivity |
| `VFO_ENF_PHASE_ZSCORE` | 3.0 | ENF phase jump sensitivity |
| `VFO_MOTION_DIVERGENCE_RATIO` | 3.0 | Local/global motion divergence ratio |
| `VFO_TIMESTAMP_DELTA_HOURS` | 1.0 | Max allowed timestamp gap |
