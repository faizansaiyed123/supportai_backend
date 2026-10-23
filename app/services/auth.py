"""
Auth business logic (D012). Plain functions per D004.

Security-critical invariants:
- Passwords are always argon2id-hashed, never compared/stored in plain text.
- Refresh tokens are stored only as a hash (sha256 - fine for high-entropy random tokens, NOT
  used for password storage).
- Refresh token rotation: every successful refresh issues a new refresh token and revokes the
  old one. Presenting an already-revoked token revokes the entire token family (theft signal).
"""
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.models.action_token import ActionToken
from app.models.organization import Membership, Organization, Role
from app.models.user import RefreshToken, User
from app.services.audit import record_audit_event

logger = get_logger(__name__)


class AuthError(Exception):
    """Base class for expected auth failures."""


class InvalidCredentialsError(AuthError):
    pass


class OrganizationSlugTakenError(AuthError):
    pass


class RefreshTokenReuseDetectedError(AuthError):
    pass


class RoleNotFoundError(AuthError):
    pass


class InvalidOrExpiredTokenError(AuthError):
    """Used for both invite and password-reset tokens - same failure mode, same generic
    handling (Sec. 10 - don't leak which specific reason a token is invalid)."""
    pass


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def register_organization_and_owner(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str,
    organization_slug: str,
) -> tuple[Organization, User]:
    existing = await db.execute(select(Organization).where(Organization.slug == organization_slug))
    if existing.scalar_one_or_none() is not None:
        raise OrganizationSlugTakenError(organization_slug)

    org = Organization(name=organization_name, slug=organization_slug)
    db.add(org)
    await db.flush()

    owner_role_result = await db.execute(
        select(Role).where(Role.org_id.is_(None), Role.name == "owner")
    )
    owner_role = owner_role_result.scalar_one_or_none()
    if owner_role is None:
        raise RuntimeError(
            "System role 'owner' not found - has the database been seeded? See docs/DEVELOPMENT.md."
        )

    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    db.add(user)
    await db.flush()

    db.add(Membership(org_id=org.id, user_id=user.id, role_id=owner_role.id, status="active"))
    await record_audit_event(
        db,
        org_id=org.id,
        actor_user_id=user.id,
        action="organization.created",
        target_type="organization",
        target_id=org.id,
        metadata={"slug": organization_slug},
    )
    await db.commit()
    return org, user


async def authenticate(
    db: AsyncSession, *, email: str, password: str, organization_slug: str
) -> tuple[User, Membership]:
    result = await db.execute(
        select(User, Membership, Organization)
        .join(Membership, Membership.user_id == User.id)
        .join(Organization, Organization.id == Membership.org_id)
        .where(User.email == email, Organization.slug == organization_slug)
    )
    row = result.first()
    if row is None:
        raise InvalidCredentialsError()

    user, membership, _org = row
    if not user.is_active or membership.status != "active":
        raise InvalidCredentialsError()
    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()

    return user, membership


async def issue_token_pair(
    db: AsyncSession,
    *,
    user: User,
    membership: Membership,
    role_name: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str]:
    settings = get_settings()
    access_token = create_access_token(user_id=user.id, org_id=membership.org_id, role=role_name)

    raw_refresh_token = secrets.token_urlsafe(48)
    family_id = uuid.uuid4()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(raw_refresh_token),
            family_id=family_id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    await record_audit_event(
        db, org_id=membership.org_id, actor_user_id=user.id, action="auth.login"
    )
    await db.commit()
    return access_token, raw_refresh_token


async def rotate_refresh_token(
    db: AsyncSession, *, raw_refresh_token: str, user_agent: str | None = None, ip_address: str | None = None
) -> tuple[str, str, User, Membership]:
    token_hash = _hash_token(raw_refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    token_row = result.scalar_one_or_none()

    if token_row is None:
        raise InvalidCredentialsError()

    if token_row.revoked_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token_row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await db.commit()
        raise RefreshTokenReuseDetectedError()

    if token_row.expires_at < datetime.now(UTC):
        raise InvalidCredentialsError()

    token_row.revoked_at = datetime.now(UTC)

    user_result = await db.execute(select(User).where(User.id == token_row.user_id))
    user = user_result.scalar_one()

    membership_result = await db.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.status == "active")
    )
    membership = membership_result.scalars().first()
    if membership is None:
        raise InvalidCredentialsError()

    role_result = await db.execute(select(Role).where(Role.id == membership.role_id))
    role = role_result.scalar_one()

    settings = get_settings()
    new_access_token = create_access_token(user_id=user.id, org_id=membership.org_id, role=role.name)
    new_raw_refresh = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(new_raw_refresh),
            family_id=token_row.family_id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    await db.commit()
    return new_access_token, new_raw_refresh, user, membership


async def revoke_all_sessions_for_user(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await record_audit_event(db, org_id=None, actor_user_id=user_id, action="auth.logout_all_sessions")
    await db.commit()


# --- Invites (D026) ---
_INVITE_TOKEN_EXPIRE_HOURS = 72
_PASSWORD_RESET_TOKEN_EXPIRE_HOURS = 1


async def create_invite(
    db: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    org_id: uuid.UUID,
    email: str,
    full_name: str,
    role_name: str,
) -> tuple[User, str]:
    """Invites a user (new or existing) to join org_id with role_name. Returns (user,
    raw_invite_token). SAFE to return raw_invite_token to the caller here - this function is
    only ever called from an authenticated, permission-checked route (members:manage), so
    there's no enumeration risk the way there is for password resets (see module docstring
    and ActionToken's docstring for the distinction).

    If the email belongs to an existing user, they're added as a new Membership without
    touching their existing password. If not, a new User is created with an unusable random
    password hash - they must go through accept_invite() (which sets a real password) before
    they can ever log in.
    """
    role_result = await db.execute(select(Role).where(Role.org_id.is_(None), Role.name == role_name))
    role = role_result.scalar_one_or_none()
    if role is None:
        raise RoleNotFoundError(role_name)

    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    if user is None:
        # Unusable placeholder password - a random 32-byte token hashed the same way a real
        # password would be, so there's no meaningfully "guessable" credential sitting here
        # before accept_invite() sets a real one.
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(32)), full_name=full_name)
        db.add(user)
        await db.flush()

    existing_membership = await db.execute(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user.id)
    )
    if existing_membership.scalar_one_or_none() is not None:
        raise AuthError(f"{email} is already a member of this organization")

    db.add(
        Membership(org_id=org_id, user_id=user.id, role_id=role.id, invited_by=actor_user_id, status="invited")
    )

    raw_token = secrets.token_urlsafe(32)
    db.add(
        ActionToken(
            user_id=user.id,
            org_id=org_id,
            purpose="invite",
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=_INVITE_TOKEN_EXPIRE_HOURS),
        )
    )
    await record_audit_event(
        db, org_id=org_id, actor_user_id=actor_user_id, action="membership.invited",
        target_type="user", target_id=user.id, metadata={"email": email, "role": role_name},
    )
    await db.commit()
    return user, raw_token


async def accept_invite(db: AsyncSession, *, raw_token: str, password: str) -> tuple[User, Membership]:
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(ActionToken).where(ActionToken.token_hash == token_hash, ActionToken.purpose == "invite")
    )
    token_row = result.scalar_one_or_none()
    if token_row is None or token_row.used_at is not None or token_row.expires_at < datetime.now(UTC):
        raise InvalidOrExpiredTokenError()

    membership_result = await db.execute(
        select(Membership).where(Membership.org_id == token_row.org_id, Membership.user_id == token_row.user_id)
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise InvalidOrExpiredTokenError()

    user_result = await db.execute(select(User).where(User.id == token_row.user_id))
    user = user_result.scalar_one()

    user.password_hash = hash_password(password)
    membership.status = "active"
    token_row.used_at = datetime.now(UTC)
    await record_audit_event(
        db, org_id=membership.org_id, actor_user_id=user.id, action="membership.accepted",
        target_type="user", target_id=user.id,
    )
    await db.commit()
    return user, membership


# --- Password reset (D027) ---
async def request_password_reset(db: AsyncSession, *, email: str) -> None:
    """Deliberately returns None always, regardless of whether the email exists - the caller
    (API route) must respond identically either way to prevent account enumeration.

    SECURITY-CRITICAL: the raw reset token is logged server-side (structured log, dev-visible
    only) and NEVER returned from this function to avoid tempting a caller into putting it in
    an HTTP response. This is a real, current limitation, not a cosmetic one: without a real
    email provider wired in (none exists yet - see docs/SECURITY.md), there is currently NO
    way for an actual end user to receive their reset token outside of a developer reading
    server logs. This feature is NOT safe or usable in a real deployment until an email
    provider is added (tracked in TODO.md) - the token mechanics here are real and correct,
    the delivery mechanism is the missing, documented piece.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    raw_token = secrets.token_urlsafe(32)
    db.add(
        ActionToken(
            user_id=user.id,
            org_id=None,
            purpose="password_reset",
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=_PASSWORD_RESET_TOKEN_EXPIRE_HOURS),
        )
    )
    await record_audit_event(db, org_id=None, actor_user_id=user.id, action="auth.password_reset_requested")
    await db.commit()

    logger.warning(
        "password_reset_token_issued_dev_mode_only",
        user_id=str(user.id),
        raw_token=raw_token,  # DEV-ONLY: see this function's docstring. Never do this once a
        # real email provider exists - the token should go ONLY to the email provider's send
        # call, never to any log a wider audience could read.
    )
    return None


async def confirm_password_reset(db: AsyncSession, *, raw_token: str, new_password: str) -> User:
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(ActionToken).where(ActionToken.token_hash == token_hash, ActionToken.purpose == "password_reset")
    )
    token_row = result.scalar_one_or_none()
    if token_row is None or token_row.used_at is not None or token_row.expires_at < datetime.now(UTC):
        raise InvalidOrExpiredTokenError()

    user_result = await db.execute(select(User).where(User.id == token_row.user_id))
    user = user_result.scalar_one()

    user.password_hash = hash_password(new_password)
    token_row.used_at = datetime.now(UTC)
    # Resetting a password is exactly the kind of event that should also revoke existing
    # sessions (if the account was compromised, a stolen refresh token shouldn't survive a
    # password reset) - reuse the existing revoke-all-sessions logic rather than duplicating it.
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await record_audit_event(db, org_id=None, actor_user_id=user.id, action="auth.password_reset_completed")
    await db.commit()
    return user
