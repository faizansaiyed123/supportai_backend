"""
Knowledge base endpoints (Phase 3). Every endpoint depends on get_current_actor and scopes
through services/knowledge.py, which filters by actor.org_id (D005 tenant isolation).
"""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, require_permission
from app.core.rate_limit import rate_limit_document_upload
from app.db.base import get_db
from app.schemas.knowledge import DocumentListResponse, DocumentPublic, URLDocumentCreateRequest
from app.services import knowledge as knowledge_service

router = APIRouter()


@router.post(
    "/orgs/{org_id}/documents",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    org_id: uuid.UUID,
    file: UploadFile = File(...),
    actor: Actor = Depends(require_permission("knowledge:manage")),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_document_upload),
):
    if org_id != actor.org_id:
        # The URL's org_id must match the authenticated actor's org - never trust the URL
        # value alone as authorization (D005).
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")

    content = await file.read()
    try:
        document = await knowledge_service.upload_document(
            db, actor=actor, filename=file.filename or "upload", content=content
        )
    except knowledge_service.FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except knowledge_service.UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    return document


@router.get("/orgs/{org_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    org_id: uuid.UUID,
    actor: Actor = Depends(require_permission("knowledge:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    documents = await knowledge_service.list_documents(db, actor=actor)
    return DocumentListResponse(documents=documents)  # type: ignore[arg-type]


@router.get("/orgs/{org_id}/documents/{document_id}", response_model=DocumentPublic)
async def get_document(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: Actor = Depends(require_permission("knowledge:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await knowledge_service.get_document(db, actor=actor, document_id=document_id)
    except knowledge_service.NotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from None


@router.delete("/orgs/{org_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: Actor = Depends(require_permission("knowledge:manage")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        await knowledge_service.delete_document(db, actor=actor, document_id=document_id)
    except knowledge_service.NotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from None


@router.post(
    "/orgs/{org_id}/documents/{document_id}/versions",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_version(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    actor: Actor = Depends(require_permission("knowledge:manage")),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_document_upload),
):
    """Re-ingests a document with new content as a new version. Closes the "versioning has a
    schema but no endpoint" gap (TODO.md)."""
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")

    content = await file.read()
    try:
        document = await knowledge_service.create_document_version(
            db, actor=actor, document_id=document_id, filename=file.filename or "upload", content=content
        )
    except knowledge_service.NotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from None
    except knowledge_service.FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except knowledge_service.UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    return document


@router.post(
    "/orgs/{org_id}/documents/from-url",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_from_url(
    org_id: uuid.UUID,
    payload: URLDocumentCreateRequest,
    actor: Actor = Depends(require_permission("knowledge:manage")),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_document_upload),
):
    """URL-type knowledge ingestion (closes a TODO.md gap). Gated by knowledge:manage (same
    as file upload) AND rate-limited the same way - fetching arbitrary URLs is at least as
    costly/abusable as file upload, and carries additional SSRF risk (see
    app/services/url_fetch.py), so it gets no less protection."""
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        document = await knowledge_service.add_url_document(db, actor=actor, url=payload.url, title=payload.title)
    except knowledge_service.UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except knowledge_service.FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except knowledge_service.KnowledgeError as exc:
        # Includes URL-fetch failures (unsafe/unreachable URL) - 422 rather than 500, since
        # this is a client-supplied-URL problem, not a server fault.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return document
