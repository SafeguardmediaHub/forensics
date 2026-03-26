"""
Unified Celery Tasks
Contains all async task definitions for Frame Analysis and Visual Forensics
"""

import os

# Must be set before librosa / numba are imported anywhere in this process.
# The 'coverage' package conflicts with numba's JIT compiler; disabling JIT
# makes librosa fall back to pure-Python/numpy paths, fixing AttributeError:
# "module 'coverage' has no attribute 'types'".
os.environ.setdefault('NUMBA_DISABLE_JIT', '1')
import sys
import json
import hashlib
import logging
import uuid
import shutil
import time
import traceback
from dataclasses import asdict
from typing import List

# ============================================================================
# PATH FIX - must run before any local imports so the worker can find
# frame_analysis.py, visual_forens.py, and pdfgneration.py regardless of
# the working directory it was launched from.
# ============================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from celery import Celery, shared_task
from celery.schedules import crontab
from audio_forensics.audio_forensics_system import AudioForensicsSystem
from frame_analysis import FrameAnalysisEngine, AnalysisMode, SamplingMode
from visual_forens import generate_forensic_report
from forensic_config import CLEANUP_MAX_AGE_HOURS, CLEANUP_INTERVAL_SECONDS
from video_forensics import VideoForensicsEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

def compute_forensic_confidence(results: dict) -> float:
    """
    Forensic-grade confidence scoring.

    A pure weighted average of module confidences always returns a high score
    because three "clean" modules (integrity 30%, ENF 20%, duplicates 10% = 60%)
    drown out modules that found something.  This caused speaker-swapped and
    spliced files to score 0.78+ ("Likely Authentic").

    New formula:
      score = (weighted_avg * 0.5) + (worst_module_conf * 0.5)
                - (flagged_module_count * 0.12)

    Rationale:
      - Weighted average captures the breadth signal (most modules are clean)
      - Worst module confidence captures the severity signal (one bad finding matters)
      - Each module that raised at least one issue deducts 12 percentage points
        (in forensics, one confirmed indicator of tampering is significant)

    Clean file (all modules 0.9+, zero issues)  → score ≈ 0.90 ("Likely Authentic")
    1 module flags (events 0.1, rest clean)      → score ≈ 0.56 ("Review Recommended")
    2+ modules flag                              → score < 0.40 ("Likely Tampered")
    """
    module_weights = {
        "integrity":     0.30,
        "speaker_match": 0.25,
        "enf":           0.20,
        "events":        0.15,
        "duplicates":    0.10,
    }

    # Exclude skipped modules (confidence is None — e.g. speaker_match and
    # duplicates are skipped for music audio).  Remaining weights are
    # renormalised so they still sum to 1.0.
    active = {
        k: w for k, w in module_weights.items()
        if results.get(k, {}).get("confidence") is not None
    }
    if not active:
        return 0.5   # nothing to assess — return neutral

    total_w = sum(active.values())
    active  = {k: w / total_w for k, w in active.items()}

    confs = {k: float(results[k]["confidence"]) for k in active}

    # Weighted average (breadth)
    base = sum(confs[k] * active[k] for k in active)

    # Worst individual module confidence (severity)
    min_conf = min(confs.values())

    # Count active modules that flagged at least one issue
    flagged_count = sum(
        1 for k in active
        if results.get(k, {}).get("issues", [])
    )

    score = (base * 0.5) + (min_conf * 0.5) - (flagged_count * 0.20)
    return round(float(max(0.0, min(score, 1.0))), 3)

# ============================================================================
# CELERY CONFIGURATION
# ============================================================================

REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')

celery_app = Celery(
    'unified_analysis',
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Windows compatibility: Force solo pool on Windows
if sys.platform == 'win32':
    celery_app.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
        worker_pool='solo',
        broker_connection_retry_on_startup=True,
    )
else:
    celery_app.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
        beat_schedule={
            'cleanup-old-jobs': {
                'task':     'tasks.cleanup_old_jobs',
                'schedule': CLEANUP_INTERVAL_SECONDS,  # seconds between runs
            },
        },
    )

# ============================================================================
# FRAME ANALYSIS TASKS
# ============================================================================

@celery_app.task(bind=True, name="work.analyze_video_task")
def analyze_video_task(self, video_path: str, mode: str, sampling_mode: str):
    """
    Celery task for video analysis.

    Runs two independent engines in sequence and surfaces a verdict from each:

    1. Frame Analysis  — temporal anomaly detection (abrupt cuts, frame
       duplication, quality drift, GOP irregularities, AV sync, motion).
    2. Visual Forensics — spatial/content detection on a sample of frames
       (ELA, noise patterns, copy-move) via generate_forensic_report_video().

    Both verdicts are included in the response.  An aggregate verdict is
    computed by taking the more severe of the two.
    """
    job_id     = self.request.id
    output_dir = f"/tmp/frame_analysis/{job_id}"

    try:
        os.makedirs(output_dir, exist_ok=True)

        if not os.path.isfile(video_path):
            raise FileNotFoundError(
                f"video_path '{video_path}' not found in the worker process. "
                "Ensure UPLOAD_FOLDER is on a shared filesystem accessible by "
                "both the Flask app and the Celery worker."
            )

        # ── Stage 1: Frame Analysis (temporal) ───────────────────────────────
        self.update_state(
            state='PROCESSING',
            meta={'stage': 'Frame analysis — initializing', 'progress': 5},
        )

        engine = FrameAnalysisEngine(job_id, output_dir)

        self.update_state(
            state='PROCESSING',
            meta={'stage': 'Frame analysis — processing video', 'progress': 15},
        )

        fa_result = engine.analyze_video(
            video_path,
            AnalysisMode(mode),
            SamplingMode(sampling_mode),
        )

        self.update_state(
            state='PROCESSING',
            meta={'stage': 'Frame analysis — saving result', 'progress': 50},
        )

        fa_result_path = os.path.join(output_dir, "frame_analysis_result.json")
        with open(fa_result_path, 'w') as f:
            json.dump(asdict(fa_result), f, default=str, indent=2)

        frame_analysis_summary = {
            'verdict':             fa_result.verdict,
            'tampering_confidence': fa_result.tampering_confidence,
            'tampering_type':      fa_result.tampering_type,
            'verdict_explanation': fa_result.verdict_explanation,
            'temporal_findings_count': len(fa_result.findings),
            'spatial_findings_count':  len(fa_result.spatial_findings),
            'result_path':         fa_result_path,
        }

        self.update_state(
            state='PROCESSING',
            meta={'stage': 'Computing verdict', 'progress': 70},
        )

        # ── Stage 2: Derive visual forensics summary from Layer 1 spatial findings
        #
        # Layer 1 (inside FrameAnalysisEngine._run_spatial_forensics) already ran
        # ELA/noise/clone on the frames flagged by temporal analysis plus a random
        # 5 % baseline.  This is targeted, precise, and already integrated into
        # fa_result.verdict.  A second broad sweep (generate_forensic_report_video)
        # was previously run here but was redundant, slow (~6 min), and diluted the
        # spatial signal by averaging across clean frames.  It has been removed.
        # The visual_forensics response section is now derived from Layer 1 data.

        _SEV_PTS = {'HIGH': 25, 'MEDIUM': 12, 'LOW': 4}
        spatial_score = min(
            sum(_SEV_PTS.get(f.severity.value, 0) for f in fa_result.spatial_findings),
            100.0,
        )
        flagged = sum(1 for f in fa_result.spatial_findings if f.severity.value == 'HIGH')

        if spatial_score >= 75.0:
            sp_verdict, sp_conf = 'Likely Tampered', 'High'
        elif spatial_score >= 35.0:
            sp_verdict, sp_conf = 'Possibly Tampered', 'Medium'
        else:
            sp_verdict, sp_conf = 'Likely Authentic', 'High'

        visual_forensics_summary = {
            'verdict':              sp_verdict,
            'tampering_likelihood': round(spatial_score, 1),
            'confidence':           sp_conf,
            'frames_analyzed':      len(fa_result.spatial_findings),
            'flagged_frames':       flagged,
            'source':               'frame_analysis_spatial_layer',
            'note': (
                'Spatial forensics on temporally-flagged frames + 5 % random '
                'baseline (Layer 1). No separate full-video visual forensics '
                'pass is run — see frame_analysis for the unified verdict.'
            ),
        }

        # ── Stage 3: Aggregate verdict ────────────────────────────────────────
        # fa_result.verdict already incorporates both temporal and spatial (Layer 1)
        # scores via _compute_verdict.  The aggregate is therefore the frame
        # analysis verdict itself — no second engine to reconcile.

        aggregate_verdict = {
            'verdict':    fa_result.verdict,
            'confidence': round(fa_result.tampering_confidence, 1),
            'note': (
                'Unified verdict from temporal (optical flow, scene, GOP, motion) '
                'and spatial (ELA, noise, clone — targeted on flagged frames) '
                'engines within FrameAnalysisEngine.'
            ),
        }

        # ── Save combined result.json (required by /api/result/<job_id>) ────────
        combined_result = {
            'job_id':            job_id,
            'status':            'completed',
            'aggregate_verdict': aggregate_verdict,
            'frame_analysis':    frame_analysis_summary,
            'visual_forensics':  visual_forensics_summary,
            'findings': {
                # Full finding detail so callers don't need a second request
                'temporal': [asdict(f) for f in fa_result.findings],
                'spatial':  [asdict(f) for f in fa_result.spatial_findings],
            },
            'created_at':        fa_result.created_at,
            'completed_at':      fa_result.completed_at,
        }
        result_path = os.path.join(output_dir, 'result.json')
        with open(result_path, 'w') as f:
            json.dump(combined_result, f, default=str, indent=2)

        # ── Integrity sidecar (chain of custody) ──────────────────────────────
        # Write a SHA-256 hash of result.json so the GET /api/result endpoint
        # can detect any post-hoc modifications to the stored result.
        with open(result_path, 'rb') as f:
            result_sha256 = hashlib.sha256(f.read()).hexdigest()
        with open(result_path + '.sha256', 'w') as f:
            f.write(result_sha256)

        self.update_state(
            state='PROCESSING',
            meta={'stage': 'Generating PDF report', 'progress': 90},
        )

        # ── PDF (non-fatal) ───────────────────────────────────────────────────
        pdf_path = None
        try:
            from pdfgneration import generate_pdf_report
            pdf_path = generate_pdf_report(fa_result_path)
            logger.info(f"PDF report generated: {pdf_path}")
        except Exception as pdf_err:
            logger.error(f"PDF generation failed (non-fatal): {pdf_err}")

        self.update_state(
            state='PROCESSING',
            meta={'stage': 'Complete', 'progress': 100},
        )

        return {
            'status':             'completed',
            'aggregate_verdict':  aggregate_verdict,
            'frame_analysis':     frame_analysis_summary,
            'visual_forensics':   visual_forensics_summary,
            'pdf_path':           pdf_path,
            'result_path':        result_path,
        }

    except Exception as e:
        logger.error(f"Video analysis failed: {e}")
        return {
            'status': 'failed',
            'error':  str(e),
        }


# ============================================================================
# VISUAL FORENSICS TASKS
# ============================================================================

@celery_app.task(bind=True)
def async_forensic_analysis(self, image_path: str, output_dir: str, options: dict = None):
    """
    Celery task for complete forensic analysis.
    Detects image manipulation and generates a comprehensive report.
    """
    try:
        if options is None:
            options = {}

        # Guard 1: create output dir in THIS worker process
        os.makedirs(output_dir, exist_ok=True)

        # Guard 2: confirm the uploaded image file exists on disk
        if not os.path.isfile(image_path):
            raise FileNotFoundError(
                f"image_path '{image_path}' not found in the worker process. "
                "Ensure UPLOAD_FOLDER is on a shared filesystem accessible by "
                "both the Flask app and the Celery worker."
            )

        self.update_state(state='PROCESSING', meta={'stage': 'Initializing', 'progress': 0})
        self.update_state(state='PROCESSING', meta={'stage': 'Extracting metadata', 'progress': 10})
        self.update_state(state='PROCESSING', meta={'stage': 'Running manipulation detection', 'progress': 30})
        self.update_state(state='PROCESSING', meta={'stage': 'Finalizing analysis', 'progress': 60})

        # Generate complete report (AI detector disabled)
        report = generate_forensic_report(image_path, output_dir, ai_detector=None)

        # Generate PDF (non-fatal if it fails)
        if options.get('generate_pdf', True):
            self.update_state(state='PROCESSING', meta={'stage': 'Generating PDF report', 'progress': 90})
            try:
                from vfappss import generate_pdf_report
                pdf_path = generate_pdf_report(report, output_dir)
                report['pdf_report'] = pdf_path
                logger.info(f"PDF report generated: {pdf_path}")
            except Exception as pdf_err:
                logger.error(f"PDF generation failed (non-fatal): {pdf_err}")

        self.update_state(state='PROCESSING', meta={'stage': 'Complete', 'progress': 100})

        assessment = report.get('overall_assessment', {})
        return {
            'status':     'success',
            'verdict':    assessment.get('verdict', 'Unknown'),
            'tampering_likelihood': assessment.get('tampering_likelihood', 0),
            'confidence': assessment.get('confidence', 'Unknown'),
            'report':     report,
            'output_dir': output_dir,
        }

    except Exception as e:
        logger.error(f"Forensic analysis failed: {e}")
        return {
            'status': 'error',
            'error':  str(e),
        }


# ============================================================================
# AUDIO FORENSICS TASKS
# ============================================================================

@celery_app.task(bind=True, name="tasks.analyze_audio_task")
def analyze_audio_task(
    self,
    audio_path: str,
    case_id: str,
    examiner: str = "System Examiner",
    enable_enhancements: bool = True
):
    try:
        self.update_state(
            state="PROCESSING",
            meta={"stage": "Initializing case", "progress": 5}
        )

        evidence_id = f"AUDIO_{uuid.uuid4().hex[:10]}"

        system = AudioForensicsSystem(
            case_id=case_id,
            examiner_name=examiner,
            enable_enhancements=enable_enhancements
        )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Chain of custody validation", "progress": 15}
        )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Signal enhancement & cleanup", "progress": 30}
        )

        results = system.run_full_analysis(
            audio_file_path=audio_path,
            evidence_id=evidence_id,
            enable_enhancement=True
        )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Generating forensic report", "progress": 90}
        )
        confidence = compute_forensic_confidence(results)

        return {
            "status": "success",
            "case_id": case_id,
            "evidence_id": evidence_id,
            "confidence_score": confidence,
            "results": results
        }

    except Exception as e:
        logger.error(f"Audio forensics task failed: {e}")
        return {"status": "failed", "error": str(e)}


# ============================================================================
# VIDEO FORENSICS TASKS
# ============================================================================

@celery_app.task(bind=True, name="work.analyze_video_forensics_task")
def analyze_video_forensics_task(self, video_path: str, mode: str = "standard"):
    """
    Celery task for video forensics analysis.

    Runs all video forensics sub-detectors in sequence:
      - CodecBitstreamForensics (double-encoding, bitrate anomalies, codec inconsistency)
      - MetadataChainForensics  (container anomalies, timestamp discrepancies, encoder mismatch)
      - ENFVideoForensics        (electrical network frequency phase discontinuities)
      - MotionVectorForensics    (optical flow anomalies, composite region detection)
      - DeepfakeDetector         (facial consistency, frequency artifacts)

    Each sub-module is non-fatal — failures produce partial results.
    """
    job_id     = self.request.id
    output_dir = f"/tmp/video_forensics/{job_id}"

    try:
        os.makedirs(output_dir, exist_ok=True)

        if not os.path.isfile(video_path):
            raise FileNotFoundError(
                f"video_path '{video_path}' not found in the worker process. "
                "Ensure UPLOAD_FOLDER is on a shared filesystem accessible by "
                "both the Flask app and the Celery worker."
            )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Initializing video forensics engine", "progress": 5},
        )

        engine = VideoForensicsEngine(job_id=job_id, output_dir=output_dir)

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Codec and bitstream analysis", "progress": 20},
        )

        # The engine handles all sub-modules internally with state updates
        # via its own logging.  We surface coarse progress milestones here.
        self.update_state(
            state="PROCESSING",
            meta={"stage": "Metadata chain analysis", "progress": 40},
        )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "ENF signal analysis", "progress": 55},
        )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Motion vector analysis", "progress": 70},
        )

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Deepfake indicator analysis", "progress": 85},
        )

        result = engine.analyze_video(video_path=video_path, mode=mode)

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Saving result", "progress": 95},
        )

        # Serialize result to dict (VideoForensicResult is a dataclass)
        import dataclasses

        def _to_dict(obj):
            if dataclasses.is_dataclass(obj):
                return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, list):
                return [_to_dict(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _to_dict(v) for k, v in obj.items()}
            if hasattr(obj, "value"):  # Enum
                return obj.value
            return obj

        result_dict = _to_dict(result)

        result_path = os.path.join(output_dir, "result.json")
        with open(result_path, "w") as f:
            json.dump(result_dict, f, default=str, indent=2)

        self.update_state(
            state="PROCESSING",
            meta={"stage": "Complete", "progress": 100},
        )

        return {
            "status":         "completed",
            "job_id":         job_id,
            "verdict":        result.verdict,
            "confidence":     result.confidence,
            "tampering_types": result.tampering_types,
            "findings_count": len(result.findings),
            "result_path":    result_path,
            "result":         result_dict,
        }

    except Exception as e:
        logger.error(f"Video forensics analysis failed: {e}")
        return {
            "status": "failed",
            "error":  str(e),
        }


# ============================================================================
# FILE CLEANUP
# ============================================================================

def _delete_old_entries(folder: str, max_age_seconds: float) -> dict:
    """
    Delete files and subdirectories inside *folder* that are older than
    *max_age_seconds* based on their last-modified time.

    Returns a summary dict with counts.
    """
    removed_dirs  = 0
    removed_files = 0
    errors        = []
    now           = time.time()

    if not os.path.isdir(folder):
        return {'removed_dirs': 0, 'removed_files': 0, 'errors': []}

    for entry in os.scandir(folder):
        try:
            age = now - entry.stat().st_mtime
            if age < max_age_seconds:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
                removed_dirs += 1
            else:
                os.remove(entry.path)
                removed_files += 1
        except Exception as e:
            errors.append(f"{entry.path}: {e}")

    return {
        'removed_dirs':  removed_dirs,
        'removed_files': removed_files,
        'errors':        errors,
    }


def run_cleanup(upload_folder: str, output_folder: str,
                max_age_hours: int = None) -> dict:
    """
    Delete uploads and job output directories older than *max_age_hours*.
    Falls back to CLEANUP_MAX_AGE_HOURS from forensic_config if not specified.
    Callable directly (sync) or via the Celery task.
    """
    if max_age_hours is None:
        max_age_hours = CLEANUP_MAX_AGE_HOURS

    max_age_seconds = max_age_hours * 3600

    upload_result = _delete_old_entries(upload_folder, max_age_seconds)
    output_result = _delete_old_entries(output_folder, max_age_seconds)

    summary = {
        'max_age_hours':   max_age_hours,
        'uploads_removed': upload_result,
        'outputs_removed': output_result,
    }
    logger.info(
        f"Cleanup complete — uploads: {upload_result['removed_files']} files, "
        f"outputs: {output_result['removed_dirs']} dirs"
    )
    return summary


@celery_app.task(name='tasks.cleanup_old_jobs')
def cleanup_old_jobs():
    """
    Periodic Celery task: delete uploads and job output dirs older than
    CLEANUP_MAX_AGE_HOURS.  Runs automatically when celery beat is active.
    Can also be called manually via POST /api/admin/cleanup.
    """
    upload_folder = os.getenv('UPLOAD_FOLDER', '/tmp/uploads')
    output_folder = os.getenv('OUTPUT_FOLDER', '/tmp/frame_analysis')
    return run_cleanup(upload_folder, output_folder)


# ============================================================================
# TASK UTILITIES
# ============================================================================

def get_task_status(task_id: str) -> dict:
    """Utility function to get task status"""
    from celery.result import AsyncResult

    task = AsyncResult(task_id, app=celery_app)

    if task.state == 'PENDING':
        return {'status': 'pending', 'message': 'Task is waiting to start'}
    elif task.state == 'PROCESSING':
        return {
            'status': 'processing',
            'stage': task.info.get('stage', 'Unknown'),
            'progress': task.info.get('progress', 0)
        }
    elif task.state == 'SUCCESS':
        return {'status': 'completed', 'result': task.result}
    elif task.state == 'FAILURE':
        return {'status': 'failed', 'error': str(task.info)}
    else:
        return {'status': task.state.lower(), 'info': str(task.info)}


# ============================================================================
# CELERY WORKER STARTUP
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("UNIFIED CELERY WORKER")
    print("=" * 60)
    print("\nTask Types Available:")
    print("  * analyze_video_task           - Video frame analysis")
    print("  * async_forensic_analysis      - Image manipulation detection")
    print("\nTo start worker:")
    print("  celery -A tasks worker --loglevel=info")
    print("=" * 60)