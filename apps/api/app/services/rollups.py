"""Reusable, scope-agnostic aggregate expressions for dashboards, directories and reports.

Every function here takes the *already scoped* filter clauses from the caller (built with
``app.services.scope``); nothing in this module decides who may see what. Aggregates are
computed with a fixed number of grouped queries — never one query per SBU or ALC — so a
directory page costs the same whether a DCU owns 3 SBUs or 30, and 200 ALCs or 800.

All activity counts use submitted-workflow data only (``DRAFT`` is excluded): a draft is
private to its ALC and is never part of an operational metric. Metrics always reflect an
activity's *current* status, so a changed decision moves the activity between aggregates
immediately while its review history stays untouched.
"""
import uuid
from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ActivityStatus, AlcStatus
from app.models import ALC, Activity, Partner
from app.services.scope import submitted_workflow

PENDING_STATUSES = (
    ActivityStatus.SUBMITTED,
    ActivityStatus.RESUBMITTED,
    ActivityStatus.UNDER_REVIEW,
)

ZERO_ACTIVITY_METRICS = {
    "activities": 0,
    "submitted": 0,
    "resubmitted": 0,
    "pending": 0,
    "corrections": 0,
    "verified": 0,
    "rejected": 0,
    "learners": 0,
    "leads": 0,
    "admissions": 0,
}


def _verified_sum(column):
    return func.coalesce(
        func.sum(case((Activity.status == ActivityStatus.VERIFIED, column), else_=0)), 0
    )


def activity_metric_columns() -> list:
    """Labelled aggregate columns over ``Activity`` (callers restrict rows to non-drafts).

    ``activities`` counts every submitted-workflow activity; ``pending`` is the review queue
    (submitted, resubmitted or under review); learners/leads/admissions are verified-only.
    """
    return [
        func.count(Activity.id).label("activities"),
        func.count(case((Activity.status == ActivityStatus.SUBMITTED, 1))).label("submitted"),
        func.count(case((Activity.status == ActivityStatus.RESUBMITTED, 1))).label("resubmitted"),
        func.count(case((Activity.status.in_(PENDING_STATUSES), 1))).label("pending"),
        func.count(case((Activity.status == ActivityStatus.CORRECTION_REQUIRED, 1))).label(
            "corrections"
        ),
        func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label("verified"),
        func.count(case((Activity.status == ActivityStatus.REJECTED, 1))).label("rejected"),
        _verified_sum(Activity.learners_reached).label("learners"),
        _verified_sum(Activity.leads_generated).label("leads"),
        _verified_sum(Activity.admissions_generated).label("admissions"),
    ]


async def activity_totals(db: AsyncSession, *filters: ColumnElement[bool]) -> dict:
    """One row of activity metrics over submitted-workflow activities matching ``filters``."""
    row = (
        (
            await db.execute(
                select(*activity_metric_columns()).where(submitted_workflow(), *filters)
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def sbu_rollup(
    db: AsyncSession,
    alc_filter: ColumnElement[bool],
    activity_filters: Iterable[ColumnElement[bool]] = (),
) -> dict[uuid.UUID, dict]:
    """Per-SBU ALC, partner and activity metrics for the ALCs matching ``alc_filter``.

    Three grouped queries in total (ALCs, activities, partners) regardless of SBU count.
    ``activity_filters`` further restricts the activity rows (e.g. a report date range)."""
    result: dict[uuid.UUID, dict] = {}

    def slot(sbu_id):
        return result.setdefault(
            sbu_id,
            {"alcs": 0, "active_alcs": 0, "inactive_alcs": 0, "partners": 0,
             **ZERO_ACTIVITY_METRICS},
        )

    alc_rows = (
        await db.execute(
            select(
                ALC.sbu_id,
                func.count(ALC.id),
                func.count(case((ALC.status == AlcStatus.ACTIVE, 1))),
            )
            .where(alc_filter)
            .group_by(ALC.sbu_id)
        )
    ).all()
    for sbu_id, total, active in alc_rows:
        entry = slot(sbu_id)
        entry.update(alcs=total, active_alcs=active, inactive_alcs=total - active)

    activity_rows = (
        (
            await db.execute(
                select(ALC.sbu_id.label("sbu_id"), *activity_metric_columns())
                .join(ALC, Activity.alc_id == ALC.id)
                .where(alc_filter, submitted_workflow(), *activity_filters)
                .group_by(ALC.sbu_id)
            )
        )
        .mappings()
        .all()
    )
    for row in activity_rows:
        data = dict(row)
        slot(data.pop("sbu_id")).update(data)

    partner_rows = (
        await db.execute(
            select(ALC.sbu_id, func.count(Partner.id))
            .join(ALC, Partner.alc_id == ALC.id)
            .where(alc_filter)
            .group_by(ALC.sbu_id)
        )
    ).all()
    for sbu_id, partners in partner_rows:
        slot(sbu_id)["partners"] = partners
    return result


def alc_activity_join(*extra: ColumnElement[bool]):
    """Outer-join condition from ``ALC`` to its submitted-workflow activities.

    The draft exclusion lives in the join (not the WHERE) so ALCs without activities still
    appear with zero counts."""
    return and_(ALC.id == Activity.alc_id, submitted_workflow(), *extra)


def partner_activity_stats():
    """Correlated (activity_count, last_activity) subqueries for a ``Partner`` row, counting
    submitted-workflow activities only."""
    activity_count = (
        select(func.count(Activity.id))
        .where(Activity.partner_id == Partner.id, submitted_workflow())
        .correlate(Partner)
        .scalar_subquery()
    )
    last_activity = (
        select(func.max(Activity.activity_date))
        .where(Activity.partner_id == Partner.id, submitted_workflow())
        .correlate(Partner)
        .scalar_subquery()
    )
    return activity_count, last_activity
