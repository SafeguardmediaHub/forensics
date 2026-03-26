# Media Forensics System — Complete Technical Textbook
### A module-by-module breakdown of what the system does, how it works, and how to verify it

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Module 1 — Video Frame Analysis](#2-module-1--video-frame-analysis)
3. [Module 2 — Image / Visual Forensics](#3-module-2--image--visual-forensics)
4. [Module 3 — Video Forensics (Bitstream, Deepfake, ENF)](#4-module-3--video-forensics-bitstream-deepfake-enf)
5. [Module 4 — Audio Forensics](#5-module-4--audio-forensics)
6. [The Scoring System — How Verdicts Are Computed](#6-the-scoring-system--how-verdicts-are-computed)
7. [Testing Guide — What to Test and How](#7-testing-guide--what-to-test-and-how)
8. [Reading Your Results — Field-by-Field Reference](#8-reading-your-results--field-by-field-reference)

---

## 1. System Architecture Overview

Before explaining each module, understand how all the pieces fit together.

### What the system is

This is a **multi-modal media forensics platform**. It takes audio files, image files, and video files as input and analyses them for signs of digital manipulation — cuts, splices, inserted content, duplicated frames, fake audio, AI-generated faces, metadata tampering, and more.

It does not "watch" or "listen" to your media in any human sense. Every analysis is pure mathematics — signal processing, statistical comparisons, and pattern detection applied to the raw bytes of the file.

### The four modules and what they target

| Module | File type | Primary question answered |
|---|---|---|
| Video Frame Analysis | Video | Was this video edited over time? (temporal manipulation) |
| Image / Visual Forensics | Images (+ video frames) | Was this image pixel-manipulated? (spatial manipulation) |
| Video Forensics | Video | Was this video re-encoded, deepfaked, or its metadata altered? |
| Audio Forensics | Audio | Was this recording spliced, silenced, speaker-swapped, or duplicated? |

When you submit a **video**, all four modules can run: Frame Analysis checks temporal continuity, Image Forensics runs on sampled frames, Video Forensics checks the bitstream and codec layer, and Audio Forensics analyses the audio track separately.

When you submit an **image**, only Image/Visual Forensics runs.

When you submit an **audio file**, only Audio Forensics runs.

### The infrastructure pipeline

```
User uploads file
       ↓
  Flask (app.py) — receives the request, validates the file, creates a job
       ↓
  Celery task (tasks.py) — picks up the job asynchronously in a worker process
       ↓
  Analysis engines run (frame_analysis.py, visual_forens.py,
                        video_forensics.py, audio_forensics_system.py)
       ↓
  Verdict computed (compute_forensic_confidence in tasks.py)
       ↓
  Results stored, returned to UI via job status endpoint
```

Redis acts as the message broker between Flask and Celery — Flask puts the job in the queue, Celery workers pull from it. This means analysis runs without blocking the web server.

---

## 2. Module 1 — Video Frame Analysis

**File:** `frame_analysis.py`
**Entry point:** `FrameAnalysisEngine.analyze_video()`
**API endpoint:** `POST /api/analyze/video`

### What it does

Video Frame Analysis treats a video as a **sequence of images over time** and looks for anything that breaks the natural flow of that sequence. A genuine, unedited video has predictable, smooth transitions between frames. Editing — cuts, deletions, inserted footage, frozen frames — creates discontinuities that show up as statistical outliers.

Think of it like reading a sentence and noticing where the ink colour suddenly changes, or where a page was clearly torn out and replaced. The words might still make sense, but the physical evidence of the edit is there.

### The 12-step analysis pipeline

When `analyze_video()` is called, it executes twelve steps in sequence:

**Step 1 — Read video metadata**
Using ffmpeg-python's `probe()`, it reads the container-level metadata: duration, frames per second (FPS), resolution, codec name, and total frame count. This is not forensic analysis — it is just reading the file's own declaration of what it contains. This becomes important later when actual frame content is compared against these declared values.

**Step 2 — Extract frames**
Frames are extracted at either:
- **Sampled mode (default):** roughly 2 frames per second. A 60-second video yields ~120 frames. This is fast enough for real-time use.
- **Full mode:** every single frame. A 60-second video at 30fps yields 1,800 frames. Use this for deep investigation.

Each extracted frame is a NumPy array of shape `(height, width, 3)` — three colour channels (Blue, Green, Red in OpenCV's convention).

**Step 3 — Calculate baseline metrics for every consecutive frame pair**
Before any anomaly detection, the system computes a full statistical picture of the video:

- **SSIM (Structural Similarity Index):** Compares two frames on luminance (brightness), contrast, and structure. Returns 0.0 (completely different) to 1.0 (identical). In a normal video, consecutive frames are visually similar — SSIM stays above 0.7 unless there's a genuine scene change.
- **Histogram distance (Bhattacharyya):** Compares the colour distribution of two frames. Returns 0.0 (same distribution) to 1.0 (entirely different). Used together with SSIM — a real cut has both low SSIM AND high histogram distance.
- **Sharpness (Laplacian variance):** High-pass filter applied to the grayscale frame; the variance of the output measures how much fine detail is present. An abrupt drop signals a quality change.
- **Brightness (mean pixel value):** Average luminance. Sudden shifts can indicate splice points where the source lighting was different.
- **Contrast (standard deviation):** How spread the pixel values are. Sudden change means the source material changed.
- **Blockiness (Sobel gradient):** Estimates JPEG/H.264 compression block artifacts by looking at edge strength. A segment from a lower-quality source has more blockiness.
- **Noise level (high-pass filter variance):** Sensor noise from the camera creates a consistent texture. Different cameras have different noise signatures.

**Step 4 — Calculate adaptive thresholds**
This is one of the most important parts of the system. Rather than using fixed thresholds (e.g., "flag any SSIM drop below 0.65"), the system computes thresholds from the video itself:

```
ssim_threshold   = max(0.30, min(0.90,  mean_ssim − N × std_ssim))
histogram_thresh = max(0.30, min(0.95,  mean_hist + N × std_hist))
```

Where `N` depends on the sensitivity mode:
- **Standard:** N = 1.5 (moderate sensitivity)
- **High Sensitivity:** N = 1.0 (more sensitive, more findings)
- **Deep Scan:** N = 0.8 (most sensitive, most findings)

**Why adaptive matters:** A wildlife documentary has dramatic scene changes (dark forest → bright savanna). A fixed SSIM threshold of 0.65 would flag every single cut as suspicious. Adaptive thresholds say "given that this particular video has these statistics, what is abnormal for it?" — the thresholds are calibrated to the video's own behaviour.

**Step 5 — PySceneDetect scene boundary detection**
PySceneDetect uses a content-based detector to identify cuts. At each detected scene boundary, metrics at the boundary frame are calculated and the confidence of that finding is computed:

```
confidence = 0.70 + min(0.29, (1 − ssim) × 0.15 + hist_dist × 0.15)
```

A very abrupt cut (SSIM near zero, histogram distance near 1.0) gets confidence close to 0.99. A gentle dissolve gets confidence near 0.70.

**Step 6 — GOP (Group of Pictures) distribution analysis**
This is codec-level analysis. In H.264/H.265 video, frames are compressed in groups called GOPs. Each GOP starts with an I-frame (a complete image) followed by P-frames and B-frames (which only store the difference from neighbouring frames). The distance between I-frames is the GOP size.

In an unedited video from a camera, GOP size is consistent — typically every 25–30 frames. When you splice video together or re-encode a section, the I-frame distribution becomes irregular because re-encoding resets the GOP counter. Unusual I-frame clustering, or a sudden change in GOP size mid-video, is a flag for editing.

**Step 7 — Optical flow motion vector consistency**
Optical flow measures how objects move between consecutive frames. The Farneback algorithm computes a flow vector for every pixel — direction and magnitude of movement. In natural camera footage, the flow field is globally consistent: camera shake moves all vectors in the same direction; a panning shot creates a uniform rightward flow.

Two things are checked:
1. **Global vs. local consistency:** If certain regions of the frame are moving in a direction completely unrelated to the rest, that region was potentially composited in from different footage.
2. **Motion continuity:** If the motion field abruptly changes character (e.g., suddenly switching from camera-motion-dominant to static), that marks a cut point.

**Step 8 — Perceptual hash duplicate detection (ImageHash)**
Each frame is converted to a perceptual hash (pHash) — a 64-bit fingerprint of the frame's visual content that is robust to small colour changes and compression. Frame pairs within a 90-frame (3-second) window are compared by Hamming distance. Pairs with distance ≤ 5 are considered near-duplicates and grouped into clusters using Union-Find.

A cluster of identical frames over several seconds means:
- A freeze frame was inserted (frame was copy-pasted to fill time)
- A loop was created (a section was repeated)
- Video was slowed down by frame repetition

**Step 9 — Anomaly detection with adaptive thresholds**
Using the thresholds computed in Step 4, each frame pair is checked for:
- **Abrupt transition:** SSIM below threshold AND histogram distance above threshold simultaneously. Finding severity is HIGH if SSIM < 0.5, MEDIUM otherwise.
- **Freeze/duplication:** SSIM above duplicate threshold (0.95–0.98). The consecutive frames are nearly identical.
- **Quality drift:** Blockiness or noise level changes by more than 30% between frames. Indicates the source material changed quality (different camera, different encoding settings).

**Step 10 — Deduplication and grouping**
Multiple adjacent findings of the same type are merged. If frames 45, 46, and 47 all flag as "abrupt transition", they become one finding spanning frames 45–47, not three separate findings.

**Step 11 — Audio-Video sync analysis**
Two independent checks:

*Duration mismatch:* ffprobe extracts the declared duration of the audio stream and the video stream. In an unedited recording they are identical. If they differ by more than 100 milliseconds, that is a strong indicator that frames were inserted or deleted — the audio and video durations no longer match.

*Onset-motion correlation:* Strong audio events (speech starts, impacts, transients — detected via librosa's onset detector) should coincide with visible motion in the video. A sudden loud sound at a timestamp where the video shows no motion means the audio and video tracks are desynchronized — which happens when content is cut from one track but not the other.

**Step 12 — Unified verdict**
The scoring model (explained in Section 6) combines temporal findings and spatial findings into a final verdict: `LIKELY_AUTHENTIC`, `REVIEW_RECOMMENDED`, or `LIKELY_TAMPERED`.

### What each finding type means

| Finding type | What it means in plain language |
|---|---|
| `ABRUPT_TRANSITION` | An unnaturally sudden visual jump between frames — possible cut or splice |
| `FREEZE_OR_DUPLICATION` | Frames are identical for too long — freeze frame or loop insertion |
| `QUALITY_DRIFT` | Compression quality suddenly changes — source material was replaced |
| `TIMING_IRREGULARITY` | Frame timing is inconsistent — frames deleted or duplicated |
| `MOTION_ANOMALY` | Motion is inconsistent with camera movement — possible composited region |
| `IRREGULAR_GOP` | I-frame distribution is uneven — video was re-encoded in sections |
| `MOTION_DISCONTINUITY` | Optical flow abruptly changes character — cut or splice point |
| `AV_SYNC_ANOMALY` | Audio and video tracks are out of sync — content was deleted from one track |

### Use cases

- **Legal evidence:** A dashcam recording submitted as evidence in a court case. Was the video edited to remove an incriminating minute?
- **Journalism:** A news clip claimed to show an event. Were frames inserted from a different recording?
- **Security footage:** CCTV footage from a premises. Was a section removed or replaced?
- **Social media:** A viral video claims to show something dramatic. Is it real footage or edited?

### How to verify the module is working correctly

**Test 1 — Freeze frame injection**
Take any genuine video. Open it in a video editor, go to a frame at timestamp T, and duplicate it 60 times (2 seconds at 30fps). Export. Submit to the system.
- **Expected:** `FREEZE_OR_DUPLICATION` finding at timestamp T.

**Test 2 — Hard cut splice**
Take two different videos (different lighting, different content). Export a 10-second clip from each. Concatenate them with no transition. Submit.
- **Expected:** `ABRUPT_TRANSITION` finding at the splice point (approximately where the two clips join).

**Test 3 — Audio deletion**
Take a video with speech. Delete 5 seconds of audio in the middle using Audacity while leaving the video stream unchanged. Export.
- **Expected:** `AV_SYNC_ANOMALY` finding — audio and video stream durations now differ.

**Test 4 — Clean genuine video**
Submit a raw, unedited camera recording.
- **Expected:** No findings, or only LOW severity findings at genuine scene changes, verdict `LIKELY_AUTHENTIC`.

---

## 3. Module 2 — Image / Visual Forensics

**File:** `visual_forens.py` and `forensic_primitives.py`
**Entry point:** `ForensicAnalyzer.analyze_image()` (called on individual frames and standalone images)
**API endpoint:** `POST /api/forensics/analyze`

### What it does

Where Frame Analysis looks at how a video changes *over time*, Image Forensics looks at a single image and asks: **were the pixels in this image altered?**

It does not compare the image to anything external. It analyses the internal consistency of the image itself — specifically, whether different regions of the image show signs of having come from different sources or having been processed differently.

The analogy is a photocopied document where someone whited-out text and retyped it. The visual content looks plausible, but under certain lighting or magnification the altered region looks slightly different from the untouched paper — different texture, different ink quality. Image forensics detects the digital equivalent of that.

### Technique 1 — Error Level Analysis (ELA)

**File:** `forensic_primitives.py → error_level_analysis()`

**The concept:**
JPEG compression is lossy — every time you save an image as JPEG, it discards some detail to reduce file size. The amount of detail discarded depends on the quality setting. Crucially, **an unaltered JPEG has a uniform compression history**. Every part of the image has been through the same number of compression cycles at the same quality level.

When you copy a region from one image and paste it into another, or when you use Photoshop to edit part of an image and re-save as JPEG, the manipulated region has a different compression history from the original parts of the image. It may have been saved at a different quality level, or compressed an extra time.

**How ELA works:**
1. Take the original image.
2. Re-save it as JPEG at quality level 95 (near-lossless).
3. Compute the pixel-by-pixel difference between the original and the re-saved version: `diff = |original − recompressed|`.
4. Amplify the difference by 10× to make it visible.
5. Apply a colour map (blue = low difference, red = high difference).

**What the result means:**
- **Uniform blue/cool colours across the whole image:** The entire image has been compressed the same number of times. Consistent — authentic indicator.
- **Hot red/yellow regions surrounded by cool regions:** Those hot regions have a different compression history. They were either: (a) pasted in from another source, (b) edited in Photoshop, (c) re-saved at a different quality setting.

**The ELA score** is the mean absolute difference across all pixels. Higher score = more inconsistency = more suspicious.

| Score | Interpretation |
|---|---|
| 0 – 8 | Low — consistent compression, likely authentic |
| 8 – 15 | Medium — some inconsistency, worth investigating |
| 15+ | High — strong evidence of manipulation or re-editing |

**Important limitation:** ELA is only meaningful for **JPEG images**. PNG files are lossless — they have no compression history to analyse. An original PNG submitted to ELA will always show high error levels and means nothing. The system checks the format before interpreting ELA scores.

**A second important limitation:** Screenshots always show high ELA scores because every region of a screenshot has a different origin (browser rendering, application rendering, desktop background). This does not mean a screenshot was manipulated.

### Technique 2 — Noise Analysis

**File:** `forensic_primitives.py → noise_analysis()`

**The concept:**
Every digital image contains sensor noise — tiny random variations in pixel values introduced by the camera's image sensor, temperature, and electronics. This noise is not random from image to image for the same camera; it has a consistent statistical fingerprint called a **Photo Response Non-Uniformity (PRNU) pattern**.

More practically: every region of an authentic photograph, taken with the same camera in the same conditions, has statistically similar noise. When you paste a region from a different photo (taken with a different camera, or in different conditions), the noise pattern in the pasted region is statistically different from the surroundings.

**How noise analysis works:**
1. Convert to grayscale.
2. Apply a Gaussian blur (5×5 kernel) to get a "smooth" estimate of the underlying image.
3. Subtract the smoothed image from the original: `noise = original − smoothed`. This isolates the high-frequency noise residual.
4. Compute local variance of the noise in 15×15 pixel windows across the entire image using a sliding filter.
5. Normalise and colour-map the result.

**What the result means:**
- **Uniform noise variance map:** The noise has the same statistical character everywhere. Good sign.
- **Regions with dramatically different variance:** Different noise texture means different source material — pasted region, airbrushed region, or AI-generated fill.

**The noise inconsistency score** is the standard deviation of the local variance map. If variance is consistent everywhere, the standard deviation of it is near zero. If some regions have wildly different variance, the standard deviation is high.

| Score | Interpretation |
|---|---|
| 0 – 25 | Low — consistent noise, authentic indicator |
| 25 – 50 | Medium — some inconsistency, investigate further |
| 50+ | High — strong noise pattern inconsistency |

### Technique 3 — Copy-Move Detection (SIFT primary, DCT fallback)

**File:** `forensic_primitives.py → copy_move_detection_sift()`

**The concept:**
Copy-move forgery is when a region within the same image is copied and pasted elsewhere in the same image — most commonly used to: clone-stamp out an object (copy background over an object to hide it), or duplicate an object to make a crowd look larger.

The forged region and the original are from the *same source image*, so they bypass ELA and noise analysis (same compression history, same camera noise). The detection relies on finding the two copies through feature matching.

**SIFT (Scale-Invariant Feature Transform) method:**
1. Detect up to 1,000 keypoints — distinctive local features (corners, blobs, edges) in the image.
2. Compute a 128-dimensional descriptor for each keypoint — a mathematical fingerprint of the local region.
3. Match each keypoint's descriptor against all other keypoints in the same image using FLANN (Fast Library for Approximate Nearest Neighbors), with a ratio test (Lowe's ratio test: `m.distance < 0.7 × n.distance`).
4. Filter out self-nearby matches — matches between keypoints within 50 pixels of each other are meaningless (they are seeing the same thing). Only matches between distant regions are suspicious.
5. Draw circles at the location of each match pair on a heatmap.

**DCT fallback method (when SIFT fails or insufficient keypoints):**
1. Divide the image into 32×32 pixel blocks.
2. Compute the 2D DCT (Discrete Cosine Transform) of each block and take the first 10 coefficients.
3. For each pair of blocks more than 64 pixels apart, compare their DCT coefficient vectors.
4. If the Euclidean distance is below 3.0, the blocks are considered copies of each other.

**The clone score** is the percentage of image area covered by suspicious match regions.

| Score | Interpretation |
|---|---|
| 0 – 2% | Low — no significant copy-move |
| 2 – 5% | Medium — possible copy-move, investigate visually |
| 5%+ | High — strong copy-move evidence |

**What the heatmap shows:** Red/yellow spots are regions that were matched to another region elsewhere in the image. When you see two clusters of red separated by distance, those two clusters likely represent the original region and its copied counterpart.

### Technique 4 — JPEG Compression Analysis

**File:** `visual_forens.py → jpeg_compression_analysis()`

**The concept:**
If an image has been edited and re-saved as JPEG, two things happen:
1. A second round of lossy compression is applied, introducing additional JPEG block artifacts.
2. The quantization tables used by the second save may differ from the first save.

The 8×8 block grid from the first compression and the new 8×8 block grid from the second compression are rarely aligned, creating visible blocky artifacts at JPEG block boundaries.

**How it works:**
1. Check every 8×8 pixel boundary in the image.
2. Measure horizontal and vertical differences at each boundary.
3. Count boundaries where the difference exceeds 10 (sign of blocking artifact).
4. Compute `artifact_score = (boundaries_with_artifacts / total_boundaries) × 100`.

Also checks `bytes_per_pixel` to estimate original save quality:
- Above 2 bytes/pixel → High quality (90–100)
- 1–2 bytes/pixel → Medium quality (70–89)
- Below 1 byte/pixel → Low quality (heavily compressed)

| Artifact score | Interpretation |
|---|---|
| 0 – 15% | Low — single compression, consistent with original |
| 15 – 30% | Medium — possible second compression |
| 30%+ | High — very likely re-saved after editing |

### Technique 5 — AI Generation Detection (Heuristic)

**File:** `visual_forens.py → detect_ai_generated_heuristic()`

This is a heuristic detector — it uses signal-processing indicators rather than a trained neural network. It scores six features:

**Feature 1 — Frequency domain ratio (20 points)**
2D FFT of the image. Compute energy in high-frequency vs low-frequency regions. Natural photographs have abundant high-frequency content (noise, fine texture, hair detail). GAN-generated images tend to be too smooth — the frequency ratio is unnaturally low (below 0.18).

**Feature 2 — Saturation uniformity (20 points)**
AI generators often produce images with unnaturally uniform colour saturation across the image. Natural photos have varied saturation — shadows are desaturated, highlights are bold. If the standard deviation of the HSV saturation channel is below 25, the image is suspiciously uniform.

**Feature 3 — Texture variance (25 points)**
Laplacian variance measures micro-texture. Real photographs have noise, skin pores, fabric weave, leaf veins. AI images render surfaces as smooth gradients. A Laplacian variance below 50 is extremely suspicious.

**Feature 4 — Frequency spectrum flatness (20 points)**
The DFT magnitude spectrum of a real photograph is not flat — there are dominant frequencies. AI images produce more uniform spectra. Standard deviation below 1.1 is a flag.

**Feature 5 — Edge density (20 points)**
Canny edge detection. Natural scenes have rich, fine edges everywhere. AI images produce smooth, rounded boundaries. Edge density below 1.2% of pixels is a flag.

**Feature 6 — LBP entropy (25 points)**
Local Binary Patterns encode micro-texture by comparing each pixel to its 8 neighbours. Real photographs have high LBP entropy — diverse texture patterns. AI images have low LBP entropy — repetitive, smooth texture. Below 4.8 is suspicious.

**AI score interpretation:**

| Score | Verdict |
|---|---|
| 85–100 | Very Likely AI-Generated |
| 60–84 | Likely AI-Generated |
| 40–59 | Possibly AI-Generated |
| 0–39 | Likely Real |

**Important caveat:** This is a heuristic, not a trained classifier. It will miss modern AI images that have been post-processed (adding noise, sharpening) and will occasionally flag heavily processed real photographs. Use it as one indicator, not a conclusion.

### Technique 6 — Metadata / EXIF Analysis

**File:** `visual_forens.py → extract_metadata()`

EXIF metadata is stored in the JPEG header and contains information written by the camera at the moment of capture: camera make and model, lens focal length, GPS coordinates, timestamp, ISO, shutter speed, aperture.

When an image is manipulated in Photoshop, GIMP, or online tools, the EXIF is often:
- Completely stripped (the tool doesn't preserve it)
- Partially modified (the software updates certain fields but not others)
- Self-contradictory (GPS says one location, timezone says another)

The system flags:
- **Missing EXIF:** For a JPEG claiming to be a camera photograph, no EXIF is suspicious.
- **GPS/timezone inconsistency:** Claimed location and timezone don't match.
- **Software-edited flag:** Some tools write their own name into the EXIF (e.g., "Adobe Photoshop CS6").

A clean original photo has rich EXIF data: camera model, timestamp, GPS, ISO, focal length. A manipulated image has sparse or absent EXIF.

### How the image verdict is computed

All technique scores are converted to points and summed:

| Finding | Points |
|---|---|
| ELA High (>15) | 30 pts |
| ELA Medium (8–15) | 18 pts |
| Noise High (>50) | 30 pts |
| Noise Medium (25–50) | 18 pts |
| Clone High (>5%) | 35 pts |
| Clone Medium (2–5%) | 20 pts |
| JPEG re-compression | 25 pts |
| Missing EXIF | 17 pts |

| Total score | Verdict |
|---|---|
| 0 – 29 | Likely Authentic |
| 30 – 59 | Possibly Tampered |
| 60+ | Likely Tampered |

### Use cases

- **Photojournalism verification:** A news agency photo shows a dramatic scene. Was the crowd size inflated with copy-move? Was an object removed from the scene?
- **Insurance fraud:** A photo submitted with an insurance claim. Was the damage edited to look worse?
- **Social media hoaxes:** A photo claiming to show an event. Was a person or object pasted in?
- **Legal documents:** A photographed document submitted as evidence. Was text altered?
- **Product authentication:** Product photos submitted to a marketplace. Were labels or serial numbers edited?

### How to verify the module is working correctly

**Test 1 — ELA on a Photoshopped image**
Take a photograph. Open in Photoshop, paint a large brush stroke on it, and re-save as JPEG quality 80. Submit.
- **Expected:** High ELA score, hot red region where brush stroke was applied.

**Test 2 — Copy-move clone stamping**
Take a photograph with a person in it. Use the Clone Stamp tool in Photoshop to paint over the person with background pixels. Save.
- **Expected:** High clone score, heatmap shows the stamped region and the source region.

**Test 3 — AI-generated image**
Download an image from a GAN (e.g., thispersondoesnotexist.com).
- **Expected:** AI detection score above 60, likely with flags on low texture variance and low frequency ratio.

**Test 4 — Original unedited photograph**
Submit a raw JPEG directly from a camera or phone, never opened in an editor.
- **Expected:** Low ELA score, low noise inconsistency, abundant EXIF, verdict Likely Authentic.

---

## 4. Module 3 — Video Forensics (Bitstream, Deepfake, ENF)

**File:** `video_forensics.py`
**Entry point:** `VideoForensicsEngine.analyze_video()`
**API endpoint:** `POST /api/forensics/video/analyze`

### What it does

This module goes deeper than Frame Analysis. Where Frame Analysis looks at the **visual content** of frames, Video Forensics looks at the **encoded structure** of the video file itself — the codec layer, metadata chain, motion field mathematics, and electrical frequency signals embedded in the luminance track.

Frame Analysis asks "does the content look right?" Video Forensics asks "does the file structure, encoding, and embedded physics look right?"

The module contains five sub-detectors:

### Sub-detector 1 — Codec Bitstream Forensics

**Class:** `CodecBitstreamForensics`

**1a. Double Encoding Detection (Benford's Law)**

When you record a video with a camera, the camera encodes it once. The DCT (Discrete Cosine Transform) coefficients of the compressed blocks follow Benford's Law — a mathematical principle stating that the first significant digit of naturally occurring numbers (1, 2, 3..., 9) follows a specific logarithmic distribution: about 30% start with 1, 17.6% start with 2, 12.5% start with 3, and so on decreasing.

This happens because DCT coefficients arise from a physical process (camera optics, sensor response), and natural data follows Benford's Law.

When a video is **re-encoded** (decoded from H.264, edited, then re-encoded to H.264), the DCT coefficients are computed on data that was already DCT-compressed. The resulting distribution deviates from Benford's Law because you are compressing already-compressed data.

**The test:**
1. Sample up to 8 keyframes evenly from the video.
2. Extract all 8×8 blocks from the Y (luminance) channel.
3. Compute 2D DCT of each block.
4. Collect all non-zero AC coefficients (skip the DC component at position 0,0).
5. Build a histogram of first digits (1–9).
6. Compute the Mean Absolute Deviation from the theoretical Benford distribution.
7. If MAD exceeds the threshold (default 0.02), flag as DOUBLE_ENCODING.

**What a finding means:** The video was decoded from one encoding and re-encoded — almost certainly because it was edited in video editing software. This does not prove malicious intent (legitimate editing also re-encodes), but it is a strong indicator that the video was not submitted in its original form.

**1b. Bitrate Segment Profiling**

ffprobe's `show_packets` mode reveals the size of every encoded packet. The system groups packet sizes into 1-second buckets to compute per-second bitrate across the entire video, then computes z-scores of that bitrate array.

A z-score above the threshold (default 3.5) at a particular second means the bitrate at that second is a statistical outlier — it deviated dramatically from the video's own average bitrate.

**Why this matters:** When a section of video is edited and replaced with content encoded at a different quality setting, or when scene complexity suddenly changes because inserted footage has different content, the bitrate spikes or drops sharply at the splice boundary.

**1c. Encoder Inconsistency Detection**

ffprobe reads the encoder tags embedded in the video stream and format container. A comparison is made between:
- The `encoder` tag (e.g., "Apple QuickTime", "Adobe Premiere Pro")
- The actual detected codec (e.g., `h264`, `hevc`, `vp9`)

Known software/codec mappings are hardcoded. If the encoder tag says "x264" but the codec is HEVC, that is an inconsistency. This can indicate:
- Metadata was manually altered to obscure the editing software's identity
- The file was processed by software that incorrectly overwrote the encoder tag
- The file was assembled from segments with different encoding metadata

### Sub-detector 2 — Metadata Chain Forensics

**Class:** `MetadataChainForensics`

**2a. Container Structure Audit**

Using pymediainfo (if installed) and ffprobe, the container structure is examined:
- **moov atom position (MP4):** In a correctly authored MP4, the `moov` atom (which contains all the metadata needed to decode the file) is near the beginning of the file ("fast start" for streaming). Edited or re-muxed files often have the `moov` atom at the end because editing software processes the media first and writes the index last.
- **Unusual container formats:** Raw video formats (rawvideo, ASF) used as containers where MP4/MOV is expected.

**2b. Timestamp Anomaly Detection**

Three different timestamps are compared:
1. `creation_time` tag embedded in the container
2. `encoded_date` tag (some codecs write this separately)
3. File modification time (`mtime`) from the filesystem

In an untampered recording, these three times should be within minutes of each other — the file was created, encoded, and last modified all around the same time.

When someone edits a video and exports it, the file mtime is updated to "now" but the internal `creation_time` tag might still reflect the original recording time. Or someone might try to backdate the `creation_time` to match the recording time they claim — but forget to also update the `encoded_date`. These discrepancies are flagged when the delta exceeds the threshold (default 1 hour).

**2c. Encoding Software Fingerprinting**

Cross-references the claimed encoder with expected codecs for that software. "Apple" products use H.264, HEVC, and AAC. "Handbrake" uses H.264, HEVC, and VP9. If the encoder tag claims Apple but the codec is VP9, that combination does not exist in practice — metadata was tampered.

### Sub-detector 3 — Deepfake Detection

**Class:** `DeepfakeDetector`

**Requires:** OpenCV DNN face model files (`deploy.prototxt` and `res10_300x300_ssd_iter_140000.caffemodel`) in the `./models/` directory. Analysis is skipped gracefully if these are absent.

**3a. Face Detection**
The ResNet-SSD face detector runs on sampled frames. For each detected face, the bounding box (x, y, width, height) and confidence are recorded.

**3b. Facial Temporal Consistency**
For each frame pair where a face is detected:
1. Crop the face region.
2. Compute **Laplacian variance** of the face crop (sharpness).
3. Compute **Laplacian variance** of the background (everything outside the face).

In a genuine recording, face sharpness varies naturally and in proportion to background sharpness — when the camera focuses on the background, both get sharper; when it focuses on the face, both adjust together.

In a deepfake — where a synthesized or swapped face is composited over the original video — the face region's sharpness variation is disconnected from the background. The face might be consistently sharp while the background varies (the composite was rendered at a fixed quality), or the face might show z-score outliers that the background does not show.

**Finding condition:** Face sharpness z-score exceeds threshold AND face-to-background variance ratio exceeds 2.0.

**3c. Frequency Artifact Analysis**
GAN-generated faces have a subtle but measurable frequency fingerprint. GANs produce images by learning a mapping in frequency space, and they tend to produce periodic high-frequency components that do not appear in real photographs — sometimes called "GAN spectral peaks."

**How it works:**
1. Crop each detected face to 128×128 pixels.
2. Apply 2D FFT.
3. Compute **spectral flatness**: the ratio of geometric mean to arithmetic mean of the magnitude spectrum.
4. Natural face images have non-flat spectra (dominant low-frequencies, falling off at high frequencies). GAN faces have more uniform spectra — higher spectral flatness.

Threshold: mean spectral flatness above 0.18 → HIGH, above 0.12 → MEDIUM.

### Sub-detector 4 — Motion Vector Forensics

**Class:** `MotionVectorForensics`

**4a. Motion Consistency Analysis**
Dense Farneback optical flow is computed between consecutive frames. The entire flow field is partitioned into 16×16 blocks. The global motion magnitude (average over the whole frame) is computed, and each block's local motion magnitude is compared to the global:

```
ratio = local_block_magnitude / global_magnitude
```

Blocks where this ratio exceeds `VFO_MOTION_DIVERGENCE_RATIO` (default 3.0) are "divergent" — they are moving more than 3× the rest of the frame. The fraction of divergent blocks per frame pair is tracked.

**What this detects:** When an object is composited into a scene, its motion (even subtle drift from the compositor) may not perfectly match the camera's global motion. Regions of localized extreme motion in an otherwise globally-moving scene are suspicious.

**4b. Composite Region Detection**
ORB keypoints are matched between consecutive frames and a homography (perspective transformation matrix) is estimated. The homography describes the camera's own movement between frames.

For each keypoint, RANSAC determines whether the point fits the estimated camera motion ("inlier") or deviates from it ("outlier"). A high ratio of outliers means many points in the frame are not following the camera's global motion — they are moving independently.

This is characteristic of pasted-in foreground elements: the background follows the camera homography; the composited element follows its own motion or no motion.

### Sub-detector 5 — ENF Video Forensics

**Class:** `ENFVideoForensics`

**The concept:**
When recording indoors, fluorescent lights and other electrically-powered light sources pulse at the frequency of the electrical mains: 50 Hz in Europe/Asia, 60 Hz in the Americas. This pulsing is invisible to the naked eye but creates a subtle, measurable oscillation in the **luminance** (brightness) of video frames recorded under these lights.

The ENF (Electrical Network Frequency) is not perfectly constant — it fluctuates continuously by tiny amounts (±0.1 Hz) as power demand changes across the grid. This fluctuation creates a unique, time-varying fingerprint. A recording made at a specific time and location has a specific ENF trajectory. If a recording is genuine and uncut, the ENF trajectory is continuous and smooth. If it was spliced together from recordings made at different times, the ENF phase jumps at the splice point.

**Important limitation:** ENF analysis in video requires very high frame rates (above 100 fps) to resolve the 50/60 Hz mains signal (Nyquist theorem: sampling must be at least twice the frequency being measured). Most standard 24–30 fps video cannot resolve 50 Hz. The module skips ENF analysis if fps < 100.

**How it works (for high-fps video):**
1. For each frame, compute the mean luminance of the central horizontal strip.
2. Build a luminance time series (one value per frame).
3. Remove the DC component (mean luminance).
4. 2D FFT of the luminance series → find the band with the most power near 50 Hz or 60 Hz.
5. Bandpass filter around the dominant ENF band (±1 Hz window).
6. Extract instantaneous phase via the Hilbert transform (analytic signal).
7. Compute first derivative of the unwrapped phase.
8. z-score the phase derivative. Jumps above threshold (default 4.0 σ) are flagged.

### How the video forensics verdict is computed

Each finding has a severity (HIGH, MEDIUM, LOW). Severity points are summed:
- HIGH = 25 points
- MEDIUM = 12 points
- LOW = 4 points

Total score is capped at 100%.

| Score | Verdict |
|---|---|
| 0 – 34% | LIKELY_AUTHENTIC |
| 35 – 74% | REVIEW_RECOMMENDED |
| 75%+ | LIKELY_TAMPERED |

### Use cases

- **Deepfake detection in video calls:** A video presented as a genuine video call recording. Were the faces real or synthesized?
- **Re-encoding provenance:** A video claims to be original phone camera footage. Does the bitstream confirm this, or was it re-encoded after editing?
- **Timestamp manipulation:** A video is claimed to have been recorded on a specific date. Do the embedded timestamps match that date?
- **Crime scene recording:** Video from a surveillance camera where someone had access to the system and potentially deleted a section.

### How to verify the module is working correctly

**Test 1 — Re-encoded video**
Take an MP4. Open in any video editor and export it without making changes. The act of exporting re-encodes it. Submit both the original and the re-exported version.
- **Expected:** The re-exported version gets a `DOUBLE_ENCODING` finding; the original does not.

**Test 2 — Timestamp manipulation**
Use ffmpeg to copy a video while changing the `creation_time` metadata to a date 10 years in the past: `ffmpeg -i input.mp4 -metadata creation_time="2014-01-01T00:00:00Z" -c copy output.mp4`. The file's actual `mtime` on disk will be today.
- **Expected:** `METADATA_TAMPERING` finding with a delta of approximately 10 years.

**Test 3 — Bitrate spike**
Take a video. Replace 3 seconds in the middle with high-motion content (explosions, fast camera movement) that was encoded at a much higher bitrate. Submit.
- **Expected:** `BITRATE_ANOMALY` finding at the timestamp of the high-bitrate segment.

---

## 5. Module 4 — Audio Forensics

**File:** `audio_forensics/audio_forensics_system.py`
**Entry point:** `AudioForensicsSystem.run_full_analysis()`
**API endpoint:** `POST /api/forensics/audio/analyze`

### What it does

Audio Forensics analyses a speech or voice recording for five categories of manipulation:
1. **Integrity** — Was the file itself damaged, clipped, or does it contain suspicious silence?
2. **Speaker consistency** — Is the same speaker present throughout, or was content from multiple recordings stitched together?
3. **ENF analysis** — Is the mains frequency signal (if present) continuous and uninterrupted?
4. **Events / splice detection** — Are there abrupt spectral changes that indicate cuts?
5. **Duplicate detection** — Are any segments of audio repeated that shouldn't be?

**Before analysis begins:** The system classifies the audio type: `speech`, `music`, `phone_speech`, or `unknown`. This classification changes how modules are run:
- **Music:** Speaker consistency and duplicates are skipped (choruses repeat legitimately; multiple instruments cause false speaker change flags). Event sensitivity is reduced.
- **Phone speech:** Codec compression artifacts are expected; event sensitivity is slightly reduced.
- **Speech:** Full analysis, standard sensitivity.

### Sub-module 1 — Integrity Analysis

**Method:** `_analyze_integrity()`

This module answers the question: **is the raw audio waveform in a healthy, unaltered state?**

**Check 1 — SHA-256 file hash**
The complete file is read and SHA-256 hashed. This serves as a **chain of custody anchor** — if the same file is submitted again later and the hash matches, you can prove it was not altered between submissions. If the hash differs from a known-good reference, the file changed.

**Check 2 — Clipping ratio**
Audio clipping occurs when the signal exceeds the maximum possible value (±1.0 in normalised floating point, ±32767 in 16-bit PCM). Clipped samples are "flat-topped" — their waveform looks like a rectangle rather than a smooth wave.

```
clip_ratio = fraction of samples where |amplitude| >= 0.999
```

Clipping in original recordings comes from recording too loud. Clipping in manipulated recordings often comes from **boosting the volume** of a section to make it louder — a common technique when trying to make a soft statement sound more emphatic, or when different recording conditions are stitched together.

A clip ratio above 0.1% flags the file.

**Check 3 — DC offset**
DC offset is a constant positive or negative shift of the entire waveform away from zero. In a properly functioning recording chain, the waveform should be centred at zero (equal positive and negative excursions). A significant DC offset (above 0.05) indicates either a hardware problem with the recording device or that the audio was processed in software that introduced an offset.

**Check 4 — Silence detection (the forensically important one)**
This is not just counting silent samples. The system uses an adaptive, distribution-aware approach:

*Step 1 — Dynamic noise floor:*
Rather than using a fixed amplitude threshold (e.g., "silence = amplitude < 0.01"), the threshold is computed relative to the file's own peak energy:
```
noise_floor = max(0.001, peak_frame_RMS × 0.01)
```
This means the threshold is −40 dB below the loudest moment in the file. A WhatsApp voice note recorded at low volume will have a different absolute noise floor than a studio recording — but both will correctly identify silence relative to their own loudest speech.

*Step 2 — Per-frame RMS (not per-sample amplitude):*
The waveform is divided into 23-millisecond frames (512 samples at 22050 Hz). The RMS (Root Mean Square) energy of each frame is computed. Frames below the noise floor are classified as silent.

Why frames, not samples? Inside a voiced speech sound, the waveform crosses zero many times per millisecond. If you check individual samples, those zero-crossings look like momentary silence — inflating the silence ratio by 3–4×. Per-frame RMS correctly identifies that the frame contains speech energy even if individual samples briefly hit zero.

*Step 3 — Group silent frames into runs:*
Consecutive silent frames are grouped into runs. A run might be: 50 frames of silence = about 1.15 seconds of silence.

*Step 4 — Compare runs to the file's own pause distribution:*
All pause runs are collected. The **median pause length** of the file is computed. The anomaly threshold is:
```
anomaly_threshold = max(3.5 seconds, median_pause × 4.0)
```
A naturally talkative person (median pause = 0.5 seconds) gets a threshold of max(3.5s, 2.0s) = 3.5s.
A speaker who habitually pauses 2 seconds at sentence ends (median pause = 2.0 seconds) gets a threshold of max(3.5s, 8.0s) = 8.0s.

This means: a silence gap that is 4× longer than the file's typical pauses AND longer than 3.5 seconds in absolute terms is flagged as anomalous.

**Why this matters forensically:** When someone deletes a section of audio, the cut creates an abrupt silence gap where speech used to be. The gap is almost always longer than natural pauses (which are 0.5–2 seconds in normal speech). This adaptive threshold catches gaps as short as 3.5 seconds while ignoring the speaker's natural pause rhythms.

**Confidence penalty calculation:**
```
base confidence = 1.0
− penalty for clipping (up to 0.5)
− penalty for DC offset (0.15)
− penalty for anomalous silence gaps (0.15 per gap, up to 0.4 total)
```

### Sub-module 2 — Speaker Consistency

**Method:** `_analyze_speaker_consistency()`

This module answers: **is the same person speaking throughout, or was this recording assembled from multiple sources?**

**How voice fingerprinting works:**
Audio is divided into 5-second non-overlapping segments. For each segment, the module computes 20-dimensional MFCC (Mel-Frequency Cepstral Coefficients) vectors — the same features used in speech recognition.

*What are MFCCs?*
The short-time Fourier Transform decomposes each 23ms frame into its frequency components. These are mapped onto the **Mel scale** — a perceptual scale of pitch that matches how the human ear perceives frequency differences (logarithmic at high frequencies, linear at low frequencies). A filterbank of triangular filters extracts energy in each Mel band. The log of these energies is passed through a Discrete Cosine Transform (DCT) to produce 20 coefficients.

These 20 numbers compactly describe the *spectral shape* of speech — the resonance patterns of the vocal tract that make each person's voice distinctive. They are largely independent of pitch and volume.

**The cosine similarity comparison:**
For each segment, the mean MFCC vector (one 20-dimensional vector per segment) is computed. All pairs of segments are compared using cosine similarity:
```
similarity = dot(A, B) / (|A| × |B|)
```
1.0 = identical, 0.0 = completely different.

For the same speaker in the same acoustic environment, the mean similarity across all segment pairs should be above 0.8.

**Voiced segment filtering:**
Before computing MFCCs, silent or mostly-silent segments are excluded. A segment must have 3 of its 4 sub-windows with RMS above the voice threshold. This prevents silent segments (which produce near-zero MFCC vectors) from dragging the mean similarity down.

**Findings:**
- `min_similarity < 0.5` → Strong flag: "possible speaker change or spliced audio"
- `mean_similarity < 0.7` → Soft flag: "below-average mean speaker similarity"

**Confidence:** `round(clip(mean_similarity, 0, 1), 3)` — the mean similarity directly becomes the confidence score.

### Sub-module 3 — ENF Analysis

**Method:** `_analyze_enf()`

Same principle as video ENF but applied to audio. The mains frequency (50/60 Hz and their harmonics at 100/120 Hz) is picked up by microphones near electrical equipment.

**The bands checked:**
- 49.5–50.5 Hz (European mains)
- 99.5–100.5 Hz (European mains 1st harmonic)
- 59.5–60.5 Hz (US mains)
- 119.5–120.5 Hz (US mains 1st harmonic)

**Narrowband SNR guard (the false positive killer):**
Before filtering around an ENF band, the system checks whether the target 1 Hz window contains a genuine narrow spectral spike or just broadband speech energy that happens to be in the band:

```
SNR_ratio = mean_power_in_target_band / mean_power_in_guard_band
```

The guard band is ±5–15 Hz around the target (excluding the target itself). If the SNR ratio is below 2.0 (less than 3 dB above surrounding noise), the band is dominated by broadband content — speech, instrument, noise — not a narrow ENF tone. This band is skipped.

This guard prevents the system from flagging a male speaker whose vocal fundamental is at 120 Hz (common) as having an ENF signal at the 60 Hz harmonic.

**Phase analysis:**
After bandpass filtering, the instantaneous phase is extracted via the Hilbert transform. The first derivative of the unwrapped phase (dφ/dt) is computed. Abrupt phase jumps (z-score above 5.0) are counted and grouped into distinct events (within-1-second grouping).

**Confidence calculation:**
```
rate = distinct_events / duration_minutes
confidence = clip(1.0 − rate × 0.30, 0, 1)
```
0 events → confidence 1.0. Each event per minute costs 30 points.

**When ENF returns None:**
If no ENF signal is detected (all bands fail the SNR guard), or if the sample rate is too low (< 200 Hz), the confidence is `None`. This is deliberate — it means "this recording does not support ENF analysis" and the module is excluded entirely from the scoring formula. Returning 0.5 for an inconclusive test would systematically lower every clean file's score.

### Sub-module 4 — Events / Splice Detection

**Method:** `_analyze_events()`

This module answers: **are there abrupt spectral transitions in the audio that indicate a cut or splice?**

**Spectral flux:**
The STFT (Short-Time Fourier Transform) is computed across the entire audio file, dividing it into 23ms frames. For each consecutive frame pair, the *spectral flux* is computed:
```
flux(t) = sum of all positive differences in spectral magnitude
         between frame t and frame t-1
```
Spectral flux measures how much new energy appeared in the spectrum between frames. In natural speech, flux is high at syllable onsets (when a new sound starts) and low during sustained vowels. In music, it is high at beat transients. A splice cut — where one recording ends and another begins — creates an extreme flux spike at the cut point.

**Adaptive threshold system (three mechanisms):**

*Mechanism 1 — Skewness-adjusted z-threshold:*
Spectral flux in speech is not normally distributed — it is heavily right-skewed (many small values, a few very large ones from plosives like 'p', 'b', 'k'). A standard z-score threshold would be fooled by the skewed distribution.

The skewness of the flux distribution is measured. The z-threshold is adjusted:
```
flux_z_thresh = 5.5 + max(0, min(3.0, (skewness − 3.0) × 0.5))
```
WhatsApp voice notes (skewness ≈ 5–6) get threshold ≈ 6.5.
Studio speech (skewness ≈ 2–3) keeps threshold 5.5.

*Mechanism 2 — Duration-proportional minimum event count:*
Background speech variation naturally produces roughly 1–2 spectral events per minute at any threshold. The minimum number of events required to flag an issue is:
```
min_events_dynamic = max(caller_min, ceil(duration_minutes × 2.5))
```
A 3-minute file must have 8+ events to be flagged. A 1-minute file needs 3. This prevents a long file from accumulating background variation to the flag threshold simply because it is long.

*Mechanism 3 — Rate cap for inconclusive files:*
If a file has zero events above the adaptive threshold, but raw event rate (at the base threshold) exceeds 4 events/minute, the file is flagged as "too noisy to assess" and confidence is returned as `None` (excluded from scoring). This handles recordings of crowded environments or noisy conditions where reliable analysis is impossible.

**RMS energy jumps:**
In parallel with spectral flux, per-frame RMS energy is computed and z-scored. Sudden energy jumps (z-score above 4.0) indicate a section where volume was dramatically altered — either boosted to amplify a soft statement or reduced to hide something.

### Sub-module 5 — Duplicate Detection

**Method:** `_analyze_duplicates()`

This module answers: **were any segments copied and pasted within this recording?**

**Segment fingerprinting:**
The audio is divided into 5-second windows with 1-second stride (overlapping). Each window is fingerprinted with a 20-coefficient MFCC mean vector.

**Similarity computation:**
Cosine similarity is computed between all window pairs that are more than 5 seconds apart (adjacent windows share content by design; only distant pairs are suspicious).

**Adaptive threshold:**

For short files or files with few comparison pairs, a fixed threshold of 0.995 is used — very high, to avoid false positives from naturally similar-sounding speech passages.

For long files (60+ seconds, 20+ comparison pairs), a MAD-based adaptive threshold is computed:
```
threshold = clip(median_similarity + 3.5 × MAD_std, 0.970, 0.9995)
```
This adapts to the file's own similarity distribution. If the speaker naturally repeats phrases (high baseline similarity), the threshold rises. If the file is highly varied, the threshold stays low.

A minimum of 3 confirmed duplicate pairs is required before an issue is raised — noise guard to prevent single-pair coincidental matches from flagging.

**What the finding means:**
Two or more confirmed duplicate segments that are far apart in time strongly suggests copy-paste manipulation. The most common forensic scenario: copying a "yes" or a single-word response from one context and pasting it into a fabricated conversation.

### Sub-module 0 — Audio Type Classification

**Method:** `_detect_audio_type()`

Before running any analysis, audio is classified. Three features are computed:

**Feature 1 — Zero Crossing Rate (ZCR):**
How frequently the waveform crosses zero per frame. Speech has moderate ZCR (voiced sounds are low ZCR, fricatives are higher). Music with cymbals, guitars, and synthesizers crosses zero far more frequently.

**Feature 2 — RMS coefficient of variation (std/mean):**
Music has strong periodic beats → high amplitude variation (high CV). Speech has gentler pauses → lower CV.

**Feature 3 — High-frequency energy ratio:**
Phone codecs hard-limit bandwidth to 3.4 kHz (narrowband) or 8 kHz (wideband). After resampling to 22050 Hz, phone speech has very little energy above 4 kHz. Music and studio speech both have substantial high-frequency content.

**Classification:**
- If all three indicators fire (`music_votes >= 3`) → `music`
- If HF ratio is very low AND ZCR is low → `phone_speech`
- Otherwise → `speech`

---

## 6. The Scoring System — How Verdicts Are Computed

**File:** `tasks.py → compute_forensic_confidence()`

### The formula

The final audio forensics confidence score is computed as:

```
score = (weighted_average × 0.5) + (worst_module_confidence × 0.5) − (flagged_count × 0.12)
```

**Three components:**

**Component 1 — Weighted average (breadth signal):**
Module confidences are combined with weights:
- Integrity: 30%
- Speaker match: 25%
- ENF: 20%
- Events: 15%
- Duplicates: 10%

Modules with `confidence = None` are excluded and weights are renormalized to sum to 1.0.

**Component 2 — Worst module confidence (severity signal):**
The minimum confidence value across all active modules. This captures severity: if one module is very suspicious, the overall score must reflect that even if all other modules are clean.

**Component 3 — Flagged module deduction:**
Each module that raised at least one issue (non-empty `issues` list) deducts 12 percentage points. In forensics, even a single confirmed indicator of manipulation is significant.

### What each score range means

| Score | Verdict | Meaning |
|---|---|---|
| 85–100% | Likely Authentic | All modules returned high confidence, no issues flagged |
| 65–84% | Likely Authentic (with notes) | Clean but some modules were inconclusive |
| 35–64% | Review Recommended | One or more modules flagged something |
| 0–34% | Likely Tampered | Multiple modules flagging, or one module with very low confidence |

### Why the formula uses both average and minimum

A pure weighted average allows three clean modules (integrity 30%, ENF 20%, duplicates 10% = 60%) to drown out one suspicious module (events at 0.10 confidence). A speaker-swapped file might score 0.78 with a pure average — which reads as "likely authentic." That is forensically wrong.

The minimum confidence term prevents this: even if all other modules are at 1.0, a single module at 0.10 forces:
```
score = (0.90 × 0.5) + (0.10 × 0.5) − 0.12 = 0.38 → Review Recommended
```

### The video frame analysis verdict (different formula)

For video (frame analysis), the verdict uses a point-accumulation model:

```
temporal_score = sum of severity points from temporal findings (capped at 100)
spatial_score  = sum of severity points from spatial findings (capped at 100)
confidence     = (temporal_score × 0.4) + (spatial_score × 0.6)
```

Spatial (per-frame pixel manipulation) findings are weighted more heavily (0.6) than temporal (cuts and freezes) findings because false-positive rates for temporal anomalies are slightly higher.

**Verdict thresholds:**
- `>= 80% AND multiple HIGH-severity signals across both temporal and spatial` → `LIKELY_TAMPERED`
- `>= 30%` → `REVIEW_RECOMMENDED`
- `< 30%` → `LIKELY_AUTHENTIC`

---

## 7. Testing Guide — What to Test and How

### Building a test set

You cannot verify that a forensics system works without a **controlled test set** — files where you know the ground truth (whether and how they were manipulated).

**Recommended test set structure:**

```
test_set/
├── clean/
│   ├── studio_speech_30s.wav
│   ├── studio_speech_3min.wav
│   ├── phone_call_1min.mp3
│   ├── whatsapp_voice_30s.m4a
│   ├── whatsapp_voice_2min.m4a
│   └── outdoor_recording.wav
└── manipulated/
    ├── deleted_segment/
    │   ├── studio_speech_3min_deleted_4s.wav    ← 4-second cut at 1:20
    │   └── studio_speech_3min_deleted_10s.wav   ← 10-second cut at 2:00
    ├── copy_paste/
    │   └── phone_call_pasted_yes.mp3             ← "yes" at 0:30 pasted to 1:45
    ├── speaker_swap/
    │   └── two_speakers_joined.wav               ← speaker A first half, speaker B second
    ├── gain_clip/
    │   └── boosted_clipping.wav                  ← +20dB on middle 30 seconds
    └── silence_injection/
        └── silence_over_cut.wav                  ← 5s silence over deleted section
```

**How to create each manipulated file:**

*Deleted segment:* Open in Audacity, select the region to remove, press Delete, export.

*Copy-paste:* In Audacity, select 3 seconds at position A, Copy, click at position B (10+ seconds away), Paste, export.

*Speaker swap:* Record two people saying different things. In Audacity, truncate person A's recording and append person B's. Export.

*Gain clipping:* Select a region, Effect → Amplify → amplify by +20dB without allowing clipping protection. Export.

*Silence injection:* Select a region, Generate → Silence. Export.

### What to check in results

For each manipulated file, verify that:

| Manipulation | Module that should fire | Field to check |
|---|---|---|
| Deleted segment | Integrity | `issues` contains "anomalous silence gap" |
| Copy-paste | Duplicates | `issues` contains "confirmed duplicate pair(s)" |
| Speaker swap | Speaker match | `min_similarity` < 0.5, issue about speaker change |
| Gain clipping | Integrity | `clip_ratio` > 0.001, clipping issue |
| Splice cut | Events | `splice_candidates` > 0, spectral flux anomaly issue |
| ENF splice | ENF | `phase_discontinuities` > 0 |

For each clean file, verify that:
- No module raises issues
- Confidence for each active module is above 0.80
- Overall score is in the "Likely Authentic" range (above 85%)

### Red flags to watch for (system health checks)

**All files returning the same score (~69%)**
Means a "neutral" module is always returning `confidence = 0.5` and dragging down `min_conf`. Check which module is returning 0.5 on every file. If ENF returns 0.5 for "not detected," it should return `None` instead.

**Very high silence ratio on clean phone/WhatsApp files**
Check that silence detection is using per-frame RMS, not per-sample amplitude. Per-sample amplitude will flag zero-crossings inside voiced speech as silence.

**Events module flagging clean short files**
Check that `min_events_dynamic` is at least `ceil(duration_minutes × 2.5)`. A 30-second file should require at least 2 events, not 3 (the absolute minimum might exceed the duration-proportional minimum).

**Speaker consistency returning 0.8 for "insufficient segments"**
Short files (under 10 seconds) and files where all segments are filtered as silent will return a neutral 0.8 with note "insufficient segments." This is correct — the module cannot assess. But if longer files (30s+) are returning this, check that the voiced segment filter is not being too aggressive.

**Duplicate detection flagging clean single-speaker recordings**
Check the similarity threshold. If the threshold is too low (below 0.990), similar-sounding syllables from the same speaker will match. Raise to 0.995.

### Per-module verification tests

**Integrity module:**
- Submit a clipped WAV (record near microphone, loud): expect `clip_ratio > 0` in results.
- Submit a WAV with 6 seconds of silence manually inserted: expect anomalous silence gap finding.
- Submit a clean quiet room recording: expect `silence_ratio` above 0.20 but no issue (short gaps are natural).

**Speaker consistency module:**
- Concatenate yourself saying a sentence with a recording of a different person. Submit.
- Expected: `min_similarity` below 0.6.
- Submit a single-speaker 2-minute recording.
- Expected: `mean_similarity` above 0.80, `min_similarity` above 0.65, no issues.

**ENF module:**
- Submit a recording made indoors near electrical equipment (laptop fan, lights).
- If ENF signal present: `enf_power` > 0, `enf_band_hz` field populated with a frequency.
- Submit an outdoor recording made far from power lines.
- Expected: `confidence = None`, note about ENF not detected.

**Events module:**
- Submit a clean 1-minute voice note, no editing.
- Expected: `splice_candidates = 0`, `noise_level_jumps = 0`, `confidence` near 1.0.
- Submit the same file with a hard cut in the middle (delete 0.5 seconds, no crossfade).
- Expected: `splice_candidates >= 1`.

**Duplicate module:**
- Copy 5 seconds from one part of a recording and paste it 30 seconds later. Submit.
- Expected: `confirmed_pairs > 0`, issue about duplicate segments.
- Submit a clean recording where the speaker repeats themselves naturally.
- Expected: Similarity may be high but below the adaptive threshold — no issue.

---

## 8. Reading Your Results — Field-by-Field Reference

### Audio forensics result JSON structure

```json
{
  "evidence_id": "AUD-20260302-0001",
  "case_id": "CASE-001",
  "examiner": "System Examiner",
  "duration_seconds": 127.4,
  "sample_rate": 22050,
  "audio_type": "phone_speech",

  "integrity": {
    "confidence": 0.85,
    "file_hash_sha256": "a3f1...",
    "clip_ratio": 0.000012,
    "dc_offset": 0.002,
    "silence_ratio": 0.318,
    "issues": []
  },

  "speaker_match": {
    "confidence": 0.791,
    "segments_analyzed": 18,
    "mean_similarity": 0.791,
    "min_similarity": 0.712,
    "issues": []
  },

  "enf": {
    "confidence": null,
    "enf_power": 0.0,
    "phase_discontinuities": 0,
    "note": "ENF signal not detected — likely a digital source or outdoor recording.",
    "issues": []
  },

  "events": {
    "confidence": 0.9,
    "splice_candidates": 0,
    "noise_level_jumps": 0,
    "events_found": 0,
    "issues": []
  },

  "duplicates": {
    "confidence": 0.95,
    "segments_compared": 142,
    "confirmed_pairs": 0,
    "mean_similarity": 0.831,
    "min_similarity": 0.551,
    "issues": []
  }
}
```

### Field-by-field meaning

**Top level:**
- `duration_seconds`: Total length of the audio after loading and resampling to 22050 Hz. Verify this matches the file's actual duration.
- `audio_type`: How the system classified the audio. If this is wrong (e.g., your voice note is classified as music), the results are unreliable. Classification errors are usually caused by unusual recording conditions.

**Integrity:**
- `confidence`: 1.0 minus penalties. 0.85 means penalties totalling 0.15 were applied.
- `file_hash_sha256`: SHA-256 of the file bytes. Store this alongside the evidence record.
- `clip_ratio`: 0.000012 = 0.0012% of samples are clipped. Under 0.001 is generally fine.
- `dc_offset`: 0.002 is very low and healthy. Above 0.05 is concerning.
- `silence_ratio`: 0.318 = 31.8% of frames are below the noise floor. This is normal for speech with natural pauses. It does not mean 31.8% of the file is silent — it means 31.8% of analysis frames (each 23ms) fall below the noise floor threshold.
- `issues`: Empty array = no problems. If populated, read the strings carefully — each one explains exactly what was found.

**Speaker match:**
- `segments_analyzed`: How many 5-second voiced segments were extracted and compared. Below 2 means the file is too short or too quiet for analysis — `confidence` will be 0.8 (neutral, not meaningful).
- `mean_similarity`: Average cosine similarity across all segment pairs. Above 0.80 = same speaker consistently. 0.70–0.80 = same speaker, possibly different conditions. Below 0.70 = suspicious.
- `min_similarity`: The worst single pair. If `min_similarity < 0.50`, two segments were very different from each other — strong indicator of speaker change.

**ENF:**
- `confidence: null`: The module could not assess this file — ENF signal not present. This is excluded from the overall score. It does NOT mean the file is suspicious.
- `enf_power`: Energy in the ENF band. Near 0.0 = no mains hum detected.
- `phase_discontinuities`: Number of phase jump events detected. 0 = clean. Above 0 = potential splice points at times of the jumps.
- `enf_band_hz`: If ENF was detected, this shows which frequency band (e.g., "119.5–120.5" for 120 Hz harmonic). Cross-reference with your region: Europe/Asia uses 50 Hz, Americas use 60 Hz.

**Events:**
- `splice_candidates`: Number of spectral flux anomalies that passed the adaptive filter and minimum count threshold. 0 = no splice points detected.
- `noise_level_jumps`: Number of RMS energy jumps. 0 = consistent volume. Above 0 = abrupt volume change detected.
- `events_found`: Total of splice_candidates + noise_level_jumps.
- `confidence`: 1.0 means no events. Decreases 0.25 per event per minute.

**Duplicates:**
- `segments_compared`: Number of non-adjacent segment pairs compared. This should be well above 20 for files over 1 minute.
- `confirmed_pairs`: Number of pairs above the similarity threshold and minimum spatial separation. Anything above 0 (with the 3-pair minimum guard) indicates copy-paste.
- `mean_similarity`: The average similarity of all compared pairs. For natural speech this is typically 0.60–0.85. Very high (above 0.95) baseline similarity may indicate the speaker repeats stock phrases often.

### How to interpret the overall verdict

**Likely Authentic (score > 85%):**
All active modules returned high confidence and none raised issues. Use this as a positive indicator, not a guarantee. The system is a screening tool — it cannot detect every form of manipulation, particularly sophisticated manipulation done with professional tools.

**Review Recommended (score 35–84%):**
One or more modules raised flags. Read the `issues` array in each module carefully. Determine whether the flag is:
- From a single module (lower concern — might be a false positive)
- From multiple modules pointing to the same timestamp (much higher concern)
- Consistent with the file type's known limitations (phone codec artifacts vs genuine splice)

**Likely Tampered (score < 35%):**
Multiple modules raised issues or one module returned a very low confidence. This warrants detailed manual review by a qualified analyst. Do not rely solely on the system verdict for decisions with legal or professional consequences.

---

*End of document. This textbook covers every module in the system as implemented in the current codebase. For threshold values, refer to `forensic_config.py`. For infrastructure configuration, refer to `tasks.py` and `app.py`.*
