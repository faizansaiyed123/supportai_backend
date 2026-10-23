"""Integration tests for ticket notes (D030) and membership role changes (D031)."""
import pytest

from app.models.ticket import Customer


async def _register_owner_with_roles(client, db_session, *, slug, email):
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
async def test_add_and_list_ticket_notes(client, db_session, seeded_roles):
    token, org_id = await _register_owner_with_roles(client, db_session, slug="acme-notes", email="owner@notes.com")
    headers = {"Authorization": f"Bearer {token}"}

    customer = Customer(org_id=org_id, name="A Customer")
    db_session.add(customer)
    await db_session.commit()

    ticket_resp = await client.post(
        f"/api/orgs/{org_id}/tickets",
        json={"customer_id": str(customer.id), "title": "Something's broken"},
        headers=headers,
    )
    ticket_id = ticket_resp.json()["id"]

    note_resp = await client.post(
        f"/api/orgs/{org_id}/tickets/{ticket_id}/notes",
        json={"content": "Called customer, awaiting logs."},
        headers=headers,
    )
    assert note_resp.status_code == 201
    assert note_resp.json()["content"] == "Called customer, awaiting logs."

    list_resp = await client.get(f"/api/orgs/{org_id}/tickets/{ticket_id}/notes", headers=headers)
    assert list_resp.status_code == 200
    notes = list_resp.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["content"] == "Called customer, awaiting logs."


@pytest.mark.asyncio
async def test_ticket_notes_are_org_scoped(client, db_session, seeded_roles):
    token_a, org_a = await _register_owner_with_roles(client, db_session, slug="acme-notes-a", email="a@notes.com")
    token_b, org_b = await _register_owner_with_roles(client, db_session, slug="acme-notes-b", email="b@notes.com")

    customer = Customer(org_id=org_b, name="B Customer")
    db_session.add(customer)
    await db_session.commit()

    ticket_resp = await client.post(
        f"/api/orgs/{org_b}/tickets",
        json={"customer_id": str(customer.id), "title": "Org B ticket"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    ticket_id = ticket_resp.json()["id"]

    # Org A's own URL with org B's ticket_id - must 404, not leak org B's ticket/notes.
    resp = await client.get(
        f"/api/orgs/{org_a}/tickets/{ticket_id}/notes", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_change_member_role(client, db_session, seeded_roles):
    owner_token, org_id = await _register_owner_with_roles(client, db_session, slug="acme-role", email="owner@role.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    invite_resp = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "member@role.com", "full_name": "Member", "role": "viewer"},
        headers=owner_headers,
    )
    invite_token = invite_resp.json()["invite_token"]
    await client.post("/api/auth/invites/accept", json={"token": invite_token, "password": "a-strong-enough-password"})

    memberships_resp = await client.get(f"/api/orgs/{org_id}/memberships", headers=owner_headers)
    memberships = memberships_resp.json()["memberships"]
    member_row = next(m for m in memberships if m["user_id"] == invite_resp.json()["user_id"])

    change_resp = await client.post(
        f"/api/orgs/{org_id}/memberships/{member_row['id']}/role", json={"role": "agent"}, headers=owner_headers
    )
    assert change_resp.status_code == 200
    # Enriched fields (email/full_name/role) - closed a gap found during frontend integration
    # where MembershipPublic originally only exposed user_id (unusable for a real Team page).
    assert change_resp.json()["email"] == "member@role.com"
    assert change_resp.json()["role"] == "agent"

    # Re-login as that member and confirm the JWT now carries the new role.
    login_resp = await client.post(
        "/api/auth/login",
        json={"email": "member@role.com", "password": "a-strong-enough-password", "organization_slug": "acme-role"},
    )
    new_token = login_resp.json()["access_token"]

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    role_in_token = pyjwt.decode(new_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["role"]
    assert role_in_token == "agent"


@pytest.mark.asyncio
async def test_role_change_cannot_set_owner(client, db_session, seeded_roles):
    owner_token, org_id = await _register_owner_with_roles(client, db_session, slug="acme-role2", email="owner@role2.com")
    resp = await client.post(
        f"/api/orgs/{org_id}/memberships/{org_id}/role",  # membership_id doesn't matter -
        # pydantic pattern validation rejects "owner" before the route body ever runs
        json={"role": "owner"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_members_read_permission_allows_agent_but_not_viewer_to_list_team(client, db_session, seeded_roles):
    """Found during frontend integration: agents can assign tickets (tickets:manage) but the
    assign dropdown needs the team list, which used to require members:manage (admin-only).
    members:read is the read-only permission that fixes it without exposing invite/role-change."""
    owner_token, org_id = await _register_owner_with_roles(client, db_session, slug="acme-mread", email="owner@mread.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    async def _join(email, role):
        invite = await client.post(
            f"/api/orgs/{org_id}/memberships/invite",
            json={"email": email, "full_name": role.title(), "role": role},
            headers=owner_headers,
        )
        accepted = await client.post(
            "/api/auth/invites/accept",
            json={"token": invite.json()["invite_token"], "password": "a-strong-enough-password"},
        )
        return {"Authorization": f"Bearer {accepted.json()['access_token']}"}

    agent_headers = await _join("agent@mread.com", "agent")
    viewer_headers = await _join("viewer@mread.com", "viewer")

    agent_resp = await client.get(f"/api/orgs/{org_id}/memberships", headers=agent_headers)
    assert agent_resp.status_code == 200
    assert {m["email"] for m in agent_resp.json()["memberships"]} >= {"owner@mread.com", "agent@mread.com"}

    viewer_resp = await client.get(f"/api/orgs/{org_id}/memberships", headers=viewer_headers)
    assert viewer_resp.status_code == 403

    # And an agent still cannot invite or change roles (members:manage is unchanged).
    invite_attempt = await client.post(
        f"/api/orgs/{org_id}/memberships/invite",
        json={"email": "x@mread.com", "full_name": "X", "role": "viewer"},
        headers=agent_headers,
    )
    assert invite_attempt.status_code == 403

