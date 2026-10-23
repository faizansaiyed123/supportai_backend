import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DocumentPublic(BaseModel):
    id: uuid.UUID
    title: str
    original_filename: str | None
    mime_type: str | None
    status: str
    status_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    documents: list[DocumentPublic]


class URLDocumentCreateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=500)
