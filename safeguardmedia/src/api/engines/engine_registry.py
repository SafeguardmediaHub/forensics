"""
Engine Registry — sys.path isolation for VFF, AFF, and work engines.

VFF and AFF both use 'core' and 'modules' as their top-level package names.
We load them sequentially using sys.path insertion, then rename their entries
in sys.modules to 'vff.*' and 'aff.*' so they don't collide with each other.

Work engines (visual_forens, frame_analysis, video_forensics) use unique
top-level module names and are loaded by adding work/ to sys.path permanently.
"""
import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
VFF_ROOT = _HERE / "vff"
AFF_ROOT = _HERE / "aff"
WORK_ROOT = _HERE / "work"

# ── Class references saved at load time ───────────────────────────────────────

# VFF
VFF_Pipeline: Optional[Any] = None
VFF_Fusion: Optional[Any] = None
VFF_Builder: Optional[Any] = None
vff_available: bool = False

# AFF
AFF_Pipeline: Optional[Any] = None
AFF_MetadataModule: Optional[Any] = None
AFF_ENFModule: Optional[Any] = None
AFF_NoiseModule: Optional[Any] = None
AFF_CompressionModule: Optional[Any] = None
AFF_ReverberationModule: Optional[Any] = None
AFF_VoiceModule: Optional[Any] = None
AFF_FusionEngine: Optional[Any] = None
aff_available: bool = False

# Work engines
work_available: bool = False

_initialized: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _push(path: str) -> None:
    if path not in sys.path:
        sys.path.insert(0, path)


def _pop(path: str) -> None:
    while path in sys.path:
        sys.path.remove(path)


def _stash(prefix: str, roots: tuple) -> None:
    """Rename sys.modules entries from root.* → prefix.root.* to free the namespace."""
    to_rename = {
        key: f"{prefix}.{key}"
        for key in list(sys.modules.keys())
        if any(key == r or key.startswith(r + ".") for r in roots)
    }
    for old, new in to_rename.items():
        sys.modules[new] = sys.modules.pop(old)


# ── VFF loader ────────────────────────────────────────────────────────────────

def _load_vff() -> None:
    global VFF_Pipeline, VFF_Fusion, VFF_Builder, vff_available
    vff_str = str(VFF_ROOT)
    _push(vff_str)
    try:
        from core.ingestion.pipeline import IngestionPipeline
        from modules.fusion.fusion_module import FusionModule
        from modules.reporting.report_builder import ForensicReportBuilder

        VFF_Pipeline = IngestionPipeline
        VFF_Fusion = FusionModule
        VFF_Builder = ForensicReportBuilder
        vff_available = True
        logger.info("VFF engine loaded")
    except Exception as e:
        logger.error(f"VFF engine failed to load: {e}")
    finally:
        _stash("vff", ("core", "modules", "config", "database"))
        _pop(vff_str)


# ── AFF loader ────────────────────────────────────────────────────────────────

def _load_aff() -> None:
    global AFF_Pipeline, AFF_MetadataModule, AFF_ENFModule
    global AFF_NoiseModule, AFF_CompressionModule, AFF_ReverberationModule
    global AFF_VoiceModule, AFF_FusionEngine, aff_available
    aff_str = str(AFF_ROOT)
    _push(aff_str)
    try:
        from core.ingestion.pipeline import IngestionPipeline
        from modules.metadata.metadata_module import MetadataModule
        from modules.enf.enf_module import ENFModule
        from modules.noise.noise_module import NoiseModule as NoiseFloorModule
        from modules.compression.compression_module import CompressionModule
        from modules.reverberation.reverberation_module import ReverberationModule
        from modules.voice.voice_module import VoiceModule
        from modules.fusion.fusion_engine import FusionEngine

        AFF_Pipeline = IngestionPipeline
        AFF_MetadataModule = MetadataModule
        AFF_ENFModule = ENFModule
        AFF_NoiseModule = NoiseFloorModule
        AFF_CompressionModule = CompressionModule
        AFF_ReverberationModule = ReverberationModule
        AFF_VoiceModule = VoiceModule
        AFF_FusionEngine = FusionEngine
        aff_available = True
        logger.info("AFF engine loaded")
    except Exception as e:
        logger.error(f"AFF engine failed to load: {e}")
    finally:
        _stash("aff", ("core", "modules"))
        _pop(aff_str)


# ── Work engines loader ───────────────────────────────────────────────────────

def _load_work() -> None:
    global work_available
    work_str = str(WORK_ROOT)
    _push(work_str)
    try:
        import visual_forens       # noqa: F401
        import frame_analysis      # noqa: F401
        import video_forensics     # noqa: F401
        work_available = True
        logger.info("Work engines loaded")
    except Exception as e:
        logger.error(f"Work engines failed to load: {e}")


# ── Public interface ──────────────────────────────────────────────────────────

def initialize() -> None:
    global _initialized
    if _initialized:
        return
    logger.info("Initialising forensic engine registry…")
    _load_vff()
    _load_aff()
    _load_work()
    _initialized = True
    logger.info(
        f"Engine registry ready — "
        f"VFF={'OK' if vff_available else 'FAILED'}  "
        f"AFF={'OK' if aff_available else 'FAILED'}  "
        f"Work={'OK' if work_available else 'FAILED'}"
    )
