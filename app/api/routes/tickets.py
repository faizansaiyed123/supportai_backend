"""Ticket endpoints (Phase 5)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, require_permission
from app.core.rate_limit import rate_limit_ticket_write
from app.db.base import get_db
from app.schemas.ticket import (
    TicketCreateRequest,
    TicketListResponse,
    TicketNoteCreateRequest,
    TicketNoteListResponse,
    TicketNotePublic,
    TicketPublic,
)
from app.services import ticket as ticket_service

router = APIRouter()


class TicketStatusUpdateRequest(BaseModel):
    status: str


@router.post("/orgs/{org_id}/tickets", response_model=TicketPublic, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    org_id: uuid.UUID,
    payload: TicketCreateRequest,
    actor: Actor = Depends(require_permission("tickets:manage")),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_ticket_write),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await ticket_service.create_ticket(
            db,
            actor=actor,
            customer_id=payload.customer_id,
            title=payload.title,
            conversation_id=payload.conversation_id,
            priority=payload.priority,
        )
    except ticket_service.InvalidTicketStateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/orgs/{org_id}/tickets", response_model=TicketListResponse)
async def list_tickets(
    org_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    actor: Actor = Depends(require_permission("tickets:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    tickets = await ticket_service.list_tickets(db, actor=actor, status_filter=status_filter)
    return TicketListResponse(tickets=tickets)  # type: ignore[arg-type]


@router.get("/orgs/{org_id}/tickets/{ticket_id}", response_model=TicketPublic)
async def get_ticket(
    org_id: uuid.UUID,
    ticket_id: uuid.UUID,
    actor: Actor = Depends(require_permission("tickets:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await ticket_service.get_ticket(db, actor=actor, ticket_id=ticket_id)
    except ticket_service.TicketNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found") from None


@router.post("/orgs/{org_id}/tickets/{ticket_id}/assign", response_model=TicketPublic)
async def assign_ticket(
    org_id: uuid.UUID,
    ticket_id: uuid.UUID,
    agent_id: uuid.UUID,
    actor: Actor = Depends(require_permission("tickets:manage")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await ticket_service.assign_ticket(db, actor=actor, ticket_id=ticket_id, agent_id=agent_id)
    except ticket_service.TicketNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found") from None


@router.post("/orgs/{org_id}/tickets/{ticket_id}/status", response_model=TicketPublic)
async def update_ticket_status(
    org_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketStatusUpdateRequest,
    actor: Actor = Depends(require_permission("tickets:manage")),
    db: AsyncSession = Depends(get_db),
):
    """Closes a Phase 5 gap - the service function (`update_ticket_status`) already existed
    but had no endpoint calling it."""
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await ticket_service.update_ticket_status(
            db, actor=actor, ticket_id=ticket_id, new_status=payload.status
        )
    except ticket_service.TicketNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found") from None
    except ticket_service.InvalidTicketStateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/orgs/{org_id}/tickets/{ticket_id}/notes", response_model=TicketNotePublic, status_code=status.HTTP_201_CREATED
)
async def add_ticket_note(
    org_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketNoteCreateRequest,
    actor: Actor = Depends(require_permission("tickets:manage")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await ticket_service.add_ticket_note(db, actor=actor, ticket_id=ticket_id, content=payload.content)
    except ticket_service.TicketNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found") from None


@router.get("/orgs/{org_id}/tickets/{ticket_id}/notes", response_model=TicketNoteListResponse)
async def list_ticket_notes(
    org_id: uuid.UUID,
    ticket_id: uuid.UUID,
    actor: Actor = Depends(require_permission("tickets:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        notes = await ticket_service.list_ticket_notes(db, actor=actor, ticket_id=ticket_id)
    except ticket_service.TicketNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found") from None
    return TicketNoteListResponse(notes=notes)  # type: ignore[arg-type]
