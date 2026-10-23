import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.knowledge import created_at_column
from app.models.mixins import UUIDPrimaryKeyMixin


class ActionToken(UUIDPrimaryKeyMixin, Base):
    """Generic single-use token for out-of-band actions: org-member invites and password
    resets (D026/D027). One table for both purposes rather than two near-identical ones -
    `purpose` distinguishes them, and both need the exact same shape (hash, expiry, used_at).

    SECURITY-CRITICAL: the raw token is never stored, only its hash (same pattern as
    RefreshToken - see D012). Whether the raw token may ever appear in an API response depends
    entirely on `purpose` and is decided in app/services/auth.py, NOT here:
    - purpose='invite': safe to return to the inviting admin (an authenticated action against
      their own org; no enumeration risk).
    - purpose='password_reset': MUST NEVER be returned in any API response - the request-reset
      endpoint is unauthenticated, so returning the token would let anyone take over any
      account by just requesting a reset for a guessed email. See services/auth.py and
      docs/SECURITY.md for the full reasoning.
    """

    __tablename__ = "action_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)  # invite | password_reset
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = created_at_column()
