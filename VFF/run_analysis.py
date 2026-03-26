#!/usr/bin/env python3
"""
VideoForensics — Quick analysis runner.
Usage: python run_analysis.py /path/to/video.mp4
"""

import sys
import logging
import time
from pathlib import Path

# Suppress internal logs unless --verbose is passed
if "--verbose" not in sys.argv:
    logging.disable(logging.WARNING)

if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    print("Usage: python run_analysis.py /path/to/video.mp4 [--verbose]")
    sys.exit(0)

video_path = Path(sys.argv[1])
if not video_path.exists():
    print(f"Error: file not found — {video_path}")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))

from core.ingestion.pipeline import IngestionPipeline
from modules.fusion.fusion_module import FusionModule
from modules.fusion.fusion_engine import Verdict

# ── Ingest ────────────────────────────────────────────────────────────────────
print(f"\nVideoForensics Analysis")
print(f"{'─'*60}")
print(f"File: {video_path.name}")

t0 = time.time()
pipeline = IngestionPipeline(extract_frames=False, extract_audio=False)
case = pipeline.ingest(video_path)

p = case.video_profile
duration = (p.total_frames / p.frame_rate) if p.frame_rate else 0
print(f"Video: {p.codec.upper()} {p.width}×{p.height} @ {p.frame_rate:.1f}fps  "
      f"{p.total_frames} frames  {duration:.1f}s")
print(f"Case ID: {case.case_id[:16]}...")
print()

# ── Analyse ───────────────────────────────────────────────────────────────────
print("Running forensic analysis...")
module = FusionModule()
result = module.run(case)
m      = result.metadata

elapsed = time.time() - t0

# ── Verdict banner ────────────────────────────────────────────────────────────
COLORS = {
    Verdict.LIKELY_AUTHENTIC: "\033[92m",   # green
    Verdict.INCONCLUSIVE:     "\033[93m",   # yellow
    Verdict.LIKELY_TAMPERED:  "\033[33m",   # orange
    Verdict.TAMPERED:         "\033[91m",   # red
}
RESET = "\033[0m"
color = COLORS.get(m["verdict"], "")

print()
print(f"{'═'*60}")
print(f"  VERDICT: {color}{m['verdict_label'].upper()}{RESET}")
print(f"  Probability : {m['fused_probability']:.3f}")
print(f"  Confidence  : {m['confidence']:.3f}")
print(f"{'═'*60}")

# ── Per-module scores ─────────────────────────────────────────────────────────
print()
print("Module scores:")
ELEV = 0.30
for mod_name, score in m["module_scores"].items():
    score = float(score)
    bar   = "█" * int(score * 20)
    flag  = " ◄ ELEVATED" if score >= ELEV else ""
    w     = m["adjusted_weights"].get(mod_name, 0)
    print(f"  {mod_name:<14} {score:.3f}  {bar:<20}  (w={float(w):.3f}){flag}")

print()
print(f"  Base score   : {m['base_score']:.3f}")
print(f"  Corroboration: ×{m['corroboration_multiplier']}  "
      f"({m['n_elevated_modules']} domain(s) elevated)")
print(f"  Fused        : {m['fused_probability']:.3f}")

# ── Conflict ──────────────────────────────────────────────────────────────────
if m["has_conflict"]:
    print()
    print(f"  ⚠  {m['conflict_description']}")

# ── Findings ──────────────────────────────────────────────────────────────────
findings = [f for f in result.findings if f.module != "fusion"]
if findings:
    print()
    print(f"Findings ({len(findings)} total):")
    SEV_COLOR = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[37m"}
    for f in findings:
        sc = SEV_COLOR.get(f.severity.value, "")
        ts = ""
        if f.temporal_location:
            ts = f"  [{f.temporal_location.start_seconds:.2f}s–{f.temporal_location.end_seconds:.2f}s]"
        print(f"  {sc}[{f.severity.value.upper():<6}]{RESET}  "
              f"{f.module:<14} {f.title}{ts}")
        # Show first line of description
        desc = f.description.split(".")[0] + "."
        if len(desc) > 100:
            desc = desc[:97] + "..."
        print(f"             {desc}")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print(f"Analysis complete in {elapsed:.1f}s")
print(f"Total findings: {m['total_findings']}")
print()
print("Note:", m["calibration_note"])
print()
