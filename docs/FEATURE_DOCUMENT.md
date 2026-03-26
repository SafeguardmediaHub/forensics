# SafeguardMedia — Feature Document

**Document type:** Product & Compliance Reference
**Audience:** Product owners, auditors, legal teams, investigators, stakeholders
**Status:** Version 1.0 — Phase 1

---

## Executive Summary

SafeguardMedia is a unified digital media forensics platform designed to detect
tampering, manipulation, and inauthenticity in video, audio, and image files.

The platform combines four independent forensic engines into a single API,
enabling investigators, legal teams, and content verifiers to submit a media
file and receive a structured, evidence-grade analysis report — including a
verdict, confidence score, and a breakdown of every anomaly detected.

The platform is built for professional use. It does not modify original files,
maintains a full audit trail, and produces findings that are traceable to
specific forensic techniques.

---

## Core Principles

### 1. Non-Destructive Analysis
The platform never modifies the original file submitted for analysis. All
processing is performed on a working copy. The original file's cryptographic
hash (SHA-256) is recorded before any analysis begins, providing proof that
the evidence was not altered by the system.

### 2. Multi-Module Corroboration
No single technique determines the outcome. Each analysis runs multiple
independent forensic modules. A verdict requires corroboration — the more
independent modules that flag an anomaly, the higher the confidence in the
result. A single module flagging an issue alone produces a lower-confidence
result than three modules independently detecting the same recording.

### 3. Transparent Scoring
Every result includes a breakdown of what each module found, what score it
produced, and what weight it carried in the final verdict. There are no
black-box outputs. Every conclusion is traceable to a specific measurement.

### 4. Pre-Calibration Disclosure
All results include a notice when the system is operating on default
(pre-calibration) thresholds. This is standard forensic practice —
thresholds tuned to a specific corpus of known authentic and tampered files
produce more accurate results. Users are informed when calibration has not
been applied.

---

## Platform Architecture

SafeguardMedia exposes one unified API with four analysis endpoints.
Each endpoint routes to a dedicated forensic engine:

| Endpoint | Engine | Media Type |
|---|---|---|
| `POST /api/v1/video/analyze` | Video Forensics (VFF) | Video files |
| `POST /api/v1/audio/analyze` | Audio Forensics (AFF) | Audio files |
| `POST /api/v1/image/analyze` | Visual Forensics | Images |
| `POST /api/v1/frames/analyze` | Frame Analysis | Video files |

All four engines share the same verdict scale and the same response structure,
allowing results from different media types to be compared and reported
consistently.

---

## Verdict Scale

All four engines produce a verdict on the same five-point scale:

| Verdict | Meaning |
|---|---|
| **Likely Authentic** | No credible evidence of manipulation found. All forensic signals are consistent with a genuine, unedited recording. |
| **Inconclusive** | Anomalies are present but may have innocent explanations (e.g. re-encoding by a messaging app, compression during upload). Further review recommended. |
| **Likely Tampered** | Multiple independent indicators of manipulation detected. Moderate to high confidence that the file has been edited or fabricated. |
| **Tampered** | Strong, corroborated evidence of manipulation. High confidence finding. |

Each verdict is accompanied by:
- A **fused probability score** (0.0 – 1.0) — the raw numerical basis for the verdict
- A **confidence rating** — how certain the system is in its verdict, separate from the score
- A **list of findings** — specific anomalies detected, each with its own severity and confidence

---

## Feature 1 — Video Forensics (VFF Engine)

### What It Does
Analyses a video file for signs of tampering, fabrication, or manipulation.
Six independent forensic modules examine the video from different physical
and technical angles simultaneously.

### Who Uses It
- Investigators verifying authenticity of video evidence
- Legal teams assessing whether footage has been edited
- Journalists verifying the provenance of video content
- Platforms screening uploaded video for deepfake or synthetic content

### Accepted Formats
MP4, MOV, AVI, MKV, MTS, M4V

### Forensic Modules

**1. Metadata Analysis**
Examines the file's internal information tags — creation date, camera model,
encoder software, container format. Inconsistencies between these tags (for
example, a file claiming to be from a camera model that uses a different codec)
are flagged as potential indicators of post-processing or re-assembly.

**2. Compression Artifact Analysis**
Every time a video is compressed (saved in a lossy format), it leaves a
characteristic fingerprint. If a video has been edited and re-saved, it will
show double-compression artefacts — the fingerprint of being compressed twice.
This module detects these artefacts using DCT (Discrete Cosine Transform)
analysis and bitrate consistency checking.

**3. Noise & Camera Fingerprint (PRNU)**
Every camera sensor has a unique, microscopic imperfection pattern — like a
fingerprint — that it imprints on every frame it captures. This is called
PRNU (Photo Response Non-Uniformity). If frames from a different camera (or
a synthetic source) are inserted into a video, the PRNU pattern changes at
the point of insertion. This module detects those changes.

**4. Temporal Integrity**
Analyses the sequence of frames over time. A genuine video has natural,
continuous motion. This module detects: duplicate frames (suggesting a
looped or frozen insert), abrupt motion discontinuities (suggesting a cut),
and optical flow anomalies (suggesting frames were replaced or reordered).

**5. Lighting & Physics Consistency**
Light and shadows in a genuine scene behave according to the laws of physics.
Light sources do not teleport. Shadow directions do not change instantaneously.
This module analyses illumination and shadow consistency across frames. Sudden,
physically impossible changes in lighting are flagged as potential indicators
of compositing or scene replacement.

**6. Audio-Visual Sync**
Analyses whether the audio track is in sync with the visual content, and
whether the acoustic environment in the audio is consistent throughout the
video. A dubbed or replaced audio track typically shows synchronisation gaps
and acoustic environment changes that are detected by this module.

### Output
```
verdict:            likely_authentic | inconclusive | likely_tampered | tampered
verdict_label:      Human-readable label
fused_probability:  0.0 – 1.0
confidence:         0.0 – 1.0
n_elevated_modules: Number of modules that flagged anomalies
module_scores:      {metadata, compression, noise, temporal, lighting, audio}
findings:           List of specific anomalies with severity and description
video:              File properties (codec, resolution, frame rate, duration, SHA-256)
```

---

## Feature 2 — Audio Forensics (AFF Engine)

### What It Does
Analyses an audio file for signs of splicing, content deletion, speaker
substitution, re-recording, or any form of post-production editing.
Six independent forensic modules examine the audio signal from different
physical and technical perspectives.

### Who Uses It
- Investigators verifying the authenticity of recorded calls, interviews, or statements
- Legal teams assessing whether an audio recording has been edited
- Courts and tribunals evaluating audio evidence admissibility
- Journalists verifying the authenticity of leaked recordings

### Accepted Formats
WAV, MP3, AAC, M4A, OGG, FLAC, MP4 (audio track), 3GP, AMR

### Forensic Modules

**1. Metadata & Format Integrity**
Examines the file's container, codec declarations, encoder strings, and
timestamps. A genuine recording has consistent, coherent metadata. A file
that has been opened in an audio editor and re-exported typically shows
metadata inconsistencies — a different encoder string, a timestamp that
does not match the declared creation date, or a container/codec mismatch.

**2. ENF Analysis (Electric Network Frequency) — The Crown Jewel**
This is the most forensically robust technique in audio analysis.

Electric mains power (50Hz in Nigeria, Europe, and Africa; 60Hz in the USA)
creates a faint but measurable hum in any recording made near electrical
equipment — phones, laptops, and most indoor environments. Critically, the
mains frequency fluctuates slightly from moment to moment in a pattern that
is unique to each instant in time, like an invisible timestamp embedded in
every recording.

If a recording was assembled from two separate sessions (even in the same room,
at the same volume), the ENF signal will be discontinuous at the edit point —
a phase jump that is mathematically impossible in a genuine single-session
recording. This is the audio forensics equivalent of a splice mark.

When an ENF discontinuity is detected, the system can pinpoint the exact
timestamp of the edit to within a fraction of a second.

**Note:** ENF analysis requires that mains hum is present in the recording.
Outdoor recordings, battery-powered devices in isolated environments, and
recordings made far from electrical equipment may not contain ENF. When ENF
is absent, the system redistributes its analytical weight to the remaining
modules and discloses this in the result.

**3. Noise Floor Consistency**
Every recording environment has a characteristic background noise profile —
a statistical fingerprint of the microphone, room acoustics, ambient sound,
and electrical environment. A genuine single-session recording has a
consistent noise floor throughout. An edited recording shows changes in the
noise floor at the edit point — even when the editing is otherwise seamless
to the ear.

This module compares the noise floor profile across the recording in 1-second
windows and detects statistical discontinuities.

**4. Compression Artifact Analysis**
Lossy audio codecs (MP3, AAC, AMR) leave characteristic spectral fingerprints.
Each codec at each quality setting has a specific frequency ceiling above which
no audio content is encoded. If a recording was edited and different segments
were encoded at different quality levels or with different codecs, those
segments will have different spectral ceilings — detectable even after
the file is re-exported.

**5. Reverberation & Room Acoustics**
Every room has a characteristic reverberation — sound bounces off walls,
floors, and furniture and decays at a rate determined by the room's size and
materials. This decay time is called RT60. A small office might have an RT60
of 0.3 seconds; a conference room 0.6 seconds; a hall 1.5 seconds.

If content from a recording made in a different room is inserted, the RT60
changes at the splice point — even if the content sounds acoustically similar
to the ear. This module tracks RT60 consistency throughout the recording.

**6. Voice & Speaker Continuity**
Every speaker has a unique vocal tract — a physical tube of unique length and
shape that determines the resonant frequencies (formants) of their voice, as
well as their characteristic pitch range. If a different person's voice is
inserted into a recording, both the pitch characteristics and the formant
structure change at the splice point. This module tracks these characteristics
continuously and flags discontinuities.

**Note:** This module only runs when voiced speech is present. Music, silence,
or non-speech audio causes the module to skip gracefully, with its weight
redistributed to other modules.

### Output
```
verdict:              likely_authentic | inconclusive | likely_tampered | tampered
verdict_label:        Human-readable label
fused_probability:    0.0 – 1.0
confidence:           0.0 – 1.0
elevated_modules:     Modules that flagged anomalies
skipped_modules:      Modules that could not run (e.g. no ENF, no speech)
corroboration_factor: Multiplier applied when multiple modules agree
has_conflict:         Whether modules produced contradictory findings
conflict_description: Plain-language description of any conflict
module_scores:        {metadata, enf, noise, compression, reverberation, voice}
findings:             List of anomalies with severity, confidence, and timestamp
audio:                File properties (codec, duration, sample rate, bitrate)
```

---

## Feature 3 — Image Forensics (Visual Forensics Engine)

### What It Does
Analyses a still image for signs of manipulation, compositing, AI generation,
or any form of post-processing that alters the image's content or integrity.

### Who Uses It
- Investigators verifying the authenticity of photographic evidence
- Insurance companies assessing whether submitted photo evidence has been altered
- Platforms moderating user-generated content
- Journalists verifying the provenance of photographic reports
- Legal teams assessing documentary photo evidence

### Accepted Formats
JPG/JPEG, PNG, WebP, BMP, TIFF

### Forensic Modules

**1. ELA — Error Level Analysis**
When a JPEG image is saved, it introduces a specific pattern of compression
artefacts. If part of an image is pasted in from a different source (or saved
at a different compression quality), that region will have a different error
level than the surrounding image. ELA makes these differences visible and
measurable. Regions with significantly different error levels than the rest
of the image are flagged as potential manipulation zones.

**2. Noise Inconsistency Analysis**
Natural photographs have a consistent, fine-grained noise texture across the
image — a result of the camera sensor's behaviour in varying light conditions.
When content is copied from another image (which has a different noise
signature), composited using AI, or artificially generated, the noise
texture is inconsistent. This module measures noise texture consistency
across image regions and flags areas that are statistically anomalous.

**3. Copy-Move Detection**
A common manipulation technique is to copy a region of an image and paste it
elsewhere within the same image — for example, to clone out an object, cover
a face, or duplicate a person. This module uses SIFT (Scale-Invariant Feature
Transform) and DCT-based methods to detect copied and pasted regions, even
when they have been scaled, rotated, or slightly altered.

**4. JPEG Compression History**
Analyses whether the image shows evidence of having been saved multiple times
at different compression levels — a common artefact of editing and re-exporting.
Double or multiple compression cycles produce characteristic block boundary
artefacts that are detectable even after subsequent saves.

**5. EXIF & Metadata Analysis**
Reads and cross-checks the image's embedded metadata — camera model, lens
information, GPS coordinates, capture timestamp, software used, and more.
Inconsistencies (such as a raw file claiming to have been edited by a specific
app that does not produce that file format, or a GPS location that does not
match the claimed location) are flagged.

### Output
```
verdict:              Likely Authentic | Review Recommended | Likely Tampered
tampering_likelihood: 0 – 100 (percentage)
confidence:           High | Medium | Low
module_scores:        {ela, noise, copy_move, jpeg_compression}
findings:             List of anomalies
metadata:             EXIF data extracted from the image
verification:         File integrity hashes
```

---

## Feature 4 — Frame Analysis Engine

### What It Does
Performs a deep, frame-by-frame forensic analysis of a video file. Where the
Video Forensics engine (VFF) analyses the video holistically using six broad
modules, the Frame Analysis engine examines the video at the level of
individual frames — looking for spatial anomalies within frames and temporal
anomalies between them.

This engine is particularly effective at detecting deepfakes, face swaps,
AI-generated video content, and subtle frame-level manipulations that may
not be detectable by broader video analysis.

### Who Uses It
- Investigators analysing video evidence for subtle or technically sophisticated manipulation
- Deepfake detection workflows
- Platforms screening for AI-generated video content
- Cases where VFF returns inconclusive and a more granular analysis is required

### Accepted Formats
MP4, MOV, AVI, MKV, MTS, M4V

### What It Analyses

**Temporal Analysis (Between Frames)**
Examines how the video changes from frame to frame:
- **Abrupt transitions** — sudden, discontinuous changes between frames that
  suggest a cut or splice
- **Frame duplication** — identical or near-identical frames appearing in
  sequence, suggesting a frozen insert or loop
- **Quality drift** — gradual degradation or change in visual quality that
  is inconsistent with the recording conditions
- **Motion inconsistency** — motion patterns that are physically implausible
  given the camera movement and scene content

**Spatial Analysis (Within Frames)**
Examines each frame's internal structure:
- **Visual anomalies** — regions of frames that are inconsistent with the
  surrounding content in noise, compression, or texture
- **Blending artefacts** — visible or measurable seams where content from
  different sources has been composited
- **ELA at frame level** — error level analysis applied to individual video
  frames, detecting compression inconsistencies within a frame

**Scene Detection**
Identifies scene changes and verifies that transitions are natural. Unexpected
scene changes, or scene changes that do not correspond to natural cuts in the
content, are flagged.

### Output
```
verdict:              Human-readable verdict string
tampering_confidence: 0.0 – 100.0 (percentage)
tampering_type:       Classification of the detected manipulation type
verdict_explanation:  Plain-language explanation of the conclusion
temporal_findings:    Count of between-frame anomalies detected
spatial_findings:     Count of within-frame anomalies detected
findings:             List of specific findings with type, severity, description,
                      frame range, and timestamp
```

---

## Findings Severity Scale

All four engines classify individual findings on the same severity scale:

| Severity | Meaning |
|---|---|
| **INFO** | Noted but not suspicious. Expected behaviour, included for completeness. |
| **LOW** | Minor anomaly. Likely has an innocent explanation (e.g. re-encoding by a messaging app). |
| **MEDIUM** | Notable anomaly. Warrants attention. Could be manipulation or could be benign. |
| **HIGH** | Strong indicator of tampering. Unlikely to have an innocent explanation. |
| **CRITICAL** | Near-certain evidence of tampering. Finding is technically unambiguous. |

---

## Audit Trail

For video analysis (VFF engine), the platform maintains a tamper-evident audit
trail for every case. Each audit log records:

- The SHA-256 and MD5 hash of the original file (recorded before any processing)
- Every operation performed on the file, with timestamps
- The identity of the analysis modules run
- The outcome of each step

Audit logs are signed with HMAC to detect any post-hoc modification of the
log itself. Each case is assigned a unique UUID that links the audit log,
the analysis result, and any generated reports.

---

## Chain of Custody

The platform is designed around the principle of chain of custody — a legal
and investigative requirement that evidence be handled in a way that proves
it was not altered between collection and presentation.

Key chain-of-custody features:
- **Pre-analysis hashing** — SHA-256 hash recorded before any processing begins
- **Working copy isolation** — analysis is performed on a copy, never the original
- **Immutable audit log** — every operation is logged with timestamps
- **Report traceability** — every finding in a report is traceable to the specific
  module and measurement that produced it

---

## Limitations & Caveats

The following limitations apply to all analyses performed by SafeguardMedia.
These should be disclosed in any formal use of the platform's outputs.

1. **Pre-calibration thresholds.** Default thresholds have not been tuned to a
   specific corpus of authentic and tampered media. Calibrated thresholds
   (tuned to a labelled dataset) produce more accurate results. Results
   produced with default thresholds should be treated as investigative leads
   rather than definitive conclusions.

2. **ENF requires indoor recordings.** ENF analysis (the most forensically
   robust audio technique) requires that mains electrical hum is present in
   the recording. Outdoor recordings and battery-isolated devices will not
   have ENF. The system discloses this in the result.

3. **PRNU requires sufficient frames.** Camera fingerprint detection (PRNU)
   requires sufficient frames for statistical reliability. Very short videos
   or low-resolution recordings may produce low-confidence PRNU results.

4. **Compression by messaging apps.** Files forwarded via WhatsApp, Telegram,
   or similar platforms are automatically re-encoded, which introduces
   compression artefacts and metadata changes that can produce false positive
   indicators. The system's thresholds account for this, but it should be
   considered when interpreting results.

5. **AI-generated content.** Detection of AI-generated (synthetic) media is
   improving but is not infallible. Sophisticated generative models may
   produce content that scores as authentic on current metrics.

6. **Expert interpretation required.** The results of this platform are
   analytical tools, not conclusions. Findings should be interpreted by
   a qualified forensic examiner in the context of the full investigation.
   The platform's output is not a substitute for expert forensic testimony.

7. **Results are probabilistic.** All verdicts are probabilistic assessments
   based on measurable signals, not absolute determinations. Confidence levels
   and caveats must be included in any formal presentation of results.

---

## Glossary

| Term | Definition |
|---|---|
| **DCT** | Discrete Cosine Transform — the mathematical operation underlying JPEG and most video compression. Used to detect double-compression artefacts. |
| **ELA** | Error Level Analysis — a technique that makes compression artefact inconsistencies visible by re-compressing an image and measuring differences. |
| **ENF** | Electric Network Frequency — the mains power frequency (50/60Hz) embedded in audio recordings, used as a timestamp and splice detector. |
| **Fused Probability** | The combined output of all forensic modules after weighting and corroboration adjustment. The numerical basis for the verdict. |
| **HMAC** | Hash-based Message Authentication Code — a cryptographic signature used to verify that audit logs have not been tampered with. |
| **MFCC** | Mel-Frequency Cepstral Coefficients — a mathematical representation of the acoustic properties of a sound, used in speaker analysis. |
| **PRNU** | Photo Response Non-Uniformity — the unique sensor noise pattern of a camera, used as a device fingerprint in video forensics. |
| **RT60** | Reverberation Time — the time it takes for sound to decay by 60dB after the source stops. Used to characterise room acoustics. |
| **SHA-256** | A cryptographic hash function that produces a unique 64-character fingerprint for any file. Used for integrity verification. |
| **SIFT** | Scale-Invariant Feature Transform — an algorithm for detecting and matching visual features in images, used in copy-move detection. |
| **Verdict** | The platform's top-level conclusion about a file's authenticity, expressed on a five-point scale from Likely Authentic to Tampered. |

---

_SafeguardMedia — Built for forensic professionals. Use responsibly._
_Document version 1.0 — corresponds to API version 0.1.0, Phase 1._
