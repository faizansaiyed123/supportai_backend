"""Conversation + AI-question endpoints (Phase 4/5)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, require_permission
from app.core.rate_limit import rate_limit_ask_question
from app.db.base import get_db
from app.schemas.conversation import (
    AnswerResponse,
    AskQuestionRequest,
    CitationPublic,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationPublic,
    MessageListResponse,
    MessagePublic,
)
from app.services import conversation as conversation_service
from app.services import rag as rag_service

router = APIRouter()


@router.post(
    "/orgs/{org_id}/conversations", response_model=ConversationPublic, status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    org_id: uuid.UUID,
    payload: ConversationCreateRequest,
    actor: Actor = Depends(require_permission("conversations:respond")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    return await conversation_service.create_conversation(db, actor=actor, customer_id=payload.customer_id)


@router.post("/orgs/{org_id}/conversations/{conversation_id}/messages", response_model=AnswerResponse)
async def ask_question(
    org_id: uuid.UUID,
    conversation_id: uuid.UUID,
    payload: AskQuestionRequest,
    actor: Actor = Depends(require_permission("conversations:respond")),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_ask_question),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        result = await rag_service.answer_question(
            db, actor=actor, conversation_id=conversation_id, question=payload.question
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found") from None

    return AnswerResponse(
        message=result.answer_text,
        low_confidence=result.low_confidence,
        citations=[
            CitationPublic(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                heading_path=c.heading_path,
                excerpt=c.content[:300],
            )
            for c in result.citations
        ],
    )


@router.post("/orgs/{org_id}/conversations/{conversation_id}/escalate", response_model=ConversationPublic)
async def escalate(
    org_id: uuid.UUID,
    conversation_id: uuid.UUID,
    actor: Actor = Depends(require_permission("conversations:respond")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await conversation_service.escalate_conversation(db, actor=actor, conversation_id=conversation_id)
    except conversation_service.ConversationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found") from None


@router.get("/orgs/{org_id}/conversations", response_model=ConversationListResponse)
async def list_conversations(
    org_id: uuid.UUID,
    actor: Actor = Depends(require_permission("conversations:read")),
    db: AsyncSession = Depends(get_db),
):
    """Closes a real gap found during frontend integration - see
    services/conversation.py:list_conversations's docstring."""
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    conversations = await conversation_service.list_conversations(db, actor=actor)
    return ConversationListResponse(conversations=conversations)  # type: ignore[arg-type]


@router.get("/orgs/{org_id}/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def list_messages(
    org_id: uuid.UUID,
    conversation_id: uuid.UUID,
    actor: Actor = Depends(require_permission("conversations:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        messages = await conversation_service.list_messages(db, actor=actor, conversation_id=conversation_id)
    except conversation_service.ConversationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found") from None
    return MessageListResponse(messages=[MessagePublic(**m) for m in messages])
