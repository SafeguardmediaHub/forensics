# FVA Platform — Bug Fix & Improvement Task List

> Generated: 2026-02-27
> Updated as tasks are completed. Status: ⬜ Pending | 🔄 In Progress | ✅ Done

---

## Active Tasks

---

### TASK-01 — Fix frontend poll timeout for frame analysis
**Status:** ✅ Done
**File:** `/home/finzyphinzy/Desktop/fvaui/apps.py`

**Problem:**
`poll_task()` has a hardcoded `max_sec=360` (6 minutes). Frame analysis legitimately
takes ~7.5 minutes. The frontend times out with "Analysis failed: Timed out after 6
minutes" even though the backend completed successfully.

**Fix:**
Change the frame analysis `poll_task` call from:
```python
status, err = poll_task(task_id)
```
to:
```python
status, err = poll_task(task_id, max_sec=900)
```
15 minutes (900 s) is a safe ceiling given current performance.

---

### TASK-02 — Fix audio `speaker_match`, `events`, `duplicates` coverage/numba conflict
**Status:** ✅ Done
**File:** `/home/finzyphinzy/Downloads/work/audio_forensics/audio_forensics_system.py`

**Problem:**
Three sub-modules still fail with `module 'coverage' has no attribute 'types'`:
- `_analyze_speaker_consistency` — uses `librosa.feature.mfcc()`
- `_analyze_events` — uses `librosa.stft()` and `librosa.feature.rms()`
- `_analyze_duplicates` — uses `librosa.feature.mfcc()`

The `NUMBA_DISABLE_JIT=1` env var approach is fragile — it can be pre-empted by
the shell environment or fail if the Celery worker was launched under `coverage run`.
The only reliable fix is to remove the librosa dependency from the hot path entirely.

**Fix:**
Replace librosa feature calls with pure numpy/scipy equivalents:
- `librosa.feature.mfcc()` → numpy FFT + mel filterbank + scipy DCT
- `librosa.stft()` → `np.fft.rfft()` in windowed frames
- `librosa.feature.rms()` → `np.sqrt(np.mean(frame**2))` per window

---

### TASK-03 — Fix AV sync `librosa.load()` conflict in frame_analysis
**Status:** ✅ Done
**File:** `/home/finzyphinzy/Downloads/work/frame_analysis.py`

**Problem:**
`_analyze_av_sync()` at line 904 calls `librosa.load(audio_tmp, sr=22050, mono=True)`.
This uses `audioread` for format decoding, hitting the same `coverage` conflict as
the audio forensics modules. The previous fix only patched `audio_forensics_system.py`,
not `frame_analysis.py`. From the logs:
```
WARNING/MainProcess] AV sync onset analysis failed: module 'coverage' has no attribute 'types'
```

**Fix:**
Replace `librosa.load()` in `_analyze_av_sync()` with the same ffmpeg subprocess
approach used in `audio_forensics_system.py`:
```python
proc = subprocess.run(["ffmpeg", "-i", audio_tmp, "-f", "f32le", "-ac", "1",
                       "-ar", "22050", "-", "-loglevel", "error"],
                       capture_output=True)
y = np.frombuffer(proc.stdout, dtype=np.float32)
sr = 22050
```
The onset analysis that follows (using `librosa.onset.*`) operates on the numpy
array, not the file — those calls are safe and unaffected.

---

### TASK-04 — Decouple visual forensics from frame analysis task
**Status:** ✅ Done
**Files:** `/home/finzyphinzy/Downloads/work/tasks.py`

**Problem:**
`analyze_video_task` runs two separate layers of visual forensics:
- Layer 1 (inside `FrameAnalysisEngine`): targeted ELA/noise/clone on frames flagged
  by temporal analysis + 5% random baseline. Feeds the main verdict.
- Layer 2 (in the task): `generate_forensic_report_video()` runs ELA/noise/clone
  again on 12 evenly-sampled frames independently. Produces a secondary verdict.

Layer 2 is responsible for ~5.5 minutes of the 7.5-minute total. It does redundant
work on potentially overlapping frames. In a properly tiered forensics system, temporal
analysis identifies suspicious regions; spatial analysis then inspects only those
regions — which Layer 1 already does correctly.

**Decision (architectural):**
Remove Layer 2 (`generate_forensic_report_video()` call) from `analyze_video_task`.
Populate the `visual_forensics` response section from the spatial findings already
computed by Layer 1. This cuts analysis time from ~7.5 min to ~2 min, eliminates
redundancy, and makes the spatial signal sharper (focused on flagged frames only).

`generate_forensic_report_video()` is kept in `visual_forens.py` — it remains
available as a standalone utility if needed in the future.

---

### TASK-05 — Fix PDF generation failure
**Status:** ✅ Done
**Files:** `/home/finzyphinzy/Downloads/work/tasks.py`, `pdfgneration.py`

**Problem:**
```
PDF generation failed (non-fatal): No module named 'pdfgneration'
```
The file `pdfgneration.py` exists. The error "No module named 'pdfgneration'" when
the file is present means Python found the file but failed to execute it — specifically
because `pdfgneration.py` has bare top-level imports from `reportlab`:
```python
from reportlab.lib.pagesizes import letter, A4
...
```
When `reportlab` is not installed, Python can't load `pdfgneration.py` at all and
surfaces the error as the module itself being missing. `reportlab` is not in
`requirements.txt`.

**Fix:**
1. Add `reportlab` to `requirements.txt`
2. Move the `reportlab` imports inside `generate_pdf_report()` so a missing
   `reportlab` gives a clear actionable error rather than a confusing
   "No module named 'pdfgneration'" message.

---

## Completed Tasks

- TASK-01 ✅ — frontend poll timeout raised to 900 s (`apps.py`)
- TASK-02 ✅ — librosa replaced with numpy/scipy in all 3 audio modules (`audio_forensics_system.py`)
- TASK-03 ✅ — librosa.load() replaced with ffmpeg PCM pipe in AV sync (`frame_analysis.py`)
- TASK-04 ✅ — Layer 2 visual forensics removed from `analyze_video_task`; visual_forensics section now derived from Layer 1 spatial findings (`tasks.py`)
- TASK-05 ✅ — reportlab imports guarded; module always loadable; clear error on missing dep (`pdfgneration.py`)

---

## Notes

- All fixes should be backward-compatible — no API contract changes
- TASK-02 and TASK-03 address the same root cause (coverage/numba/audioread
  conflict) in two different files
- TASK-04 is an architectural change, not a bug fix — test before and after
  to confirm verdict quality is preserved
- PDF generation is non-fatal; TASK-05 can be done last
