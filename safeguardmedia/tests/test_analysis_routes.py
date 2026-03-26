import pytest


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
    assert data["verdict"] == "likely_authentic"
    assert data["verdict_label"] == "Likely Authentic"
    assert data["probability"] == 0.17
    assert data["confidence"] == 0.9
    assert data["file"]["filename"] == "sample.png"
    assert data["file"]["sha256"] == "abc123"
    assert data["engine_detail"]["tampering_likelihood"] == 17.0


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
            "fused_probability": 0.194,
            "confidence": 0.5942,
            "findings": [
                {
                    "module": "voice",
                    "title": "Voice consistency anomaly",
                    "severity": "medium",
                    "confidence": 0.81,
                    "description": "Potential speaker inconsistency detected.",
                }
            ],
            "audio": {"sha256": "sha-audio"},
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
    assert data["verdict"] == "likely_authentic"
    assert data["probability"] == 0.194
    assert data["confidence"] == 0.5942
    assert data["findings"][0]["module"] == "voice"
    assert data["file"]["filename"] == "sample.wav"
    assert data["file"]["sha256"] == "sha-audio"


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
