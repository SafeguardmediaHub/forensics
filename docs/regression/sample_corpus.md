# Sample Corpus

Approved Phase 2 regression corpus.

## VFF

### Primary sample
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4`
Purpose:
- current known-good local video fixture
- used for direct VFF baseline capture
- used for frame-analysis baseline capture

Constraint:
- this is currently the only clearly available local video fixture in the repo

## AFF

### Likely-authentic sample
- `AFF/AUD-20230414-WA0012.m4a`
Purpose:
- known direct-run AFF sample
- baseline for authentic or near-authentic audio behavior

### Tampered sample
- `AFF/content_deleted.wav`
Purpose:
- baseline for elevated/tampered AFF behavior
- verifies findings and elevated module preservation

### Additional selected sample
- `AFF/Coach Musa_1.m4a`
Purpose:
- extra AFF sample for wrapper sanity checks

## Image Forensics

Current image fixtures are extracted frames from the approved local video.

### Selected samples
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/ca16e742-84d7-434b-8dc0-7231a11b8e1e/frames/frame_000000_00000000.png`
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/ca16e742-84d7-434b-8dc0-7231a11b8e1e/frames/frame_000600_00020000.png`
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/ca16e742-84d7-434b-8dc0-7231a11b8e1e/frames/frame_001200_00040000.png`

Purpose:
- baseline the image-forensics path with real local PNGs
- provide early, mid, and later visual snapshots from the same source video

Constraint:
- these are not standalone curated image fixtures

## Frame Analysis

### Primary sample
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4`
Purpose:
- baseline for the frame-analysis path using the same approved local video

## Coverage Notes

Current Phase 2 baseline coverage is acceptable for starting wrapper work, but
it should be improved later by adding:
- at least one dedicated external image fixture set
- at least one additional video fixture for VFF and frame analysis
- at least one additional clearly authentic audio fixture and one additional
  clearly tampered audio fixture
