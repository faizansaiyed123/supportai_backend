from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, get_current_actor
from app.core.config import get_settings
from app.core.rate_limit import rate_limit_login, rate_limit_password_reset, rate_limit_register
from app.db.base import get_db
from app.models.organization import Role
from app.schemas.auth import (
    AcceptInviteRequest,
    AccessTokenResponse,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    RegisterRequest,
    UserPublic,
)
from app.services import auth as auth_service

router = APIRouter()

_REFRESH_COOKIE_NAME = "refresh_token"
_REFRESH_COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, raw_refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=raw_refresh_token,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path=_REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
    )


@router.post("/auth/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_register),
):
    try:
        _org, user = await auth_service.register_organization_and_owner(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            organization_name=payload.organization_name,
            organization_slug=payload.organization_slug,
        )
    except auth_service.OrganizationSlugTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Organization slug already taken") from None
    return user


@router.post("/auth/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_login),
):
    try:
        user, membership = await auth_service.authenticate(
            db, email=payload.email, password=payload.password, organization_slug=payload.organization_slug
        )
    except auth_service.InvalidCredentialsError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from None

    role_result = await db.execute(select(Role).where(Role.id == membership.role_id))
    role = role_result.scalar_one()

    settings = get_settings()
    access_token, raw_refresh_token = await auth_service.issue_token_pair(
        db,
        user=user,
        membership=membership,
        role_name=role.name,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, raw_refresh_token)
    return AccessTokenResponse(access_token=access_token, expires_in_minutes=settings.access_token_expire_minutes)


@router.post("/auth/refresh", response_model=AccessTokenResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw_refresh_token = request.cookies.get(_REFRESH_COOKIE_NAME)
    if not raw_refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")

    try:
        new_access, new_refresh, _user, _membership = await auth_service.rotate_refresh_token(
            db,
            raw_refresh_token=raw_refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    except auth_service.RefreshTokenReuseDetectedError:
        response.delete_cookie(_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalidated - possible token reuse detected. Please log in again.",
        ) from None
    except auth_service.InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from None

    _set_refresh_cookie(response, new_refresh)
    settings = get_settings()
    return AccessTokenResponse(access_token=new_access, expires_in_minutes=settings.access_token_expire_minutes)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_db)):
    await auth_service.revoke_all_sessions_for_user(db, user_id=actor.user_id)
    response.delete_cookie(_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)


@router.post("/auth/invites/accept", response_model=AccessTokenResponse)
async def accept_invite(
    payload: AcceptInviteRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    try:
        user, membership = await auth_service.accept_invite(db, raw_token=payload.token, password=payload.password)
    except auth_service.InvalidOrExpiredTokenError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invite token") from None

    role_result = await db.execute(select(Role).where(Role.id == membership.role_id))
    role = role_result.scalar_one()
    settings = get_settings()
    access_token, raw_refresh_token = await auth_service.issue_token_pair(
        db,
        user=user,
        membership=membership,
        role_name=role.name,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, raw_refresh_token)
    return AccessTokenResponse(access_token=access_token, expires_in_minutes=settings.access_token_expire_minutes)


@router.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequestRequest,
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(rate_limit_password_reset),
):
    """Always returns the same 202 response whether or not the email exists - see
    services/auth.py:request_password_reset's docstring for why (account-enumeration
    prevention) and for the current, documented limitation that no real email is sent yet."""
    await auth_service.request_password_reset(db, email=payload.email)
    return {"detail": "If that email is registered, password reset instructions have been issued."}


@router.post("/auth/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(payload: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_db)):
    try:
        await auth_service.confirm_password_reset(db, raw_token=payload.token, new_password=payload.new_password)
    except auth_service.InvalidOrExpiredTokenError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token") from None
