# Frame Analysis Baseline Status

Phase 2 status for frame-analysis baseline capture.

Primary full-length sample:
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4`

Observed behavior in the shared `safeguardmedia/.venv` environment:
- temporal and spatial artifacts are generated under `/tmp/sgm_frames_baseline`
- the run is much slower than AFF and image baseline capture
- the run logs a non-fatal missing dependency:
  - `ImageHash detection failed: No module named 'imagehash'`

Practical Phase 2 conclusion:
- frame-analysis baseline is still pending as a stable completed baseline
- wrapper work can proceed, but frame-analysis parity should remain tracked as
  an open item until the environment/dependency issue is resolved or a stable
  canonical clip is approved

Derived short-clip sample created for faster direct-run testing:
- `/tmp/phase2_frames_sample.mp4`

Use:
- helpful for local debugging and faster iteration
- not yet approved as the canonical stored frame-analysis baseline sample

Wrapper status:
- `work/engine_runner.py frames /tmp/phase2_frames_sample.mp4 --mode standard --sampling-mode sampled`
  now completes successfully in the shared environment
- current wrapper run still logs non-fatal environment/dependency issues:
  - `ImageHash detection failed: No module named 'imagehash'`
  - `AV sync onset analysis failed: name 'librosa' is not defined`
- this proves the wrapper boundary is callable, but it does not close formal
  frame-analysis regression parity for Phase 2/3
