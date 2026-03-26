#!/usr/bin/env python3
"""
VideoForensics — Full analysis + PDF report generator.
Usage: python generate_report.py /path/to/video.mp4 [--output /path/to/report.pdf]
"""
import sys, logging, time
from pathlib import Path
from datetime import datetime, timezone

if "--verbose" not in sys.argv:
    logging.disable(logging.WARNING)

if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    print("Usage: python generate_report.py /path/to/video.mp4 [--output report.pdf]")
    sys.exit(0)

video_path = Path(sys.argv[1])
if not video_path.exists():
    print(f"Error: file not found — {video_path}")
    sys.exit(1)

# Output path
if "--output" in sys.argv:
    idx = sys.argv.index("--output")
    out_path = Path(sys.argv[idx + 1])
else:
    out_path = video_path.parent / f"vf_report_{video_path.stem}.pdf"

sys.path.insert(0, str(Path(__file__).parent))

from core.ingestion.pipeline import IngestionPipeline
from modules.fusion.fusion_module import FusionModule
from modules.reporting.report_builder import ForensicReportBuilder
from modules.fusion.fusion_engine import Verdict

COLORS = {
    Verdict.LIKELY_AUTHENTIC: "\033[92m",
    Verdict.INCONCLUSIVE:     "\033[93m",
    Verdict.LIKELY_TAMPERED:  "\033[33m",
    Verdict.TAMPERED:         "\033[91m",
}
RESET = "\033[0m"

print(f"\nVideoForensics — Full Analysis + Report")
print(f"{'─'*60}")
print(f"Input:  {video_path}")
print(f"Report: {out_path}")
print()

t0 = time.time()

# Ingest
pipeline = IngestionPipeline(extract_frames=False, extract_audio=False)
case     = pipeline.ingest(video_path)
p        = case.video_profile
duration = (p.total_frames / p.frame_rate) if p.frame_rate else 0
print(f"Video:  {p.codec.upper()} {p.width}×{p.height} @ {p.frame_rate:.1f}fps  "
      f"{p.total_frames} frames  {duration:.1f}s")
print()

# Run fusion
print("Running forensic analysis...")
fusion_result = FusionModule().run(case)
m = fusion_result.metadata

color = COLORS.get(m["verdict"], "")
print(f"\n{'═'*60}")
print(f"  VERDICT: {color}{m['verdict_label'].upper()}{RESET}")
print(f"  Probability: {m['fused_probability']:.3f}   Confidence: {m['confidence']:.3f}")
print(f"  Elevated:    {m['n_elevated_modules']} domain(s): {m['modules_in_agreement']}")
print(f"{'═'*60}\n")

# Build PDF
print("Generating PDF report...")
ts = datetime.now(timezone.utc)
builder = ForensicReportBuilder()
builder.build(
    output_path        = out_path,
    case_id            = case.case_id,
    filename           = video_path.name,
    video_profile      = p,
    fusion_result      = fusion_result,
    analysis_timestamp = ts,
)

elapsed = time.time() - t0
print(f"Report written: {out_path}")
print(f"Total time: {elapsed:.1f}s")
print()
