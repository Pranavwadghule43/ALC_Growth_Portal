"""Admin hierarchy reassignment: move an ALC to another SBU, or an SBU to another DCU.

A move changes exactly one foreign key (``ALC.sbu_id`` or ``SBU.dcu_id``). Nothing else is
rewritten: the ALC / SBU keeps its id, code, name and login accounts, and every activity,
evidence file, review, revision, partner and task stays attached to the same rows. Access for
DCU / SBU users is resolved from the live hierarchy by ``services.scope`` on every request, so
it follows the move on the very next request. Passwords and sessions are never touched.

Rules:

* the target must exist (422), be active (422) and sit in a complete, active hierarchy
  (SBU -> DCU -> RCU, all active) (422); the moved ALC / SBU itself must exist (404);
* moving to the current parent is an explicit no-op: 200 with ``changed: false``, nothing is
  written and no audit entry is recorded;
* validation happens before any write, and the change plus its audit entry are committed
  together in one transaction (rolled back on any failure).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ALC, DCU, RCU, SBU, User
from app.services.audit import record_audit


def _unit(unit) -> dict | None:
    return {"id": str(unit.id), "code": unit.code, "name": unit.name} if unit else None


async def _chain_from_sbu(db: AsyncSession, sbu: SBU | None) -> dict:
    dcu = await db.get(DCU, sbu.dcu_id) if sbu and sbu.dcu_id else None
    rcu = await db.get(RCU, dcu.rcu_id) if dcu else None
    return {"rcu": _unit(rcu), "dcu": _unit(dcu), "sbu": _unit(sbu)}


async def alc_hierarchy(db: AsyncSession, alc: ALC) -> dict:
    """``{"rcu", "dcu", "sbu"}`` of an ALC's current placement (``None`` where missing)."""
    sbu = await db.get(SBU, alc.sbu_id) if alc.sbu_id else None
    return await _chain_from_sbu(db, sbu)


async def sbu_hierarchy(db: AsyncSession, sbu: SBU) -> dict:
    """``{"rcu", "dcu"}`` of an SBU's current placement (``None`` where missing)."""
    chain = await _chain_from_sbu(db, sbu)
    return {"rcu": chain["rcu"], "dcu": chain["dcu"]}


async def require_valid_dcu(db: AsyncSession, dcu_id: uuid.UUID, label: str = "DCU") -> DCU:
    """The single rule for placing an SBU under a DCU (reassignment and SBU creation): the DCU
    must exist and be active, and its RCU must exist and be active. Raises 422 otherwise."""
    dcu = await db.scalar(select(DCU).where(DCU.id == dcu_id).with_for_update(read=True))
    if dcu is None:
        raise HTTPException(status_code=422, detail=f"{label} not found")
    if not dcu.is_active:
        raise HTTPException(status_code=422, detail=f"{label} is inactive")
    rcu = await db.get(RCU, dcu.rcu_id)
    if rcu is None or not rcu.is_active:
        raise HTTPException(status_code=422, detail=f"{label} does not belong to an active RCU")
    return dcu


async def _move(
    db: AsyncSession,
    row,
    field: str,
    value,
    *,
    action: str,
    entity_type: str,
    metadata: dict,
    actor: User,
    request: Request,
) -> None:
    """Set one foreign key, audit it and commit: all-or-nothing."""
    try:
        setattr(row, field, value)
        await record_audit(db, action, entity_type, row.id, actor, request, metadata)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise


async def reassign_alc(
    db: AsyncSession, alc_id: uuid.UUID, sbu_id: uuid.UUID, *, actor: User, request: Request
) -> dict:
    alc = await db.scalar(select(ALC).where(ALC.id == alc_id).with_for_update())
    if alc is None:
        raise HTTPException(status_code=404, detail="ALC not found")
    target = await db.scalar(select(SBU).where(SBU.id == sbu_id).with_for_update(read=True))
    if target is None:
        raise HTTPException(status_code=422, detail="Target SBU not found")
    if not target.is_active:
        raise HTTPException(status_code=422, detail="Target SBU is inactive")
    if target.dcu_id is None:
        raise HTTPException(status_code=422, detail="Target SBU is not assigned to a DCU")
    await require_valid_dcu(db, target.dcu_id, "Target SBU's DCU")

    previous = await alc_hierarchy(db, alc)
    if alc.sbu_id == target.id:
        return _alc_result(alc, previous, previous, changed=False)

    current = await _chain_from_sbu(db, target)
    metadata = {
        "alc_code": alc.alc_code,
        "from": previous,
        "to": current,
        "cross_dcu": (previous["dcu"] or {}).get("id") != current["dcu"]["id"],
    }
    # The only write: one foreign key.
    await _move(
        db,
        alc,
        "sbu_id",
        target.id,
        action="alc_reassigned",
        entity_type="alc",
        metadata=metadata,
        actor=actor,
        request=request,
    )
    return _alc_result(alc, previous, current, changed=True)


def _alc_result(alc: ALC, previous: dict, current: dict, *, changed: bool) -> dict:
    return {
        "changed": changed,
        "alc": {
            "id": str(alc.id),
            "alc_code": alc.alc_code,
            "alc_name": alc.alc_name,
            "status": alc.status.value,
            "sbu_id": str(alc.sbu_id) if alc.sbu_id else None,
        },
        "previous": previous,
        "current": current,
    }


async def reassign_sbu(
    db: AsyncSession, sbu_id: uuid.UUID, dcu_id: uuid.UUID, *, actor: User, request: Request
) -> dict:
    sbu = await db.scalar(select(SBU).where(SBU.id == sbu_id).with_for_update())
    if sbu is None:
        raise HTTPException(status_code=404, detail="SBU not found")
    target = await require_valid_dcu(db, dcu_id, "Target DCU")

    previous = await sbu_hierarchy(db, sbu)
    alc_count = await db.scalar(select(func.count(ALC.id)).where(ALC.sbu_id == sbu.id)) or 0
    if sbu.dcu_id == target.id:
        return _sbu_result(sbu, previous, previous, alc_count, changed=False)

    current = {"rcu": _unit(await db.get(RCU, target.rcu_id)), "dcu": _unit(target)}
    metadata = {"sbu_code": sbu.code, "from": previous, "to": current, "alc_count": alc_count}
    # The only write: one foreign key; the SBU's ALCs follow through the hierarchy.
    await _move(
        db,
        sbu,
        "dcu_id",
        target.id,
        action="sbu_reassigned",
        entity_type="sbu",
        metadata=metadata,
        actor=actor,
        request=request,
    )
    return _sbu_result(sbu, previous, current, alc_count, changed=True)


def _sbu_result(sbu: SBU, previous: dict, current: dict, alc_count: int, *, changed: bool):
    return {
        "changed": changed,
        "sbu": {
            "id": str(sbu.id),
            "code": sbu.code,
            "name": sbu.name,
            "is_active": sbu.is_active,
            "dcu_id": str(sbu.dcu_id) if sbu.dcu_id else None,
        },
        "alc_count": alc_count,
        "previous": previous,
        "current": current,
    }
