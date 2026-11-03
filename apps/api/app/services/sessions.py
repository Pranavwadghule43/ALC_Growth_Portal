"""Persistent families. Auth mutations lock User, then family, then refresh token.

Helpers participate in the caller's transaction. Raw refresh tokens go only to the cookie
issuer; the database stores SHA256 digests. Failed replay detection must be committed.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import new_refresh_token
from app.config import settings
from app.models import AuthSession, RefreshToken, User


async def lock_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    # Reload identity state after any concurrent reset; don't flush stale credential edits.
    with db.no_autoflush:
        return await db.scalar(select(User).where(User.id == user_id).with_for_update()
                               .execution_options(populate_existing=True))


async def new_session(db: AsyncSession, user_id: uuid.UUID) -> tuple[AuthSession, str]:
    """Caller holds User lock. The refresh family has an absolute expiry."""
    now = datetime.now(timezone.utc)
    family = AuthSession(id=uuid.uuid4(), user_id=user_id, created_at=now,
                         expires_at=now + timedelta(days=settings.refresh_token_days))
    db.add(family)
    await db.flush()
    raw, digest = new_refresh_token()
    db.add(RefreshToken(user_id=user_id, session_id=family.id, token_hash=digest,
                        expires_at=family.expires_at))
    return family, raw


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, now: datetime) -> int:
    """Caller holds User lock; stable family id closes logout/rotation races."""
    result = await db.execute(update(AuthSession).where(
        AuthSession.id == session_id, AuthSession.revoked_at.is_(None)
    ).values(revoked_at=now).execution_options(synchronize_session=False))
    await db.execute(update(RefreshToken).where(
        RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None)
    ).values(revoked_at=now).execution_options(synchronize_session=False))
    return result.rowcount or 0


async def revoke_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Caller locks User before editing credentials; revoke every old device family."""
    now = datetime.now(timezone.utc)
    await db.execute(update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    ).values(revoked_at=now).execution_options(synchronize_session=False))
    result = await db.execute(update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    ).values(revoked_at=now).execution_options(synchronize_session=False))
    return result.rowcount or 0


async def claim_refresh_token(
    db: AsyncSession, token_hash: str, now: datetime
) -> tuple[User, AuthSession] | None:
    """Consume once under per-user serialization; replay revokes the whole family.

    Failed replay can write revocation, so the route must COMMIT before returning401.
    The preliminary indexed hash lookup discovers lock ownership; grants no permission.
    """
    reference = (await db.execute(select(RefreshToken.user_id, RefreshToken.session_id).where(
        RefreshToken.token_hash == token_hash))).one_or_none()
    if reference is None or reference.session_id is None:
        return None
    user = await lock_user(db, reference.user_id)
    if user is None:
        return None
    family = await db.scalar(select(AuthSession).where(
        AuthSession.id == reference.session_id, AuthSession.user_id == user.id
    ).with_for_update().execution_options(populate_existing=True))
    if family is None or family.revoked_at is not None:
        return None
    token = await db.scalar(select(RefreshToken).where(
        RefreshToken.token_hash == token_hash, RefreshToken.user_id == user.id,
        RefreshToken.session_id == family.id,
    ).with_for_update().execution_options(populate_existing=True))
    if token is None:
        return None
    if token.revoked_at is not None:
        await revoke_session(db, family.id, now)
        return None
    # SQLite returns naive timestamps; persisted values always represent UTC.
    if (family.expires_at.replace(tzinfo=timezone.utc) <= now
            or token.expires_at.replace(tzinfo=timezone.utc) <= now):
        await revoke_session(db, family.id, now)
        return None
    claimed = await db.execute(update(RefreshToken).where(
        RefreshToken.id == token.id, RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > now,
    ).values(revoked_at=now).returning(RefreshToken.id)
      .execution_options(synchronize_session=False))
    if claimed.scalar_one_or_none() is None:
        await revoke_session(db, family.id, now)
        return None
    return user, family


async def cleanup_expired_sessions(db: AsyncSession, *, batch_size: int = 1000) -> dict:
    """Indexed, bounded expiry cleanup. Keep unexpired tombstones for replay/logout.

    Scheduled maintenance only; legacy records become removable at original expiry.
    """
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between1 and10000")
    now = datetime.now(timezone.utc)
    ids = (await db.scalars(select(AuthSession.id).where(AuthSession.expires_at <= now)
                           .order_by(AuthSession.expires_at).limit(batch_size)
                           .with_for_update(skip_locked=True))).all()
    tokens_deleted = 0
    if ids:
        result = await db.execute(delete(RefreshToken).where(RefreshToken.session_id.in_(ids)))
        tokens_deleted += result.rowcount or 0
        await db.execute(delete(AuthSession).where(AuthSession.id.in_(ids)))
    legacy = (await db.scalars(select(RefreshToken.id).where(
        RefreshToken.session_id.is_(None), RefreshToken.expires_at <= now
    ).order_by(RefreshToken.expires_at).limit(batch_size))).all()
    if legacy:
        result = await db.execute(delete(RefreshToken).where(RefreshToken.id.in_(legacy)))
        tokens_deleted += result.rowcount or 0
    return {"sessions_deleted": len(ids), "refresh_rows_deleted": tokens_deleted,
            "legacy_rows_deleted": len(legacy)}
