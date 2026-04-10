import pytest

from api.services.analysis_service import _strip_engine_verdicts


class TestStripEngineVerdicts:
    """Verify that verdict-related keys are removed from engine_detail."""

    def test_strips_top_level_verdict_keys(self):
        raw = {
            "engine": "aff",
            "verdict": "likely_authentic",
            "verdict_label": "Likely Authentic",
            "fused_probability": 0.82,
            "confidence": 0.77,
        }
        cleaned = _strip_engine_verdicts(raw)
        assert "verdict" not in cleaned
        assert "verdict_label" not in cleaned
        # Non-verdict keys preserved
        assert cleaned["engine"] == "aff"
        assert cleaned["fused_probability"] == 0.82
        assert cleaned["confidence"] == 0.77

    def test_strips_nested_report_blocks(self):
        raw = {
            "engine": "work",
            "verdict": "Likely Authentic",
            "tampering_likelihood": 17.0,
            "report": {
                "overall_assessment": {"verdict": "Likely Authentic", "tampering_likelihood": 17},
                "user_friendly_summary": {"status": "LOW RISK", "trust_level": "Likely Authentic"},
                "manipulation_detection": {"ela": {"ela_score": 0.18}},
            },
        }
        cleaned = _strip_engine_verdicts(raw)
        assert "verdict" not in cleaned
        assert "tampering_likelihood" not in cleaned
        assert "overall_assessment" not in cleaned["report"]
        assert "user_friendly_summary" not in cleaned["report"]
        # Non-verdict report keys preserved
        assert cleaned["report"]["manipulation_detection"]["ela"]["ela_score"] == 0.18

    def test_strips_frames_verdict_keys(self):
        raw = {
            "engine": "work",
            "verdict": "LIKELY_AUTHENTIC",
            "verdict_explanation": "Low confidence",
            "tampering_confidence": 0.12,
            "findings": [],
        }
        cleaned = _strip_engine_verdicts(raw)
        assert "verdict" not in cleaned
        assert "verdict_explanation" not in cleaned
        assert "tampering_confidence" not in cleaned
        assert cleaned["findings"] == []

    def test_handles_no_report_key(self):
        raw = {"engine": "aff", "verdict": "likely_authentic"}
        cleaned = _strip_engine_verdicts(raw)
        assert "report" not in cleaned
        assert "verdict" not in cleaned

    def test_does_not_mutate_original(self):
        raw = {"verdict": "test", "engine": "aff"}
        _strip_engine_verdicts(raw)
        assert "verdict" in raw


@pytest.mark.asyncio
async def test_unified_analyze_rejects_invalid_media_type(client):
    response = await client.post(
        "/api/v1/analyze",
        data={"media_type": "document"},
        files={"file": ("sample.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 422
    assert "Unsupported media_type" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unified_analyze_rejects_invalid_options_json(client):
    response = await client.post(
        "/api/v1/analyze",
        data={"media_type": "image", "options": "{bad json"},
        files={"file": ("sample.png", b"fake", "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid JSON in 'options'"


@pytest.mark.asyncio
async def test_unified_analyze_returns_normalized_result(client, monkeypatch):
    from api.services import analysis_service

    def fake_run_runner(*, media_type, file_path, output_dir, options, timeout_s):
        assert media_type == "image"
        assert file_path.name.endswith(".png")
        assert output_dir is not None
        assert options == {"generate_report": True}
        return {
            "engine": "work",
            "mode": "image",
            "verdict": "Likely Authentic",
            "tampering_likelihood": 17.0,
            "confidence": "High",
            "note": "Assessment based on forensic analysis only (AI detection disabled)",
            "file": {"sha256": "abc123"},
            "report": {},
        }

    monkeypatch.setattr(analysis_service, "_run_runner", fake_run_runner)

    response = await client.post(
        "/api/v1/analyze",
        data={"media_type": "image", "options": '{"generate_report": true}'},
        files={"file": ("sample.png", b"fake-image-bytes", "image/png")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["media_type"] == "image"
    assert data["engine"] == "work"

    # New fields
    assert data["risk_score"] == 17
    assert data["risk_band"] == "low"
    assert data["measurement_confidence"] == 0.9
    assert data["calibration_status"] == "pre_calibration"
    assert isinstance(data["elevated_detectors"], list)
    # Empty report → no EXIF → `exif_metadata` flagged as elevated + unavailable
    assert "exif_metadata" in data["elevated_detectors"]
    assert any(c["name"] == "exif_metadata" for c in data["checks_unavailable"])
    assert data["interpretation"]["summary"]
    assert data["interpretation"]["what_this_means"]
    actions = [s["action"] for s in data["interpretation"]["next_steps"]]
    assert "reverse_image_lookup" in actions
    assert "verify_source" in actions

    # Deprecated fields — present but null
    assert data["verdict"] is None
    assert data["verdict_label"] is None
    assert data["probability"] is None
    assert data["tampering_likelihood"] is None

    # Unchanged
    assert data["file"]["filename"] == "sample.png"
    assert data["file"]["sha256"] == "abc123"
    # engine_detail should NOT contain verdict fields
    assert "verdict" not in data["engine_detail"]
    assert "tampering_likelihood" not in data["engine_detail"]
    # but should still carry non-verdict engine output
    assert data["engine_detail"]["engine"] == "work"


@pytest.mark.asyncio
async def test_legacy_audio_route_uses_normalized_runner_result(client, monkeypatch):
    from api.services import analysis_service

    def fake_run_runner(*, media_type, file_path, output_dir, options, timeout_s):
        assert media_type == "audio"
        assert output_dir is None
        return {
            "engine": "aff",
            "verdict": "likely_authentic",
            "verdict_label": "Likely Authentic",
            "fused_probability": 0.82,
            "confidence": 0.77,
            "elevated_modules": ["noise", "voice"],
            "module_scores": {
                "metadata": 0.1,
                "enf": 0.2,
                "noise": 0.8,
                "compression": 0.3,
                "reverberation": 0.4,
                "voice": 0.85,
            },
            "skipped_modules": ["enf"],
            "findings": [
                {
                    "module": "voice",
                    "title": "Voice consistency anomaly",
                    "severity": "medium",
                    "confidence": 0.81,
                    "description": "Potential speaker inconsistency detected.",
                }
            ],
            "audio": {"sha256": "sha-audio", "codec": "aac"},
            "calibration_note": "Pre-calibration thresholds in use.",
        }

    monkeypatch.setattr(analysis_service, "_run_runner", fake_run_runner)

    response = await client.post(
        "/api/v1/audio/analyze",
        files={"file": ("sample.wav", b"fake-audio", "audio/wav")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["media_type"] == "audio"
    assert data["engine"] == "aff"

    # New fields
    assert data["risk_score"] == 82
    assert data["risk_band"] == "high"
    assert data["measurement_confidence"] == 0.77
    assert data["calibration_status"] == "pre_calibration"
    assert data["elevated_detectors"] == ["noise", "voice"]
    assert any(c["name"] == "enf" for c in data["checks_unavailable"])
    assert data["interpretation"]["summary"]
    assert data["interpretation"]["what_this_means"]
    assert len(data["interpretation"]["next_steps"]) > 0
    actions = [s["action"] for s in data["interpretation"]["next_steps"]]
    assert "verify_source" in actions
    assert "ai_detection_audio" in actions

    # Deprecated fields — present but null
    assert data["verdict"] is None
    assert data["verdict_label"] is None
    assert data["probability"] is None
    assert data["tampering_likelihood"] is None

    # Unchanged
    assert data["findings"][0]["module"] == "voice"
    assert data["file"]["filename"] == "sample.wav"
    assert data["file"]["sha256"] == "sha-audio"

    # engine_detail should NOT contain verdict fields
    assert "verdict" not in data["engine_detail"]
    assert "verdict_label" not in data["engine_detail"]
    # but should still carry non-verdict engine output
    assert data["engine_detail"]["engine"] == "aff"
    assert data["engine_detail"]["fused_probability"] == 0.82


@pytest.mark.asyncio
async def test_runner_failure_is_mapped_to_503(client, monkeypatch):
    from api.services import analysis_service

    def fake_run_runner(*, media_type, file_path, output_dir, options, timeout_s):
        raise RuntimeError("audio runner failed: missing dependency")

    monkeypatch.setattr(analysis_service, "_run_runner", fake_run_runner)

    response = await client.post(
        "/api/v1/audio/analyze",
        files={"file": ("sample.wav", b"fake-audio", "audio/wav")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "audio runner failed: missing dependency"


@pytest.mark.asyncio
async def test_video_route_enqueues_job_and_returns_202(client, monkeypatch):
    from api.services import job_service

    class DummyStore:
        def __init__(self):
            self.saved = {}

        def save(self, job_id, payload, ttl_seconds=86400):
            self.saved[job_id] = payload

        def get(self, job_id):
            return self.saved.get(job_id)

    sent = {}
    store = DummyStore()

    def fake_send_task(name, kwargs, task_id):
        sent["name"] = name
        sent["kwargs"] = kwargs
        sent["task_id"] = task_id

    monkeypatch.setattr(job_service, "get_job_store", lambda: store)
    monkeypatch.setattr(job_service.celery_app, "send_task", fake_send_task)

    response = await client.post(
        "/api/v1/video/analyze",
        files={"file": ("sample.mp4", b"fake-video", "video/mp4")},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["media_type"] == "video"
    assert "job_id" in data
    assert sent["name"] == "analyze_media"
    assert sent["kwargs"]["media_type"] == "video"
    assert sent["kwargs"]["original_filename"] == "sample.mp4"
    assert sent["task_id"] == data["job_id"]


@pytest.mark.asyncio
async def test_get_job_status_returns_stored_payload(client, monkeypatch):
    from api.services import job_service

    class DummyStore:
        def get(self, job_id):
            return {
                "job_id": job_id,
                "status": "completed",
                "media_type": "video",
                "submitted_at": "2026-03-24T14:00:00+00:00",
                "started_at": "2026-03-24T14:00:05+00:00",
                "completed_at": "2026-03-24T14:01:00+00:00",
                "result": {"engine": "vff", "verdict": "likely_authentic"},
                "error": None,
            }

    class DummyAsyncResult:
        state = "SUCCESS"
        result = {"engine": "vff", "verdict": "likely_authentic"}

    monkeypatch.setattr(job_service, "get_job_store", lambda: DummyStore())
    monkeypatch.setattr(job_service.celery_app, "AsyncResult", lambda job_id: DummyAsyncResult())

    response = await client.get("/api/v1/jobs/job-123")

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "job-123"
    assert data["status"] == "completed"
    assert data["result"]["engine"] == "vff"


@pytest.mark.asyncio
async def test_get_job_result_returns_completed_result(client, monkeypatch):
    from api.routers import jobs as jobs_router

    monkeypatch.setattr(
        jobs_router,
        "get_job_result",
        lambda job_id: {"engine": "work", "verdict": "likely_authentic"},
    )

    response = await client.get("/api/v1/jobs/job-456/result")

    assert response.status_code == 200
    assert response.json()["engine"] == "work"
