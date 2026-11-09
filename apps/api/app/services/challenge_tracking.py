"""Hierarchy-scoped reporting for one published Admin Growth Challenge.

This is a read model over existing verified activities. It creates no parallel participation
workflow: an ALC participates after its first verified activity in the challenge window and
completes when all four configured targets are met.
"""
from __future__ import annotations

import math
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import ActivityStatus, Role
from app.models import ALC, DCU, SBU, Activity, ActivityEvidence, GrowthChallenge, User
from app.services.growth_challenge import challenge_out, current_date, targets_of
from app.services.scope import alc_scope, can_access_alc, can_access_sbu


async def visible_challenges(db: AsyncSession, user: User) -> list[dict]:
    query = select(GrowthChallenge)
    if user.role != Role.ADMIN:
        query = query.where(GrowthChallenge.is_published.is_(True))
    rows = list(
        await db.scalars(
            query.order_by(GrowthChallenge.start_date.desc(), GrowthChallenge.created_at.desc())
        )
    )
    today = current_date()
    return [challenge_out(row, today) for row in rows]


async def visible_challenge(db: AsyncSession, user: User, challenge_id: uuid.UUID):
    filters = [GrowthChallenge.id == challenge_id]
    if user.role != Role.ADMIN:
        filters.append(GrowthChallenge.is_published.is_(True))
    challenge = await db.scalar(select(GrowthChallenge).where(*filters))
    if not challenge:
        raise HTTPException(404, "Growth Challenge not found")
    return challenge


def _status(row: dict, targets: dict[str, int]) -> str:
    completed = all(int(row[key]) >= target for key, target in targets.items())
    if completed:
        return "COMPLETED"
    if int(row["activity_count"]):
        return "IN_PROGRESS"
    return "NOT_STARTED"


async def progress(
    db: AsyncSession,
    user: User,
    challenge_id: uuid.UUID,
    *,
    page: int,
    page_size: int,
    search: str | None = None,
    sbu_id: uuid.UUID | None = None,
    alc_id: uuid.UUID | None = None,
) -> dict:
    challenge = await visible_challenge(db, user, challenge_id)
    if sbu_id is not None and not await can_access_sbu(db, user, sbu_id):
        raise HTTPException(404, "SBU not found")
    if alc_id is not None and not await can_access_alc(db, user, alc_id):
        raise HTTPException(404, "ALC not found")

    metric = (
        select(
            Activity.alc_id.label("alc_id"),
            func.coalesce(func.sum(Activity.leads_generated), 0).label("prospects"),
            func.count(case((func.lower(Activity.activity_type).like("%meeting%"), 1))).label(
                "meetings"
            ),
            func.count(case((func.lower(Activity.activity_type).like("%pilot%"), 1))).label(
                "pilots"
            ),
            func.count(
                func.distinct(
                    case(
                        (
                            and_(
                                func.lower(Activity.activity_type).like("%partnership%"),
                                Activity.partner_id.is_not(None),
                            ),
                            Activity.partner_id,
                        )
                    )
                )
            ).label("partnerships"),
            func.count(Activity.id).label("activity_count"),
        )
        .where(
            Activity.status == ActivityStatus.VERIFIED,
            Activity.activity_date >= challenge.start_date,
            Activity.activity_date <= challenge.end_date,
        )
        .group_by(Activity.alc_id)
        .subquery("challenge_metrics")
    )
    evidence = (
        select(
            Activity.alc_id.label("alc_id"),
            func.count(ActivityEvidence.id).label("evidence_count"),
        )
        .join(ActivityEvidence, ActivityEvidence.activity_id == Activity.id)
        .where(
            Activity.status == ActivityStatus.VERIFIED,
            Activity.activity_date >= challenge.start_date,
            Activity.activity_date <= challenge.end_date,
            ActivityEvidence.is_active.is_(True),
        )
        .group_by(Activity.alc_id)
        .subquery("challenge_evidence")
    )
    filters = [
        ALC.status == "ACTIVE",
        SBU.is_active.is_(True),
        DCU.is_active.is_(True),
        alc_scope(user),
    ]
    if sbu_id is not None:
        filters.append(ALC.sbu_id == sbu_id)
    if alc_id is not None:
        filters.append(ALC.id == alc_id)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(or_(ALC.alc_code.ilike(pattern), ALC.alc_name.ilike(pattern)))
    rows = (
        await db.execute(
            select(
                ALC.id,
                ALC.alc_code,
                ALC.alc_name,
                SBU.id.label("sbu_id"),
                SBU.code.label("sbu_code"),
                SBU.name.label("sbu_name"),
                DCU.id.label("dcu_id"),
                DCU.code.label("dcu_code"),
                DCU.name.label("dcu_name"),
                func.coalesce(metric.c.prospects, 0).label("prospects"),
                func.coalesce(metric.c.meetings, 0).label("meetings"),
                func.coalesce(metric.c.pilots, 0).label("pilots"),
                func.coalesce(metric.c.partnerships, 0).label("partnerships"),
                func.coalesce(metric.c.activity_count, 0).label("activity_count"),
                func.coalesce(evidence.c.evidence_count, 0).label("evidence_count"),
            )
            .join(SBU, ALC.sbu_id == SBU.id)
            .join(DCU, SBU.dcu_id == DCU.id)
            .outerjoin(metric, metric.c.alc_id == ALC.id)
            .outerjoin(evidence, evidence.c.alc_id == ALC.id)
            .where(*filters)
            .order_by(ALC.alc_code, ALC.id)
        )
    ).mappings().all()
    targets = targets_of(challenge)
    items = [{**dict(row), "progress_status": _status(row, targets)} for row in rows]
    counts = {
        status: sum(item["progress_status"] == status for item in items)
        for status in ("NOT_STARTED", "IN_PROGRESS", "COMPLETED")
    }
    sbu_groups: dict[uuid.UUID, dict] = {}
    for item in items:
        group = sbu_groups.setdefault(
            item["sbu_id"],
            {
                "sbu_id": item["sbu_id"],
                "sbu_code": item["sbu_code"],
                "sbu_name": item["sbu_name"],
                "eligible": 0,
                "participating": 0,
                "completed": 0,
            },
        )
        group["eligible"] += 1
        group["participating"] += item["progress_status"] != "NOT_STARTED"
        group["completed"] += item["progress_status"] == "COMPLETED"
    for group in sbu_groups.values():
        group["completion_percent"] = round(
            group["completed"] / group["eligible"] * 100, 1
        ) if group["eligible"] else 0.0
    total = len(items)
    start = (page - 1) * page_size
    return {
        "challenge": challenge_out(challenge, current_date()),
        "targets": targets,
        "summary": {
            "eligible": total,
            "participating": total - counts["NOT_STARTED"],
            "completed": counts["COMPLETED"],
            "in_progress": counts["IN_PROGRESS"],
            "not_started": counts["NOT_STARTED"],
            "completion_percent": round(counts["COMPLETED"] / total * 100, 1) if total else 0.0,
        },
        "sbu_breakdown": sorted(sbu_groups.values(), key=lambda row: row["sbu_code"]),
        "items": items[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


async def evidence_activities(
    db: AsyncSession, user: User, challenge_id: uuid.UUID, alc_id: uuid.UUID
) -> dict:
    challenge = await visible_challenge(db, user, challenge_id)
    if not await can_access_alc(db, user, alc_id):
        raise HTTPException(404, "ALC not found")
    activities = list(
        await db.scalars(
            select(Activity)
            .options(selectinload(Activity.evidence))
            .where(
                Activity.alc_id == alc_id,
                Activity.status == ActivityStatus.VERIFIED,
                Activity.activity_date >= challenge.start_date,
                Activity.activity_date <= challenge.end_date,
            )
            .order_by(Activity.activity_date.desc(), Activity.id)
        )
    )
    return {
        "items": [
            {
                "id": activity.id,
                "activity_number": activity.activity_number,
                "activity_type": activity.activity_type,
                "activity_date": activity.activity_date,
                "outcome": activity.outcome,
                "evidence": [
                    {
                        "id": item.id,
                        "original_filename": item.original_filename,
                        "mime_type": item.mime_type,
                        "file_size": item.file_size,
                        "uploaded_at": item.uploaded_at,
                        "access_url": f"/api/portal/evidence/{item.id}/access",
                    }
                    for item in activity.evidence
                    if item.is_active
                ],
            }
            for activity in activities
        ]
    }
