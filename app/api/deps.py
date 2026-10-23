"""
Shared FastAPI dependencies. get_current_actor turns a bearer token into a trusted
(user_id, org_id, role) triple. Service functions take this Actor, never a raw org_id parsed
from the URL/body alone (D005 tenant-isolation invariant).
"""
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import InvalidTokenError, decode_access_token
from app.db.base import get_db

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str


async def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Actor:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from None

    return Actor(
        user_id=uuid.UUID(payload["sub"]),
        org_id=uuid.UUID(payload["org_id"]),
        role=payload["role"],
    )


def require_role(*allowed_roles: str):
    """Coarse role-gating - use where only a handful of roles ever plausibly apply and the
    exact permission set doesn't matter. Prefer require_permission() for anything that should
    respect Role.permissions (D017) - see that dependency's docstring."""

    async def _check(actor: Actor = Depends(get_current_actor)) -> Actor:
        if actor.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return actor

    return _check


def require_permission(permission: str):
    """Fine-grained permission-string gating (D017/D026), backed by
    app/services/authz.py:actor_has_permission - checks the acting role's Role.permissions
    (or the "*" wildcard) rather than a hardcoded role-name allowlist. Use this for any
    endpoint whose access should be configurable via roles/permissions rather than a fixed
    set of role names baked into the route. This is now the DEFAULT choice for new
    permission-gated endpoints; require_role() remains for the handful of places (mostly
    auth itself) where role identity, not a permission string, is the actual thing being
    checked."""

    async def _check(
        actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_db)
    ) -> Actor:
        from app.services.authz import actor_has_permission  # local import: avoids a
        # services -> api -> services import cycle (services/authz.py doesn't import
        # api/deps.py, but several other services do, and deps.py is imported very early)

        allowed = await actor_has_permission(
            db, actor_role=actor.role, org_id=actor.org_id, permission=permission
        )
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return actor

    return _check
