import pytest


@pytest.mark.asyncio
async def test_liveness(client):
    resp = await client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_database_ok(client):
    resp = await client.get("/api/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] is True

