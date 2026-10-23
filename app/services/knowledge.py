"""
Knowledge base business logic (Phase 3, D010). Plain functions per D004. Every function takes
an Actor (never a bare org_id) and scopes all queries by actor.org_id - the tenant isolation
invariant (D005).
"""
import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.core.config import get_settings
from app.models.knowledge import Document, DocumentVersion, KnowledgeSource
from app.services.audit import record_audit_event
from app.services.extraction import SUPPORTED_MIME_TYPES, sniff_mime_type
from app.services.storage import get_storage, safe_relative_path
from app.services.url_fetch import URLFetchError, fetch_url


class KnowledgeError(Exception):
    pass


class UnsupportedFileTypeError(KnowledgeError):
    pass


class FileTooLargeError(KnowledgeError):
    pass


class NotFoundError(KnowledgeError):
    pass


async def get_or_create_manual_source(db: AsyncSession, *, actor: Actor) -> KnowledgeSource:
    """Documents uploaded without picking a specific knowledge source land in a default
    per-org 'manual' source, created lazily on first use."""
    result = await db.execute(
        select(KnowledgeSource).where(
            KnowledgeSource.org_id == actor.org_id, KnowledgeSource.type == "manual"
        )
    )
    source = result.scalars().first()
    if source is not None:
        return source

    source = KnowledgeSource(
        org_id=actor.org_id, type="manual", name="Manual uploads", created_by=actor.user_id
    )
    db.add(source)
    await db.flush()
    return source


def _validate_upload(*, content: bytes, filename: str) -> str:
    """Shared by upload_document (first version) and create_document_version (subsequent
    versions) - size/MIME validation shouldn't be re-implemented per call site."""
    settings = get_settings()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise FileTooLargeError(f"File exceeds {settings.max_upload_size_mb}MB limit")

    mime_type = sniff_mime_type(content, declared_filename=filename)
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise UnsupportedFileTypeError(f"Unsupported file type: {mime_type}")
    return mime_type


async def upload_document(
    db: AsyncSession,
    *,
    actor: Actor,
    filename: str,
    content: bytes,
    title: str | None = None,
    knowledge_source_id: uuid.UUID | None = None,
):
    """Creates Document + first DocumentVersion, saves the file, and enqueues ingestion.
    Returns the Document. Ingestion (extraction/chunking/embedding) happens asynchronously
    in a background job (app/workers/ingestion.py) - this function returns as soon as the
    file is durably saved and the job is enqueued (Sec. 13 - don't block on large documents)."""
    mime_type = _validate_upload(content=content, filename=filename)

    if knowledge_source_id is None:
        source = await get_or_create_manual_source(db, actor=actor)
        knowledge_source_id = source.id
    else:
        # Re-verify the source belongs to this org - never trust a client-supplied FK alone
        # across tenant boundaries (D005).
        result = await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.id == knowledge_source_id, KnowledgeSource.org_id == actor.org_id
            )
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError("knowledge_source_id not found in this organization")

    document = Document(
        org_id=actor.org_id,
        knowledge_source_id=knowledge_source_id,
        title=title or filename,
        original_filename=filename,
        mime_type=mime_type,
        status="pending",
    )
    db.add(document)
    await db.flush()

    relative_path = safe_relative_path(
        org_id=actor.org_id, document_id=document.id, filename=filename, version_number=1
    )
    storage = get_storage()
    storage.save(relative_path, content)

    version = DocumentVersion(
        org_id=actor.org_id,
        document_id=document.id,
        version_number=1,
        storage_path=relative_path,
        content_hash=hashlib.sha256(content).hexdigest(),
        created_by=actor.user_id,
    )
    db.add(version)
    await db.flush()

    document.current_version_id = version.id
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="document.uploaded",
        target_type="document",
        target_id=document.id,
        metadata={"filename": filename, "mime_type": mime_type},
    )
    await db.commit()

    from app.workers.ingestion import ingest_document_version

    await ingest_document_version.configure().defer_async(document_version_id=str(version.id))

    await db.refresh(document)
    return document


async def create_document_version(
    db: AsyncSession,
    *,
    actor: Actor,
    document_id: uuid.UUID,
    filename: str,
    content: bytes,
):
    """Re-ingests a document with new content, creating a NEW DocumentVersion rather than
    mutating the existing one (versions are immutable, see models/knowledge.py) and
    re-triggering the ingestion pipeline. Closes the "document versioning has a schema but no
    endpoint" gap (TODO.md).

    The document's mime_type is re-detected from the new content (not assumed to match the
    previous version's) - a re-upload could legitimately be a different format (e.g.
    replacing a stale PDF with an updated Markdown source)."""
    document = await get_document(db, actor=actor, document_id=document_id)
    mime_type = _validate_upload(content=content, filename=filename)

    version_count_result = await db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    )
    existing_versions = version_count_result.scalars().all()
    next_version_number = max((v.version_number for v in existing_versions), default=0) + 1

    relative_path = safe_relative_path(
        org_id=actor.org_id, document_id=document.id, filename=filename, version_number=next_version_number
    )
    storage = get_storage()
    storage.save(relative_path, content)

    version = DocumentVersion(
        org_id=actor.org_id,
        document_id=document.id,
        version_number=next_version_number,
        storage_path=relative_path,
        content_hash=hashlib.sha256(content).hexdigest(),
        created_by=actor.user_id,
    )
    db.add(version)
    await db.flush()

    document.current_version_id = version.id
    document.mime_type = mime_type
    document.status = "pending"
    document.status_error = None
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="document.reingested",
        target_type="document",
        target_id=document.id,
        metadata={"version_number": next_version_number},
    )
    await db.commit()

    from app.workers.ingestion import ingest_document_version

    await ingest_document_version.configure().defer_async(document_version_id=str(version.id))

    await db.refresh(document)
    return document


async def get_document(db: AsyncSession, *, actor: Actor, document_id: uuid.UUID) -> Document:
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.org_id == actor.org_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise NotFoundError("document not found")
    return document


async def list_documents(db: AsyncSession, *, actor: Actor, limit: int = 50, offset: int = 0) -> list[Document]:
    result = await db.execute(
        select(Document)
        .where(Document.org_id == actor.org_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def delete_document(db: AsyncSession, *, actor: Actor, document_id: uuid.UUID) -> None:
    document = await get_document(db, actor=actor, document_id=document_id)
    # Chunks cascade-delete via FK ondelete=CASCADE (see models/knowledge.py). File cleanup
    # in storage is best-effort here; a failed delete leaves an orphaned file rather than a
    # dangling DB reference, which is the safer failure mode.
    result = await db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    )
    storage = get_storage()
    for version in result.scalars().all():
        try:
            storage.delete(version.storage_path)
        except Exception:
            pass
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="document.deleted",
        target_type="document",
        target_id=document.id,
    )
    await db.delete(document)
    await db.commit()


async def add_url_document(db: AsyncSession, *, actor: Actor, url: str, title: str | None = None):
    """URL-type knowledge ingestion (Sec. 13/18, closes a TODO.md gap). Fetches `url` through
    app/services/url_fetch.py's SSRF-guarded fetch_url() - never bypass that and fetch a URL
    directly elsewhere in the codebase. Otherwise follows the same
    validate -> save -> create Document/DocumentVersion -> enqueue-ingestion path as file
    uploads, so it benefits from the exact same downstream extraction/chunking/embedding
    pipeline and the exact same failure handling."""
    try:
        content, content_type = await fetch_url(url)
    except URLFetchError as exc:
        raise KnowledgeError(f"Failed to fetch URL: {exc}") from exc

    # Normalize the fetched content-type to one of our supported mime types - a web server
    # might report e.g. "text/html; charset=utf-8" (already split off by fetch_url) or
    # something not in our allowlist, in which case this is treated the same as an
    # unsupported file upload.
    mime_type = content_type if content_type in SUPPORTED_MIME_TYPES else "text/html"
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise UnsupportedFileTypeError(f"Unsupported content type from URL: {content_type}")

    settings = get_settings()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise FileTooLargeError(f"Fetched content exceeds {settings.max_upload_size_mb}MB limit")

    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.org_id == actor.org_id, KnowledgeSource.type == "url")
    )
    source = result.scalars().first()
    if source is None:
        source = KnowledgeSource(org_id=actor.org_id, type="url", name="URL imports", created_by=actor.user_id)
        db.add(source)
        await db.flush()

    document = Document(
        org_id=actor.org_id,
        knowledge_source_id=source.id,
        title=title or url,
        original_filename=None,
        mime_type=mime_type,
        status="pending",
    )
    db.add(document)
    await db.flush()

    # ".html" as the nominal extension regardless of exact mime subtype - keeps storage paths
    # predictable; the actual extraction path (services/extraction.py) dispatches on the
    # stored mime_type column, not the file extension, so this is cosmetic only.
    relative_path = safe_relative_path(
        org_id=actor.org_id, document_id=document.id, filename="source.html", version_number=1
    )
    storage = get_storage()
    storage.save(relative_path, content)

    version = DocumentVersion(
        org_id=actor.org_id,
        document_id=document.id,
        version_number=1,
        storage_path=relative_path,
        content_hash=hashlib.sha256(content).hexdigest(),
        created_by=actor.user_id,
    )
    db.add(version)
    await db.flush()

    document.current_version_id = version.id
    await record_audit_event(
        db, org_id=actor.org_id, actor_user_id=actor.user_id, action="document.url_imported",
        target_type="document", target_id=document.id, metadata={"url": url},
    )
    await db.commit()
    await db.refresh(document)

    from app.workers.ingestion import ingest_document_version

    await ingest_document_version.configure().defer_async(document_version_id=str(version.id))

    return document
