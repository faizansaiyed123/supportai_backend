from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.demo_api import app, reset_demo_state


@pytest.fixture
async def client():
    reset_demo_state()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://demo.test") as session:
        yield session


async def login(client: AsyncClient, email: str = "owner@supportai.demo") -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo1234", "organization_slug": "demo"},
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.asyncio
async def test_health_and_auth(client):
    health = await client.get("/health")
    assert health.json()["storage"] == "in-memory"

    headers = await login(client)
    customers = await client.get("/api/orgs/00000000-0000-0000-0000-000000000001/customers", headers=headers)

    assert customers.status_code == 200
    assert len(customers.json()["customers"]) == 3


@pytest.mark.asyncio
async def test_conversation_returns_grounded_answer_and_citation(client):
    headers = await login(client)
    conversation = await client.post(
        "/api/orgs/00000000-0000-0000-0000-000000000001/conversations",
        headers=headers,
        json={},
    )
    conversation_id = conversation.json()["id"]

    answer = await client.post(
        f"/api/orgs/00000000-0000-0000-0000-000000000001/conversations/{conversation_id}/messages",
        headers=headers,
        json={"question": "How long do I have to request a refund?"},
    )

    assert answer.status_code == 200
    assert "30 days" in answer.json()["message"]
    assert answer.json()["low_confidence"] is False
    assert answer.json()["citations"][0]["document_id"] == "doc-1"

    history = await client.get(
        f"/api/orgs/00000000-0000-0000-0000-000000000001/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert len(history.json()["messages"]) == 2


@pytest.mark.asyncio
async def test_unknown_question_is_low_confidence(client):
    headers = await login(client)
    conversation = await client.post(
        "/api/orgs/00000000-0000-0000-0000-000000000001/conversations",
        headers=headers,
    )
    conversation_id = conversation.json()["id"]

    answer = await client.post(
        f"/api/orgs/00000000-0000-0000-0000-000000000001/conversations/{conversation_id}/messages",
        headers=headers,
        json={"question": "What is the support team's favorite color?"},
    )

    assert answer.json()["low_confidence"] is True
    assert answer.json()["citations"] == []


@pytest.mark.asyncio
async def test_ticket_lifecycle_and_internal_notes(client):
    headers = await login(client, "agent@supportai.demo")

    ticket = await client.get(
        "/api/orgs/00000000-0000-0000-0000-000000000001/tickets/ticket-1",
        headers=headers,
    )
    assert ticket.status_code == 200

    updated = await client.post(
        "/api/orgs/00000000-0000-0000-0000-000000000001/tickets/ticket-1/status",
        headers=headers,
        json={"status": "resolved"},
    )
    assert updated.json()["status"] == "resolved"

    note = await client.post(
        "/api/orgs/00000000-0000-0000-0000-000000000001/tickets/ticket-1/notes",
        headers=headers,
        json={"content": "Verified in demo workflow."},
    )
    assert note.status_code == 200

    notes = await client.get(
        "/api/orgs/00000000-0000-0000-0000-000000000001/tickets/ticket-1/notes",
        headers=headers,
    )
    assert any(item["content"] == "Verified in demo workflow." for item in notes.json()["notes"])


@pytest.mark.asyncio
async def test_role_permissions_are_reflected_in_demo(client):
    viewer = await login(client, "viewer@supportai.demo")
    response = await client.post(
        "/api/orgs/00000000-0000-0000-0000-000000000001/tickets",
        headers=viewer,
        json={"customer_id": "customer-1", "title": "Should be blocked"},
    )
    assert response.status_code == 403

    admin = await login(client, "admin@supportai.demo")
    create = await client.post(
        "/api/orgs/00000000-0000-0000-0000-000000000001/customers",
        headers=admin,
        json={"name": "Demo Customer", "email": "demo@example.test"},
    )
    assert create.status_code == 200
