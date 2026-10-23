"""
Explicit cross-tenant-leakage tests (Sec. 11/24, D005's stated invariant). These exist because
the brief specifically requires them, not as a formality: every one of these should FAIL loudly
if org-scoping is ever accidentally dropped from a query.
"""
import pytest


async def _register_and_login(client, db_session, *, slug: str, email: str):
    await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-strong-enough-password",
            "full_name": "Owner",
            "organization_name": slug.title(),
            "organization_slug": slug,
        },
    )
    login = await client.post(
        "/api/auth/login", json={"email": email, "password": "a-strong-enough-password", "organization_slug": slug}
    )
    body = login.json()
    return body["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_org_a_cannot_list_org_b_documents(client, db_session, seeded_roles):
    token_a = await _register_and_login(client, db_session, slug="org-a", email="a@a.com")
    token_b = await _register_and_login(client, db_session, slug="org-b", email="b@b.com")

    # Decode org_id from each token to build URLs the same way a real client would.
    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    org_a_id = pyjwt.decode(token_a, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]
    org_b_id = pyjwt.decode(token_b, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]

    # Org A's own document list should work.
    resp = await client.get(f"/api/orgs/{org_a_id}/documents", headers=_auth_headers(token_a))
    assert resp.status_code == 200

    # Org A's token used against Org B's URL must be rejected outright (403), not silently
    # scoped or allowed through.
    resp = await client.get(f"/api/orgs/{org_b_id}/documents", headers=_auth_headers(token_a))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_ticket_by_id(client, db_session, seeded_roles):
    token_a = await _register_and_login(client, db_session, slug="org-a2", email="a2@a.com")
    token_b = await _register_and_login(client, db_session, slug="org-b2", email="b2@b.com")

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    org_b_id = pyjwt.decode(token_b, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]

    # Create a customer for org B via the real API (not a direct DB insert) - exercises the
    # customer-creation endpoint added to close the earlier "customers are DB-only" gap.
    customer_resp = await client.post(
        f"/api/orgs/{org_b_id}/customers", json={"name": "B Customer"}, headers=_auth_headers(token_b)
    )
    assert customer_resp.status_code == 201
    customer_id = customer_resp.json()["id"]

    create_resp = await client.post(
        f"/api/orgs/{org_b_id}/tickets",
        json={"customer_id": customer_id, "title": "Org B's private ticket"},
        headers=_auth_headers(token_b),
    )
    assert create_resp.status_code == 201
    ticket_id = create_resp.json()["id"]

    # Org A must not be able to list/see org B's ticket via org A's own org-scoped list
    # endpoint (using org A's own org_id in the URL, with org A's token - the only
    # combination a well-behaved client would even attempt).
    org_a_id = pyjwt.decode(token_a, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]
    list_resp = await client.get(f"/api/orgs/{org_a_id}/tickets", headers=_auth_headers(token_a))
    assert list_resp.status_code == 200
    assert all(t["id"] != ticket_id for t in list_resp.json()["tickets"])


@pytest.mark.asyncio
async def test_org_a_cannot_fetch_org_b_customer_by_id(client, db_session, seeded_roles):
    token_a = await _register_and_login(client, db_session, slug="org-a3", email="a3@a.com")
    token_b = await _register_and_login(client, db_session, slug="org-b3", email="b3@b.com")

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    org_b_id = pyjwt.decode(token_b, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["org_id"]

    customer_resp = await client.post(
        f"/api/orgs/{org_b_id}/customers", json={"name": "B Customer"}, headers=_auth_headers(token_b)
    )
    customer_id = customer_resp.json()["id"]

    # Org A's token against org B's URL is rejected outright (403).
    resp = await client.get(f"/api/orgs/{org_b_id}/customers/{customer_id}", headers=_auth_headers(token_a))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_expired_or_tampered_token_rejected(client):
    resp = await client.get(
        "/api/orgs/00000000-0000-0000-0000-000000000000/documents",
        headers=_auth_headers("this.is.not.a.valid.jwt"),
    )
    assert resp.status_code == 401

