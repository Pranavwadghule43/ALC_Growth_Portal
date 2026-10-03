"""Regional leaderboards readable by every operational role (ADMIN, DCU, SBU, ALC).

These endpoints are intentionally *not* scoped to the caller's hierarchy: every role sees the
same regional result. They grant no other access — ``/admin`` and ``/portal`` keep their own
guards and scopes — and return only the aggregate fields listed in
``app.services.leaderboard``.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_csrf, require_operational_user
from app.models import User
from app.services import leaderboard

router = APIRouter(
    prefix="/leaderboard", tags=["Leaderboard"], dependencies=[Depends(require_csrf)]
)


@router.get("/region-top10")
async def region_top10(
    _: User = Depends(require_operational_user), db: AsyncSession = Depends(get_db)
):
    """Top 10 ACTIVE ALCs under an active SBU / DCU / RCU, by lifetime verified performance."""
    return await leaderboard.region_top10(db)
