"""Master data for the RCU → DCU → SBU hierarchy and an idempotent seeding routine.

Only the hierarchy that is actually known is created: one RCU (Pune) and its four DCUs. The
only SBU links made are the real Nashik SBUs (SBU 4, SBU 6, SBU 7 → DCU Nashik). No other DCU
or SBU is inferred. Any other SBU — including the demo "SBU 1" — is left without a DCU, so no
DCU login can reach it (hierarchy scope fails closed).

Seeding never overwrites an SBU that an administrator has already assigned to a DCU, never
creates or edits SBUs or ALCs, and never touches ``ALC.sbu_id``. The Alembic migration
``20260923_0004`` performs the same seed with its own frozen copy of these constants.
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DCU, RCU, SBU

RCU_PUNE = ("RCU_PUNE", "RCU Pune")
DCUS = (
    ("DCU_AHILYA_NAGAR", "DCU Ahilya Nagar"),
    ("DCU_NASHIK", "DCU Nashik"),
    ("DCU_PUNE_NORTH", "DCU Pune North"),
    ("DCU_PUNE_SOUTH", "DCU Pune South"),
)
# SBU code → DCU code for SBUs whose placement is known. Matched case-insensitively.
SBU_DCU_LINKS = {
    "SBU 4": "DCU_NASHIK",
    "SBU 6": "DCU_NASHIK",
    "SBU 7": "DCU_NASHIK",
}


async def ensure_hierarchy(db: AsyncSession) -> dict:
    """Create any missing RCU/DCU masters and link the known Nashik SBUs. Idempotent.

    Returns a summary; the caller commits."""
    summary: dict = {"rcus_created": 0, "dcus_created": 0, "sbus_linked": [], "conflicts": []}

    rcu_code, rcu_name = RCU_PUNE
    rcu = await db.scalar(select(RCU).where(RCU.code == rcu_code))
    if rcu is None:
        rcu = RCU(code=rcu_code, name=rcu_name)
        db.add(rcu)
        await db.flush()
        summary["rcus_created"] += 1

    dcus: dict[str, DCU] = {}
    for code, name in DCUS:
        dcu = await db.scalar(select(DCU).where(DCU.code == code))
        if dcu is None:
            dcu = DCU(code=code, name=name, rcu_id=rcu.id)
            db.add(dcu)
            await db.flush()
            summary["dcus_created"] += 1
        dcus[code] = dcu

    for sbu_code, dcu_code in SBU_DCU_LINKS.items():
        sbu = await db.scalar(
            select(SBU).where(func.lower(func.trim(SBU.code)) == sbu_code.lower())
        )
        if sbu is None:
            continue  # SBU not created yet; re-run after the SBU master exists.
        target = dcus[dcu_code]
        if sbu.dcu_id is None:
            sbu.dcu_id = target.id
            summary["sbus_linked"].append(sbu.code)
        elif sbu.dcu_id != target.id:
            # Already placed elsewhere by an administrator: report, never overwrite.
            summary["conflicts"].append(sbu.code)
    await db.flush()
    return summary
