# Regression Baseline

This directory stores the Phase 2 regression corpus and baseline expectations
for the current engines before wrapper-based integration work changes the
execution path.

Purpose:
- preserve the currently working behavior of `VFF`, `AFF`, and `work`
- give wrapper work a concrete comparison target
- detect integration regressions before Node.js cutover

Rules:
- baseline captures should come from the current direct engine path, not from a
  future wrapper implementation
- comparison should focus on stable forensic outputs, not incidental runtime
  details like UUIDs, timestamps, or ANSI console formatting
- when a baseline is updated intentionally, the reason should be recorded

Contents:
- `sample_corpus.md` — approved files and the role of each sample
- `comparison_rules.md` — exact-match vs tolerance-based comparison rules
- `wrapped_runner_commands.md` — repeatable wrapper-run commands for parity checks
- `baselines/` — current baseline records for selected samples

Current limitations:
- the repo contains strong AFF sample coverage
- the repo contains one clearly usable local video sample:
  `safeguardmedia/src/api/engines/vff/working/.../original.mp4`
- image samples are currently taken from extracted frames of that video because
  no dedicated image fixture set is checked into the repo
