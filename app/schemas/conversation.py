import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreateRequest(BaseModel):
    customer_id: uuid.UUID | None = None


class ConversationPublic(BaseModel):
    id: uuid.UUID
    status: str
    assigned_agent_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AskQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)


class CitationPublic(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    heading_path: str | None
    excerpt: str


class AnswerResponse(BaseModel):
    message: str
    citations: list[CitationPublic]
    low_confidence: bool


class MessagePublic(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    citations: list[CitationPublic]


class MessageListResponse(BaseModel):
    messages: list[MessagePublic]


class ConversationListResponse(BaseModel):
    conversations: list[ConversationPublic]
