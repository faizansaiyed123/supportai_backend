import pytest

REGISTER_PAYLOAD = {
    "email": "owner@acme.com",
    "password": "a-strong-enough-password",
    "full_name": "Ada Owner",
    "organization_name": "Acme Inc",
    "organization_slug": "acme",
}


@pytest.mark.asyncio
async def test_register_creates_org_and_owner(client, db_session, seeded_roles):
    resp = await client.post("/api/auth/register", json=REGISTER_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "owner@acme.com"
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_register_duplicate_slug_rejected(client, db_session, seeded_roles):
    first = await client.post("/api/auth/register", json=REGISTER_PAYLOAD)
    assert first.status_code == 201

    dup = dict(REGISTER_PAYLOAD, email="b@acme.com")
    second = await client.post("/api/auth/register", json=dup)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401_generic_message(client, db_session, seeded_roles):
    await client.post("/api/auth/register", json=REGISTER_PAYLOAD)

    resp = await client.post(
        "/api/auth/login",
        json={"email": "owner@acme.com", "password": "wrong-password", "organization_slug": "acme"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_success_returns_access_token_and_sets_refresh_cookie(client, db_session, seeded_roles):
    await client.post("/api/auth/register", json=REGISTER_PAYLOAD)

    resp = await client.post(
        "/api/auth/login",
        json={"email": "owner@acme.com", "password": "a-strong-enough-password", "organization_slug": "acme"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_old_token_reuse_is_rejected(client, db_session, seeded_roles):
    await client.post("/api/auth/register", json=REGISTER_PAYLOAD)
    login_resp = await client.post(
        "/api/auth/login",
        json={"email": "owner@acme.com", "password": "a-strong-enough-password", "organization_slug": "acme"},
    )
    old_refresh_cookie = login_resp.cookies["refresh_token"]

    client.cookies.set("refresh_token", old_refresh_cookie)
    first_refresh = await client.post("/api/auth/refresh")
    assert first_refresh.status_code == 200
    new_refresh_cookie = first_refresh.cookies["refresh_token"]
    assert new_refresh_cookie != old_refresh_cookie

    client.cookies.set("refresh_token", old_refresh_cookie)
    reuse_attempt = await client.post("/api/auth/refresh")
    assert reuse_attempt.status_code == 401

    client.cookies.set("refresh_token", new_refresh_cookie)
    after_family_revoked = await client.post("/api/auth/refresh")
    assert after_family_revoked.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_missing_token(client):
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 401
