# Comparison Rules

These rules define how current direct engine output should be compared against
future wrapper-based output.

## Exact-Match Fields

These fields should match exactly unless a deliberate baseline update is
approved:
- `verdict`
- `verdict_label`
- selected sample file
- elevated module names
- skipped module names
- finding titles when the engine output is stable enough to preserve them

## Tolerance-Based Fields

These fields may vary slightly due to runtime/environment differences:
- `fused_probability`
- `confidence`
- `tampering_likelihood`
- `tampering_confidence`
- per-module floating-point scores

Recommended tolerance:
- probabilities/confidence: absolute delta <= `0.02`
- per-module scores: absolute delta <= `0.03`

If a result crosses a verdict threshold, treat that as a regression even if the
numerical delta is small.

## Findings Comparison

Compare findings by:
- title
- severity where available
- module where available

Do not fail comparison solely because:
- ordering changed
- timestamps shifted slightly
- non-deterministic IDs changed

## Ignore For Regression Purposes

Do not compare these fields directly:
- UUIDs and job IDs
- raw timestamps
- elapsed wall time
- ANSI console formatting
- log noise

## Release-Blocking Regression

Treat these as release-blocking:
- verdict changes unexpectedly
- elevated/skipped module set changes unexpectedly
- a known tampered sample becomes likely authentic/inconclusive without approval
- a known likely-authentic sample becomes likely_tampered/tampered without approval
- key findings disappear or change materially without explanation
