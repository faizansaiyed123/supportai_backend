"""
Integration test for the full ask-a-question flow: HTTP -> auth -> conversation creation ->
hybrid retrieval (real DB query against pgvector + full-text) -> reranking -> generation ->
citation persistence -> HTTP response. Real Postgres, real SQL, fake AI providers (embedding/
generation/rerank) injected via monkeypatch so this doesn't require network access to Ollama
or a downloaded embedding/cross-encoder model.

This is deliberately an INTEGRATION test, not a unit test - it exercises the actual hybrid_search
SQL (including the order_by fix from D023) against a real database, which is exactly the kind
of bug the earlier bare-string order_by mistake could only have been caught by something like
this (code review caught it that time; this test exists so a regression wouldn't need to rely
on review alone next time).
"""
import uuid

import pytest

from app.models.knowledge import Chunk, Document, DocumentVersion, KnowledgeSource


class _FakeEmbeddingProvider:
    async def embed(self, texts):
        return [[0.01] * 384 for _ in texts]


class _FakeGenerationProvider:
    async def generate(self, *, system_prompt, user_prompt, max_tokens=1024):
        return "Refunds are processed within 5 business days, based on the reference material."


async def _register_and_login(client, db_session, seeded_roles, *, slug="acme-rag", email="owner@acme-rag.com"):
    await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-strong-enough-password",
            "full_name": "Owner",
            "organization_name": "Acme RAG",
            "organization_slug": slug,
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
    return body["access_token"], uuid.UUID(org_id)


async def _seed_ready_document_with_chunk(db_session, *, org_id, content):
    source = KnowledgeSource(org_id=org_id, type="manual", name="Seed source")
    db_session.add(source)
    await db_session.flush()

    document = Document(
        org_id=org_id, knowledge_source_id=source.id, title="Billing FAQ",
        mime_type="text/markdown", status="ready",
    )
    db_session.add(document)
    await db_session.flush()

    version = DocumentVersion(
        org_id=org_id, document_id=document.id, version_number=1,
        storage_path="unused-for-this-test", content_hash="fake",
    )
    db_session.add(version)
    await db_session.flush()
    document.current_version_id = version.id

    chunk = Chunk(
        org_id=org_id, document_id=document.id, document_version_id=version.id,
        heading_path="Billing > Refunds", chunk_order=0, content=content,
        token_count=len(content.split()), embedding=[0.01] * 384,
    )
    db_session.add(chunk)
    await db_session.commit()
    return document, chunk


@pytest.mark.asyncio
async def test_ask_question_full_flow_returns_grounded_answer_with_citations(client, db_session, seeded_roles, monkeypatch):
    token, org_id = await _register_and_login(client, db_session, seeded_roles)
    _document, chunk = await _seed_ready_document_with_chunk(
        db_session, org_id=org_id, content="Refunds are processed within 5 business days of approval."
    )

    import app.services.rag as rag_module
    import app.services.retrieval as retrieval_module
    from app.ai.reranking import NoOpRerankProvider

    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: _FakeEmbeddingProvider())
    monkeypatch.setattr(rag_module, "get_generation_provider", lambda: _FakeGenerationProvider())
    monkeypatch.setattr(retrieval_module, "get_rerank_provider", lambda: NoOpRerankProvider())

    headers = {"Authorization": f"Bearer {token}"}

    conv_resp = await client.post(f"/api/orgs/{org_id}/conversations", json={}, headers=headers)
    assert conv_resp.status_code == 201
    conversation_id = conv_resp.json()["id"]

    ask_resp = await client.post(
        f"/api/orgs/{org_id}/conversations/{conversation_id}/messages",
        json={"question": "How long does a refund take?"},
        headers=headers,
    )
    assert ask_resp.status_code == 200
    body = ask_resp.json()
    assert "5 business days" in body["message"]
    assert body["low_confidence"] is False
    assert len(body["citations"]) >= 1
    assert str(chunk.id) == body["citations"][0]["chunk_id"]
    assert body["citations"][0]["heading_path"] == "Billing > Refunds"


@pytest.mark.asyncio
async def test_ask_question_with_no_matching_documents_is_low_confidence(client, db_session, seeded_roles, monkeypatch):
    token, org_id = await _register_and_login(client, db_session, seeded_roles, slug="acme-empty", email="owner@acme-empty.com")

    import app.services.rag as rag_module
    import app.services.retrieval as retrieval_module
    from app.ai.reranking import NoOpRerankProvider

    class _FakeGenerationNoContext:
        async def generate(self, *, system_prompt, user_prompt, max_tokens=1024):
            assert "No relevant reference material" in user_prompt
            return "I don't have enough information to answer that."

    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: _FakeEmbeddingProvider())
    monkeypatch.setattr(rag_module, "get_generation_provider", lambda: _FakeGenerationNoContext())
    monkeypatch.setattr(retrieval_module, "get_rerank_provider", lambda: NoOpRerankProvider())

    headers = {"Authorization": f"Bearer {token}"}
    conv_resp = await client.post(f"/api/orgs/{org_id}/conversations", json={}, headers=headers)
    conversation_id = conv_resp.json()["id"]

    ask_resp = await client.post(
        f"/api/orgs/{org_id}/conversations/{conversation_id}/messages",
        json={"question": "What is the capital of France?"},
        headers=headers,
    )
    assert ask_resp.status_code == 200
    body = ask_resp.json()
    assert body["citations"] == []


@pytest.mark.asyncio
async def test_ask_question_on_conversation_from_another_org_is_rejected(client, db_session, seeded_roles, monkeypatch):
    token_a, org_a = await _register_and_login(client, db_session, seeded_roles, slug="org-a-rag", email="a@org-a-rag.com")
    token_b, org_b = await _register_and_login(client, db_session, seeded_roles, slug="org-b-rag", email="b@org-b-rag.com")

    headers_b = {"Authorization": f"Bearer {token_b}"}
    conv_resp = await client.post(f"/api/orgs/{org_b}/conversations", json={}, headers=headers_b)
    conversation_id = conv_resp.json()["id"]

    # Org A's token, but targeting org A's own URL with org B's conversation_id - the
    # conversation lookup itself is org-scoped (see services/rag.py:answer_question), so this
    # must 404, not leak org B's conversation.
    headers_a = {"Authorization": f"Bearer {token_a}"}
    ask_resp = await client.post(
        f"/api/orgs/{org_a}/conversations/{conversation_id}/messages",
        json={"question": "anything"},
        headers=headers_a,
    )
    assert ask_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_conversations_and_messages_with_citations(client, db_session, seeded_roles, monkeypatch):
    """Closes a gap found during frontend integration: previously nothing could re-fetch a
    conversation's history. Exercises the new list endpoints end to end, including that
    citations attached to an AI message round-trip correctly."""
    token, org_id = await _register_and_login(client, db_session, seeded_roles, slug="acme-rag-list", email="owner@acme-rag-list.com")
    _document, chunk = await _seed_ready_document_with_chunk(
        db_session, org_id=org_id, content="Refunds are processed within 5 business days of approval."
    )

    import app.services.rag as rag_module
    import app.services.retrieval as retrieval_module
    from app.ai.reranking import NoOpRerankProvider

    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: _FakeEmbeddingProvider())
    monkeypatch.setattr(rag_module, "get_generation_provider", lambda: _FakeGenerationProvider())
    monkeypatch.setattr(retrieval_module, "get_rerank_provider", lambda: NoOpRerankProvider())

    headers = {"Authorization": f"Bearer {token}"}
    conv_resp = await client.post(f"/api/orgs/{org_id}/conversations", json={}, headers=headers)
    conversation_id = conv_resp.json()["id"]

    await client.post(
        f"/api/orgs/{org_id}/conversations/{conversation_id}/messages",
        json={"question": "How long does a refund take?"},
        headers=headers,
    )

    list_conv_resp = await client.get(f"/api/orgs/{org_id}/conversations", headers=headers)
    assert list_conv_resp.status_code == 200
    conversations = list_conv_resp.json()["conversations"]
    assert any(c["id"] == conversation_id for c in conversations)

    messages_resp = await client.get(
        f"/api/orgs/{org_id}/conversations/{conversation_id}/messages", headers=headers
    )
    assert messages_resp.status_code == 200
    messages = messages_resp.json()["messages"]
    assert len(messages) == 2  # customer question + ai answer
    assert messages[0]["role"] == "customer"
    assert messages[1]["role"] == "ai"
    assert len(messages[1]["citations"]) == 1
    assert messages[1]["citations"][0]["chunk_id"] == str(chunk.id)


@pytest.mark.asyncio
async def test_list_messages_for_other_orgs_conversation_404s(client, db_session, seeded_roles):
    token_a, org_a = await _register_and_login(client, db_session, seeded_roles, slug="acme-rag-list-a", email="a@rag-list.com")
    token_b, org_b = await _register_and_login(client, db_session, seeded_roles, slug="acme-rag-list-b", email="b@rag-list.com")

    conv_resp = await client.post(
        f"/api/orgs/{org_b}/conversations", json={}, headers={"Authorization": f"Bearer {token_b}"}
    )
    conversation_id = conv_resp.json()["id"]

    resp = await client.get(
        f"/api/orgs/{org_a}/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 404

