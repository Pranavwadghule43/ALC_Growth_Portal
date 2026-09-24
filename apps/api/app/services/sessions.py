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