import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CustomerCreateRequest(BaseModel):
    external_id: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)


class CustomerPublic(BaseModel):
    id: uuid.UUID
    external_id: str | None
    email: str | None
    name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerListResponse(BaseModel):
    customers: list[CustomerPublic]
