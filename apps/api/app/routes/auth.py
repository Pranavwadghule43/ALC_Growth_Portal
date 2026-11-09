from datetime import datetime, timezone

import jwt
import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    decode_access_token,
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
from app.models import ALC, AuthSession, RefreshToken, User
from app.observability import set_actor
from app.schemas import ChangePasswordIn, LoginIn, UserOut
from app.services.audit import record_audit
from app.services.login_limiter import begin_login_attempt
from app.services.sessions import (
    claim_refresh_token,
    lock_user,
    new_session,
    revoke_session,
    revoke_user_sessions,
)

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
    """Serialize issuance against reset; never issue from a stale verified password."""
    verified_hash, verified_role = user.password_hash, user.role
    locked = await lock_user(db, user.id)
    if (locked is None or locked.password_hash != verified_hash or locked.role != verified_role
            or not locked.is_active):
        raise HTTPException(401, "Account changed; please sign in again")
    # Reload hierarchy too, since a concurrent account/hierarchy edit may have completed.
    user = await db.scalar(select(User).options(*AUTH_USER_LOADERS).where(User.id == locked.id)
                           .execution_options(populate_existing=True))
    if not account_available(user):
        raise HTTPException(401, "Account unavailable")
    family, raw_refresh = await new_session(db, user.id)
    user.last_login_at = datetime.now(timezone.utc)
    await record_audit(db, action, "user", user.id, user, request)
    await db.commit()
    set_auth_cookies(response, create_access_token(user.id, family.id),
                     raw_refresh, new_csrf_token())
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
    and must use ``/auth/admin-login``. Operational usernames are resolved first. If none
    matches, an ALC centre code is accepted only when exactly one active ALC account belongs
    to that centre. The client never sends a trusted role."""
    identifier = payload.identifier.lower()
    # Rate limits run before any Argon2 work; a throttled attempt gets 429 and no hashing.
    attempt = await begin_login_attempt(request, "portal", identifier)
    # Username is the unambiguous identity for every operational account. DCU/SBU email is
    # also supported. Centre code is deliberately a fallback: a legacy ALC whose username
    # equals its centre code remains independently accessible even when that centre later
    # gains another account.
    direct_matches = list(
        await db.scalars(
            select(User)
            .where(
                User.role.in_([Role.DCU, Role.SBU, Role.ALC]),
                or_(
                    func.lower(User.username) == identifier,
                    and_(
                        User.role.in_([Role.DCU, Role.SBU]),
                        func.lower(User.email) == identifier,
                    ),
                ),
            )
            .limit(2)
        )
    )
    matches = direct_matches
    if not matches:
        # Only active accounts participate in the convenience centre-code login. Zero or
        # multiple matches are both handled like an unknown identifier and use the dummy
        # password hash below, so account count/availability is not disclosed.
        matches = list(
            await db.scalars(
                select(User)
                .join(ALC, User.alc_id == ALC.id)
                .where(
                    User.role == Role.ALC,
                    User.is_active.is_(True),
                    func.lower(ALC.alc_code) == identifier,
                )
                .limit(2)
            )
        )
    user = matches[0] if len(matches) == 1 else None
    candidate_id = user.id if user is not None else None
    candidate_hash = login_password_hash(user)
    candidate_role = user.role if user is not None else None
    candidate_active = bool(user and user.is_active)
    # End the read-only transaction before queued/CPU-heavy Argon2 work. ``commit`` is
    # intentional: AsyncSession.rollback() expires every ORM object in the identity map, while
    # this application uses expire_on_commit=False. This releases the checkout without causing
    # later async lazy-loads / MissingGreenlet failures. A successful password is
    # never enough on its own: the account is reloaded and revalidated afterwards, and
    # _establish_session locks it once more before issuing credentials.
    await db.commit()
    password_ok = await verify_password_async(payload.password, candidate_hash)
    if candidate_id is None or not candidate_active or not password_ok:
        await record_audit(db, "login_failed", "user", request=request)
        log.info("login_failed", scope="portal")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username/ALC code or password")
    user = await db.scalar(
        select(User).options(*AUTH_USER_LOADERS).where(User.id == candidate_id)
        .execution_options(populate_existing=True)
    )
    if (user is None or not user.is_active or user.password_hash != candidate_hash
            or user.role != candidate_role):
        await record_audit(db, "login_failed", "user", request=request)
        await db.commit()
        raise HTTPException(status_code=401, detail="Account changed; please sign in again")
    if not account_available(user):
        await attempt.credentials_valid()
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
        .where(
            User.role == Role.ADMIN,
            or_(
                func.lower(User.username) == identifier,
                func.lower(User.email) == identifier,
            ),
        )
    )
    user = await db.scalar(query)
    candidate_id = user.id if user is not None else None
    candidate_hash = login_password_hash(user)
    candidate_role = user.role if user is not None else None
    candidate_active = bool(user and user.is_active)
    await db.commit()  # release the pooled DB connection without expiring loaded ORM state
    password_ok = await verify_password_async(payload.password, candidate_hash)
    if candidate_id is None or not candidate_active or not password_ok:
        await record_audit(db, "admin_login_failed", "user", request=request)
        log.info("login_failed", scope="admin")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user = await db.scalar(
        select(User).options(*USER_RESPONSE_LOADERS).where(User.id == candidate_id)
        .execution_options(populate_existing=True)
    )
    if (user is None or not user.is_active or user.password_hash != candidate_hash
            or user.role != candidate_role):
        await record_audit(db, "admin_login_failed", "user", request=request)
        await db.commit()
        raise HTTPException(status_code=401, detail="Account changed; please sign in again")
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
    claimed = await claim_refresh_token(db, token_digest(refresh_token), now)
    if claimed is None:
        # Replay detection may have revoked the family, including a racing successor.
        await db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    locked_user, family = claimed
    user = await db.scalar(
        select(User)
        .options(*AUTH_USER_LOADERS)
        .where(User.id == locked_user.id, User.is_active.is_(True))
        .execution_options(populate_existing=True)
    )
    if not user or not account_available(user):
        await revoke_session(db, family.id, now)
        await db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    raw, digest = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            session_id=family.id,
            token_hash=digest,
            expires_at=family.expires_at,
        )
    )
    await db.commit()
    set_auth_cookies(response, create_access_token(user.id, family.id), raw, new_csrf_token())
    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(
    response: Response,
    access_token: str | None = Cookie(default=None),
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    target = None
    if access_token:
        try:
            identity = decode_access_token(access_token)
            target = (identity.user_id, identity.session_id)
        except (jwt.InvalidTokenError, ValueError):
            pass  # idempotent logout; expired access can still use the refresh family
    if target is None and refresh_token:
        stored = (await db.execute(select(RefreshToken.user_id, RefreshToken.session_id).where(
            RefreshToken.token_hash == token_digest(refresh_token)))).one_or_none()
        if stored and stored.session_id:
            target = (stored.user_id, stored.session_id)
    if target and await lock_user(db, target[0]):
        family_id = await db.scalar(select(AuthSession.id).where(
            AuthSession.id == target[1], AuthSession.user_id == target[0]).with_for_update())
        if family_id:
            await revoke_session(db, family_id, datetime.now(timezone.utc))
        await db.commit()
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("csrf_token", path="/")
    return {"message": "Logged out"}


@router.post("/change-password", dependencies=[Depends(require_csrf)])
async def change_password(
    payload: ChangePasswordIn,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await verify_password_async(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if await verify_password_async(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="New password must be different from the current password"
        )
    verified_hash = user.password_hash
    new_hash = await hash_password_async(payload.new_password)
    user = await lock_user(db, user.id)
    family = await db.scalar(select(AuthSession).where(
        AuthSession.id == request.state.auth_session_id,
        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > datetime.now(timezone.utc),
    ).with_for_update().execution_options(populate_existing=True)) if user else None
    if user is None or user.password_hash != verified_hash or family is None:
        raise HTTPException(401, "Session changed; please sign in again")
    user.password_hash = new_hash
    user.must_change_password = False
    await revoke_user_sessions(db, user.id)
    replacement, raw = await new_session(db, user.id)
    await record_audit(db, "password_changed", "user", user.id, user)
    await db.commit()
    set_auth_cookies(response, create_access_token(user.id, replacement.id), raw, new_csrf_token())
    return {"message": "Password changed"}
