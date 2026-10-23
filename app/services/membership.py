"""
Org membership management beyond invite creation (D026 added invites; this closes the
remaining "no endpoint changes an existing member's role" gap, TODO.md). Plain functions per
D004.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.models.organization import Membership, Role
from app.models.user import User
from app.services.audit import record_audit_event

_CHANGEABLE_ROLES = {"admin", "agent", "viewer"}  # deliberately excludes "owner" - same
# reasoning as InviteRequest's role field (schemas/auth.py): ownership transfer should be its
# own explicit, more-guarded action if it's ever built, not a side effect of a routine
# role-change call that any admin can make.


class MembershipNotFoundError(Exception):
    pass


class RoleNotFoundError(Exception):
    pass


class InvalidRoleChangeError(Exception):
    pass


async def list_memberships(db: AsyncSession, *, actor: Actor) -> list[dict]:
    """Returns plain dicts (not ORM objects) - MembershipPublic needs email/full_name/role
    which live on User/Role, not Membership itself. Found and fixed during frontend
    integration: MembershipPublic originally only exposed user_id, which would have meant the
    Team page could only show raw UUIDs instead of names/emails - not a usable UI."""
    result = await db.execute(
        select(Membership, User, Role)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.org_id == actor.org_id)
    )
    return [
        {
            "id": membership.id,
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": role.name,
            "status": membership.status,
            "created_at": membership.created_at,
        }
        for membership, user, role in result.all()
    ]


async def change_member_role(
    db: AsyncSession, *, actor: Actor, membership_id: uuid.UUID, new_role_name: str
) -> dict:
    if new_role_name not in _CHANGEABLE_ROLES:
        raise InvalidRoleChangeError(f"Cannot set role to {new_role_name!r} via this endpoint")

    result = await db.execute(
        select(Membership).where(Membership.id == membership_id, Membership.org_id == actor.org_id)
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise MembershipNotFoundError()

    role_result = await db.execute(select(Role).where(Role.org_id.is_(None), Role.name == new_role_name))
    role = role_result.scalar_one_or_none()
    if role is None:
        raise RoleNotFoundError(new_role_name)

    old_role_result = await db.execute(select(Role).where(Role.id == membership.role_id))
    old_role = old_role_result.scalar_one_or_none()
    old_role_name = old_role.name if old_role else "unknown"

    membership.role_id = role.id
    await record_audit_event(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action="membership.role_changed",
        target_type="membership",
        target_id=membership.id,
        metadata={"from": old_role_name, "to": new_role_name, "target_user_id": str(membership.user_id)},
    )
    await db.commit()
    await db.refresh(membership)

    user_result = await db.execute(select(User).where(User.id == membership.user_id))
    user = user_result.scalar_one()

    return {
        "id": membership.id,
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": new_role_name,
        "status": membership.status,
        "created_at": membership.created_at,
    }
