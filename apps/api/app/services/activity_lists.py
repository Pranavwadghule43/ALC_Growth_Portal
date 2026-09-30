"""Lean activity rows for list screens (queues, activity lists, dashboards, ALC detail).

A list request costs a fixed number of statements however many rows it returns:

1. the page query (``activity_list_query``) selects only the displayed columns — no
   ``Activity`` ORM objects are built, so the model's evidence / review / revision
   collections are never loaded;
2. one summary query (``activity_summaries``) computes evidence count, revision count,
   correction flag, latest resubmission and latest remark with correlated SQL subqueries,
   restricted to that page's activity ids.

Detail and review endpoints still load full activities (``ActivityOut``).
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Row, Select, desc, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ActivityStatus
from app.models import (
    ALC,
    Activity,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    Partner,
)
from app.schemas import ActivityListOut

# Columns of the page query that map one-to-one onto ``ActivityListOut``.
_BASE_FIELDS = (
    "id",
    "activity_number",
    "alc_id",
    "activity_type",
    "activity_date",
    "status",
    "submitted_at",
    "updated_at",
    "learners_reached",
    "leads_generated",
    "admissions_generated",
    "partner_name",
)


def activity_list_query(*extra_columns) -> Select:
    """``SELECT`` of the lean list columns (plus ``extra_columns``, e.g. labelled ALC / SBU
    columns) from ``activities`` outer-joined to its partner. Callers add joins, filters,
    ordering and pagination."""
    return (
        select(
            Activity.id,
            Activity.activity_number,
            Activity.alc_id,
            Activity.activity_type,
            Activity.activity_date,
            Activity.status,
            Activity.submitted_at,
            Activity.updated_at,
            Activity.learners_reached,
            Activity.leads_generated,
            Activity.admissions_generated,
            Partner.partner_name.label("partner_name"),
            *extra_columns,
        )
        .select_from(Activity)
        .outerjoin(Partner, Activity.partner_id == Partner.id)
    )


def _correlated(query: Select):
    return query.correlate(Activity).scalar_subquery()


async def activity_summaries(
    db: AsyncSession, activity_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Per-activity list summaries for ``activity_ids`` in one statement."""
    if not activity_ids:
        return {}
    evidence_count = _correlated(
        select(func.count(ActivityEvidence.id)).where(
            ActivityEvidence.activity_id == Activity.id, ActivityEvidence.is_active.is_(True)
        )
    )
    revision_count = _correlated(
        select(func.count(ActivityRevision.id)).where(ActivityRevision.activity_id == Activity.id)
    )
    latest_revision_at = _correlated(
        select(ActivityRevision.created_at)
        .where(ActivityRevision.activity_id == Activity.id)
        .order_by(desc(ActivityRevision.revision_number))
        .limit(1)
    )
    had_correction = (
        exists()
        .where(
            ActivityReview.activity_id == Activity.id,
            ActivityReview.new_status == ActivityStatus.CORRECTION_REQUIRED,
        )
        .correlate(Activity)
    )

    def latest_remarked(column):
        return _correlated(
            select(column)
            .where(
                ActivityReview.activity_id == Activity.id,
                ActivityReview.remark.is_not(None),
                ActivityReview.remark != "",
            )
            .order_by(desc(ActivityReview.reviewed_at), desc(ActivityReview.id))
            .limit(1)
        )

    rows = await db.execute(
        select(
            Activity.id,
            evidence_count.label("evidence_count"),
            revision_count.label("revision_count"),
            latest_revision_at.label("latest_revision_at"),
            had_correction.label("had_correction"),
            latest_remarked(ActivityReview.remark).label("last_remark"),
            latest_remarked(ActivityReview.action).label("last_remark_action"),
        ).where(Activity.id.in_(activity_ids))
    )
    return {
        row.id: {
            "evidence_count": row.evidence_count,
            "revision_count": row.revision_count,
            # A resubmission exists once an activity has a second submission snapshot.
            "resubmitted_at": row.latest_revision_at if row.revision_count > 1 else None,
            "had_correction": bool(row.had_correction),
            "last_remark": row.last_remark,
            "last_remark_action": row.last_remark_action,
        }
        for row in rows
    }


async def lean_activities(db: AsyncSession, rows: Sequence[Row]) -> list[ActivityListOut]:
    """``ActivityListOut`` for each page row (built with ``activity_list_query``), in order."""
    summaries = await activity_summaries(db, [row.id for row in rows])
    return [
        ActivityListOut(
            **{field: getattr(row, field) for field in _BASE_FIELDS}, **summaries[row.id]
        )
        for row in rows
    ]


async def activity_total(db: AsyncSession, filters: Sequence[ColumnElement[bool]]) -> int:
    """Exact number of activities matching ``filters``: a paginated list's ``total``.

    ``COUNT(*)`` over ``activities`` alone. Every list filter is an ``activities`` column or an
    ``IN`` subquery (hierarchy scope, ALC / partner search), so no join can add or drop rows,
    and ``id`` is the NOT NULL primary key: the result equals ``COUNT(activities.id)``. Not
    needing any column value lets PostgreSQL count from an index alone (e.g. an index-only scan
    of ``ix_activities_alc_status`` for a DCU / SBU scope) instead of visiting every row.
    """
    return await db.scalar(select(func.count()).select_from(Activity).where(*filters)) or 0


def alc_ids_matching(pattern: str):
    """``IN`` subquery of ALC ids whose code or name matches ``pattern`` (ILIKE). The ALC
    table is small, so resolving ids first lets list and count queries filter
    ``activities.alc_id`` without joining ``alcs``."""
    return select(ALC.id).where(or_(ALC.alc_code.ilike(pattern), ALC.alc_name.ilike(pattern)))


def partner_ids_matching(pattern: str):
    """``IN`` subquery of partner ids whose name matches ``pattern`` (ILIKE)."""
    return select(Partner.id).where(Partner.partner_name.ilike(pattern))


def activity_search(pattern: str, *, partners: bool) -> ColumnElement[bool]:
    """Activity number, ALC code / name and (optionally) partner-name search, expressed on
    ``activities`` columns only. Equivalent to ILIKE over the joined ALC / partner rows."""
    clauses = [
        Activity.activity_number.ilike(pattern),
        Activity.alc_id.in_(alc_ids_matching(pattern)),
    ]
    if partners:
        clauses.append(Activity.partner_id.in_(partner_ids_matching(pattern)))
    return or_(*clauses)
