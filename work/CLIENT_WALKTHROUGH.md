# FVA Platform — Client Walkthrough
### Presentation Guide | February 2026

---

## Before You Start — Two Things to Say Upfront

> *"What you're seeing today is the analysis engine running in a raw testing environment. The results on screen are the raw data output — in the final platform, everything will be translated into plain language, colour-coded summaries, and easy-to-read reports. Think of this as looking at the engine under the hood before the car dashboard is built."*

> *"Analysis in this environment takes a few minutes because it's running on a local machine. Once deployed to a server, the same analysis will run significantly faster."*

---

## What This Platform Does — One Sentence

> *"This platform takes a video, image, or audio file and automatically answers the question: has this been tampered with, and if so, where and how?"*

---

---

# MODULE 1 — Video Frame Analysis
### *"The most powerful module — detects edits made to video over time"*

---

## What it does (say this before running)

This module watches a video the same way a forensic expert would — frame by frame — looking for moments where something doesn't add up. It catches things the human eye would miss: a fraction of a second where the video was cut and stitched back together, a section of footage that was secretly repeated, or a moment where the audio and video stopped matching.

**Real-world use cases:**
- A video of an incident has a 3-second section removed
- Footage from a CCTV camera has been looped to hide an event
- A speech clip has had words removed and sentences spliced together

---

## While it's running — keep him engaged with this

*The progress bar will move through stages. Explain each one as it happens:*

**"Extracting frames"**
> *"It's pulling individual frames out of the video — think of it like flipping through every page of a flipbook to inspect each one."*

**"Optical flow analysis"**
> *"It's measuring how things move between frames. If someone walking smoothly suddenly jumps position between two frames, that's a red flag. It's looking for exactly those kinds of unnatural jumps."*

**"Scene detection"**
> *"It's identifying where the scene genuinely changes versus where it was cut and forced to change. A legitimate cut in a film looks different to a forensic splice."*

**"Spatial forensics"**
> *"Now it's zooming into individual suspicious frames and checking whether the image itself has been altered — things pasted in, regions cloned, or image quality that doesn't match the rest of the video."*

**"Computing verdict"**
> *"It's combining all the signals it found and producing a confidence score. Like a judge reviewing all the evidence before reaching a verdict."*

---

## What the result means

| Result | Plain meaning |
|--------|--------------|
| **Likely Authentic** | No significant signs of editing found |
| **Review Recommended** | Some unusual signals — worth a closer look by a human expert |
| **Likely Tampered** | Strong evidence of editing or manipulation |

The **confidence percentage** tells you how certain the system is. 90%+ means the evidence is strong. 40% means the system spotted something but it could also be explained by normal video processing.

---

---

# MODULE 2 — Image Forensics
### *"Detects manipulation within a single photograph or image"*

---

## What it does (say this before running)

Every time an image is edited — whether a region is copied and pasted, an object is removed, brightness is changed selectively, or the image has been saved and re-saved multiple times — it leaves invisible traces in the image data. This module reads those traces.

**Real-world use cases:**
- A photo used as evidence has had a person or object removed
- A document screenshot has been edited to change a number or name
- An image circulating on social media has been digitally composed from multiple sources

---

## While it's running — keep him engaged with this

**"Running ELA — Error Level Analysis"**
> *"It's re-compressing the image and comparing it to the original. Genuine photos compress consistently across the whole image. Edited regions compress differently because they were modified after the original compression — this shows up as bright patches on the ELA map."*

**"Noise analysis"**
> *"Every camera adds a tiny, unique noise pattern to photos — like a fingerprint. If different parts of the image have different noise patterns, it means they came from different sources. That's what it's checking right now."*

**"Copy-move detection"**
> *"It's searching for regions within the image that are copies of each other — a common technique used to clone an area of a photo to cover something up."*

---

## What the result means

Same verdict scale as above. Additionally:
- **ELA score** — higher means more compression inconsistency (more suspicious)
- **Noise inconsistency** — higher means the image looks like it was assembled from multiple sources
- **Clone score** — higher means copy-paste activity was detected

---

---

# MODULE 3 — Audio Forensics
### *"Detects cuts, splices, and tampering in audio recordings"*

---

## What it does (say this before running)

Audio is surprisingly easy to tamper with and surprisingly hard to fake perfectly. This module analyses a recording from five different angles simultaneously to catch signs of editing — from a section of audio being removed, to a word being replaced, to two different recordings being joined together.

**Real-world use cases:**
- A phone call recording has had a sentence removed or replaced
- A statement given in an interview has been re-ordered
- Audio from two different conversations has been spliced into one

---

## While it's running — keep him engaged with this

> *"It's running five checks simultaneously on the audio. I'll tell you what each one is looking for..."*

**Integrity check**
> *"First, it's verifying the file hasn't been modified since it was created — like a digital seal on an envelope."*

**Speaker consistency**
> *"It's listening to whether the same voice is present throughout the recording. A change in vocal characteristics — even subtle ones the ear misses — can indicate that a section was replaced with a different recording of the same person."*

**ENF analysis — Electrical Network Frequency**
> *"This is a powerful technique. Every electrical recording environment has a faint 50Hz or 60Hz hum from the mains power supply — it's in every recording made indoors. This frequency drifts slightly over time in a pattern that's been mapped historically. If the recording was cut and rejoined, the hum pattern will have a jump at that exact point. It's essentially a timestamp baked into the recording itself."*

**Event detection**
> *"It's looking for abrupt changes in the sound — sudden shifts in background noise, volume, or audio texture that indicate a cut point."*

**Duplicate detection**
> *"It's checking whether any section of audio appears more than once — a sign that a segment was copied and pasted within the recording."*

---

## What the result means

Each of the five modules gives a **confidence score from 0–100%**, where 100% means that module found the audio to be authentic. The overall score is a weighted combination of all five.

A single low score in ENF with a high score everywhere else tells a different story than all five scores being low — the system is designed to show you exactly which indicator fired and why.

---

---

# MODULE 4 — Video Forensics (Bitstream Analysis)
### *"Analyses the hidden technical fingerprint of the video file itself"*
### ⚠ Status: In active development — core functionality working, refinements in progress

---

## What it does (say this before running)

While Video Frame Analysis watches *what happens* in the video, this module looks at *how the video file is built*. Every video file is a container of compressed data — think of it like a very complex zip file. When a video is edited and re-exported, or when it's been artificially generated, the internal structure of the file changes in ways that reveal the manipulation.

**Real-world use cases:**
- Deepfake video detection (AI-generated face swaps)
- Detecting videos that have been exported from editing software versus genuine camera recordings
- Identifying videos that have been re-encoded to hide previous editing

---

## What's working now

| Detector | Status | What it catches |
|----------|--------|----------------|
| Double-encoding detection | ✅ Working | Video re-exported through editing software |
| Codec & metadata analysis | ✅ Working | Mismatched encoding fingerprints |
| Motion vector forensics | ✅ Working | Unnatural motion patterns from AI generation |
| Deepfake face analysis | ✅ Working (requires model files) | AI face-swap and face generation |
| ENF video analysis | ⚠ Physics constraint | Only effective on specialist high-framerate cameras (100fps+) — standard cameras cannot capture this signal |

---

## While it's running — keep him engaged with this

**"Codec and bitstream analysis"**
> *"It's reading the raw compressed data of the video — looking for statistical patterns that shouldn't be there if the video is genuine. Imagine if a document printed by a genuine printer had slightly different ink chemistry to one that was forged — this is the digital equivalent."*

**"Metadata chain analysis"**
> *"Every video file carries a record of where it came from and what touched it. It's checking whether that record is consistent — or whether something tried to cover its tracks."*

**"Deepfake indicator analysis"**
> *"It's extracting faces from the video and analysing them for signs of artificial generation — things like unnatural sharpness patterns around facial boundaries, or statistical frequency signatures that AI image generators leave behind."*

---

## What the result means

Same verdict scale. This module returns a list of specific **finding types** — each one names exactly what kind of anomaly was detected (e.g. "Double Encoding", "Codec Inconsistency", "Motion Vector Anomaly").

---

## Honest note on current status

> *"This module is nearly production-ready. During testing we identified a small number of refinements needed — particularly around how it handles videos that have already been processed by social media platforms before reaching us, which changes the baseline characteristics the detectors are calibrated against. We're addressing those now. The core detectors are functional and returning results."*

---

---

## Closing Notes for the Presentation

### On the results looking complex:
> *"What you're seeing is the raw forensic data — the same way a blood test result shows raw numbers that a doctor then interprets for the patient. In the main platform, these results will be translated into clear plain-language summaries, visual reports, and simple red/amber/green indicators that anyone can read without technical knowledge."*

### On the speed:
> *"Today's analysis is running on a single local machine. In production, this runs on dedicated cloud infrastructure where the same analysis completes in a fraction of the time. Frame analysis on a 2-minute video, for example, would go from 7–8 minutes down to under 2 minutes."*

### On what's next:
> *"The four modules you've seen today cover the full spectrum — temporal video analysis, image manipulation, audio authenticity, and bitstream-level video forensics. The next phase is integrating these into the main platform UI so results are presented in a way that's immediately useful to non-technical users."*

---

*Document prepared for client walkthrough — February 2026*
