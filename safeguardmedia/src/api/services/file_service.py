from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.config import settings


@dataclass(frozen=True)
class RuntimePaths:
    job_id: str
    upload_path: Path
    output_dir: Path | None


def ensure_runtime_dirs() -> None:
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)


def build_runtime_paths(
    media_type: str,
    suffix: str,
    job_id: str,
    uses_output_dir: bool,
) -> RuntimePaths:
    ensure_runtime_dirs()

    upload_root = Path(settings.upload_dir) / media_type
    upload_root.mkdir(parents=True, exist_ok=True)
    upload_path = upload_root / f"{job_id}{suffix}"

    output_dir: Path | None = None
    if uses_output_dir:
        output_dir = Path(settings.output_dir) / media_type / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

    return RuntimePaths(job_id=job_id, upload_path=upload_path, output_dir=output_dir)


def cleanup_runtime_paths(
    upload_path: Path | None = None,
    output_dir: Path | None = None,
    remove_output_dir: bool = False,
) -> None:
    if upload_path and upload_path.exists():
        upload_path.unlink(missing_ok=True)
        _remove_empty_parents(upload_path.parent, Path(settings.upload_dir))

    if remove_output_dir and output_dir and output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
        _remove_empty_parents(output_dir.parent, Path(settings.output_dir))


def cleanup_expired_runtime_files(now: datetime | None = None) -> dict[str, int]:
    ensure_runtime_dirs()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.cleanup_max_age_hours)

    removed_uploads = _cleanup_tree(Path(settings.upload_dir), cutoff, remove_dirs=False)
    removed_output_dirs = _cleanup_tree(Path(settings.output_dir), cutoff, remove_dirs=True)

    return {
        "removed_uploads": removed_uploads,
        "removed_output_dirs": removed_output_dirs,
    }


def _cleanup_tree(root: Path, cutoff: datetime, remove_dirs: bool) -> int:
    if not root.exists():
        return 0

    removed = 0
    if remove_dirs:
        for media_dir in (p for p in root.iterdir() if p.is_dir()):
            for path in (p for p in media_dir.iterdir() if p.is_dir()):
                if _is_older_than(path, cutoff):
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
        _remove_empty_dirs_below(root)
        return removed

    for path in root.rglob("*"):
        if path.is_file() and _is_older_than(path, cutoff):
            path.unlink(missing_ok=True)
            removed += 1
    _remove_empty_dirs_below(root)
    return removed


def _is_older_than(path: Path, cutoff: datetime) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified < cutoff


def _remove_empty_parents(start: Path, stop: Path) -> None:
    current = start
    stop = stop.resolve()
    while True:
        try:
            resolved = current.resolve()
        except FileNotFoundError:
            break
        if resolved == stop:
            break
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _remove_empty_dirs_below(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue
