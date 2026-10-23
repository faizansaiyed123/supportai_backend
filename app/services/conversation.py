"""Conversation lifecycle business logic (Phase 5). Plain functions per D004."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.models.conversation import Conversation, Message, MessageCitation
from app.models.knowledge import Chunk
from app.services.audit import record_audit_event


class ConversationNotFoundError(Exception):
    pass


async def create_conversation(
    db: AsyncSession, *, actor: Actor, customer_id: uuid.UUID | None = None
) -> Conversation:
    conversation = Conversation(org_id=actor.org_id, customer_id=customer_id, status="open")
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def get_conversation(db: AsyncSession, *, actor: Actor, conversation_id: uuid.UUID) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.org_id == actor.org_id
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise ConversationNotFoundError()
    return conversation


async def escalate_conversation(db: AsyncSession, *, actor: Actor, conversation_id: uuid.UUID) -> Conversation:
    # Escalation is audited (Sec. 50) - it's the administratively significant event here;
    # routine conversation creation is not audited to avoid flooding the audit log with
    # every-day traffic that carries little security/compliance signal.
    conversation = await get_conversation(db, actor=actor, conversation_id=conversation_id)
    conversation.status = "escalated"
    await record_audit_event(
        db, org_id=actor.org_id, actor_user_id=actor.user_id, action="conversation.escalated",
        target_type="conversation", target_id=conversation.id,
    )
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def list_conversations(db: AsyncSession, *, actor: Actor, status_filter: str | None = None) -> list[Conversation]:
    """Closes a real gap found during frontend integration work: there was previously no way
    to retrieve existing conversations at all - only create/ask/escalate existed. A chat UI
    with no way to list or reload past conversations would either lose all history on refresh
    or have to fake persistence client-side; neither is acceptable."""
    query = select(Conversation).where(Conversation.org_id == actor.org_id)
    if status_filter:
        query = query.where(Conversation.status == status_filter)
    result = await db.execute(query.order_by(Conversation.created_at.desc()))
    return list(result.scalars().all())


async def list_messages(db: AsyncSession, *, actor: Actor, conversation_id: uuid.UUID) -> list[dict]:
    """Same motivation as list_conversations - closes the "can create messages but never
    re-read them" gap. Re-verifies the conversation belongs to this org before returning any
    messages (D005) - get_conversation already does this and raises ConversationNotFoundError
    if not, which the caller (API route) maps to a 404.

    Returns plain dicts (not ORM objects) because each message needs its citations attached -
    a shape the Message model itself doesn't carry. Matches the dict shape
    schemas/conversation.py's MessagePublic expects."""
    await get_conversation(db, actor=actor, conversation_id=conversation_id)
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = list(result.scalars().all())
    if not messages:
        return []

    message_ids = [m.id for m in messages]
    citation_result = await db.execute(
        select(MessageCitation, Chunk)
        .join(Chunk, Chunk.id == MessageCitation.chunk_id)
        .where(MessageCitation.message_id.in_(message_ids))
        .order_by(MessageCitation.rank.asc())
    )
    citations_by_message: dict[uuid.UUID, list[dict]] = {}
    for citation, chunk in citation_result.all():
        citations_by_message.setdefault(citation.message_id, []).append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "heading_path": chunk.heading_path,
                "excerpt": chunk.content[:300],
            }
        )

    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at,
            "citations": citations_by_message.get(m.id, []),
        }
        for m in messages
    ]
