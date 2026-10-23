"""
Ingestion background job (Sec. 13/20, D008). Runs in the worker process
(docker-compose.yml `worker` service), not the API process - document processing must not
block API requests.

Failure handling (Sec. 27): any failure updates the Document's status to 'failed' with a
human-readable status_error rather than leaving it stuck at 'processing' forever or raising
an unhandled exception that procrastinate would just retry blindly. Procrastinate's own retry
policy still applies on top (network blips to the embedding provider, etc.) - see
app.configure(retry=...) below.

The actual logic lives in `_ingest_document_version_impl`, taking an explicit `db` session,
separate from the `@procrastinate_app.task`-decorated `ingest_document_version` that opens a
real session and calls it. This split exists specifically so tests can call the logic directly
with a test session, without needing to know or assume anything about how procrastinate's task
decorator wraps the function internally (tests/workers/test_ingestion.py).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.factory import get_embedding_provider
from app.core.logging import get_logger
from app.db.base import AsyncSessionLocal
from app.models.knowledge import Chunk, Document, DocumentVersion
from app.services.chunking import chunk_document
from app.services.extraction import ExtractionError, extract_text
from app.services.storage import get_storage
from app.workers.app import procrastinate_app

logger = get_logger(__name__)


async def _ingest_document_version_impl(document_version_id: str, db: AsyncSession) -> None:
    result = await db.execute(
        select(DocumentVersion).where(DocumentVersion.id == document_version_id)
    )
    version = result.scalar_one_or_none()
    if version is None:
        logger.error("ingestion_version_not_found", document_version_id=document_version_id)
        return

    doc_result = await db.execute(select(Document).where(Document.id == version.document_id))
    document = doc_result.scalar_one()

    document.status = "processing"
    await db.commit()

    try:
        storage = get_storage()
        raw_bytes = storage.read(version.storage_path)
        text = extract_text(content=raw_bytes, mime_type=document.mime_type or "application/octet-stream")

        if not text.strip():
            raise ExtractionError("Extracted text is empty")

        version.extracted_text = text
        await db.commit()

        chunk_results = chunk_document(text)
        if not chunk_results:
            raise ExtractionError("Document produced no chunks after extraction")

        embedding_provider = get_embedding_provider()
        embeddings = await embedding_provider.embed([c.content for c in chunk_results])

        for chunk_result, embedding in zip(chunk_results, embeddings, strict=False):
            db.add(
                Chunk(
                    org_id=document.org_id,
                    document_id=document.id,
                    document_version_id=version.id,
                    heading_path=chunk_result.heading_path,
                    chunk_order=chunk_result.order,
                    content=chunk_result.content,
                    token_count=chunk_result.token_count,
                    embedding=embedding,
                )
            )

        document.status = "ready"
        document.status_error = None
        await db.commit()
        logger.info(
            "ingestion_succeeded",
            document_id=str(document.id),
            chunk_count=len(chunk_results),
        )

    except Exception as exc:
        await db.rollback()
        document.status = "failed"
        document.status_error = str(exc)[:2000]  # bounded - never let a huge error blow up
        # the column or leak excessive internals to anyone reading document status
        await db.commit()
        logger.error("ingestion_failed", document_id=str(document.id), error=str(exc))
        # Re-raise so procrastinate's retry policy can decide whether to retry - the document
        # row already reflects 'failed' in the meantime rather than staying silently stuck at
        # 'processing'.
        raise


@procrastinate_app.task(name="ingest_document_version", retry=3)
async def ingest_document_version(document_version_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await _ingest_document_version_impl(document_version_id, db)
