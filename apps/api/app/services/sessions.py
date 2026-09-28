"""Session (refresh token) revocation helpers."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, keep_token_hash: str | None = None
) -> int:
    """Revoke every active refresh token for ``user_id``.

    Used after a password reset or change so that browsers signed in before the change
    cannot keep refreshing their session. ``keep_token_hash`` spares the caller's own
    session (used when a user changes their own password). Returns the number revoked.
    """
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    if keep_token_hash:
        stmt = stmt.where(RefreshToken.token_hash != keep_token_hash)
    result = await db.execute(stmt)
    return result.rowcount or 0

async def claim_refresh_token(
    db: AsyncSession, token_hash: str, now: datetime
) -> uuid.UUID | None:
    """Atomically consume a refresh token for rotation; return its user id, or ``None``.

    A single conditional ``UPDATE ... RETURNING`` both checks and revokes the token, so when
    two refresh requests present the same token at the same moment only one of them can
    succeed (PostgreSQL row locking makes the second request re-check ``revoked_at`` after
    the first commits and match nothing). The caller must commit to keep the revocation, or
    roll back to leave the token unused.
    """
    result = await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .values(revoked_at=now)
        .returning(RefreshToken.user_id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none()