import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=1, max_length=255)
    organization_name: str = Field(min_length=1, max_length=255)
    organization_slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    organization_slug: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserPublic(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str

    model_config = {"from_attributes": True}


class InviteRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern=r"^(admin|agent|viewer)$")  # never allow inviting as 'owner'
    # via this endpoint - ownership transfer, if ever built, should be its own explicit,
    # more-guarded action, not a side effect of a routine invite call.


class InviteResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    invite_token: str  # safe to return here - see services/auth.py:create_invite's docstring
    expires_in_hours: int = 72


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=200)


class PasswordResetRequestRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=200)
