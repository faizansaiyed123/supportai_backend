import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TicketCreateRequest(BaseModel):
    customer_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    conversation_id: uuid.UUID | None = None
    priority: str = "normal"


class TicketPublic(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    priority: str
    customer_id: uuid.UUID
    assigned_agent_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketListResponse(BaseModel):
    tickets: list[TicketPublic]


class TicketNoteCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class TicketNotePublic(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketNoteListResponse(BaseModel):
    notes: list[TicketNotePublic]
