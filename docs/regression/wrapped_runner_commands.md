# Wrapped Runner Commands

Repeatable commands for Phase 2/3 wrapper-based regression checks.

Use these after capturing the direct-engine baselines in `baselines/`.

## AFF

Run the AFF wrapper against a known sample:

```bash
cd AFF
../safeguardmedia/.venv/bin/python engine_runner.py AUD-20230414-WA0012.m4a
```

Compare primarily:
- `verdict`
- `fused_probability`
- `confidence`
- `elevated_modules`
- `module_scores`

## VFF

Run the VFF wrapper against the approved local sample:

```bash
cd VFF
../safeguardmedia/.venv/bin/python engine_runner.py \
  ../safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4
```

Compare primarily:
- `verdict`
- `fused_probability`
- `confidence`
- `module_scores`
- `total_findings`
- key finding titles

## work image

Run the image wrapper against the approved local image sample:

```bash
cd work
../safeguardmedia/.venv/bin/python engine_runner.py image \
  ../safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/ca16e742-84d7-434b-8dc0-7231a11b8e1e/frames/frame_000000_00000000.png
```

Compare primarily:
- `verdict`
- `tampering_likelihood`
- `confidence`
- `note`

## work frames

Run the frame-analysis wrapper against a local video sample:

```bash
cd work
../safeguardmedia/.venv/bin/python engine_runner.py frames \
  ../safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4 \
  --mode standard \
  --sampling-mode sampled
```

Current status:
- useful for wrapper validation
- not yet approved as a stable completed regression checkpoint
- parity remains open until the frame-analysis baseline is stabilized
