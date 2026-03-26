import pytest


@pytest.mark.asyncio
async def test_health_returns_200(client):
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_shape(client):
    response = await client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "timestamp" in data
    assert "env" in data


@pytest.mark.asyncio
async def test_health_timestamp_is_iso8601(client):
    from datetime import datetime
    response = await client.get("/health")
    ts = response.json()["timestamp"]
    # Should parse without error
    datetime.fromisoformat(ts)
