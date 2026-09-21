from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import (
    create_access_token,
    hash_password,
    new_csrf_token,
    new_refresh_token,
    token_digest,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_csrf
from app.enums import Role
from app.models import ALC, RefreshToken, User
from app.schemas import ChangePasswordIn, LoginIn, UserOut
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["Authentication"])


def set_auth_cookies(response: Response, access: str, refresh: str, csrf: str) -> None:
    common = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax", "path": "/"}
    response.set_cookie(
        "access_token", access, max_age=settings.access_token_minutes * 60, **common
    )
    response.set_cookie(
        "refresh_token", refresh, max_age=settings.refresh_token_days * 86400, **common
    )
    response.set_cookie(
        "csrf_token",
        csrf,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.refresh_token_days * 86400,
    )


async def check_rate_limit(request: Request, scope: str = "login", limit: int = 10) -> None:
    # ``scope`` namespaces the counter so admin logins are throttled independently of
    # portal logins; a stricter ``limit`` can be configured for the admin scope later.
    key = f"{scope}:{request.client.host if request.client else 'unknown'}"
    try:
        redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, 60)
        await redis.aclose()
        if attempts > limit:
            raise HTTPException(
                status_code=429, detail="Too many login attempts. Try again shortly."
            )
    except HTTPException:
        raise
    except Exception:
        if settings.app_env == "production":
            raise HTTPException(status_code=503, detail="Login temporarily unavailable") from None


async def _establish_session(
    user: User, request: Request, response: Response, db: AsyncSession, action: str = "login"
) -> User:
    """Issue a fresh session for an already-authenticated user: rotate a refresh token,
    stamp last-login, audit, and set the auth cookies. Shared by portal and admin login."""
    raw_refresh, refresh_hash = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
        )
    )
    user.last_login_at = datetime.now(timezone.utc)
    await record_audit(db, action, "user", user.id, user, request)
    await db.commit()
    set_auth_cookies(response, create_access_token(user.id), raw_refresh, new_csrf_token())
    return user


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    """Operational-portal login for SBU and ALC only. ADMIN accounts are rejected here and
    must use ``/auth/admin-login``. The role is derived server-side from the identifier: an
    SBU authenticates with a username/email, an ALC with its unique ALC code. The client
    never sends a trusted role."""
    await check_rate_limit(request, scope="login")
    identifier = payload.identifier.lower()
    query = (
        select(User)
        .outerjoin(ALC, User.alc_id == ALC.id)
        .options(selectinload(User.alc), selectinload(User.sbu))
        .where(
            or_(
                and_(
                    User.role == Role.SBU,
                    or_(
                        func.lower(User.username) == identifier,
                        func.lower(User.email) == identifier,
                    ),
                ),
                and_(User.role == Role.ALC, func.lower(ALC.alc_code) == identifier),
            )
        )
    )
    user = await db.scalar(query)
    if (
        not user
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        await record_audit(db, "login_failed", "user", request=request)
        await db.commit()
        raise HTTPException(
            status_code=401, detail="Invalid username/ALC code or password"
        )
    if user.alc and user.alc.status.value != "ACTIVE":
        raise HTTPException(status_code=401, detail="Account unavailable")
    return await _establish_session(user, request, response, db)


@router.post("/admin-login", response_model=UserOut)
async def admin_login(
    payload: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    """Administrator login for ADMIN only. SBU and ALC accounts are rejected here and must
    use ``/auth/login``. Throttled under a separate rate-limit scope so stricter admin
    limits can be configured independently."""
    await check_rate_limit(request, scope="admin_login")
    identifier = payload.identifier.lower()
    query = (
        select(User)
        .options(selectinload(User.alc), selectinload(User.sbu))
        .where(
            User.role == Role.ADMIN,
            or_(
                func.lower(User.username) == identifier,
                func.lower(User.email) == identifier,
            ),
        )
    )
    user = await db.scalar(query)
    if (
        not user
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        await record_audit(db, "admin_login_failed", "user", request=request)
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return await _establish_session(user, request, response, db, action="admin_login")


@router.post("/refresh", response_model=UserOut)
async def rotate_refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required")
    now = datetime.now(timezone.utc)
    stored = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_digest(refresh_token),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    if not stored:
        raise HTTPException(status_code=401, detail="Session expired")
    user = await db.scalar(
        select(User)
        .options(selectinload(User.alc), selectinload(User.sbu))
        .where(User.id == stored.user_id, User.is_active.is_(True))
    )
    if not user or (user.alc and user.alc.status.value != "ACTIVE"):
        raise HTTPException(status_code=401, detail="Session expired")
    stored.revoked_at = now
    raw, digest = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=digest,
            expires_at=now + timedelta(days=settings.refresh_token_days),
        )
    )
    await db.commit()
    set_auth_cookies(response, create_access_token(user.id), raw, new_csrf_token())
    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if refresh_token:
        stored = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_digest(refresh_token))
        )
        if stored:
            stored.revoked_at = datetime.now(timezone.utc)
            await db.commit()
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("csrf_token", path="/")
    return {"message": "Logged out"}


@router.post("/change-password", dependencies=[Depends(require_csrf)])
async def change_password(
    payload: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await record_audit(db, "password_changed", "user", user.id, user)
    await db.commit()
    return {"message": "Password changed"}
