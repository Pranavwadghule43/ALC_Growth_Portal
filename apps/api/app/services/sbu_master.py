"""Seed the SBU master records for DCU Ahilya Nagar, Pune North and Pune South.

The canonical SBU identifiers and their DCUs come from ``master_mappings``. The seed uses the
existing RCU Pune DCU records (it never creates an RCU or DCU) and, per canonical SBU:

* no existing SBU matches          -> create it under its DCU, with ``code`` = the canonical
  identifier and ``name`` = its friendly display name;
* exact code, same DCU             -> leave unchanged (a different existing name is only
  reported, never overwritten);
* exact code, not yet under a DCU  -> place it under its DCU (reported as ``linked``);
* exact code, under another DCU    -> conflict: the seed stops, nothing is moved;
* a different spelling matches     -> variant: the seed stops so the canonical plan can be
  reviewed (e.g. an existing ``SBU Pune North 1``, or another code already using the
  display name ``Pune North SBU 1``);
* several existing SBUs match      -> ambiguity: the seed stops.

"Matches" uses the same spelling rules the Phase 3A importer uses to resolve an SBU, so a
successful seed guarantees each canonical identifier resolves to exactly one SBU at import.

The whole plan is checked before anything is written. The seed only adds or places SBU rows
(plus one audit entry when it changes something); it never reads or writes ALCs, users,
passwords, activities, partners, evidence, reviews or revisions, never renames an SBU, and
never touches the Nashik SBUs. Idempotent: a second run reports every SBU unchanged. The
caller commits.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AlcStatus
from app.models import ALC, DCU, RCU, SBU
from app.services.audit import record_audit
from app.services.master_import import _key, _lookup, _norm
from app.services.master_mappings import (
    RCU_CODE,
    SBU_DISPLAY_NAMES,
    SBUS_BY_DCU,
    remaining_sbus,
)


class SbuSeedBlocked(Exception):
    """The seed cannot proceed safely; carries the plan. Nothing was written."""

    def __init__(self, report: dict):
        self.report = report
        super().__init__("SBU seed blocked: " + "; ".join(report["blockers"]))


def _keys(value: str) -> set[str]:
    return {_norm(value), _key(value, "sbu")}


def _matches(canonical: str, sbu: SBU) -> bool:
    """Does an existing SBU's code or name read as this identifier or its display name?"""
    wanted = _keys(canonical) | _keys(SBU_DISPLAY_NAMES[canonical])
    return bool(wanted & (_keys(sbu.code) | _keys(sbu.name)))


async def nashik_snapshot(db: AsyncSession) -> dict[str, dict]:
    """DCU and ALC counts (total / active) for each Nashik SBU, for regression checks."""
    result = {}
    for code in SBUS_BY_DCU["Nashik"]:
        sbu = await db.scalar(select(SBU).where(SBU.code == code))
        if sbu is None:
            result[code] = None
            continue
        dcu_code = await db.scalar(select(DCU.code).where(DCU.id == sbu.dcu_id))
        total = await db.scalar(select(func.count(ALC.id)).where(ALC.sbu_id == sbu.id))
        active = await db.scalar(
            select(func.count(ALC.id)).where(ALC.sbu_id == sbu.id, ALC.status == AlcStatus.ACTIVE)
        )
        result[code] = {"dcu": dcu_code, "alcs": total, "active": active}
    return result


async def plan(db: AsyncSession) -> dict:
    """Decide the action for every canonical SBU without writing anything."""
    blockers: list[str] = []
    rcu = await db.scalar(select(RCU).where(RCU.code == RCU_CODE))
    dcus = {d.code: d for d in (await db.scalars(select(DCU))).all()}
    sbus = (await db.scalars(select(SBU))).all()
    dcu_codes = {d.id: d.code for d in dcus.values()}
    if rcu is None:
        blockers.append(f"RCU '{RCU_CODE}' does not exist; run scripts.seed_hierarchy first")

    items = []
    for code, dcu_code in remaining_sbus():
        item = {"sbu": code, "name": SBU_DISPLAY_NAMES[code], "dcu": dcu_code}
        dcu = dcus.get(dcu_code)
        matches = [s for s in sbus if _matches(code, s)]
        if dcu is None:
            item["action"] = "blocked"
            item["reason"] = f"DCU '{dcu_code}' does not exist; run scripts.seed_hierarchy first"
        elif rcu is not None and dcu.rcu_id != rcu.id:
            item["action"] = "blocked"
            item["reason"] = f"DCU '{dcu_code}' is not under {RCU_CODE}"
        elif len(matches) > 1:
            item["action"] = "ambiguous"
            item["reason"] = "matches several existing SBUs: " + ", ".join(
                sorted(s.code for s in matches)
            )
        elif not matches:
            item["action"] = "create"
        else:
            existing = matches[0]
            item["existing"] = existing.code
            if existing.code != code:
                item["action"] = "variant"
                item["reason"] = (
                    f"an existing SBU '{existing.code}' matches this identifier with a "
                    "different spelling; review the canonical plan before seeding"
                )
            elif existing.dcu_id == dcu.id:
                item["action"] = "unchanged"
                if existing.name != item["name"]:
                    item["note"] = f"existing name '{existing.name}' kept (not renamed)"
            elif existing.dcu_id is None:
                item["action"] = "link"
                if existing.name != item["name"]:
                    item["note"] = f"existing name '{existing.name}' kept (not renamed)"
            else:
                item["action"] = "conflict"
                item["reason"] = (
                    f"already under {dcu_codes.get(existing.dcu_id)}; "
                    "an existing SBU is never moved automatically"
                )
        if "reason" in item:
            blockers.append(f"{code}: {item['reason']}")
        items.append(item)

    # Each existing SBU may satisfy at most one canonical identifier.
    claimed: dict[str, list[str]] = {}
    for item in items:
        if item.get("existing"):
            claimed.setdefault(item["existing"], []).append(item["sbu"])
    for existing, codes in claimed.items():
        if len(codes) > 1:
            blockers.append(f"existing SBU '{existing}' matches several identifiers: {codes}")

    return {"items": items, "blockers": blockers, "counts": _count(items)}


def _count(items) -> dict[str, int]:
    counts = {"create": 0, "link": 0, "unchanged": 0}
    for item in items:
        if item["action"] in counts:
            counts[item["action"]] += 1
    return counts


async def seed_remaining_sbus(db: AsyncSession, *, dry_run: bool = False) -> dict:
    """Apply the plan (unless ``dry_run``). Raises ``SbuSeedBlocked`` before writing if any
    SBU is blocked, conflicting, a spelling variant or ambiguous. The caller commits."""
    nashik_before = await nashik_snapshot(db)
    report = await plan(db)
    report["nashik_before"] = nashik_before
    if report["blockers"]:
        raise SbuSeedBlocked(report)
    if dry_run:
        report["dry_run"] = True
        return report

    dcus = {d.code: d for d in (await db.scalars(select(DCU))).all()}
    for item in report["items"]:
        dcu = dcus[item["dcu"]]
        if item["action"] == "create":
            db.add(SBU(code=item["sbu"], name=item["name"], dcu_id=dcu.id, is_active=True))
        elif item["action"] == "link":
            sbu = await db.scalar(select(SBU).where(SBU.code == item["sbu"]))
            sbu.dcu_id = dcu.id
    await db.flush()

    # Post-conditions: Nashik untouched, and every identifier resolves (as the Phase 3A
    # importer resolves it) to exactly the seeded SBU under the right DCU.
    report["nashik_after"] = await nashik_snapshot(db)
    problems = []
    if report["nashik_after"] != nashik_before:
        problems.append("Nashik SBUs changed")
    table = _lookup((await db.scalars(select(SBU))).all(), "sbu")
    for code, dcu_code in remaining_sbus():
        sbu = table.get(_norm(code))
        if sbu is None or sbu.code != code or sbu.dcu_id != dcus[dcu_code].id:
            problems.append(f"{code} does not resolve to one SBU under {dcu_code}")
    if problems:
        report["blockers"] = problems
        raise SbuSeedBlocked(report)

    changed = [i for i in report["items"] if i["action"] in ("create", "link")]
    if changed:
        await record_audit(
            db,
            "SBU_MASTER_SEED",
            "SBU",
            metadata={"changes": [{k: i[k] for k in ("sbu", "dcu", "action")} for i in changed]},
        )
        await db.flush()
    return report


async def hierarchy_tree(db: AsyncSession) -> list[dict]:
    """RCU / DCU / SBU rows with ALC counts, including SBUs that have no DCU."""
    rows = (
        await db.execute(
            select(RCU.code, DCU.code, SBU.code, func.count(ALC.id))
            .select_from(SBU)
            .outerjoin(DCU, SBU.dcu_id == DCU.id)
            .outerjoin(RCU, DCU.rcu_id == RCU.id)
            .outerjoin(ALC, ALC.sbu_id == SBU.id)
            .group_by(RCU.code, DCU.code, SBU.code)
            .order_by(RCU.code, DCU.code, SBU.code)
        )
    ).all()
    return [{"rcu": r, "dcu": d, "sbu": s, "alcs": n} for r, d, s, n in rows]
