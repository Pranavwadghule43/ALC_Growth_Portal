"""Growth Challenge period (variable duration, globally configured).

A Growth Challenge runs for a configured period of any length (15, 30, 45, 60, 90 ... days),
never a fixed 30 days. The duration is always derived from the two inclusive dates and is
never stored.

Where the period comes from, for one ALC (see ``resolve``):

1. ``challenge_progress``: an explicit per-ALC override (its own dates and targets).
2. ``growth_challenges``: the global challenge an Admin configured. It applies to every ALC,
   including ALCs created later; no per-ALC rows are written for it.
3. Neither: the challenge is ``NOT_CONFIGURED``. There is no rolling 30-day fallback.

Within one source the period in effect today is: the one containing today, otherwise the next
upcoming one, otherwise the most recently completed one. Across the two sources the same
order applies first (ACTIVE, then UPCOMING, then COMPLETED), and the per-ALC override wins
when both offer a period of the same status. So an expired override never hides a running
global challenge, and a running override always beats the global challenge.

Day counting (both dates inclusive), for a period ``start .. end``:

    total_days      = (end - start) + 1
    status          = UPCOMING before start, ACTIVE from start through end, COMPLETED after
    elapsed_days    = 0 before the start, ``(today - start) + 1`` while active, total after
    remaining_days  = ``end - today`` while active (0 on the last day), total before, 0 after
    activities count from ``start`` to ``min(end, today)``: a completed challenge is frozen
    at its end date and an upcoming one counts nothing

"Today" is the calendar date in the application time zone, Asia/Kolkata, whatever time zone
the server's operating system uses (``current_date``).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChallengeProgress, GrowthChallenge

APP_TIMEZONE_NAME = "Asia/Kolkata"
DEFAULT_TARGETS = {"prospects": 40, "meetings": 20, "pilots": 10, "partnerships": 5}
TARGET_KEYS = tuple(DEFAULT_TARGETS)

NOT_CONFIGURED = "NOT_CONFIGURED"  # no per-ALC override and no global challenge
UPCOMING = "UPCOMING"  # starts after today
ACTIVE = "ACTIVE"  # today is inside the period (first and last day included)
COMPLETED = "COMPLETED"  # ended before today
_STATUS_ORDER = {ACTIVE: 0, UPCOMING: 1, COMPLETED: 2}

SOURCE_ALC = "ALC_OVERRIDE"  # challenge_progress row for this ALC
SOURCE_GLOBAL = "GLOBAL"  # growth_challenges row


# --------------------------------------------------------------------------- #
# Today, in the application time zone
# --------------------------------------------------------------------------- #
def app_timezone() -> tzinfo:
    """Asia/Kolkata. Where the IANA database is unavailable (e.g. Windows without the
    ``tzdata`` package) the fixed +05:30 offset is used: India has no daylight saving time,
    so the two are equivalent for the current date."""
    try:
        return ZoneInfo(APP_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=5, minutes=30), APP_TIMEZONE_NAME)


def current_date(now: datetime | None = None) -> date:
    """Today's calendar date in Asia/Kolkata, independent of the server's OS time zone.

    ``now`` is for tests; a naive value is taken as UTC."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(app_timezone()).date()


# --------------------------------------------------------------------------- #
# Period arithmetic
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChallengePeriod:
    start: date
    end: date

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days + 1

    def status(self, today: date) -> str:
        if today < self.start:
            return UPCOMING
        if today > self.end:
            return COMPLETED
        return ACTIVE

    def elapsed_days(self, today: date) -> int:
        if today < self.start:
            return 0
        return min((today - self.start).days + 1, self.total_days)

    def remaining_days(self, today: date) -> int:
        if today < self.start:
            return self.total_days
        return max((self.end - today).days, 0)

    def days_until_start(self, today: date) -> int:
        return max((self.start - today).days, 0)

    def time_progress_percent(self, today: date) -> float:
        return round(self.elapsed_days(today) / self.total_days * 100, 1)

    def counting_end(self, today: date) -> date:
        """Last activity date that counts: nothing after the period, nothing in the future."""
        return min(self.end, today)

    def as_dict(self, today: date) -> dict:
        return {
            "period_start": self.start,
            "period_end": self.end,
            "configured": True,
            "status": self.status(today),
            "total_days": self.total_days,
            "elapsed_days": self.elapsed_days(today),
            "remaining_days": self.remaining_days(today),
            "days_until_start": self.days_until_start(today),
            "time_progress_percent": self.time_progress_percent(today),
        }


NOT_CONFIGURED_PERIOD = {
    "period_start": None,
    "period_end": None,
    "configured": False,
    "status": NOT_CONFIGURED,
    "total_days": 0,
    "elapsed_days": 0,
    "remaining_days": 0,
    "days_until_start": 0,
    "time_progress_percent": 0.0,
}


@dataclass(frozen=True)
class Resolution:
    """The Growth Challenge that applies to one ALC today (``period`` None = not configured)."""

    period: ChallengePeriod | None = None
    targets: dict[str, int] = field(default_factory=dict)
    source: str | None = None
    name: str | None = None
    challenge_id: uuid.UUID | None = None

    @property
    def configured(self) -> bool:
        return self.period is not None

    def as_dict(self, today: date) -> dict:
        base = self.period.as_dict(today) if self.period else dict(NOT_CONFIGURED_PERIOD)
        return {
            **base,
            "name": self.name,
            "period_source": self.source,
            "challenge_id": self.challenge_id,
            "targets": dict(self.targets),
        }


# --------------------------------------------------------------------------- #
# Which period applies
# --------------------------------------------------------------------------- #
def _select(items: Sequence, today: date, start_of, end_of):
    """Active (latest start if several), else next upcoming, else most recently completed.
    Items whose end is before their start are ignored."""
    valid = [i for i in items if end_of(i) >= start_of(i)]
    active = [i for i in valid if start_of(i) <= today <= end_of(i)]
    if active:
        return max(active, key=lambda i: (start_of(i), end_of(i)))
    upcoming = [i for i in valid if start_of(i) > today]
    if upcoming:
        return min(upcoming, key=lambda i: (start_of(i), end_of(i)))
    if valid:
        return max(valid, key=lambda i: (end_of(i), start_of(i)))
    return None


def select_row(rows: Iterable[ChallengeProgress], today: date) -> ChallengeProgress | None:
    """The per-ALC override in effect today, from one ALC's ``challenge_progress`` rows."""
    return _select(
        list(rows), today, lambda r: r.challenge_period_start, lambda r: r.challenge_period_end
    )


def select_challenge(
    challenges: Iterable[GrowthChallenge], today: date
) -> GrowthChallenge | None:
    """The global Growth Challenge in effect today."""
    return _select(list(challenges), today, lambda c: c.start_date, lambda c: c.end_date)


def targets_of(row: ChallengeProgress | GrowthChallenge) -> dict[str, int]:
    return {key: getattr(row, f"{key}_target") for key in TARGET_KEYS}


def resolve(
    alc_rows: Iterable[ChallengeProgress],
    challenges: Iterable[GrowthChallenge],
    today: date,
) -> Resolution:
    """The challenge for one ALC: per-ALC override, else global challenge, else none."""
    candidates: list[tuple[int, int, Resolution]] = []
    override = select_row(alc_rows, today)
    if override is not None:
        period = ChallengePeriod(override.challenge_period_start, override.challenge_period_end)
        candidates.append(
            (_STATUS_ORDER[period.status(today)], 0,
             Resolution(period, targets_of(override), SOURCE_ALC))
        )
    challenge = select_challenge(challenges, today)
    if challenge is not None:
        period = ChallengePeriod(challenge.start_date, challenge.end_date)
        candidates.append(
            (_STATUS_ORDER[period.status(today)], 1,
             Resolution(period, targets_of(challenge), SOURCE_GLOBAL, challenge.name,
                        challenge.id))
        )
    if not candidates:
        return Resolution()
    return min(candidates, key=lambda c: (c[0], c[1]))[2]


def group_rows_by_alc(
    rows: Iterable[ChallengeProgress],
) -> dict[uuid.UUID, list[ChallengeProgress]]:
    grouped: dict[uuid.UUID, list[ChallengeProgress]] = {}
    for row in rows:
        grouped.setdefault(row.alc_id, []).append(row)
    return grouped


# --------------------------------------------------------------------------- #
# Global configuration
# --------------------------------------------------------------------------- #
async def all_challenges(db: AsyncSession) -> list[GrowthChallenge]:
    """Every configured global challenge, newest period first (a small table)."""
    return list(
        await db.scalars(
            select(GrowthChallenge).order_by(
                GrowthChallenge.start_date.desc(), GrowthChallenge.created_at.desc()
            )
        )
    )


def validate_period(start: date, end: date) -> str | None:
    """Why ``start .. end`` is not an acceptable challenge period, or None when it is."""
    if end < start:
        return "End date must be on or after the start date"
    return None  # any length is allowed: there is no maximum duration


async def overlapping_challenge(
    db: AsyncSession, start: date, end: date, exclude_id: uuid.UUID | None = None
) -> GrowthChallenge | None:
    """A configured challenge sharing at least one day with ``start .. end`` (inclusive)."""
    query = select(GrowthChallenge).where(
        GrowthChallenge.start_date <= end, GrowthChallenge.end_date >= start
    )
    if exclude_id is not None:
        query = query.where(GrowthChallenge.id != exclude_id)
    return await db.scalar(query.order_by(GrowthChallenge.start_date).limit(1))


def challenge_out(challenge: GrowthChallenge, today: date) -> dict:
    """A configured global challenge as the Admin API returns it."""
    period = ChallengePeriod(challenge.start_date, challenge.end_date)
    return {
        "id": challenge.id,
        "name": challenge.name,
        "start_date": challenge.start_date,
        "end_date": challenge.end_date,
        "targets": targets_of(challenge),
        "status": period.status(today),
        "total_days": period.total_days,
        "elapsed_days": period.elapsed_days(today),
        "remaining_days": period.remaining_days(today),
        "days_until_start": period.days_until_start(today),
        "time_progress_percent": period.time_progress_percent(today),
        "created_by": challenge.created_by,
        "updated_by": challenge.updated_by,
        "created_at": challenge.created_at,
        "updated_at": challenge.updated_at,
    }
