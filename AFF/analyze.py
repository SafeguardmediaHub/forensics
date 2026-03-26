from pathlib import Path
from core.ingestion.pipeline import IngestionPipeline
from modules.metadata.metadata_module import MetadataModule
from modules.enf.enf_module import ENFModule
from modules.noise.noise_module import NoiseModule
from modules.compression.compression_module import CompressionModule
from modules.reverberation.reverberation_module import ReverberationModule
from modules.voice.voice_module import VoiceModule
from modules.fusion.fusion_engine import FusionEngine

def analyze(path: str):
    p = Path(path)
    case = IngestionPipeline(temp_dir=Path("/tmp/af_run")).ingest(p)

    for mod in [MetadataModule(), ENFModule(), NoiseModule(),
                CompressionModule(), ReverberationModule(), VoiceModule()]:
        case.add_score(mod._safe_run(case))

    result = FusionEngine().fuse(case)

    print(f"\n{'='*55}")
    print(f"  FILE:    {p.name}")
    print(f"  VERDICT: {result.verdict.label}")
    print(f"  SCORE:   {result.fused_probability:.3f}  (confidence: {result.confidence:.2f})")
    print(f"  ELAPSED: {result.total_elapsed_s:.1f}s")
    print(f"{'='*55}")

    if result.elevated_modules:
        print(f"\n  Elevated modules: {', '.join(result.elevated_modules)}")
        if len(result.elevated_modules) >= 2:
            print(f"  Corroboration factor: {result.corroboration_factor:.3f}")

    if result.all_findings:
        print(f"\n  Findings ({len(result.all_findings)}):")
        for f in result.all_findings:
            print(f"    [{f.severity.name}] {f.module}: {f.title}")
            if f.temporal_location:
                print(f"      → {f.temporal_location.start_seconds:.1f}s–{f.temporal_location.end_seconds:.1f}s")

    if result.has_conflict:
        print(f"\n  ⚠ CONFLICT: {result.conflict_description}")

    if result.calibration_note:
        print(f"\n  Note: {result.calibration_note}")

    return result

if __name__ == "__main__":
    import sys
    analyze(sys.argv[1])