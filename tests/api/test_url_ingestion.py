"""Integration test for URL-type knowledge ingestion (POST .../documents/from-url).

IMPORTANT TESTABILITY NOTE (a real gap found while writing this test, not just a modeling
choice): app/services/knowledge.py's upload/add-url functions enqueue a job via
`ingest_document_version.configure().defer_async(...)`, which is procrastinate's real
job-enqueue call. Procrastinate manages its OWN database tables (via `procrastinate schema
--apply`, applied separately from our Alembic migrations - see docs/DEVELOPMENT.md), which
tests/conftest.py's `Base.metadata.create_all()` does NOT create, since they aren't SQLAlchemy
models. That means any test that exercises the real upload/from-url endpoint WITHOUT mocking
the enqueue call would fail for a reason unrelated to what it's actually testing (a missing
procrastinate table, not a bug in the endpoint). This test monkeypatches
`app.workers.ingestion.ingest_document_version` with a fake that has a no-op
`.configure().defer_async(...)` for exactly this reason - it tests the HTTP/validation/DB-write
layer, not procrastinate's own job-table mechanics (which is procrastinate's own test suite's
job to verify, not this project's).
"""
import pytest


class _FakeConfiguredTask:
    async def defer_async(self, **kwargs):
        return None


class _FakeIngestTask:
    def configure(self):
        return _FakeConfiguredTask()


async def _register_and_login(client, db_session, seeded_roles, *, slug="acme-url", email="owner@acme-url.com"):
    await client.post(
        "/api/auth/register",
        json={
            "email": email, "password": "a-strong-enough-password", "full_name": "Owner",
            "organization_name": "Acme URL", "organization_slug": slug,
        },
    )
    login = await client.post(
        "/api/auth/login", json={"email": email, "password": "a-strong-enough-password", "organization_slug": slug}
    )
    body = login.json()

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    org_id = pyjwt.decode(body["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]
    return body["access_token"], org_id


@pytest.mark.asyncio
async def test_url_pointing_at_private_address_is_rejected(client, db_session, seeded_roles):
    token, org_id = await _register_and_login(client, db_session, seeded_roles)

    resp = await client.post(
        f"/api/orgs/{org_id}/documents/from-url",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert "disallowed address" in resp.json()["detail"].lower() or "resolves" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_url_with_disallowed_scheme_is_rejected(client, db_session, seeded_roles):
    token, org_id = await _register_and_login(client, db_session, seeded_roles, slug="acme-url2", email="owner@acme-url2.com")

    resp = await client.post(
        f"/api/orgs/{org_id}/documents/from-url",
        json={"url": "file:///etc/passwd"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_successful_url_fetch_creates_document_and_enqueues_ingestion(client, db_session, seeded_roles, monkeypatch):
    token, org_id = await _register_and_login(client, db_session, seeded_roles, slug="acme-url3", email="owner@acme-url3.com")

    import app.services.knowledge as knowledge_module
    import app.workers.ingestion as ingestion_module

    async def _fake_fetch_url(url):
        return b"<html><body><h1>Docs</h1><p>Some content.</p></body></html>", "text/html"

    monkeypatch.setattr(knowledge_module, "fetch_url", _fake_fetch_url)
    monkeypatch.setattr(ingestion_module, "ingest_document_version", _FakeIngestTask())

    resp = await client.post(
        f"/api/orgs/{org_id}/documents/from-url",
        json={"url": "https://example.com/docs", "title": "Example Docs"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Example Docs"
    assert body["mime_type"] == "text/html"
    assert body["status"] == "pending"


async def _register_owner(client, db_session, *, slug, email):
    await client.post(
        "/api/auth/register",
        json={
            "email": email, "password": "a-strong-enough-password", "full_name": "Owner",
            "organization_name": "Acme URL4", "organization_slug": slug,
        },
    )
    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "a-strong-enough-password", "organization_slug": slug},
    )
    body = login.json()

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    org_id = pyjwt.decode(body["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]
    return body["access_token"], org_id


@pytest.mark.asyncio
async def test_viewer_role_cannot_add_url_document(client, db_session, seeded_roles):
    """knowledge:manage is required - a viewer (knowledge:read only) must be denied."""
    owner_token, org_id = await _register_owner(client, db_session, slug="acme-url4", email="owner@acme-url4.com")

    invite_resp = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "viewer@acme-url4.com", "full_name": "Viewer", "role": "viewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    invite_token = invite_resp.json()["invite_token"]
    accept_resp = await client.post(
        "/api/auth/invites/accept", json={"token": invite_token, "password": "a-strong-enough-password"}
    )
    viewer_token = accept_resp.json()["access_token"]

    resp = await client.post(
        f"/api/orgs/{org_id}/documents/from-url",
        json={"url": "https://example.com/docs"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403

