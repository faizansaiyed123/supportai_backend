"""
Integration test for the ingestion pipeline (app/workers/ingestion.py) - extraction, chunking,
embedding, and Chunk row creation - against a REAL test database. The embedding provider is
faked (via monkeypatch) rather than using the real sentence-transformers model, so this test
doesn't require network access or a downloaded model to run; it verifies the pipeline's
control flow and DB writes, not embedding quality (that's what app/ai/embeddings.py's own
correctness would need separately, and can't be checked without the real model - see
docs/AI.md).

Calls the task function directly rather than enqueueing through procrastinate and running a
worker process - this tests the actual ingestion LOGIC, not procrastinate's own job-dispatch
machinery (which is a well-tested third-party library, not something this project needs to
re-verify).
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.knowledge import Chunk, Document, DocumentVersion, KnowledgeSource
from app.models.organization import Organization
from app.workers import ingestion as ingestion_module


class _FakeEmbeddingProvider:
    """Returns a deterministic, fixed-length fake vector per input text - enough to exercise
    the pipeline's control flow without needing the real model. Dimension matches
    settings.embedding_dimensions (384) so it would pass the real dimension-mismatch check
    the real provider does."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * 384 for _ in texts]


async def _create_pending_document_version(db, *, org_id, mime_type, storage_path, content):
    org = Organization(id=org_id, name="Test Org", slug="test-org")
    db.add(org)
    await db.flush()

    source = KnowledgeSource(org_id=org_id, type="manual", name="Test source")
    db.add(source)
    await db.flush()

    document = Document(
        org_id=org_id,
        knowledge_source_id=source.id,
        title="Test Document",
        original_filename="test.md",
        mime_type=mime_type,
        status="pending",
    )
    db.add(document)
    await db.flush()

    version = DocumentVersion(
        org_id=org_id,
        document_id=document.id,
        version_number=1,
        storage_path=storage_path,
        content_hash="fake-hash-for-test",
    )
    db.add(version)
    await db.flush()
    document.current_version_id = version.id
    await db.commit()
    return document, version


@pytest.mark.asyncio
async def test_ingestion_pipeline_produces_chunks_with_embeddings(db_session, tmp_path, monkeypatch):
    org_id = uuid.uuid4()

    # Fake storage rooted at a pytest tmp_path, and a fake embedding provider - both injected
    # via monkeypatch so this test needs neither real disk paths outside tmp_path nor network
    # access to download an embedding model.
    from app.services.storage import LocalFileStorage

    fake_storage = LocalFileStorage.__new__(LocalFileStorage)
    fake_storage._root = tmp_path
    tmp_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ingestion_module, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(ingestion_module, "get_embedding_provider", lambda: _FakeEmbeddingProvider())

    content = b"# Getting Started\n\nWelcome to the product. This section explains setup.\n\n## Installation\n\nRun the installer and follow the prompts.\n"
    storage_path = f"{org_id}/test-doc.md"
    fake_storage.save(storage_path, content)

    document, version = await _create_pending_document_version(
        db_session, org_id=org_id, mime_type="text/markdown", storage_path=storage_path, content=content
    )

    # Calls the pure-logic function directly with this test's own session, rather than the
    # @procrastinate_app.task-decorated wrapper (which opens its own session) - this avoids
    # depending on any assumption about how procrastinate's decorator wraps the function.
    await ingestion_module._ingest_document_version_impl(str(version.id), db_session)

    await db_session.refresh(document)
    assert document.status == "ready"
    assert document.status_error is None

    result = await db_session.execute(select(Chunk).where(Chunk.document_id == document.id))
    chunks = result.scalars().all()
    assert len(chunks) >= 1
    assert all(c.embedding is not None for c in chunks)
    assert all(len(c.embedding) == 384 for c in chunks)
    heading_paths = {c.heading_path for c in chunks}
    assert "Getting Started" in heading_paths or "Getting Started > Installation" in heading_paths


@pytest.mark.asyncio
async def test_ingestion_pipeline_marks_document_failed_on_extraction_error(db_session, tmp_path, monkeypatch):
    org_id = uuid.uuid4()
    from app.services.storage import LocalFileStorage

    fake_storage = LocalFileStorage.__new__(LocalFileStorage)
    fake_storage._root = tmp_path
    tmp_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ingestion_module, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(ingestion_module, "get_embedding_provider", lambda: _FakeEmbeddingProvider())

    # Empty content -> extract_text will produce empty text -> ExtractionError -> status='failed'
    storage_path = f"{org_id}/empty.md"
    fake_storage.save(storage_path, b"")

    document, version = await _create_pending_document_version(
        db_session, org_id=org_id, mime_type="text/markdown", storage_path=storage_path, content=b""
    )

    with pytest.raises(Exception):
        await ingestion_module._ingest_document_version_impl(str(version.id), db_session)

    await db_session.refresh(document)
    assert document.status == "failed"
    assert document.status_error is not None

