"""Region Top 10 and the regional score directory, shared by every operational role.

Both are views of ONE regional ranking (``regional_ranking``): the directory lists every
eligible ALC with its regional rank and score, and the Top 10 is exactly its first 10 rows.
Eligibility, metrics, percentiles, weights, scores and ranks therefore have a single source.

Intentionally regional: ADMIN, DCU, SBU and ALC users all see the same ranking, so nothing
here uses ``app.services.scope``. Any DCU / SBU filtering of the directory happens after
scoring (in the UI) and never re-normalises a subset. It exposes only ALC identity (code,
name, SBU, DCU), four lifetime aggregates, an annual count and a score — never contact or
user details.

Metrics (lifetime, each activity's *current* status):

* ``verified_leads``      — SUM(activities.leads_generated)      WHERE status = VERIFIED
* ``verified_admissions`` — SUM(activities.admissions_generated) WHERE status = VERIFIED
* ``activities_done``     — COUNT(activities)                     WHERE status = VERIFIED
* ``partners``            — COUNT(partners) WHERE status = 'ACTIVE' (partner rows, never
  de-duplicated by name)

Eligibility: ``ALC.status = ACTIVE`` under an active SBU, active DCU and active RCU, a complete
ALC → SBU → DCU → RCU chain (unplaced and demo ALCs drop out of the inner joins), and at least
one non-zero metric.

Scoring: each metric becomes a regional percentile over *every* eligible ALC (before the Top
10 is taken)::

    percentile(v) = 100 * (eligible values strictly lower than v) / (eligible_count - 1)

When every eligible ALC has the same value for a metric (this includes a single eligible
ALC) the formula is undefined or meaningless, so the component is 100 for a non-zero value
and 0 for zero. The final score weights five components equally (20% each). Scores are
kept as exact fractions; only the response rounds them (2 decimals, half up). Ranking is the
exact score descending, then ``alc_code`` ascending.

Query shape: ONE statement. Verified activities and active partners are each pre-aggregated
by ``alc_id`` in their own subquery and only then joined to the ALC/SBU/DCU/RCU chain, so an
ALC's activities are never multiplied by its partners, and no activity or partner row is
loaded into Python. Normalisation runs in Python over the aggregated rows (one per eligible
ALC in the region).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ActivityStatus, AlcStatus
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    Activity,
    Partner,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
    YearlyActivitySubmission,
)
from app.services.growth_challenge import current_date
from app.services.yearly_activities import eligible_activity_ids

PERIOD = "LIFETIME"
TOP_N = 10
METRICS = ("verified_leads", "verified_admissions", "activities_done", "partners",
           "yearly_completed_count")
WEIGHTS = {metric: Fraction(1, 5) for metric in METRICS}


class RegionalRows(list):
    """Aggregate-only ranking plus the regional activity denominator, even if empty."""

    def __init__(self, rows=(), *, eligible_yearly_activities: int = 0):
        super().__init__(rows)
        self.eligible_yearly_activities = eligible_yearly_activities


def activity_year(year: int | None = None) -> int:
    return year if year is not None else current_date().year


def eligible_yearly_activities_query(year: int):
    """Stable participant rows, including inactive or not-yet-activated DCUs, all finalize.

    Never infer participation from voluntarily activated runs. Definitions without any
    participants fail closed. The DCU snapshot remains required after reorganization.
    """
    definition = YearlyActivityDefinition
    return (
        select(definition.id, definition.rcu_id)
        .where(definition.id.in_(eligible_activity_ids(year))).cte("eligible_yearly_activity")
    )


@dataclass
class LeaderboardRow:
    alc_id: uuid.UUID
    alc_code: str
    alc_name: str
    sbu_code: str
    sbu_name: str
    dcu_code: str
    dcu_name: str
    verified_leads: int
    verified_admissions: int
    activities_done: int
    partners: int
    yearly_completed_count: int = 0
    components: dict[str, Fraction] = field(default_factory=dict)
    score: Fraction = Fraction(0)


def regional_metrics_query(year: int | None = None):
    """One aggregate statement; a metadata-only row preserves empty-ranking counts."""
    eligible = eligible_yearly_activities_query(activity_year(year))
    completion = YearlyActivityCompletion
    submission = YearlyActivitySubmission
    yearly = (
        select(completion.alc_id, eligible.c.rcu_id,
               func.count(func.distinct(completion.yearly_activity_id))
               .label("yearly_completed_count"))
        .join(eligible, eligible.c.id == completion.yearly_activity_id)
        .join(submission, submission.participation_id == completion.id)
        .join(YearlyActivityDCURun,
              YearlyActivityDCURun.id == completion.yearly_activity_dcu_run_id)
        .where(submission.status == "VERIFIED", YearlyActivityDCURun.status == "FINALIZED")
        .group_by(completion.alc_id, eligible.c.rcu_id)
        .subquery("yearly_completion")
    )
    verified = (
        select(
            Activity.alc_id.label("alc_id"),
            func.coalesce(func.sum(Activity.leads_generated), 0).label("verified_leads"),
            func.coalesce(func.sum(Activity.admissions_generated), 0).label(
                "verified_admissions"
            ),
            func.count(Activity.id).label("activities_done"),
        )
        .where(Activity.status == ActivityStatus.VERIFIED)
        .group_by(Activity.alc_id)
        .subquery("verified_activity")
    )
    active_partners = (
        select(Partner.alc_id.label("alc_id"), func.count(Partner.id).label("partners"))
        .where(Partner.status == "ACTIVE")
        .group_by(Partner.alc_id)
        .subquery("active_partners")
    )
    leads = func.coalesce(verified.c.verified_leads, 0)
    admissions = func.coalesce(verified.c.verified_admissions, 0)
    done = func.coalesce(verified.c.activities_done, 0)
    partners = func.coalesce(active_partners.c.partners, 0)
    yearly_count = func.coalesce(yearly.c.yearly_completed_count, 0)
    metrics = (
        select(
            ALC.id.label("alc_id"),
            ALC.alc_code,
            ALC.alc_name,
            SBU.code.label("sbu_code"),
            SBU.name.label("sbu_name"),
            DCU.code.label("dcu_code"),
            DCU.name.label("dcu_name"),
            leads.label("verified_leads"),
            admissions.label("verified_admissions"),
            done.label("activities_done"),
            partners.label("partners"),
            yearly_count.label("yearly_completed_count"),
        )
        # Inner joins: only ALCs with a complete ALC -> SBU -> DCU -> RCU chain.
        .join(SBU, ALC.sbu_id == SBU.id)
        .join(DCU, SBU.dcu_id == DCU.id)
        .join(RCU, DCU.rcu_id == RCU.id)
        .outerjoin(verified, verified.c.alc_id == ALC.id)
        .outerjoin(active_partners, active_partners.c.alc_id == ALC.id)
        .outerjoin(yearly, (yearly.c.alc_id == ALC.id) & (yearly.c.rcu_id == RCU.id))
        .where(
            # Every level of the chain must be active.
            ALC.status == AlcStatus.ACTIVE,
            SBU.is_active.is_(True),
            DCU.is_active.is_(True),
            RCU.is_active.is_(True),
            or_(leads > 0, admissions > 0, done > 0, partners > 0, yearly_count > 0),
        )
        .subquery("regional_metrics")
    )
    counts = (
        select(func.count(eligible.c.id).label("eligible_yearly_activities"))
        .join(RCU, RCU.id == eligible.c.rcu_id).where(RCU.is_active.is_(True))
        .subquery("yearly_population")
    )
    return (
        select(*metrics.c, counts.c.eligible_yearly_activities)
        .select_from(counts.outerjoin(metrics, true()))
        .order_by(metrics.c.alc_code)
    )


def percentile_scores(values: list[int]) -> list[Fraction]:
    """Regional percentile of each value among ``values`` as exact fractions in 0..100.

    Equal values share one precomputed Fraction. This preserves the established strictly-
    lower percentile definition while avoiding a bisect and Fraction construction for every
    ALC when many centres share the same metric value.
    """
    if not values:
        return []
    ordered = sorted(values)
    if ordered[0] == ordered[-1]:
        component = Fraction(100) if ordered[0] > 0 else Fraction(0)
        return [component] * len(values)
    denominator = len(values) - 1
    by_value: dict[int, Fraction] = {}
    lower = 0
    index = 0
    while index < len(ordered):
        value = ordered[index]
        by_value[value] = Fraction(100 * lower, denominator)
        next_index = index + 1
        while next_index < len(ordered) and ordered[next_index] == value:
            next_index += 1
        lower += next_index - index
        index = next_index
    return [by_value[value] for value in values]


def score_rows(rows: list[LeaderboardRow]) -> list[LeaderboardRow]:
    """Fill each row's component and final scores, normalised over every row given."""
    for row in rows:
        row.components.clear()
    for metric in METRICS:
        values = [getattr(row, metric) for row in rows]
        for row, component in zip(rows, percentile_scores(values), strict=True):
            row.components[metric] = component
    equal_weight = WEIGHTS[METRICS[0]]
    for row in rows:
        row.score = sum((row.components[m] for m in METRICS), Fraction(0)) * equal_weight
    return rows


def rank_rows(rows: list[LeaderboardRow], limit: int | None = TOP_N) -> list[LeaderboardRow]:
    """Exact score descending, then ALC code ascending; the first ``limit`` rows (all when
    ``limit`` is None)."""
    return RegionalRows(
        sorted(rows, key=lambda r: (-r.score, r.alc_code))[:limit],
        eligible_yearly_activities=getattr(rows, "eligible_yearly_activities", 0),
    )


def display_score(score: Fraction) -> float:
    """Score rounded to 2 decimals (half up) for display."""
    exact = Decimal(score.numerator) / Decimal(score.denominator)
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def eligible_rows(db: AsyncSession, year: int | None = None) -> list[LeaderboardRow]:
    result = (await db.execute(regional_metrics_query(year))).mappings().all()
    return RegionalRows([
        LeaderboardRow(**{key: (int(value) if key in METRICS else value)
                          for key, value in row.items() if key != "eligible_yearly_activities"})
        for row in result if row["alc_id"] is not None
    ], eligible_yearly_activities=result[0]["eligible_yearly_activities"] if result else 0)


async def regional_ranking(db: AsyncSession, year: int | None = None) -> list[LeaderboardRow]:
    """Every eligible ALC, scored against the whole eligible region and in regional rank
    order. The single source for both the Top 10 and the score directory."""
    return rank_rows(score_rows(await eligible_rows(db, year)), limit=None)


def leaderboard_payload(ranked: list[LeaderboardRow], year: int | None = None) -> dict:
    """Response body: ``rank`` is the row's position in the complete regional ranking."""
    return {
        "period": PERIOD,
        "activity_year": activity_year(year),
        "eligible_yearly_activities": getattr(ranked, "eligible_yearly_activities", 0),
        "generated_at": datetime.now(timezone.utc),
        "items": [
            {
                "rank": position,
                "alc_id": row.alc_id,
                "alc_code": row.alc_code,
                "alc_name": row.alc_name,
                "sbu": {"code": row.sbu_code, "name": row.sbu_name},
                "dcu": {"code": row.dcu_code, "name": row.dcu_name},
                "verified_leads": row.verified_leads,
                "verified_admissions": row.verified_admissions,
                "activities_done": row.activities_done,
                "partners": row.partners,
                "yearly_completed_count": row.yearly_completed_count,
                "score": display_score(row.score),
            }
            for position, row in enumerate(ranked, 1)
        ],
    }


async def region_top10(db: AsyncSession, year: int | None = None) -> dict:
    """The first 10 rows of the regional ranking."""
    ranked = await regional_ranking(db, year)
    return leaderboard_payload(RegionalRows(
        ranked[:TOP_N], eligible_yearly_activities=ranked.eligible_yearly_activities
    ), year)


async def region_scores(db: AsyncSession, year: int | None = None) -> dict:
    """Every eligible ALC with its regional rank and score (same values as the Top 10)."""
    return leaderboard_payload(await regional_ranking(db, year), year)
