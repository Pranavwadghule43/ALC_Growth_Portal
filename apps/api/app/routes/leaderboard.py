"""Regional leaderboards readable by every operational role (ADMIN, DCU, SBU, ALC):
the Region Top 10 and the complete regional score directory.

These endpoints are intentionally *not* scoped to the caller's hierarchy: every role sees the
same regional result. They grant no other access — ``/admin`` and ``/portal`` keep their own
guards and scopes — and return only the aggregate fields listed in
``app.services.leaderboard``.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, release_read_only_transaction
from app.dependencies import require_csrf, require_operational_user
from app.models import User
from app.response_cache import get_or_create
from app.services import leaderboard

router = APIRouter(
    prefix="/leaderboard", tags=["Leaderboard"], dependencies=[Depends(require_csrf)]
)


def _response(payload: dict) -> JSONResponse:
    """Return the already-small public schema without FastAPI's generic recursive encoder.

    The service keeps UUID/datetime values for Python callers and tests; the HTTP edge only
    converts the two non-JSON primitive fields.
    """
    content = dict(payload)
    generated = payload["generated_at"]
    content["generated_at"] = (
        generated.isoformat() if hasattr(generated, "isoformat") else generated
    )
    content["items"] = [dict(item, alc_id=str(item["alc_id"])) for item in payload["items"]]
    return JSONResponse(content=content)


async def _regional_payload(db: AsyncSession, year: int | None) -> dict:
    # Auth has already been checked and is intentionally not cached. Release its transaction
    # before Redis waits. Both public endpoints then reuse this one full regional ranking.
    await release_read_only_transaction(db)

    async def build():
        rows = await leaderboard.eligible_rows(db, year)
        await release_read_only_transaction(db)
        ranked = leaderboard.rank_rows(leaderboard.score_rows(rows), limit=None)
        return leaderboard.leaderboard_payload(ranked, year)

    return (
        await get_or_create(
            "leaderboard",
            (year or "current",),
            settings.response_cache_leaderboard_ttl_seconds,
            build,
        )
    ).value


@router.get("/region-top10")
async def region_top10(
    _: User = Depends(require_operational_user), db: AsyncSession = Depends(get_db),
    year: int | None = Query(None, ge=2000, le=2100),
):
    """Top 10 ACTIVE ALCs under an active SBU / DCU / RCU, by lifetime verified performance."""
    payload = await _regional_payload(db, year)
    return _response({**payload, "items": payload["items"][:leaderboard.TOP_N]})


@router.get("/region-scores")
async def region_scores(
    _: User = Depends(require_operational_user), db: AsyncSession = Depends(get_db),
    year: int | None = Query(None, ge=2000, le=2100),
):
    """Every eligible ALC with its regional rank and score, from the same ranking as the Top
    10. Not filtered or re-scored by the caller's scope; the UI filters rows only."""
    return _response(await _regional_payload(db, year))
