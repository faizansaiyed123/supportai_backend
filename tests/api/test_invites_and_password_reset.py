"""
Integration tests for the invite flow (D026) and password-reset flow (D027), including the
enumeration-safety property of password-reset-request (must respond identically whether or
not the email exists) and that a newly-invited 'viewer' role is actually denied on a
permission-gated endpoint - the first real test of require_permission() with a non-owner role,
since registration only ever creates owners.
"""
import pytest


async def _register_owner(client, db_session, seeded_roles, *, slug, email):
    await client.post(
        "/api/auth/register",
        json={
            "email": email, "password": "a-strong-enough-password", "full_name": "Owner",
            "organization_name": "Acme", "organization_slug": slug,
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
async def test_invite_creates_membership_and_returns_token(client, db_session, seeded_roles):
    owner_token, org_id = await _register_owner(client, db_session, seeded_roles, slug="acme-invite", email="owner@invite.com")

    resp = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "newagent@invite.com", "full_name": "New Agent", "role": "viewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "newagent@invite.com"
    assert body["invite_token"]


@pytest.mark.asyncio
async def test_invite_rejects_owner_role(client, db_session, seeded_roles):
    owner_token, org_id = await _register_owner(client, db_session, seeded_roles, slug="acme-invite2", email="owner@invite2.com")
    resp = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "x@invite2.com", "full_name": "X", "role": "owner"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    # Pydantic pattern validation rejects "owner" before it ever reaches the service.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_cannot_invite(client, db_session, seeded_roles):
    """A viewer (no members:manage) must be rejected - exercises require_permission() end to
    end with a real non-owner role for the first time (registration only ever produces
    owners)."""
    owner_token, org_id = await _register_owner(client, db_session, seeded_roles, slug="acme-invite3", email="owner@invite3.com")

    invite_resp = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "viewer@invite3.com", "full_name": "Viewer User", "role": "viewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    invite_token = invite_resp.json()["invite_token"]

    accept_resp = await client.post(
        "/api/auth/invites/accept", json={"token": invite_token, "password": "a-strong-enough-password"}
    )
    assert accept_resp.status_code == 200
    viewer_access_token = accept_resp.json()["access_token"]

    # The viewer tries to invite someone else - must be denied (403), not silently allowed.
    resp = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "another@invite3.com", "full_name": "Another", "role": "viewer"},
        headers={"Authorization": f"Bearer {viewer_access_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_accept_invite_with_invalid_token_rejected(client):
    resp = await client.post(
        "/api/auth/invites/accept", json={"token": "not-a-real-token", "password": "a-strong-enough-password"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_password_reset_request_identical_response_for_existing_and_nonexistent_email(client, db_session, seeded_roles):
    await client.post(
        "/api/auth/register",
        json={
            "email": "real@pwreset.com", "password": "a-strong-enough-password", "full_name": "Real User",
            "organization_name": "Acme", "organization_slug": "acme-pwreset",
        },
    )

    real_resp = await client.post("/api/auth/password-reset/request", json={"email": "real@pwreset.com"})
    fake_resp = await client.post("/api/auth/password-reset/request", json={"email": "does-not-exist@pwreset.com"})

    # Enumeration-safety: identical status and body regardless of whether the email exists.
    assert real_resp.status_code == fake_resp.status_code == 202
    assert real_resp.json() == fake_resp.json()
    # And critically: no token is ever present in either response body.
    assert "token" not in real_resp.text.lower()
    assert "token" not in fake_resp.text.lower()


@pytest.mark.asyncio
async def test_password_reset_confirm_with_invalid_token_rejected(client):
    resp = await client.post(
        "/api/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "a-different-strong-password"},
    )
    assert resp.status_code == 400

