"""Support ticket business logic (Phase 5, Sec. 15). Plain functions per D004."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.models.ticket import Ticket, TicketNote
from app.services.audit import record_audit_event

_VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
_VALID_STATUSES = {"open", "pending", "resolved", "closed"}


class TicketNotFoundError(Exception):
    pass


class InvalidTicketStateError(Exception):
    pass


async def create_ticket(
    db: AsyncSession,
    *,
    actor: Actor,
    customer_id: uuid.UUID,
    title: str,
    conversation_id: uuid.UUID | None = None,
    priority: str = "normal",
) -> Ticket:
    if priority not in _VALID_PRIORITIES:
        raise InvalidTicketStateError(f"invalid priority: {priority}")
    ticket = Ticket(
        org_id=actor.org_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
        title=title,
        priority=priority,
        status="open",
    )
    db.add(ticket)
    await db.flush()
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="ticket.created",
        target_type="ticket",
        target_id=ticket.id,
        metadata={"priority": priority},
    )
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def get_ticket(db: AsyncSession, *, actor: Actor, ticket_id: uuid.UUID) -> Ticket:
    result = await db.execute(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.org_id == actor.org_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise TicketNotFoundError()
    return ticket


async def list_tickets(db: AsyncSession, *, actor: Actor, status_filter: str | None = None) -> list[Ticket]:
    query = select(Ticket).where(Ticket.org_id == actor.org_id)
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    result = await db.execute(query.order_by(Ticket.created_at.desc()))
    return list(result.scalars().all())


async def assign_ticket(db: AsyncSession, *, actor: Actor, ticket_id: uuid.UUID, agent_id: uuid.UUID) -> Ticket:
    ticket = await get_ticket(db, actor=actor, ticket_id=ticket_id)
    ticket.assigned_agent_id = agent_id
    ticket.status = "pending"
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="ticket.assigned",
        target_type="ticket",
        target_id=ticket.id,
        metadata={"assigned_agent_id": str(agent_id)},
    )
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def update_ticket_status(db: AsyncSession, *, actor: Actor, ticket_id: uuid.UUID, new_status: str) -> Ticket:
    if new_status not in _VALID_STATUSES:
        raise InvalidTicketStateError(f"invalid status: {new_status}")
    ticket = await get_ticket(db, actor=actor, ticket_id=ticket_id)
    previous_status = ticket.status
    ticket.status = new_status
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="ticket.status_changed",
        target_type="ticket",
        target_id=ticket.id,
        metadata={"from": previous_status, "to": new_status},
    )
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def add_ticket_note(
    db: AsyncSession, *, actor: Actor, ticket_id: uuid.UUID, content: str
) -> TicketNote:
    """Closes a Phase 5 gap - TicketNote has existed in the schema since Session 3 with no
    endpoint ever creating one. Internal-only by design (models/ticket.py's own docstring) -
    there is no customer-facing read path anywhere in this API for ticket notes, and this
    function doesn't create one; the org-membership-only auth model this whole API uses means
    "internal" here effectively means "visible to any org member with tickets:read", which is
    the correct boundary given there's no separate customer-authenticated surface yet."""
    ticket = await get_ticket(db, actor=actor, ticket_id=ticket_id)  # 404s if wrong org/missing
    note = TicketNote(org_id=actor.org_id, ticket_id=ticket.id, author_id=actor.user_id, content=content)
    db.add(note)
    await db.flush()
    await record_audit_event(
        db, org_id=actor.org_id, actor_user_id=actor.user_id, action="ticket.note_added",
        target_type="ticket", target_id=ticket.id, metadata={"note_id": str(note.id)},
    )
    await db.commit()
    await db.refresh(note)
    return note


async def list_ticket_notes(db: AsyncSession, *, actor: Actor, ticket_id: uuid.UUID) -> list[TicketNote]:
    await get_ticket(db, actor=actor, ticket_id=ticket_id)  # 404s / org-scopes implicitly
    result = await db.execute(
        select(TicketNote).where(TicketNote.ticket_id == ticket_id).order_by(TicketNote.created_at.asc())
    )
    return list(result.scalars().all())
