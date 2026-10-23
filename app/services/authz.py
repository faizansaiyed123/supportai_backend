"""
Permission-checking service (Phase 2 hardening item, closes the TODO left in api/deps.py's
coarse require_role). Plain functions per D004.

Role.permissions is a flat list of permission strings (e.g. "knowledge:manage") or the
wildcard "*" (system 'owner' role only). This module is the single place that interprets
that list - callers never inspect Role.permissions directly.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Membership, Role


class PermissionDeniedError(Exception):
    pass


async def get_role_permissions(db: AsyncSession, *, role_name: str, org_id) -> list[str]:
    """Looks up a role's permission list. System roles (org_id=NULL) apply to every org;
    an org-custom role with the same name (not built yet, D005) would take precedence -
    not implemented, so this always resolves the system role for now."""
    result = await db.execute(select(Role).where(Role.org_id.is_(None), Role.name == role_name))
    role = result.scalar_one_or_none()
    if role is None:
        return []
    return role.permissions


async def actor_has_permission(db: AsyncSession, *, actor_role: str, org_id, permission: str) -> bool:
    permissions = await get_role_permissions(db, role_name=actor_role, org_id=org_id)
    return "*" in permissions or permission in permissions


async def require_permission(db: AsyncSession, *, actor_role: str, org_id, permission: str) -> None:
    if not await actor_has_permission(db, actor_role=actor_role, org_id=org_id, permission=permission):
        raise PermissionDeniedError(permission)


async def verify_active_membership(db: AsyncSession, *, user_id, org_id) -> Membership:
    """Re-verifies (not just trusts the JWT) that the acting user is still an active member of
    the org they claim - used on sensitive operations where a just-revoked membership should
    take effect immediately rather than waiting for the access token to expire (Sec. 11)."""
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id, Membership.org_id == org_id, Membership.status == "active"
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise PermissionDeniedError("not an active member of this organization")
    return membership
