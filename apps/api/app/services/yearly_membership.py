"""Serialize rare membership changes with yearly participation snapshots.

Lock regional roots in a deterministic order *before* local ALC/SBU/run locks. PostgreSQL
row locks protect activation versus reassignment; SQLite tests exercise functional rules.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RCU, YearlyActivityCompletion, YearlyActivityDCURun


async def lock_membership(db: AsyncSession) -> None:
    await db.execute(select(RCU.id).order_by(RCU.id).with_for_update())


async def require_no_pending_participation(db: AsyncSession, alc_ids) -> None:
    pending = await db.scalar(
        select(YearlyActivityCompletion.id)
        .join(YearlyActivityDCURun,
              YearlyActivityCompletion.yearly_activity_dcu_run_id == YearlyActivityDCURun.id)
        .where(YearlyActivityCompletion.alc_id.in_(alc_ids),
               YearlyActivityDCURun.status != "FINALIZED")
        .limit(1)
    )
    if pending:
        raise HTTPException(
            409, "Finalize pending yearly activity participation before hierarchy reassignment"
        )
