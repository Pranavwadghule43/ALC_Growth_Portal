"""Shared regional activities, stable participation and immutable finalized decisions."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict

from fastapi import HTTPException, Request
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AlcStatus, Role
from app.models import (
    ALC,
    DCU,
    SBU,
    User,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
    utcnow,
)
from app.models import (
    YearlyActivitySubmission as Submission,
)
from app.services.audit import record_audit
from app.services.growth_challenge import current_date
from app.services.scope import alc_scope
from app.services.yearly_membership import lock_membership
from app.yearly_schemas import CompletionIn, DefinitionIn, RunIn

Definition = YearlyActivityDefinition
Run = YearlyActivityDCURun
Completion = YearlyActivityCompletion


def local_today():
    return current_date()


def current_year() -> int:
    return local_today().year


def user_rcu(user: User):
    if user.role == Role.DCU:
        return user.dcu.rcu_id
    if user.role == Role.SBU:
        return user.sbu.dcu.rcu_id
    if user.role == Role.ALC:
        return user.alc.sbu.dcu.rcu_id
    return None


def user_dcu(user: User):
    if user.role == Role.DCU:
        return user.dcu_id
    if user.role == Role.SBU:
        return user.sbu.dcu_id
    if user.role == Role.ALC:
        return user.alc.sbu.dcu_id
    return None


def run_scope(user: User):
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.DCU:
        return Run.dcu_id == user.dcu_id
    return Run.dcu_id == user_dcu(user)


def definition_scope(user: User):
    if user.role == Role.ADMIN:
        return True
    # Ownership is derived from the authenticated DCU at creation and is not editable.
    # ADMIN retains oversight; every operational role is restricted to its current DCU
    # hierarchy.  Do not use historic participation as a visibility bypass after a move.
    return Definition.introduced_by_dcu_id == user_dcu(user)


def eligible_activity_ids(year: int | None = None):
    """No voluntarily-activated participant shortcut: AVAILABLE runs block eligibility."""
    query = (
        select(Definition.id)
        .join(Run, Run.yearly_activity_id == Definition.id)
        .group_by(Definition.id)
        .having(
            func.count(Run.id) > 0, func.sum(case((Run.status != "FINALIZED", 1), else_=0)) == 0
        )
    )
    if year is not None:
        query = query.where(Definition.calendar_year == year)
    return query


def run_out(run: Run, dcu_name: str) -> dict:
    return {
        "id": run.id,
        "dcu_id": run.dcu_id,
        "dcu_name": dcu_name,
        "status": run.status,
        "scheduled_start_date": run.scheduled_start_date,
        "scheduled_end_date": run.scheduled_end_date,
        "instructions": run.instructions,
        "activated_at": run.activated_at,
        "finalized_at": run.finalized_at,
        "created_by": run.created_by,
        "updated_by": run.updated_by,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


async def annual_summary(db: AsyncSession, user: User, year: int) -> dict:
    # Count definition/ALC pairs in the same owning hierarchy.  This is equivalent to
    # definitions × authorized ALCs for operational roles, while ADMIN totals remain
    # accurate across multiple DCUs with different activity populations.
    applicable = (
        select(func.count())
        .select_from(Definition)
        .join(SBU, SBU.dcu_id == Definition.introduced_by_dcu_id)
        .join(ALC, ALC.sbu_id == SBU.id)
        .where(
            Definition.calendar_year == year,
            definition_scope(user),
            alc_scope(user, ALC.id),
        )
        .scalar_subquery()
    )
    counts = (
        select(
            func.count(Submission.id).label("completed"),
            func.sum(
                case(
                    (Completion.yearly_activity_id.in_(eligible_activity_ids(year)), 1),
                    else_=0,
                )
            ).label("official"),
        )
        .select_from(Completion)
        .join(Submission, Submission.participation_id == Completion.id)
        .join(Definition, Completion.yearly_activity_id == Definition.id)
        .where(
            Definition.calendar_year == year,
            Submission.status == "VERIFIED",
            alc_scope(user, Completion.alc_id),
            definition_scope(user),
        )
        .subquery("annual_completion_counts")
    )
    row = (
        await db.execute(
            select(applicable, counts.c.completed, counts.c.official).select_from(counts)
        )
    ).one()
    denominator = int(row[0] or 0)
    completed, official = int(row[1] or 0), int(row[2] or 0)
    return {
        "completed_count": completed,
        "total_applicable": denominator,
        "completion_percentage": round(100 * completed / denominator, 2) if denominator else 0,
        "official_completed_count": official,
    }


async def list_definitions(
    db: AsyncSession, user: User, *, year: int, q: str | None, page: int, page_size: int
) -> dict:
    clauses = [Definition.calendar_year == year, definition_scope(user)]
    if q:
        term = f"%{q.strip()}%"
        clauses.append(or_(Definition.name.ilike(term), Definition.activity_code.ilike(term)))
    total = await db.scalar(select(func.count(Definition.id)).where(*clauses)) or 0
    definition_rows = (
        await db.execute(
            select(Definition, DCU.id, DCU.code, DCU.name)
            .join(DCU, Definition.introduced_by_dcu_id == DCU.id)
            .where(*clauses)
            .order_by(Definition.activity_code, Definition.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    definitions = [entry[0] for entry in definition_rows]
    introducers = {
        row[0].id: {"id": row[1], "code": row[2], "name": row[3]} for row in definition_rows
    }
    ids = [row.id for row in definitions]
    grouped = defaultdict(list)
    counts = {}
    if ids:
        run_counts = (
            select(
                Run.yearly_activity_id.label("activity_id"),
                func.count(Run.id).label("participant_count"),
                func.sum(case((Run.status == "FINALIZED", 1), else_=0)).label("finalized_count"),
            )
            .where(Run.yearly_activity_id.in_(ids))
            .group_by(Run.yearly_activity_id)
            .cte("yearly_run_counts")
        )
        runs = await db.execute(
            select(
                run_counts.c.activity_id,
                Run,
                DCU.name,
                run_counts.c.participant_count,
                run_counts.c.finalized_count,
            )
            .select_from(
                run_counts.outerjoin(
                    Run,
                    and_(
                        Run.yearly_activity_id == run_counts.c.activity_id,
                        run_scope(user),
                    ),
                ).outerjoin(DCU, DCU.id == Run.dcu_id)
            )
            .order_by(DCU.code)
        )
        for activity_id, run, dcu_name, count, finalized in runs:
            counts[activity_id] = (int(count), int(finalized or 0))
            if run is not None:
                grouped[run.yearly_activity_id].append(run_out(run, dcu_name))
    items = []
    for row in definitions:
        count, finalized = counts.get(row.id, (0, 0))
        items.append(
            {
                "id": row.id,
                "rcu_id": row.rcu_id,
                "calendar_year": row.calendar_year,
                "activity_code": row.activity_code,
                "name": row.name,
                "description": row.description,
                "introduced_by_dcu": introducers[row.id],
                "created_by": row.created_by,
                "updated_by": row.updated_by,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "regional_status": "ELIGIBLE" if count and count == finalized else "PENDING",
                "participant_count": count,
                "finalized_count": finalized,
                "runs": grouped[row.id],
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size),
        "summary": await annual_summary(db, user, year),
    }


async def create_definition(
    db: AsyncSession, user: User, data: DefinitionIn, request: Request
) -> dict:
    await lock_membership(db)
    rcu_id = user_rcu(user)
    definition = Definition(
        **data.model_dump(),
        rcu_id=rcu_id,
        introduced_by_dcu_id=user.dcu_id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(definition)
    try:
        await db.flush()
        # A DCU-created definition is shared only inside that DCU hierarchy.  The
        # authoritative owner comes from the authenticated user, never request data.
        db.add(
            Run(
                yearly_activity_id=definition.id,
                dcu_id=user.dcu_id,
                created_by=user.id,
                updated_by=user.id,
            )
        )
        await record_audit(
            db,
            "yearly_activity_registered",
            "yearly_activity_definition",
            definition.id,
            user,
            request,
            {
                "activity_code": definition.activity_code,
                "calendar_year": definition.calendar_year,
                "owner_dcu_id": str(user.dcu_id),
                "participant_dcu_ids": [str(user.dcu_id)],
            },
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409,
            "Activity code already exists for this DCU and year; "
            "search your activity list and reuse it",
        ) from None
    return {
        "id": definition.id,
        "activity_code": definition.activity_code,
        "calendar_year": definition.calendar_year,
    }


async def get_run(db: AsyncSession, user: User, run_id: uuid.UUID, *, write=False) -> Run:
    clause = Run.dcu_id == user.dcu_id if write else run_scope(user)
    query = (
        select(Run)
        .join(Definition, Run.yearly_activity_id == Definition.id)
        .where(Run.id == run_id, clause, definition_scope(user))
    )
    if write:
        query = query.with_for_update()
    run = await db.scalar(query)
    if not run:
        raise HTTPException(404, "Activity run not found")
    return run


async def update_run(
    db: AsyncSession, user: User, run_id: uuid.UUID, data: RunIn, request: Request
) -> dict:
    # Same lock ordering as reassignment: membership roots, then run, then snapshot.
    await lock_membership(db)
    run = await get_run(db, user, run_id, write=True)
    if run.status == "FINALIZED":
        raise HTTPException(409, "Finalized activities are locked")
    definition = await db.get(Definition, run.yearly_activity_id)
    before = {
        "status": run.status,
        "scheduled_start_date": str(run.scheduled_start_date),
        "scheduled_end_date": str(run.scheduled_end_date),
    }
    fields = data.model_fields_set
    if "scheduled_start_date" in fields:
        if run.status not in {"AVAILABLE", "SCHEDULED"}:
            raise HTTPException(409, "An activated schedule is locked")
        if not data.scheduled_start_date or not data.scheduled_end_date:
            raise HTTPException(422, "Both schedule dates are required")
        if (
            data.scheduled_start_date.year != definition.calendar_year
            or data.scheduled_end_date.year != definition.calendar_year
        ):
            raise HTTPException(422, "Schedule dates must belong to the activity calendar year")
        run.scheduled_start_date = data.scheduled_start_date
        run.scheduled_end_date = data.scheduled_end_date
    desired = data.status or (
        "SCHEDULED" if run.status == "AVAILABLE" and run.scheduled_start_date else run.status
    )
    transitions = {
        "AVAILABLE": {"SCHEDULED"},
        "SCHEDULED": {"SCHEDULED", "ACTIVE"},
        "ACTIVE": {"ACTIVE", "MARKING"},
        "MARKING": {"MARKING"},
    }
    if desired not in transitions[run.status]:
        raise HTTPException(409, "Invalid activity lifecycle transition")
    if not run.scheduled_start_date or not run.scheduled_end_date:
        raise HTTPException(422, "Schedule the activity before activation")
    if desired == "ACTIVE" and run.scheduled_start_date > local_today():
        raise HTTPException(409, "Activity cannot activate before its scheduled start")
    if desired == "MARKING" and run.scheduled_end_date > local_today():
        raise HTTPException(409, "Completion marking cannot begin before the scheduled end")
    if desired == "ACTIVE" and run.status == "SCHEDULED":
        # Scalar projection, one row per ALC; never loads evidence/review ORM graphs.
        already = (
            select(Completion.alc_id)
            .where(Completion.yearly_activity_id == run.yearly_activity_id)
            .correlate(None)
        )
        members = await db.execute(
            select(ALC.id, ALC.sbu_id)
            .join(SBU, ALC.sbu_id == SBU.id)
            .where(
                SBU.dcu_id == run.dcu_id,
                SBU.is_active.is_(True),
                ALC.status == AlcStatus.ACTIVE,
                ALC.id.not_in(already),
            )
        )
        db.add_all(
            [
                Completion(
                    yearly_activity_id=run.yearly_activity_id,
                    yearly_activity_dcu_run_id=run.id,
                    alc_id=alc_id,
                    snapshot_sbu_id=sbu_id,
                    completed=None,
                )
                for alc_id, sbu_id in members
            ]
        )
        run.activated_at, run.activated_by = utcnow(), user.id
    if "instructions" in fields:
        run.instructions = data.instructions
    run.status, run.updated_by = desired, user.id
    await record_audit(
        db,
        "yearly_activity_run_updated",
        "yearly_activity_dcu_run",
        run.id,
        user,
        request,
        {"before": before, "status": desired},
    )
    await db.commit()
    dcu = await db.get(DCU, run.dcu_id)
    return run_out(run, dcu.name)


async def list_completions(
    db: AsyncSession,
    user: User,
    run_id: uuid.UUID,
    *,
    q: str | None,
    sbu_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> dict:
    run = await get_run(db, user, run_id)
    base = [Completion.yearly_activity_dcu_run_id == run.id, alc_scope(user, Completion.alc_id)]
    query = (
        select(
            Completion,
            ALC.alc_code,
            ALC.alc_name,
            SBU.name.label("sbu_name"),
            Submission.id,
            Submission.status,
        )
        .join(ALC, Completion.alc_id == ALC.id)
        .join(SBU, Completion.snapshot_sbu_id == SBU.id)
        .outerjoin(Submission, Submission.participation_id == Completion.id)
    )
    counts = (
        await db.execute(
            select(
                func.count(Completion.id),
                func.sum(case((Submission.status == "VERIFIED", 1), else_=0)),
                func.sum(case((Submission.status == "REJECTED", 1), else_=0)),
            )
            .outerjoin(Submission, Submission.participation_id == Completion.id)
            .where(*base)
        )
    ).one()
    total_all, yes, no = (int(value or 0) for value in counts)
    sbus = [
        {"id": key, "name": name}
        for key, name in await db.execute(
            select(SBU.id, SBU.name)
            .join(Completion, Completion.snapshot_sbu_id == SBU.id)
            .where(*base)
            .distinct()
            .order_by(SBU.name)
        )
    ]
    clauses = list(base)
    if q:
        term = f"%{q.strip()}%"
        clauses.append(or_(ALC.alc_name.ilike(term), ALC.alc_code.ilike(term)))
    if sbu_id:
        clauses.append(Completion.snapshot_sbu_id == sbu_id)
    filtered = query.where(*clauses)
    total = await db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    rows = (
        await db.execute(
            filtered.order_by(ALC.alc_code).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    definition = await db.get(Definition, run.yearly_activity_id)
    page_ids = [row.alc_id for row, *_ in rows]
    annual = {
        alc_id: (int(done or 0), int(official or 0))
        for alc_id, done, official in await db.execute(
            select(
                Completion.alc_id,
                func.count(Completion.id),
                func.sum(
                    case(
                        (
                            Completion.yearly_activity_id.in_(
                                eligible_activity_ids(definition.calendar_year)
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .join(Submission, Submission.participation_id == Completion.id)
            .join(Definition, Completion.yearly_activity_id == Definition.id)
            .where(
                Completion.alc_id.in_(page_ids),
                Submission.status == "VERIFIED",
                Definition.rcu_id == definition.rcu_id,
                Definition.calendar_year == definition.calendar_year,
                definition_scope(user),
            )
            .group_by(Completion.alc_id)
        )
    }
    annual_total = (
        await db.scalar(
            select(func.count(Definition.id)).where(
                Definition.rcu_id == definition.rcu_id,
                Definition.calendar_year == definition.calendar_year,
                definition_scope(user),
            )
        )
        or 0
    )
    items = [
        {
            "alc_id": row.alc_id,
            "alc_code": code,
            "alc_name": name,
            "sbu_id": row.snapshot_sbu_id,
            "sbu_name": sbu_name,
            "completed": True if status == "VERIFIED" else False if status == "REJECTED" else None,
            "legacy_completed": row.completed,
            "submission_id": submission_id,
            "submission_status": status or "NOT_SUBMITTED",
            "marked_at": row.marked_at,
            "updated_at": row.updated_at,
            "marked_by": row.marked_by,
            "yearly_completed_count": annual.get(row.alc_id, (0, 0))[0],
            "yearly_official_completed_count": annual.get(row.alc_id, (0, 0))[1],
            "yearly_total_applicable": annual_total,
            "can_edit": False,
        }
        for row, code, name, sbu_name, submission_id, status in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size),
        "sbus": sbus,
        "summary": {"yes": yes, "no": no, "not_marked": total_all - yes - no},
    }


async def mark_completion(
    db: AsyncSession,
    user: User,
    run_id: uuid.UUID,
    alc_id: uuid.UUID,
    data: CompletionIn,
    request: Request,
) -> dict:
    raise HTTPException(410, "Manual completion is retired; use evidence verification")


async def finalize_run(db: AsyncSession, user: User, run_id: uuid.UUID, request: Request) -> dict:
    # Every mark and finalization locks the same run row: no finalize/mark race.
    run = await get_run(db, user, run_id, write=True)
    if run.status != "MARKING" or run.scheduled_end_date > local_today():
        raise HTTPException(409, "Only an ended activity in MARKING can be finalized")
    pending = await db.scalar(
        select(func.count(Submission.id))
        .join(Completion, Submission.participation_id == Completion.id)
        .where(
            Completion.yearly_activity_dcu_run_id == run.id,
            Submission.status.in_({"SUBMITTED", "RESUBMITTED", "CORRECTION_REQUIRED"}),
        )
    )
    if pending:
        raise HTTPException(
            409, f"Resolve all pending reviews/corrections before finalization ({pending} pending)"
        )
    run.status, run.finalized_at, run.finalized_by = "FINALIZED", utcnow(), user.id
    run.updated_by = user.id
    await record_audit(
        db,
        "yearly_activity_run_finalized",
        "yearly_activity_dcu_run",
        run.id,
        user,
        request,
        {"yearly_activity_id": str(run.yearly_activity_id)},
    )
    await db.commit()
    return {"id": run.id, "status": run.status, "finalized_at": run.finalized_at}
