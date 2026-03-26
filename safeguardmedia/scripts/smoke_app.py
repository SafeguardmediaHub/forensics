from __future__ import annotations

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from api.main import app

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


async def main() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=300.0,
    ) as client:
        health = await client.get("/health")
        health.raise_for_status()
        print("health:", health.json()["status"], flush=True)

        with IMAGE_SAMPLE.open("rb") as handle:
            image_response = await client.post(
                "/api/v1/analyze",
                data={"media_type": "image"},
                files={"file": (IMAGE_SAMPLE.name, handle, "image/png")},
            )
        image_response.raise_for_status()
        image_json = image_response.json()
        print("image:", image_json["engine"], image_json["verdict"], image_json["probability"], flush=True)

        with AUD_SAMPLE.open("rb") as handle:
            audio_response = await client.post(
                "/api/v1/analyze",
                data={"media_type": "audio"},
                files={"file": (AUD_SAMPLE.name, handle, "audio/mp4")},
            )
        audio_response.raise_for_status()
        audio_json = audio_response.json()
        print("audio:", audio_json["engine"], audio_json["verdict"], audio_json["probability"], flush=True)

        print("smoke-app: ok", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
