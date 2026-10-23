"""Org membership/invite endpoints (D026, closes the "no way to add a user to an EXISTING
org" gap - registration only ever creates a brand new org + owner)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, require_permission
from app.db.base import get_db
from app.schemas.auth import InviteRequest, InviteResponse
from app.schemas.membership import MembershipListResponse, MembershipPublic, RoleChangeRequest
from app.services import auth as auth_service
from app.services import membership as membership_service

router = APIRouter()


@router.post(
    "/orgs/{org_id}/memberships/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED
)
async def invite_member(
    org_id: uuid.UUID,
    payload: InviteRequest,
    actor: Actor = Depends(require_permission("members:manage")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        user, raw_token = await auth_service.create_invite(
            db,
            actor_user_id=actor.user_id,
            org_id=org_id,
            email=payload.email,
            full_name=payload.full_name,
            role_name=payload.role,
        )
    except auth_service.RoleNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown role: {payload.role}",
        ) from None
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return InviteResponse(user_id=user.id, email=user.email, invite_token=raw_token)


@router.get("/orgs/{org_id}/memberships", response_model=MembershipListResponse)
async def list_memberships(
    org_id: uuid.UUID,
    actor: Actor = Depends(require_permission("members:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    memberships = await membership_service.list_memberships(db, actor=actor)
    return MembershipListResponse(memberships=memberships)  # type: ignore[arg-type]


@router.post("/orgs/{org_id}/memberships/{membership_id}/role", response_model=MembershipPublic)
async def change_member_role(
    org_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: RoleChangeRequest,
    actor: Actor = Depends(require_permission("members:manage")),
    db: AsyncSession = Depends(get_db),
):
    """Closes the "no endpoint changes an existing member's role" gap (TODO.md). Cannot set
    role to 'owner' - see services/membership.py's _CHANGEABLE_ROLES for why."""
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await membership_service.change_member_role(
            db, actor=actor, membership_id=membership_id, new_role_name=payload.role
        )
    except membership_service.MembershipNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found") from None
    except membership_service.RoleNotFoundError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown role") from None
    except membership_service.InvalidRoleChangeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
