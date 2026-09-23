"""Ensure the RCU → DCU master data exists and link the known Nashik SBUs.

Idempotent and additive. Creates RCU Pune and its four DCUs if missing, then links SBU 4,
SBU 6 and SBU 7 to DCU Nashik when they exist and are not yet assigned. Never creates, edits
or deletes SBUs/ALCs, never changes an ALC's SBU, and never overrides an SBU an administrator
already placed under another DCU (such conflicts are reported). The Alembic migration
``20260923_0004`` performs the same seed; re-run this after the SBU master is created on a
fresh database.

Usage:
    python -m scripts.seed_hierarchy
"""
import asyncio

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import ALC, DCU, SBU
from app.services.hierarchy import ensure_hierarchy


async def run() -> int:
    async with SessionLocal() as db:
        summary = await ensure_hierarchy(db)
        await db.commit()
        print(
            f"RCUs created: {summary['rcus_created']} | DCUs created: {summary['dcus_created']}"
        )
        print(f"SBUs linked now: {summary['sbus_linked'] or 'none'}")
        if summary["conflicts"]:
            print(f"NOT changed (already under another DCU): {summary['conflicts']}")
        rows = (
            await db.execute(
                select(DCU.code, SBU.code, func.count(ALC.id))
                .select_from(DCU)
                .outerjoin(SBU, SBU.dcu_id == DCU.id)
                .outerjoin(ALC, ALC.sbu_id == SBU.id)
                .group_by(DCU.code, SBU.code)
                .order_by(DCU.code, SBU.code)
            )
        ).all()
        print("Hierarchy (DCU / SBU / ALCs):")
        for dcu_code, sbu_code, alcs in rows:
            print(f"  {dcu_code} / {sbu_code or '(no SBU)'} / {alcs}")
        unassigned = (
            await db.scalars(select(SBU.code).where(SBU.dcu_id.is_(None)).order_by(SBU.code))
        ).all()
        if unassigned:
            print(f"SBUs without a DCU (no DCU can access them): {list(unassigned)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
