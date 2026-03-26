"""
Unified Visual Analysis API
Combines Frame Analysis and Visual Forensics endpoints
"""

import os
import sys
import json
import uuid
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ============================================================================
# PATH FIX - ensures Flask can find sibling modules (tasks, frame_analysis, etc.)
# regardless of the directory Python was launched from.
# ============================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Import task definitions (after path fix)
from tasks import (
    analyze_video_task,
    analyze_video_forensics_task,
    async_forensic_analysis,
    analyze_audio_task,
    compute_forensic_confidence,
    cleanup_old_jobs,
    run_cleanup,
    celery_app,
)
from audio_forensics.audio_forensics_system import AudioForensicsSystem

# ============================================================================
# FLASK APP CONFIGURATION
# ============================================================================

app = Flask(__name__)

PORT          = int(os.getenv('PORT', 5000))
REDIS_URL     = os.getenv('REDIS_URL', 'redis://localhost:6379')
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', '/tmp/uploads')
OUTPUT_FOLDER = os.getenv('OUTPUT_FOLDER', '/tmp/frame_analysis')

CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Rate limiter — uses Redis so limits are shared across all Flask workers.
# Individual endpoint limits are applied via @limiter.limit() decorators.
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=REDIS_URL,
    default_limits=[],          # no blanket default; explicit per endpoint
    strategy='fixed-window',
)

app.config['UPLOAD_FOLDER']       = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER']       = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH']  = 500 * 1024 * 1024   # 500 MB
app.config['CELERY_BROKER_URL']   = REDIS_URL
app.config['CELERY_RESULT_BACKEND'] = REDIS_URL

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'bmp', 'tiff', 'webp'}

# ============================================================================
# HELPERS
# ============================================================================

def allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS

def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

MAX_AUDIO_DURATION = 15 * 60  # 15 minutes
ALLOWED_AUDIO_EXTENSIONS = {"wav", "mp3", "flac", "ogg", "m4a"}


def validate_audio_file(path: str):
    ext = path.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio format '.{ext}'. "
            f"Allowed: {sorted(ALLOWED_AUDIO_EXTENSIONS)}"
        )

    try:
        # Use ffprobe to read duration — avoids audioread backend-probing
        # which can conflict with the coverage package at runtime.
        import subprocess as _sp, json as _json
        probe = _sp.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            raise RuntimeError(probe.stderr.strip() or "ffprobe returned a non-zero exit code")
        duration = float(_json.loads(probe.stdout)["format"]["duration"])
    except Exception as e:
        raise ValueError(f"Cannot read audio file: {e}") from e

    if duration > MAX_AUDIO_DURATION:
        raise ValueError(
            f"Audio duration ({duration / 60:.1f} min) exceeds the 15-minute limit"
        )

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/api/health', methods=['GET'])
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'services': {
            'frame_analysis':  'enabled',
            'visual_forensics': 'enabled',
            'video_forensics': 'enabled',
        },
        'endpoints': {
            'video':               'POST /api/analyze/video',
            'forensics':           'POST /api/forensics/analyze  (also: POST /api/analyze)',
            'video_forensics':     'POST /api/forensics/video/analyze',
            'video_forensics_status': 'GET  /api/forensics/video/status/<task_id>',
            'job_status':          'GET  /api/job/<job_id>',
            'result':              'GET  /api/result/<job_id>',
            'pdf':                 'GET  /api/export/pdf/<job_id>',
        },
        'timestamp': datetime.now().isoformat()
    }), 200

# ============================================================================
# FRAME ANALYSIS ENDPOINTS
# ============================================================================

@app.route('/api/analyze/video', methods=['POST'])
@limiter.limit("100 per hour")
def analyze_video_endpoint():
    """Submit a video for frame analysis."""
    try:
        if 'video' not in request.files:
            return jsonify({'error': 'No video file. Send the file under the key "video" in form-data.'}), 400

        file = request.files['video']
        if file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400

        if not allowed_video_file(file.filename):
            return jsonify({'error': f'Invalid video type. Allowed: {ALLOWED_VIDEO_EXTENSIONS}'}), 400

        mode          = request.form.get('mode', 'standard')
        sampling_mode = request.form.get('sampling_mode', 'sampled')

        filename = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(filepath)

        task = analyze_video_task.delay(filepath, mode, sampling_mode)

        return jsonify({
            'job_id': task.id,
            'status': 'queued',
            'type':   'video_analysis'
        }), 202

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# VISUAL FORENSICS ENDPOINTS
# Both URLs point to the same handler so Postman works with either.
# ============================================================================

def _handle_forensics_request():
    """Shared logic for forensic analysis (sync or async)."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded. Send the image under the key "file" in form-data.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    if not allowed_image_file(file.filename):
        return jsonify({'error': f'Invalid image type. Allowed: {ALLOWED_IMAGE_EXTENSIONS}'}), 400

    async_mode   = request.form.get('async', 'false').lower() == 'true'
    generate_pdf = request.form.get('pdf',   'true').lower()  == 'true'

    filename   = secure_filename(file.filename)
    unique_id  = str(uuid.uuid4())
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_id}_{filename}")
    output_dir  = os.path.join(app.config['OUTPUT_FOLDER'], unique_id)
    os.makedirs(output_dir, exist_ok=True)

    file.save(upload_path)

    if async_mode:
        task = async_forensic_analysis.delay(upload_path, output_dir, {'generate_pdf': generate_pdf})
        return jsonify({
            'status':   'processing',
            'task_id':  task.id,
            'type':     'forensic_analysis',
            'message':  'Analysis started. Poll GET /api/job/<task_id> for status.'
        }), 202

    else:
        # Synchronous path
        from visual_forens import generate_forensic_report
        report = generate_forensic_report(upload_path, output_dir, ai_detector=None)

        if generate_pdf:
            try:
                from pdfgneration import generate_pdf_report
                pdf_path = generate_pdf_report(report, output_dir)
                report['pdf_report'] = pdf_path
            except Exception as pdf_err:
                report['pdf_report'] = None
                app.logger.warning(f"PDF generation skipped: {pdf_err}")

        assessment = report.get('overall_assessment', {})
        return jsonify({
            'status':               'success',
            'verdict':              assessment.get('verdict', 'Unknown'),
            'tampering_likelihood': assessment.get('tampering_likelihood', 0),
            'confidence':           assessment.get('confidence', 'Unknown'),
            'report':               report,
            'output_directory':     output_dir,
        }), 200


@app.route('/api/forensics/analyze', methods=['POST'])
@limiter.limit("100 per hour")
def analyze_forensics():
    """Forensics endpoint (primary URL)."""
    try:
        return _handle_forensics_request()
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze', methods=['POST'])
@limiter.limit("100 per hour")
def analyze_forensics_alias():
    """Forensics endpoint (legacy alias — same handler)."""
    try:
        return _handle_forensics_request()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# AUDIO FORENSICS ENDPOINTS
# ============================================================================

@app.route("/api/forensics/audio/analyze", methods=["POST"])
@limiter.limit("100 per hour")
def analyze_audio_forensics():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No audio file provided"}), 400

        audio_file = request.files["file"]
        if audio_file.filename == "":
            return jsonify({"error": "Empty filename"}), 400

        case_id    = request.form.get("case_id", f"CASE_{uuid.uuid4().hex[:8]}")
        examiner   = request.form.get("examiner", "API Examiner")
        async_mode = request.form.get("async", "false").lower() == "true"

        upload_dir = app.config["UPLOAD_FOLDER"]
        os.makedirs(upload_dir, exist_ok=True)

        filename    = secure_filename(audio_file.filename)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        audio_path  = os.path.join(upload_dir, unique_name)
        audio_file.save(audio_path)

        try:
            validate_audio_file(audio_path)
        except ValueError as ve:
            os.remove(audio_path)
            return jsonify({"error": str(ve)}), 400

        if async_mode:
            task = analyze_audio_task.delay(audio_path, case_id, examiner)
            return jsonify({
                "status":  "submitted",
                "task_id": task.id,
                "case_id": case_id,
            }), 202

        # Synchronous path — call the system directly
        evidence_id = f"AUDIO_{uuid.uuid4().hex[:10]}"
        system = AudioForensicsSystem(
            case_id=case_id,
            examiner_name=examiner,
            enable_enhancements=True,
        )
        results    = system.run_full_analysis(
            audio_file_path=audio_path,
            evidence_id=evidence_id,
            enable_enhancement=True,
        )
        confidence = compute_forensic_confidence(results)
        return jsonify({
            "status":           "success",
            "case_id":          case_id,
            "evidence_id":      evidence_id,
            "confidence_score": confidence,
            "results":          results,
        }), 200

    except Exception as e:
        app.logger.error(f"Audio forensics endpoint error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ============================================================================
# JOB STATUS ENDPOINTS
# ============================================================================

@app.route('/api/job/<job_id>', methods=['GET'])
@app.route('/api/status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Check job status for any analysis type."""
    try:
        task = celery_app.AsyncResult(job_id)

        if task.state == 'PENDING':
            return jsonify({'status': 'pending', 'message': 'Task is waiting to start'})

        elif task.state == 'PROCESSING':
            return jsonify({
                'status':   'processing',
                'stage':    task.info.get('stage', 'Unknown'),
                'progress': task.info.get('progress', 0)
            })

        elif task.state == 'SUCCESS':
            return jsonify({'status': 'completed', 'result': task.result})

        elif task.state == 'FAILURE':
            return jsonify({'status': 'failed', 'error': str(task.info)}), 500

        else:
            return jsonify({'status': task.state.lower(), 'info': str(task.info)})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# VIDEO FORENSICS ENDPOINTS
# ============================================================================

ALLOWED_VIDEO_FORENSICS_MODES = {"standard", "high_sensitivity", "deep_scan"}


@app.route("/api/forensics/video/analyze", methods=["POST"])
@limiter.limit("100 per hour")
def analyze_video_forensics_endpoint():
    """
    Submit a video for bitstream / metadata / deepfake / motion / ENF forensic analysis.

    Form params
    -----------
    file   : video file (required)
    mode   : standard | high_sensitivity | deep_scan  (default: standard)
    async  : true | false  (default: false)

    Async response
    --------------
    { job_id, status: 'queued', type: 'video_forensics' }

    Sync response
    -------------
    Full VideoForensicResult dict.
    """
    if "file" not in request.files:
        return jsonify({"error": "No video file provided. Send the file under the key 'file' in form-data."}), 400

    video_file = request.files["file"]
    if video_file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    if not allowed_video_file(video_file.filename):
        return jsonify({"error": f"Invalid video type. Allowed: {ALLOWED_VIDEO_EXTENSIONS}"}), 400

    mode = request.form.get("mode", "standard")
    if mode not in ALLOWED_VIDEO_FORENSICS_MODES:
        return jsonify({"error": f"Invalid mode '{mode}'. Allowed: {sorted(ALLOWED_VIDEO_FORENSICS_MODES)}"}), 400

    async_mode = request.form.get("async", "false").lower() == "true"

    upload_dir = app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)

    filename    = secure_filename(video_file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    video_path  = os.path.join(upload_dir, unique_name)
    video_file.save(video_path)

    if async_mode:
        task = analyze_video_forensics_task.delay(video_path, mode)
        return jsonify({
            "job_id": task.id,
            "status": "queued",
            "type":   "video_forensics",
        }), 202

    # Synchronous path — run the engine directly (no Celery overhead)
    try:
        from video_forensics import VideoForensicsEngine
        import dataclasses

        job_id     = str(uuid.uuid4())
        output_dir = os.path.join(app.config["OUTPUT_FOLDER"], job_id)
        engine     = VideoForensicsEngine(job_id=job_id, output_dir=output_dir)
        result     = engine.analyze_video(video_path=video_path, mode=mode)

        def _to_dict(obj):
            if dataclasses.is_dataclass(obj):
                return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, list):
                return [_to_dict(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _to_dict(v) for k, v in obj.items()}
            if hasattr(obj, "value"):
                return obj.value
            return obj

        return jsonify(_to_dict(result)), 200

    except Exception as e:
        app.logger.error(f"Sync video forensics failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/forensics/video/status/<task_id>", methods=["GET"])
def get_video_forensics_status(task_id):
    """Poll the status of an async video forensics job."""
    try:
        task = celery_app.AsyncResult(task_id)

        if task.state == "PENDING":
            return jsonify({
                "status":  "pending",
                "message": "Video forensics analysis is waiting to start",
            })

        elif task.state == "PROCESSING":
            return jsonify({
                "status":   "processing",
                "stage":    task.info.get("stage", "Unknown"),
                "progress": task.info.get("progress", 0),
            })

        elif task.state == "SUCCESS":
            return jsonify({
                "status": "completed",
                "result": task.result,
            })

        elif task.state == "FAILURE":
            return jsonify({
                "status": "failed",
                "error":  str(task.info),
            }), 500

        return jsonify({
            "status": task.state.lower(),
            "info":   str(task.info),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/forensics/audio/status/<task_id>", methods=["GET"])
def get_audio_forensics_status(task_id):
    try:
        task = celery_app.AsyncResult(task_id)

        if task.state == "PENDING":
            return jsonify({
                "status": "pending",
                "message": "Audio analysis is waiting to start"
            })

        elif task.state == "PROCESSING":
            return jsonify({
                "status": "processing",
                "stage": task.info.get("stage", "Unknown"),
                "progress": task.info.get("progress", 0)
            })

        elif task.state == "SUCCESS":
            return jsonify({
                "status": "completed",
                "result": task.result
            })

        elif task.state == "FAILURE":
            return jsonify({
                "status": "failed",
                "error": str(task.info)
            }), 500

        return jsonify({
            "status": task.state.lower(),
            "info": str(task.info)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# RESULT / DOWNLOAD ENDPOINTS
# ============================================================================

@app.route('/api/result/<job_id>', methods=['GET'])
def get_result(job_id):
    """
    Get saved JSON result for a completed job.

    Includes an `integrity` block that verifies the result has not been
    modified since it was written (chain-of-custody check).
    """
    try:
        result_path = os.path.join(app.config['OUTPUT_FOLDER'], job_id, 'result.json')
        if not os.path.exists(result_path):
            return jsonify({'error': 'Result not found'}), 404

        with open(result_path, 'rb') as f:
            raw = f.read()

        data = json.loads(raw)

        # ── Integrity verification ────────────────────────────────────────────
        hash_path = result_path + '.sha256'
        integrity = {'verified': False, 'note': 'No integrity sidecar found'}
        if os.path.exists(hash_path):
            with open(hash_path) as fh:
                stored_hash = fh.read().strip()
            current_hash = hashlib.sha256(raw).hexdigest()
            if current_hash == stored_hash:
                integrity = {'verified': True, 'sha256': current_hash}
            else:
                integrity = {
                    'verified': False,
                    'note': 'Hash mismatch — result.json may have been modified',
                    'stored_sha256':  stored_hash,
                    'current_sha256': current_hash,
                }

        return jsonify({'integrity': integrity, 'result': data})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/cleanup', methods=['POST'])
def trigger_cleanup():
    """
    Manually trigger file cleanup.  Deletes uploads and job output directories
    older than CLEANUP_MAX_AGE_HOURS (default 24 h, set via env var).

    Optional JSON body:
      { "max_age_hours": 12 }   — override TTL for this run only
      { "async": true }         — dispatch as a Celery task instead of blocking
    """
    try:
        body         = request.get_json(silent=True) or {}
        max_age      = body.get('max_age_hours')
        use_async    = str(body.get('async', 'false')).lower() == 'true'

        if use_async:
            task = cleanup_old_jobs.delay()
            return jsonify({'status': 'queued', 'task_id': task.id}), 202

        summary = run_cleanup(
            upload_folder=app.config['UPLOAD_FOLDER'],
            output_folder=app.config['OUTPUT_FOLDER'],
            max_age_hours=int(max_age) if max_age is not None else None,
        )
        return jsonify({'status': 'done', 'summary': summary}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/export/pdf/<job_id>', methods=['GET'])
def export_pdf(job_id):
    """Download PDF report for a completed job."""
    try:
        pdf_path = os.path.join(app.config['OUTPUT_FOLDER'], job_id, 'result_report.pdf')
        if not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF not found. Ensure the job completed and PDF generation was enabled.'}), 404

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'analysis-report-{job_id}.pdf'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download/<path:filename>', methods=['GET'])
def download_file(filename):
    """Download any generated file (heatmaps, etc.) — path-traversal safe."""
    try:
        base     = os.path.realpath(app.config['OUTPUT_FOLDER'])
        resolved = os.path.realpath(os.path.join(base, filename))

        if not resolved.startswith(base + os.sep):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(resolved):
            return jsonify({'error': 'File not found'}), 404

        return send_file(resolved, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("UNIFIED VISUAL ANALYSIS API")
    print("=" * 60)
    print(f"\nListening on http://0.0.0.0:{PORT}")
    print("\nAll endpoints:")
    print("  GET  /api/health")
    print("  POST /api/analyze/video")
    print("  POST /api/forensics/analyze              <- visual forensics (primary)")
    print("  POST /api/analyze                        <- visual forensics (alias)")
    print("  POST /api/forensics/video/analyze        <- video forensics (bitstream/deepfake/ENF)")
    print("  GET  /api/forensics/video/status/<id>    <- video forensics job status")
    print("  GET  /api/job/<job_id>")
    print("  GET  /api/status/<job_id>")
    print("  GET  /api/result/<job_id>")
    print("  GET  /api/export/pdf/<job_id>")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=PORT)
