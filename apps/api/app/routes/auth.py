from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    hash_password_async,
    login_password_hash,
    new_csrf_token,
    new_refresh_token,
    token_digest,
    verify_password_async,
)
from app.config import settings
from app.database import get_db
from app.dependencies import (
    AUTH_USER_LOADERS,
    USER_RESPONSE_LOADERS,
    account_available,
    get_current_user,
    require_csrf,
)
from app.enums import Role
from app.models import ALC, RefreshToken, User
from app.observability import set_actor
from app.schemas import ChangePasswordIn, LoginIn, UserOut
from app.services.audit import record_audit
from app.services.login_limiter import begin_login_attempt
from app.services.sessions import claim_refresh_token, revoke_user_sessions

log = structlog.get_logger("app.auth")
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
    # Operational log: who signed in (internal id and role). Never the identifier typed,
    # the password, or any token.
    set_actor(request, user.id, user.role)
    log.info(
        "login_succeeded", kind=action, user_id=str(user.id), role=user.role.value,
        change_required=bool(user.must_change_password),  # a flag, not a credential
    )
    return user


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    """Operational-portal login for DCU, SBU and ALC only. ADMIN accounts are rejected here
    and must use ``/auth/admin-login``. The role is derived server-side from the identifier:
    a DCU or SBU authenticates with a username/email, an ALC with its unique ALC code. The
    client never sends a trusted role."""
    identifier = payload.identifier.lower()
    # Rate limits run before any Argon2 work; a throttled attempt gets 429 and no hashing.
    attempt = await begin_login_attempt(request, "portal", identifier)
    query = (
        select(User)
        .outerjoin(ALC, User.alc_id == ALC.id)
        .options(*AUTH_USER_LOADERS)
        .where(
            or_(
                and_(
                    User.role.in_([Role.DCU, Role.SBU]),
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
    # Exactly one Argon2 verification on every path (unknown identifier -> dummy hash;
    # existing account, active or not -> its own hash), so the response time does not reveal
    # whether the identifier exists. Only then are the account checks applied.
    password_ok = await verify_password_async(payload.password, login_password_hash(user))
    if not user or not user.is_active or not password_ok:
        await record_audit(db, "login_failed", "user", request=request)
        log.info("login_failed", scope="portal")  # no identifier: it may be a real username
        await db.commit()
        raise HTTPException(
            status_code=401, detail="Invalid username/ALC code or password"
        )
    # Inactive ALC, or a DCU login not linked to exactly one active DCU: no session.
    if not account_available(user):
        await attempt.credentials_valid()  # right password: not a credential failure
        log.info("login_refused", scope="portal", reason="account_unavailable",
                 user_id=str(user.id), role=user.role.value)
        raise HTTPException(status_code=401, detail="Account unavailable")
    await attempt.succeeded()
    return await _establish_session(user, request, response, db)


@router.post("/admin-login", response_model=UserOut)
async def admin_login(
    payload: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    """Administrator login for ADMIN only. SBU and ALC accounts are rejected here and must
    use ``/auth/login``. Throttled under a separate rate-limit scope so stricter admin
    limits can be configured independently."""
    identifier = payload.identifier.lower()
    attempt = await begin_login_attempt(request, "admin", identifier)
    query = (
        select(User)
        .options(*USER_RESPONSE_LOADERS)
        .where(
            User.role == Role.ADMIN,
            or_(
                func.lower(User.username) == identifier,
                func.lower(User.email) == identifier,
            ),
        )
    )
    user = await db.scalar(query)
    # Same equal-work rule as the operational login (see ``login``).
    password_ok = await verify_password_async(payload.password, login_password_hash(user))
    if not user or not user.is_active or not password_ok:
        await record_audit(db, "admin_login_failed", "user", request=request)
        log.info("login_failed", scope="admin")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    await attempt.succeeded()
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
    # Check-and-revoke in one statement: of several requests presenting the same token, only
    # one can rotate it; the others get the same 401 as an expired or revoked token.
    user_id = await claim_refresh_token(db, token_digest(refresh_token), now)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Session expired")
    user = await db.scalar(
        select(User)
        .options(*AUTH_USER_LOADERS)
        .where(User.id == user_id, User.is_active.is_(True))
    )
    if not user or not account_available(user):
        await db.rollback()  # as before: an unavailable account's token is left untouched
        raise HTTPException(status_code=401, detail="Session expired")
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
    refresh_token: str | None = Cookie(default=None),
):
    if not await verify_password_async(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if await verify_password_async(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="New password must be different from the current password"
        )
    user.password_hash = await hash_password_async(payload.new_password)
    user.must_change_password = False
    # Sign out every other browser; keep the session making this request.
    await revoke_user_sessions(
        db, user.id, keep_token_hash=token_digest(refresh_token) if refresh_token else None
    )
    await record_audit(db, "password_changed", "user", user.id, user)
    await db.commit()
    return {"message": "Password changed"}