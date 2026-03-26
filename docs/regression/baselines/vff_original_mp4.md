# VFF Baseline: `original.mp4`

Engine:
- `VFF`

Sample:
- `safeguardmedia/src/api/engines/vff/working/ca16e742-84d7-434b-8dc0-7231a11b8e1e/original.mp4`

Capture method:
- direct engine execution via `VFF/run_analysis.py`

Comparison fields:
- verdict: `likely_authentic`
- probability: `0.157`
- confidence: `0.258`
- module_scores:
  - `metadata`: `0.030`
  - `compression`: `0.144`
  - `noise`: `0.188`
  - `temporal`: `0.292`
  - `lighting`: `0.000`
  - `audio`: `0.131`

Key findings:
- `Incomplete metadata`
- `PRNU camera fingerprint anomaly`
- `Noise pattern anomaly`
- `Frame duplication detected`

Notes:
- direct console run reported 9 total findings
- direct console run took about 105 seconds
- this sample should remain useful as the first wrapper-regression checkpoint for VFF
