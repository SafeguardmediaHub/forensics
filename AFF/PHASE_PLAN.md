# AudioForensics — Full Phase Plan

> **Discipline:** Same engineering rigour as VideoForensics.
> Same phased architecture, modular design, calibration infrastructure,
> test suite, API, and UI — built from scratch around what audio forensics
> actually is as a field.

---

## Stack

| Tool | Role |
|---|---|
| Python 3 | Core language |
| numpy | Signal arrays, FFT, linear algebra |
| scipy.signal | Filtering, STFT, Welch PSD, Hilbert |
| scipy.io.wavfile | WAV read/write |
| sklearn | Calibration, scoring metrics |
| matplotlib | Charts for PDF reports |
| reportlab | PDF generation |
| flask | HTTP API and Web UI |
| ffmpeg / ffprobe | Audio extraction and format inspection |

No librosa — all DSP built from primitives for full control and no hidden magic.

---

## Directory Structure

```
audioforensics/
├── core/
│   ├── models.py           # Shared dataclasses — AudioCase, AudioProfile, Finding
│   ├── ingestion/          # Phase 1 — pipeline entry point
│   ├── test_generator.py   # Phase 0 — synthetic test audio
│   └── check_deps.py       # Phase 0 — dependency audit
├── modules/
│   ├── metadata/           # Phase 2
│   ├── enf/                # Phase 3
│   ├── noise/              # Phase 4
│   ├── compression/        # Phase 5
│   ├── reverberation/      # Phase 6
│   ├── voice/              # Phase 7
│   ├── fusion/             # Phase 8
│   └── reporting/          # Phase 9
├── api/                    # Phase 10
├── calibration/            # Phase 11
├── ui/                     # Phase 12
└── tests/
    └── test_all_phases.py  # Grows with each phase
```

---

## Key Differences from VideoForensics

Audio tampering detection works on fundamentally different signals.
The modules, the physics, and the detection strategies are all unique to audio.

| Aspect | VideoForensics | AudioForensics |
|---|---|---|
| Crown jewel technique | DCT double compression | ENF (Electric Network Frequency) |
| Primary signal | Pixel values, motion vectors | Waveform samples, spectral content |
| Splice evidence | Optical flow discontinuity | Phase jump in 50/60Hz mains hum |
| Room/environment | Lighting consistency | Reverberation & RT60 |
| Identity continuity | N/A | Vocal tract, fundamental frequency |
| Compression artifacts | DCT block boundaries | Spectral band cutoffs, MDCT patterns |
| Temporal resolution | Per-frame (~30fps) | Per-sample (16,000/s) |
| Test data | Mandelbrot fractal video | Synthetic speech with known splice points |

---

## ENF Grid

All analysis defaults to the **50Hz grid** (Nigeria, Europe, Africa, Asia).
The system also supports 60Hz (USA, Canada, parts of South America).
Grid is set at ingestion time and propagates to every module automatically.

| Grid | Nominal | Band | Harmonic |
|---|---|---|---|
| 50Hz (default) | 50.0 Hz | 49–51 Hz | 100 Hz |
| 60Hz | 60.0 Hz | 59–61 Hz | 120 Hz |

---

## Supported Input Formats

| Format | Extension | Notes |
|---|---|---|
| WAV | .wav | Any sample rate, any bit depth |
| MP3 | .mp3 | LAME tags preserved for forensics |
| AAC / M4A | .aac, .m4a | iTunes and Android recordings |
| OGG Vorbis | .ogg | Linux/web recordings |
| FLAC | .flac | Lossless — reference recordings |
| MP4 (audio) | .mp4 | Audio track extracted |
| 3GP / AMR | .3gp, .amr | Phone call recordings (narrowband) |

All formats converted to **16kHz mono 16-bit WAV** for analysis.
Original sample rate preserved separately for compression artifact detection.

---

## Verdict Scale

| Verdict | Fused Probability | Meaning |
|---|---|---|
| Likely Authentic | < 0.20 | No credible evidence of manipulation |
| Inconclusive | 0.20 – 0.40 | Anomalies present but explainable |
| Likely Tampered | 0.40 – 0.65 | Multiple indicators, moderate confidence |
| Tampered | > 0.65 | Strong corroborated evidence of editing |

Thresholds are pre-calibration defaults. Phase 11 fits them to a labelled corpus.

---

## Module Weight Hierarchy (Pre-Calibration)

ENF is weighted highest because it is the most forensically robust technique —
when mains hum is present and a phase jump is detected, that is mathematically
close to proof of a multi-session recording. Other modules provide supporting
evidence and cover cases where ENF is absent.

| Module | Default Weight | Notes |
|---|---|---|
| ENF | 0.30 | Highest — most court-admissible |
| Noise Floor | 0.22 | Works on all recordings |
| Compression | 0.18 | Strong on lossy formats |
| Reverberation | 0.12 | Requires sufficient speech |
| Voice | 0.10 | Requires voiced speech |
| Metadata | 0.08 | Lowest — easily forged |

Weights are uncertainty-adjusted at runtime: if ENF is absent (no mains hum
detected), its weight redistributes to the other modules proportionally.

---

## Phase 0 — Foundation & Data Models ✅ COMPLETE

**What it builds:**
- `AudioCase`, `AudioProfile`, `Finding`, `Severity`, `Verdict`, `ENFGrid`,
  `AudioCodec`, `TemporalLocation`, `ModuleScore`, `FusionResult` dataclasses
- `BaseAudioModule` interface that every detection module implements
- `TestAudioGenerator` — 7 synthetic test files with exact ground truth
- `check_deps.py` — environment verification

**Test files generated:**

| File | Ground Truth | What Module It Tests |
|---|---|---|
| `authentic_speech.wav` | No tampering | Baseline for all modules |
| `authentic_music.wav` | No tampering | Non-speech baseline |
| `spliced_enf.wav` | Phase jump at t=5.0s | ENF module |
| `spliced_noise.wav` | Noise floor change at t=5.0s | Noise module |
| `double_compressed.wav` | Double LP filter artifacts | Compression module |
| `room_change.wav` | RT60 change at t=5.0s | Reverberation module |
| `speaker_change.wav` | F0 jump 120Hz→220Hz at t=5.0s | Voice module |

**Tests:** 13/13 passing

---

## Phase 1 — Ingestion Pipeline

**What it builds:**
- `IngestionPipeline` class — single entry point for all analysis
- `ffprobe` extraction: codec, sample rate, channels, duration, bitrate,
  encoder string, container format, creation timestamp
- SHA-256 hash of original file (file identity for chain of custody)
- Partial hash of first 1MB (fast integrity check)
- `ffmpeg` extraction to lossless WAV — two outputs:
  - `wav_mono_16k`: 16kHz mono for analysis modules
  - `wav_full`: original sample rate, original channels for compression analysis
- Signal-level measurements computed directly from WAV samples:
  - DC offset (bias in the signal)
  - Peak amplitude and RMS level
  - Clipping detection (samples at ±full-scale)
  - Silence ratio (fraction of file below threshold)
  - Effective bandwidth (measured spectral cutoff)
- ENF grid auto-detection heuristic (50 vs 60Hz based on file metadata locale)

**Key design decision:**
Ingestion never modifies the original file. It produces read-only derivative
files in a temp directory. The SHA-256 of the original is locked in before
anything else happens.

**Tests:** T20–T29

---

## Phase 2 — Metadata & Format Integrity

**What it builds:**
- `MetadataModule` — analyses container and encoder metadata for anomalies

**What it detects:**

| Signal | How | What it means |
|---|---|---|
| Container/codec mismatch | Compare declared vs extracted | File re-wrapped after editing |
| Encoder fingerprint | Parse LAME tags, encoder strings | Unexpected encoder used |
| Timestamp gap | creation_time vs file mtime | File modified after creation |
| Bitrate inconsistency | Declared vs measured | VBR pattern in claimed CBR |
| Truncation | Expected vs actual samples | Recording cut short |
| Sample rate mismatch | Declared rate vs signal content | Resampling artifact |
| Missing metadata | Fields absent that should exist | Stripped by editing tool |

**Scoring:** Low base weight (metadata is easily forged). High confidence
bonus when multiple metadata signals agree.

**Tests:** T30–T39

---

## Phase 3 — ENF (Electric Network Frequency) Analysis

**The crown jewel of audio forensics.**

Electric mains hum (50Hz in Nigeria/Europe, 60Hz in USA) is embedded in
recordings made near electrical equipment — phones, laptops, and most
indoor environments. The grid frequency fluctuates slightly minute-to-minute
in a pattern that is unique to each moment in time, like a timestamp.
If a recording was assembled from two different sessions, the ENF phase
will be discontinuous at the cut point.

**What it builds:**
- `ENFModule` — full ENF extraction and analysis pipeline

**Signal processing pipeline:**
1. Bandpass filter: 8th-order Butterworth, 49–51Hz (or 59–61Hz)
2. STFT-based instantaneous frequency estimation (4096-sample window)
3. Hilbert transform for phase and instantaneous frequency
4. ENF presence test: SNR above 6dB required for reliable analysis
5. Phase continuity analysis: detect jumps > threshold
6. Frequency drift consistency: detect step changes inconsistent with
   natural grid variation
7. Second harmonic analysis (100Hz) for corroboration

**Findings generated:**
- `ENF phase discontinuity` — HIGH severity, with timestamp of splice
- `ENF frequency step change` — HIGH severity
- `ENF absent` — informational, triggers weight redistribution in fusion
- `ENF SNR low` — confidence penalty, not a finding

**Confidence model:**
Confidence is a function of ENF SNR. A clean indoor recording with strong
mains hum gets confidence 0.90+. A quiet outdoor recording with weak ENF
gets confidence 0.30 — the module still reports but fusion discounts it.

**Tests:** T40–T49

---

## Phase 4 — Noise Floor & Background Consistency

**Works on every recording regardless of ENF presence.**

Every recording environment has a characteristic noise floor — a statistical
fingerprint of the microphone, the room, the ambient sound, and the
electrical environment. A genuine single-session recording has a consistent
noise floor throughout. An edited recording has discontinuities where the
noise floor changes.

**What it builds:**
- `NoiseFloorModule` — segment-by-segment noise profiling

**Signal processing pipeline:**
1. Segment audio into 1-second windows (512-sample overlap)
2. Identify silence windows (below voice activity threshold)
3. Compute Welch PSD for each silence window (4096-point FFT)
4. Model noise floor as mean PSD vector per segment
5. KL-divergence between adjacent windows → discontinuity score
6. Statistical change-point detection (CUSUM algorithm)
7. Global noise floor trend analysis (gradual vs sudden change)

**Findings generated:**
- `Noise floor discontinuity` — HIGH severity at detected change point
- `Spectral noise profile change` — MEDIUM severity for gradual shifts
- `Abnormal noise pattern` — LOW severity for unusual but consistent noise

**Tests:** T50–T59

---

## Phase 5 — Compression & Encoding Artifact Analysis

**Detects when audio was edited in a lossy format and re-saved.**

Every lossy codec leaves a characteristic spectral fingerprint. MP3 at 128kbps
cuts off above ~16kHz. AAC at 96kbps has a characteristic rolloff above 18kHz.
AMR (phone codec) cuts off at 4kHz or 8kHz. If different segments of a
recording were encoded at different quality levels, or with different codecs,
those segments have different spectral ceilings — invisible to the ear,
visible in the spectrogram.

**What it builds:**
- `CompressionModule` — spectral bandwidth and artifact analysis

**Signal processing pipeline:**
1. Short-time spectral analysis (STFT, 2048-point window)
2. Per-segment spectral ceiling detection (energy rolloff frequency)
3. Statistical model of ceiling frequency distribution
4. Change-point detection on ceiling frequency track
5. MDCT artifact pattern analysis (MP3-specific)
6. Double-compression detection: second-generation codec noise floor
7. Bitrate consistency estimation from spectral content

**Codec fingerprints:**

| Codec & Bitrate | Spectral Ceiling | Artifact Pattern |
|---|---|---|
| MP3 128kbps | ~16.0 kHz | Hard cutoff, MDCT ringing |
| MP3 192kbps | ~18.0 kHz | Soft rolloff |
| AAC 96kbps | ~18.0 kHz | Smooth rolloff |
| AAC 128kbps | ~20.0 kHz | Near-transparent |
| AMR-NB | ~4.0 kHz | Hard cutoff |
| AMR-WB | ~8.0 kHz | Hard cutoff |

**Findings generated:**
- `Inconsistent spectral ceiling` — HIGH severity
- `Double compression artifact` — MEDIUM severity
- `Codec change at segment boundary` — HIGH severity

**Tests:** T60–T69

---

## Phase 6 — Reverberation & Room Consistency

**Detects when audio was recorded in two different physical environments.**

Every room has a characteristic reverberation — sound bounces off walls,
floors, and furniture and decays at a rate determined by the room's size
and materials. This is captured as RT60 (the time for sound to decay 60dB).
A small office might have RT60 of 0.3s. A large hall might have 1.5s.
If a splice is inserted from a recording made in a different room, the
RT60 changes — even if the voices sound similar.

**What it builds:**
- `ReverberationModule` — RT60 estimation and consistency analysis

**Signal processing pipeline:**
1. Voice Activity Detection (VAD) — find speech segments
2. Per-segment RT60 estimation via decay curve fitting
3. Direct-to-Reverberant Ratio (DRR) tracking
4. Statistical model of RT60 distribution
5. Change-point detection on RT60 track
6. Graceful degradation: low-reverb environments reduce confidence,
   module does not claim false findings

**RT60 estimation method:**
Schroeder backward integration on energy decay curves,
with linear regression on the -5dB to -35dB decay slope
(avoids noise floor contamination at -60dB).

**Findings generated:**
- `Room acoustic change` — HIGH severity with timestamp
- `Reverberation time inconsistency` — MEDIUM severity
- `Dry environment` — informational, confidence penalty applied

**Tests:** T70–T79

---

## Phase 7 — Voice & Speaker Continuity

**Detects when a different person's voice is inserted into a recording.**

Every speaker has a characteristic vocal tract — a physical tube of unique
length and shape that determines the resonant frequencies (formants) of
their voice. Fundamental frequency (F0, or pitch) varies continuously
during speech but has a characteristic range per speaker. If someone
else's voice is spliced in, both F0 and the formant structure change.

**What it builds:**
- `VoiceModule` — pitch and formant continuity analysis

**Signal processing pipeline:**
1. Voice Activity Detection — only analyse voiced speech frames
2. F0 extraction via autocorrelation (Yin-style algorithm)
3. Formant estimation via LPC (Linear Predictive Coding) analysis
4. Long-Term Average Spectrum (LTAS) — speaker identity proxy
5. Per-segment F0 statistics: mean, variance, range
6. Formant trajectory smoothness analysis
7. Graceful degradation: silence, music, noise → module skipped

**What it detects:**

| Signal | Threshold | Finding |
|---|---|---|
| F0 mean jump | > 30Hz between adjacent segments | Speaker change |
| F0 range change | > 50% shift | Prosody inconsistency |
| Formant F1/F2 jump | > 200Hz | Vocal tract change |
| LTAS divergence | KL-div > 0.8 | Long-term speaker change |

**Findings generated:**
- `Speaker change detected` — HIGH severity with timestamp
- `Pitch discontinuity` — MEDIUM severity
- `Formant trajectory break` — MEDIUM severity
- `No voiced speech` — informational, module skipped

**Tests:** T80–T89

---

## Phase 8 — Adaptive Fusion Engine

**Combines all module scores into a single probability and verdict.**

Same architecture as VideoForensics fusion — but with audio-specific
weight hierarchy and ENF-absent handling.

**What it builds:**
- `FusionEngine` — weighted, uncertainty-adjusted score combination
- `FusionModule` — runs all modules and calls FusionEngine

**Fusion algorithm:**

```
1. Collect ModuleScore from each module
2. For each module:
   a. If skipped → weight = 0, redistribute to others
   b. Adjust weight by confidence (low confidence → down-weight)
   c. ENF absent flag → redistribute ENF weight proportionally
3. Compute base_score = weighted sum of module scores
4. Corroboration multiplier:
   n modules elevated (score > 0.30):
     0 elevated → ×1.0
     2 elevated → ×1.2
     3 elevated → ×1.4
     4+ elevated → ×1.6
5. fused_probability = clip(base_score × corroboration, 0, 1)
6. Conflict detection: any two modules differ by > 0.4 → flag
7. Map fused_probability to Verdict via threshold table
```

**Conflict handling:**
If ENF says 0.85 (strong tamper evidence) but voice says 0.05
(speaker sounds continuous), that conflict is flagged explicitly
in the report. The verdict is not suppressed — it reflects the
weight of evidence — but the conflict is disclosed.

**Tests:** T90–T99

---

## Phase 9 — PDF Report Generation

**Court-ready PDF report using ReportLab.**

Same approach as VideoForensics reporting — professional, structured,
printable, with every technical claim traceable to specific findings.

**What it builds:**
- `ForensicReportBuilder` — ReportLab-based PDF generator

**Report sections:**

1. **Cover page**
   - Case ID, filename, SHA-256 hash
   - Colour-coded verdict banner
   - Analysis timestamp
   - Pre-calibration caveat

2. **Executive summary**
   - Module score table with visual bars
   - Finding count by severity
   - Conflict notice if present
   - Corroboration summary

3. **ENF Analysis** *(if ENF present)*
   - Frequency track chart (matplotlib, rendered into PDF)
   - Annotated discontinuity markers
   - SNR and confidence values

4. **Noise Floor Analysis**
   - Per-segment noise profile chart
   - Change-point annotations

5. **Detailed findings**
   - All findings grouped by module
   - Each finding: severity badge, confidence, timestamp, description

6. **Technical appendix**
   - Methodology per module
   - Limitations and caveats
   - Chain of custody (file hash, analysis timestamp)

**Tests:** T100–T109

---

## Phase 10 — HTTP API

**REST API for integration with case management systems.**

Same Flask pattern as VideoForensics API.

**What it builds:**
- `api/app.py` — Flask application factory
- `api/server.py` — CLI entrypoint

**Endpoints:**

| Method | Endpoint | Input | Output |
|---|---|---|---|
| GET | `/api/v1/health` | — | `OK` |
| GET | `/api/v1/version` | — | JSON version info |
| POST | `/api/v1/analyze` | `multipart/form-data` with `audio` field | Full analysis JSON |
| POST | `/api/v1/report` | `multipart/form-data` with `audio` field | PDF download |

**Usage:**
```bash
# Start server
python api/server.py --host 0.0.0.0 --port 5000

# JSON analysis
curl -X POST http://localhost:5000/api/v1/analyze \
  -F "audio=@/path/to/recording.mp3" | python3 -m json.tool

# PDF report
curl -X POST http://localhost:5000/api/v1/report \
  -F "audio=@/path/to/recording.mp3" \
  -o report.pdf
```

**Tests:** T110–T119

---

## Phase 11 — Calibration Infrastructure

**Same architecture as VideoForensics calibration.**

Threshold fitting from a labelled corpus of authentic and tampered recordings.
Without calibration, the system uses conservative default thresholds.
With a corpus of 50+ videos per class, thresholds tighten significantly.

**What it builds:**
- `calibration/corpus.py` — `CalibrationCorpus`, `CalibrationRecord`
- `calibration/engine.py` — `CalibrationEngine`, threshold fitting
- `calibration/runner.py` — `CalibrationRunner`, corpus analysis
- `calibration/report.py` — `CalibrationReportBuilder`
- `calibrate.py` — CLI

**Corpus CSV format:**
```csv
path,label,notes
/recordings/phone_call.mp3,authentic,original Samsung S21 recording
/recordings/edited_call.mp3,tampered,splice at 45s confirmed by review
/recordings/meeting.wav,authentic,Zoom recording, unedited
```

**CLI usage:**
```bash
# Step 1: run analysis on your labelled corpus
python calibrate.py run --corpus corpus.csv

# Step 2: compute metrics and generate PDF report
python calibrate.py analyze --results calibration_results.json

# Step 3: apply calibrated thresholds (after reviewing the report)
python calibrate.py apply --corpus corpus.json

# Demo on synthetic test audio (verifies infrastructure)
python calibrate.py demo
```

**Minimum corpus for threshold fitting:**
- 10 authentic recordings (different devices, environments)
- 10 tampered recordings (different manipulation types)

**For production forensic use:**
- 50+ authentic (diverse devices: phone, laptop, recorder, Zoom)
- 50+ tampered (diverse: splice, delete, insert, re-record, speed change)

**Tests:** T120–T129

---

## Phase 12 — Web UI

**Flask web interface for interactive testing.**

Same pattern as VideoForensics UI — upload file, see results, download PDF.
Audio-specific additions: waveform display and ENF frequency track chart,
both rendered server-side with matplotlib and embedded in the results page.

**What it builds:**
- `ui/server.py` — Flask UI server

**Features:**
- Drag-and-drop audio upload
- Live progress indicator (8 steps, one per module)
- Results panel:
  - Verdict badge (colour-coded)
  - 6 module score cards with visual bars
  - All findings sorted by severity
  - Waveform visualisation (server-rendered PNG)
  - ENF frequency track chart (if ENF detected)
  - File properties table
- PDF Report download button

**Usage:**
```bash
python ui/server.py
# Open http://localhost:5050
```

**Tests:** T130–T134

---

## Build Order Summary

```
Phase  0  ✅  Foundation & Data Models
Phase  1      Ingestion Pipeline
Phase  2      Metadata & Format Integrity
Phase  3      ENF Analysis          ← build this before noise/voice
Phase  4      Noise Floor Consistency
Phase  5      Compression Artifacts
Phase  6      Reverberation & Room
Phase  7      Voice & Speaker
Phase  8      Fusion Engine
Phase  9      PDF Reporting
Phase 10      HTTP API
Phase 11      Calibration
Phase 12      Web UI
```

---

## Calibration Sources

**Authentic recordings (sources):**
- Your own phone recordings (Samsung, iPhone, etc.)
- Zoom / WhatsApp / Teams call recordings
- Court-provided original recordings
- Public speech datasets (Common Voice, VCTK)

**Tampered recordings (how to create):**
```bash
# Splice two clips at 30 seconds
ffmpeg -i clip_a.mp3 -i clip_b.mp3 \
  -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" spliced.mp3

# Delete a segment (cut from 10s to 15s)
ffmpeg -i original.mp3 \
  -af "aselect='not(between(t,10,15))',asetpts=N/SR/TB" deleted.mp3

# Re-encode at lower quality (double compression)
ffmpeg -i original.mp3 -ab 64k reencoded_low.mp3

# Speed change (alters prosody)
ffmpeg -i original.mp3 -af "atempo=1.15" speed_changed.mp3
```

---

*AudioForensics — Built with the same discipline as VideoForensics.*
*Phase 0 complete. Phase 1 ready to begin.*
