"""Region Top 10: one regional ALC leaderboard shared by every operational role.

Intentionally regional: ADMIN, DCU, SBU and ALC users all see the same ranking, so nothing
here uses ``app.services.scope``. It exposes only ALC identity (code, name, SBU, DCU), four
lifetime aggregates and a score — never activity, partner, contact or user details.

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
and 0 for zero. The final score weights the four components equally (25% each). Scores are
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
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ActivityStatus, AlcStatus
from app.models import ALC, DCU, RCU, SBU, Activity, Partner

PERIOD = "LIFETIME"
TOP_N = 10
METRICS = ("verified_leads", "verified_admissions", "activities_done", "partners")
WEIGHTS = {metric: Fraction(1, 4) for metric in METRICS}


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
    components: dict[str, Fraction] = field(default_factory=dict)
    score: Fraction = Fraction(0)


def regional_metrics_query():
    """The single aggregate statement: one row per eligible ALC with its four metrics."""
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
    return (
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
        )
        # Inner joins: only ALCs with a complete ALC -> SBU -> DCU -> RCU chain.
        .join(SBU, ALC.sbu_id == SBU.id)
        .join(DCU, SBU.dcu_id == DCU.id)
        .join(RCU, DCU.rcu_id == RCU.id)
        .outerjoin(verified, verified.c.alc_id == ALC.id)
        .outerjoin(active_partners, active_partners.c.alc_id == ALC.id)
        .where(
            # Every level of the chain must be active.
            ALC.status == AlcStatus.ACTIVE,
            SBU.is_active.is_(True),
            DCU.is_active.is_(True),
            RCU.is_active.is_(True),
            or_(leads > 0, admissions > 0, done > 0, partners > 0),
        )
        .order_by(ALC.alc_code)
    )


def percentile_scores(values: list[int]) -> list[Fraction]:
    """Regional percentile of each value among ``values`` (all eligible ALCs), as exact
    fractions in 0..100. Equal values get equal scores."""
    if not values:
        return []
    ordered = sorted(values)
    if ordered[0] == ordered[-1]:
        # Every eligible ALC has the same value (or there is only one eligible ALC).
        return [Fraction(100) if value > 0 else Fraction(0) for value in values]
    denominator = len(values) - 1
    return [Fraction(100 * bisect_left(ordered, value), denominator) for value in values]


def score_rows(rows: list[LeaderboardRow]) -> list[LeaderboardRow]:
    """Fill each row's component and final scores, normalised over every row given."""
    for metric in METRICS:
        for row, component in zip(
            rows, percentile_scores([getattr(r, metric) for r in rows]), strict=True
        ):
            row.components[metric] = component
    for row in rows:
        row.score = sum((row.components[m] * WEIGHTS[m] for m in METRICS), Fraction(0))
    return rows


def rank_rows(rows: list[LeaderboardRow], limit: int = TOP_N) -> list[LeaderboardRow]:
    """Exact score descending, then ALC code ascending; the first ``limit`` rows."""
    return sorted(rows, key=lambda r: (-r.score, r.alc_code))[:limit]


def display_score(score: Fraction) -> float:
    """Score rounded to 2 decimals (half up) for display."""
    exact = Decimal(score.numerator) / Decimal(score.denominator)
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def eligible_rows(db: AsyncSession) -> list[LeaderboardRow]:
    result = await db.execute(regional_metrics_query())
    return [
        LeaderboardRow(**{key: (int(value) if key in METRICS else value)
                          for key, value in row.items()})
        for row in result.mappings()
    ]


async def region_top10(db: AsyncSession) -> dict:
    ranked = rank_rows(score_rows(await eligible_rows(db)))
    return {
        "period": PERIOD,
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
                "score": display_score(row.score),
            }
            for position, row in enumerate(ranked, 1)
        ],
    }
