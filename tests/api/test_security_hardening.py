"""Tests for security-headers middleware and rate limiting (TODO.md hardening items)."""
import pytest

from app.core.rate_limit import _FixedWindowLimiter, _login_limiter


@pytest.mark.asyncio
async def test_security_headers_present_on_every_response(client):
    resp = await client.get("/api/health/live")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" in resp.headers
    assert "content-security-policy" in resp.headers


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_after_threshold(client, db_session, seeded_roles):
    # Pin the limit to the production default rather than reading it from the environment: the
    # dev docker-compose stack overrides it so the E2E suite can register many orgs, and this
    # test would otherwise pass or fail depending on where it happens to be run.
    limiter = _login_limiter()
    original_max = limiter.max_requests
    limiter.max_requests = 10

    try:
        await client.post(
            "/api/auth/register",
            json={
                "email": "owner@acme.com",
                "password": "a-strong-enough-password",
                "full_name": "Ada Owner",
                "organization_name": "Acme Inc",
                "organization_slug": "acme",
            },
        )

        responses = []
        for _ in range(12):
            resp = await client.post(
                "/api/auth/login",
                json={"email": "owner@acme.com", "password": "wrong-password", "organization_slug": "acme"},
            )
            responses.append(resp.status_code)

        # First 10 are rejected as bad credentials (401); beyond the configured threshold (10/min,
        # see app/core/rate_limit.py) the limiter itself should kick in with 429.
        assert 429 in responses
    finally:
        limiter.max_requests = original_max


def test_fixed_window_limiter_unit():
    limiter = _FixedWindowLimiter(max_requests=3, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-a")
    limiter.check("client-a")
    with pytest.raises(Exception):
        limiter.check("client-a")
    # A different key has its own independent bucket.
    limiter.check("client-b")


@pytest.mark.asyncio
async def test_cors_preflight_allows_frontend_origin_with_credentials(client):
    """Regression test: the CORS default was http://localhost:5173 (a Vite port picked before
    the frontend existed) while Next.js dev serves on :3000, so every browser request would
    have been blocked. Credentialed (cookie) CORS requires an explicit origin, not '*'."""
    resp = await client.options(
        "/api/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_cors_preflight_rejects_unlisted_origin(client):
    resp = await client.options(
        "/api/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in resp.headers

