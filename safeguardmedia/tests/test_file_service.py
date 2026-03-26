from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.config import settings
from api.services.file_service import (
    build_runtime_paths,
    cleanup_expired_runtime_files,
    cleanup_runtime_paths,
    ensure_runtime_dirs,
)


def test_build_runtime_paths_uses_per_media_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "outputs"))

    paths = build_runtime_paths(
        media_type="video",
        suffix=".mp4",
        job_id="vff_job123",
        uses_output_dir=True,
    )

    assert paths.upload_path == tmp_path / "uploads" / "video" / "vff_job123.mp4"
    assert paths.output_dir == tmp_path / "outputs" / "video" / "vff_job123"
    assert paths.upload_path.parent.is_dir()
    assert paths.output_dir.is_dir()


def test_cleanup_runtime_paths_removes_upload_and_empty_parent_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "outputs"))

    ensure_runtime_dirs()
    upload_path = tmp_path / "uploads" / "audio" / "aff_job456.wav"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b"audio")

    cleanup_runtime_paths(upload_path=upload_path)

    assert not upload_path.exists()
    assert not upload_path.parent.exists()


def test_cleanup_expired_runtime_files_removes_only_old_runtime_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "cleanup_max_age_hours", 24)

    old_time = datetime.now(timezone.utc) - timedelta(hours=48)
    recent_time = datetime.now(timezone.utc) - timedelta(hours=1)

    old_upload = tmp_path / "uploads" / "video" / "old.mp4"
    recent_upload = tmp_path / "uploads" / "video" / "recent.mp4"
    old_output = tmp_path / "outputs" / "frames" / "old-job"
    recent_output = tmp_path / "outputs" / "frames" / "recent-job"

    old_upload.parent.mkdir(parents=True, exist_ok=True)
    recent_upload.parent.mkdir(parents=True, exist_ok=True)
    old_output.mkdir(parents=True, exist_ok=True)
    recent_output.mkdir(parents=True, exist_ok=True)

    old_upload.write_bytes(b"old")
    recent_upload.write_bytes(b"recent")
    (old_output / "result.json").write_text("{}")
    (recent_output / "result.json").write_text("{}")

    old_ts = old_time.timestamp()
    recent_ts = recent_time.timestamp()

    os.utime(old_upload, (old_ts, old_ts))
    os.utime(recent_upload, (recent_ts, recent_ts))
    os.utime(old_output / "result.json", (old_ts, old_ts))
    os.utime(recent_output / "result.json", (recent_ts, recent_ts))
    os.utime(old_output, (old_ts, old_ts))
    os.utime(recent_output, (recent_ts, recent_ts))

    summary = cleanup_expired_runtime_files(now=datetime.now(timezone.utc))

    assert summary == {"removed_uploads": 1, "removed_output_dirs": 1}
    assert not old_upload.exists()
    assert recent_upload.exists()
    assert not old_output.exists()
    assert recent_output.exists()
