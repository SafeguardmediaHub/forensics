from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SAMPLE = (
    REPO_ROOT
    / "safeguardmedia"
    / "src"
    / "api"
    / "engines"
    / "vff"
    / "working"
    / "ca16e742-84d7-434b-8dc0-7231a11b8e1e"
    / "ca16e742-84d7-434b-8dc0-7231a11b8e1e"
    / "frames"
    / "frame_000000_00000000.png"
)
AUD_SAMPLE = REPO_ROOT / "AFF" / "Test run.m4a"
VIDEO_SAMPLE = (
    REPO_ROOT
    / "safeguardmedia"
    / "src"
    / "api"
    / "engines"
    / "vff"
    / "working"
    / "ca16e742-84d7-434b-8dc0-7231a11b8e1e"
    / "original.mp4"
)


async def post_file(
    client: httpx.AsyncClient,
    base_url: str,
    media_type: str,
    file_path: Path,
    content_type: str,
) -> httpx.Response:
    with file_path.open("rb") as handle:
        response = await client.post(
            f"{base_url}/api/v1/analyze",
            data={"media_type": media_type},
            files={"file": (file_path.name, handle, content_type)},
        )
    return response


async def poll_job(client: httpx.AsyncClient, base_url: str, job_id: str) -> dict:
    for _ in range(180):
        response = await client.get(f"{base_url}/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        status = payload["status"]
        if status in {"completed", "failed"}:
            return payload
        await asyncio.sleep(2)
    raise RuntimeError(f"Timed out waiting for job {job_id}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a running SafeguardMedia API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=1200.0) as client:
        health = await client.get(f"{args.base_url}/health")
        health.raise_for_status()
        print("health:", health.json()["status"], flush=True)

        image_response = await post_file(client, args.base_url, "image", IMAGE_SAMPLE, "image/png")
        image_response.raise_for_status()
        image_json = image_response.json()
        print("image:", image_json["engine"], image_json["verdict"], image_json["probability"], flush=True)

        audio_response = await post_file(client, args.base_url, "audio", AUD_SAMPLE, "audio/mp4")
        audio_response.raise_for_status()
        audio_json = audio_response.json()
        print("audio:", audio_json["engine"], audio_json["verdict"], audio_json["probability"], flush=True)

        for media_type in ("video", "frames"):
            response = await post_file(client, args.base_url, media_type, VIDEO_SAMPLE, "video/mp4")
            response.raise_for_status()
            payload = response.json()
            assert response.status_code == 202, payload
            print(media_type, "queued:", payload["job_id"], flush=True)
            job = await poll_job(client, args.base_url, payload["job_id"])
            print(media_type, "status:", job["status"], flush=True)
            if job["status"] != "completed":
                raise RuntimeError(f"{media_type} job failed: {job}")
            result = await client.get(f"{args.base_url}/api/v1/jobs/{payload['job_id']}/result")
            result.raise_for_status()
            result_json = result.json()
            print(media_type, "result:", result_json["engine"], result_json["verdict"], flush=True)

        print("smoke-http: ok", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
