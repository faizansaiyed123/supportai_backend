import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MembershipPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MembershipListResponse(BaseModel):
    memberships: list[MembershipPublic]


class RoleChangeRequest(BaseModel):
    role: str = Field(pattern=r"^(admin|agent|viewer)$")
