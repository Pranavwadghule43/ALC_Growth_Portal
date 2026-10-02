"""Growth Challenge: variable duration, period resolution and progress.

The challenge period is a configured start / end date pair; the duration is derived from it
(15, 30, 45, 60, 90 ... days), never assumed to be 30.

* Period arithmetic: total / elapsed / remaining days, status, boundaries, leap and month ends.
* Which period applies: per-ALC override, then the global challenge, then NOT_CONFIGURED
  (there is no rolling 30-day fallback).
* ALC endpoint and dashboard: counts use the configured date range.
* Admin overview: every ALC is measured over the period that applies to it.

"Today" is pinned (``growth_challenge.current_date``), so no test depends on the real date.
Configuration endpoints, time zone and audit: ``test_growth_challenge_config.py``.
Migration: ``test_growth_challenge_migration.py``.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.auth import hash_password
from app.enums import ActivityStatus, Role
from app.models import ALC, Activity, ChallengeProgress, GrowthChallenge, Partner, User
from app.services import growth_challenge
from app.services.growth_challenge import (
    ACTIVE,
    COMPLETED,
    DEFAULT_TARGETS,
    NOT_CONFIGURED,
    SOURCE_ALC,
    SOURCE_GLOBAL,
    UPCOMING,
    ChallengePeriod,
    resolve,
    select_challenge,
    select_row,
)
from tests.conftest import login

TODAY = date(2026, 3, 10)
DURATIONS = [15, 30, 45, 60, 90]
ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
ALC_B = ("00010002", "StrongAlcPassB!", "ALC")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
PERIOD_KEYS = {
    "period_start", "period_end", "configured", "status", "total_days", "elapsed_days",
    "remaining_days", "days_until_start", "time_progress_percent", "name", "period_source",
    "challenge_id", "targets",
}
ZERO = {"prospects": 0, "meetings": 0, "pilots": 0, "partnerships": 0}


def period_of(days: int, start: date = TODAY) -> ChallengePeriod:
    return ChallengePeriod(start, start + timedelta(days=days - 1))


def row(start: date, end: date, alc_id=None, **targets) -> ChallengeProgress:
    """A per-ALC override (``challenge_progress``)."""
    return ChallengeProgress(
        alc_id=alc_id or uuid.uuid4(), challenge_period_start=start, challenge_period_end=end,
        prospects_target=targets.get("prospects", 40), meetings_target=targets.get("meetings", 20),
        pilots_target=targets.get("pilots", 10),
        partnerships_target=targets.get("partnerships", 5),
    )


def challenge(start: date, end: date, name="Growth Challenge", **targets) -> GrowthChallenge:
    """A global challenge (``growth_challenges``)."""
    return GrowthChallenge(
        name=name, start_date=start, end_date=end,
        prospects_target=targets.get("prospects", 40), meetings_target=targets.get("meetings", 20),
        pilots_target=targets.get("pilots", 10),
        partnerships_target=targets.get("partnerships", 5),
    )


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(growth_challenge, "current_date", lambda: TODAY)
    return TODAY


async def as_user(client, who):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await login(client, *who)).status_code == 200


def verified(alc, number, on: date, kind="Awareness Session", leads=0, partner=None):
    return Activity(
        activity_number=number, alc_id=alc.id, activity_type=kind, ecosystem="College",
        activity_date=on, location="Nashik", leads_generated=leads,
        description="Structured outreach session.", outcome="Done",
        status=ActivityStatus.VERIFIED, partner_id=partner.id if partner else None,
    )


# --------------------------------------------------------------------------- #
# Period arithmetic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("days", DURATIONS)
def test_start_end_and_total_days(days):
    period = period_of(days)
    assert period.start == TODAY
    assert period.end == TODAY + timedelta(days=days - 1)
    assert period.total_days == days


@pytest.mark.parametrize("days", DURATIONS)
def test_first_day_is_day_one(days):
    period = period_of(days)
    assert period.status(period.start) == ACTIVE
    assert period.elapsed_days(period.start) == 1
    assert period.remaining_days(period.start) == days - 1
    assert period.time_progress_percent(period.start) == round(100 / days, 1)


@pytest.mark.parametrize("days", DURATIONS)
def test_last_day_is_still_active_and_complete_the_day_after(days):
    period = period_of(days)
    assert period.status(period.end) == ACTIVE  # boundary day belongs to the challenge
    assert period.elapsed_days(period.end) == days
    assert period.remaining_days(period.end) == 0
    assert period.time_progress_percent(period.end) == 100.0
    after = period.end + timedelta(days=1)
    assert period.status(after) == COMPLETED
    assert (period.elapsed_days(after), period.remaining_days(after)) == (days, 0)
    assert period.time_progress_percent(after) == 100.0
    assert period.counting_end(after) == period.end  # nothing after the end counts


@pytest.mark.parametrize("days", DURATIONS)
def test_before_start_is_upcoming(days):
    period = period_of(days)
    before = period.start - timedelta(days=4)
    assert period.status(before) == UPCOMING
    assert period.elapsed_days(before) == 0
    assert period.remaining_days(before) == days
    assert period.days_until_start(before) == 4
    assert period.time_progress_percent(before) == 0.0
    assert period.days_until_start(period.start) == 0


@pytest.mark.parametrize("days", DURATIONS)
def test_elapsed_plus_remaining_is_total_on_every_day(days):
    period = period_of(days)
    for offset in range(days):
        day = period.start + timedelta(days=offset)
        assert period.elapsed_days(day) == offset + 1
        assert period.remaining_days(day) == (period.end - day).days
        assert period.elapsed_days(day) + period.remaining_days(day) == days


def test_progress_is_elapsed_over_configured_total_not_thirty():
    day_15 = TODAY + timedelta(days=14)
    assert period_of(15).time_progress_percent(day_15) == 100.0
    assert period_of(30).time_progress_percent(day_15) == 50.0
    assert period_of(45).time_progress_percent(day_15) == 33.3
    assert period_of(60).time_progress_percent(day_15) == 25.0


def test_leap_day_and_month_and_year_boundaries():
    leap = ChallengePeriod(date(2028, 2, 15), date(2028, 3, 15))  # 2028 has 29 February
    assert leap.total_days == 30
    assert leap.elapsed_days(date(2028, 2, 29)) == 15
    assert leap.remaining_days(date(2028, 2, 29)) == 15
    non_leap = ChallengePeriod(date(2027, 2, 15), date(2027, 3, 15))
    assert non_leap.total_days == 29
    year_end = ChallengePeriod(date(2026, 12, 18), date(2027, 2, 15))  # 60 days over New Year
    assert year_end.total_days == 60
    assert year_end.elapsed_days(date(2027, 1, 1)) == 15
    assert year_end.remaining_days(date(2027, 1, 1)) == 45
    assert ChallengePeriod(TODAY, TODAY).total_days == 1  # one-day period


def test_no_configuration_means_not_configured_not_rolling_30_days():
    resolution = resolve([], [], TODAY)
    assert (resolution.configured, resolution.period, resolution.source) == (False, None, None)
    body = resolution.as_dict(TODAY)
    assert (body["status"], body["configured"]) == (NOT_CONFIGURED, False)
    assert (body["period_start"], body["period_end"]) == (None, None)
    assert (body["total_days"], body["elapsed_days"], body["remaining_days"]) == (0, 0, 0)
    assert (body["days_until_start"], body["time_progress_percent"]) == (0, 0.0)
    assert (body["targets"], body["name"], body["period_source"]) == ({}, None, None)
    assert set(body) == PERIOD_KEYS
    # The old rolling-window fallback no longer exists anywhere in the service.
    for gone in ("default_period", "DEFAULT_CHALLENGE_DAYS", "ROLLING"):
        assert not hasattr(growth_challenge, gone)


# --------------------------------------------------------------------------- #
# Which configured period applies
# --------------------------------------------------------------------------- #
def test_period_selection_prefers_active_then_upcoming_then_completed():
    past = row(TODAY - timedelta(days=80), TODAY - timedelta(days=51))
    older = row(TODAY - timedelta(days=200), TODAY - timedelta(days=150))
    current = row(TODAY - timedelta(days=9), TODAY + timedelta(days=35))
    soon = row(TODAY + timedelta(days=40), TODAY + timedelta(days=99))
    later = row(TODAY + timedelta(days=120), TODAY + timedelta(days=150))
    assert select_row([past, older, current, soon, later], TODAY) is current
    assert select_row([past, older, soon, later], TODAY) is soon
    assert select_row([past, older], TODAY) is past
    assert select_row([], TODAY) is None
    overlapping = row(TODAY - timedelta(days=2), TODAY + timedelta(days=12))
    assert select_row([current, overlapping], TODAY) is overlapping  # latest start wins


def test_global_challenge_selection_uses_the_same_order():
    past = challenge(TODAY - timedelta(days=80), TODAY - timedelta(days=51), "Past")
    current = challenge(TODAY - timedelta(days=9), TODAY + timedelta(days=35), "Current")
    soon = challenge(TODAY + timedelta(days=40), TODAY + timedelta(days=99), "Soon")
    assert select_challenge([past, current, soon], TODAY) is current
    assert select_challenge([past, soon], TODAY) is soon
    assert select_challenge([past], TODAY) is past
    assert select_challenge([], TODAY) is None


def test_invalid_rows_are_ignored_and_targets_follow_the_period():
    backwards = row(TODAY + timedelta(days=5), TODAY - timedelta(days=5))
    assert select_row([backwards], TODAY) is None
    assert resolve([backwards], [], TODAY).configured is False
    custom = row(TODAY, TODAY + timedelta(days=44), prospects=90, meetings=45, pilots=20,
                 partnerships=9)
    resolution = resolve([backwards, custom], [], TODAY)
    assert (resolution.period.total_days, resolution.source) == (45, SOURCE_ALC)
    assert resolution.targets == {"prospects": 90, "meetings": 45, "pilots": 20, "partnerships": 9}


def test_source_priority_override_then_global_then_none():
    active_global = challenge(TODAY - timedelta(days=4), TODAY + timedelta(days=25), "Global",
                              prospects=70)
    active_override = row(TODAY - timedelta(days=1), TODAY + timedelta(days=13), prospects=11)
    # 1. Both active: the per-ALC override wins.
    both = resolve([active_override], [active_global], TODAY)
    assert (both.source, both.period.total_days, both.targets["prospects"]) == (SOURCE_ALC, 15, 11)
    assert both.name is None
    # 2. No override: the global challenge.
    only_global = resolve([], [active_global], TODAY)
    assert (only_global.source, only_global.name) == (SOURCE_GLOBAL, "Global")
    assert (only_global.period.total_days, only_global.targets["prospects"]) == (30, 70)
    # 3. Neither: not configured.
    assert resolve([], [], TODAY).configured is False


def test_an_expired_or_future_override_does_not_hide_a_running_global_challenge():
    active_global = challenge(TODAY - timedelta(days=4), TODAY + timedelta(days=25))
    expired = row(TODAY - timedelta(days=90), TODAY - timedelta(days=61))
    future = row(TODAY + timedelta(days=60), TODAY + timedelta(days=89))
    assert resolve([expired], [active_global], TODAY).source == SOURCE_GLOBAL
    assert resolve([future], [active_global], TODAY).source == SOURCE_GLOBAL
    # Same status on both sides -> the override; a better status always wins.
    done_global = challenge(TODAY - timedelta(days=40), TODAY - timedelta(days=11))
    next_global = challenge(TODAY + timedelta(days=5), TODAY + timedelta(days=34))
    assert resolve([expired], [done_global], TODAY).source == SOURCE_ALC
    assert resolve([future], [next_global], TODAY).source == SOURCE_ALC
    assert resolve([expired], [next_global], TODAY).source == SOURCE_GLOBAL  # upcoming > done
    assert resolve([future], [done_global], TODAY).source == SOURCE_ALC


# --------------------------------------------------------------------------- #
# ALC endpoint
# --------------------------------------------------------------------------- #
async def test_alc_without_any_configuration_sees_not_configured(client, session, seeded, today):
    alc = seeded["alc_a"]
    session.add(verified(alc, "N-1", today, "School Meeting", leads=4))  # would count in 30 days
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["status"], body["configured"]) == (NOT_CONFIGURED, False)
    assert (body["period_start"], body["period_end"]) == (None, None)
    assert (body["total_days"], body["elapsed_days"], body["remaining_days"]) == (0, 0, 0)
    assert (body["targets"], body["achieved"]) == ({}, {})  # nothing is pretended
    assert body["source"] == "verified activities; partnerships require a linked partner"
    assert PERIOD_KEYS <= set(body)
    assert (await client.get("/api/portal/dashboard")).json()["challenge"] == body


@pytest.mark.parametrize("days", DURATIONS)
async def test_alc_uses_the_global_challenge_duration(client, session, seeded, today, days):
    start = today - timedelta(days=9)  # today is day 10
    end = start + timedelta(days=days - 1)
    session.add(challenge(start, end, f"{days}-day run", prospects=days))
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["period_start"], body["period_end"]) == (str(start), str(end))
    assert (body["status"], body["configured"]) == (ACTIVE, True)
    assert (body["name"], body["period_source"]) == (f"{days}-day run", SOURCE_GLOBAL)
    assert body["total_days"] == days
    assert body["elapsed_days"] == 10
    assert body["remaining_days"] == days - 10
    assert body["days_until_start"] == 0
    assert body["time_progress_percent"] == round(10 / days * 100, 1)
    assert body["targets"]["prospects"] == days
    assert body["achieved"] == ZERO


async def test_counts_follow_the_configured_range_not_30_days(client, session, seeded, today):
    alc = seeded["alc_a"]
    partner = Partner(alc_id=alc.id, partner_name="Nashik College", partner_type="College",
                      ecosystem="College")
    session.add(partner)
    await session.flush()
    session.add_all([
        verified(alc, "C-1", today - timedelta(days=5), "School Meeting", leads=3),
        verified(alc, "C-2", today - timedelta(days=20), "Pilot Batch", leads=5),
        verified(alc, "C-3", today - timedelta(days=45), "Partnership MoU", leads=7,
                 partner=partner),
        verified(alc, "C-4", today - timedelta(days=70), "School Meeting", leads=100),
    ])
    config = challenge(today - timedelta(days=59), today)  # 60 days ending today
    session.add(config)
    await session.commit()
    await as_user(client, ALC_A)
    sixty = (await client.get("/api/portal/challenge")).json()
    assert sixty["total_days"] == 60
    # 45 days ago is outside any 30-day window but inside this 60-day challenge.
    assert sixty["achieved"] == {"prospects": 15, "meetings": 1, "pilots": 1, "partnerships": 1}

    config.start_date = today - timedelta(days=14)  # now 15 days ending today
    await session.commit()
    fifteen = (await client.get("/api/portal/challenge")).json()
    assert fifteen["total_days"] == 15
    # 20 days ago is inside a 30-day window but outside this 15-day challenge.
    assert fifteen["achieved"] == {"prospects": 3, "meetings": 1, "pilots": 0, "partnerships": 0}


async def test_boundary_days_are_inclusive(client, session, seeded, today):
    alc = seeded["alc_a"]
    start, end = today - timedelta(days=44), today  # 45 days, today is the last day
    session.add(challenge(start, end))
    session.add_all([
        verified(alc, "B-1", start, "School Meeting", leads=1),  # first day
        verified(alc, "B-2", end, "School Meeting", leads=2),  # last day
        verified(alc, "B-3", start - timedelta(days=1), "School Meeting", leads=40),  # before
    ])
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["status"], body["elapsed_days"], body["remaining_days"]) == (ACTIVE, 45, 0)
    assert body["achieved"]["prospects"] == 3 and body["achieved"]["meetings"] == 2


async def test_alc_sees_day_one_on_the_first_day(client, session, seeded, today):
    session.add(challenge(today, today + timedelta(days=29)))
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["status"], body["elapsed_days"], body["remaining_days"]) == (ACTIVE, 1, 29)


async def test_completed_challenge_is_frozen_at_its_end_date(client, session, seeded, today):
    alc = seeded["alc_a"]
    start, end = today - timedelta(days=40), today - timedelta(days=11)  # 30 days, ended
    session.add(challenge(start, end))
    session.add_all([
        verified(alc, "D-1", end, "Pilot Batch", leads=8),
        verified(alc, "D-2", end + timedelta(days=1), "Pilot Batch", leads=30),  # after the end
        verified(alc, "D-3", today, "Pilot Batch", leads=30),
    ])
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["status"], body["total_days"]) == (COMPLETED, 30)
    assert (body["elapsed_days"], body["remaining_days"]) == (30, 0)
    assert body["time_progress_percent"] == 100.0
    assert (body["period_start"], body["period_end"]) == (str(start), str(end))
    assert body["achieved"] == {"prospects": 8, "meetings": 0, "pilots": 1, "partnerships": 0}


async def test_upcoming_challenge_counts_nothing_not_even_future_activity(
    client, session, seeded, today
):
    alc = seeded["alc_a"]
    start = today + timedelta(days=7)
    session.add(challenge(start, start + timedelta(days=89)))  # 90 days
    session.add_all([
        verified(alc, "U-1", today, "Pilot Batch", leads=9),  # before the start
        verified(alc, "U-2", start + timedelta(days=2), "Pilot Batch", leads=5),  # future-dated
    ])
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["status"], body["total_days"]) == (UPCOMING, 90)
    assert (body["elapsed_days"], body["remaining_days"], body["days_until_start"]) == (0, 90, 7)
    assert body["achieved"] == ZERO


async def test_dashboard_carries_the_same_challenge(client, session, seeded, today):
    session.add(challenge(today - timedelta(days=4), today + timedelta(days=10)))  # 15 days
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    dashboard = (await client.get("/api/portal/dashboard")).json()
    assert dashboard["challenge"] == body
    assert (body["total_days"], body["elapsed_days"]) == (15, 5)


async def test_new_alc_automatically_joins_the_global_challenge(client, session, seeded, today):
    session.add(challenge(today - timedelta(days=4), today + timedelta(days=40), "Autumn"))
    await session.commit()
    # Created after the challenge was configured; nothing is written for it.
    new_alc = ALC(alc_code="00019999", alc_name="New Centre", sbu_id=seeded["sbu4"].id)
    session.add(new_alc)
    await session.flush()
    session.add(User(username="alc-new", password_hash=hash_password("StrongAlcPassN!"),
                     role=Role.ALC, alc_id=new_alc.id))
    session.add(verified(new_alc, "W-1", today, "School Meeting", leads=6))
    await session.commit()
    await as_user(client, ("00019999", "StrongAlcPassN!", "ALC"))
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["status"], body["name"]) == (ACTIVE, "Autumn")
    assert body["period_source"] == SOURCE_GLOBAL
    assert (body["total_days"], body["elapsed_days"]) == (45, 5)
    assert body["achieved"]["prospects"] == 6
    # The global challenge is one row; no per-ALC rows were created to represent it.
    assert await session.scalar(select(func.count(ChallengeProgress.id))) == 0
    assert await session.scalar(select(func.count(GrowthChallenge.id))) == 1


async def test_per_alc_override_beats_the_global_challenge_for_that_alc_only(
    client, session, seeded, today
):
    session.add(challenge(today - timedelta(days=9), today + timedelta(days=20), "Global"))  # 30
    session.add(row(today - timedelta(days=2), today + timedelta(days=12),
                    seeded["alc_a"].id, prospects=12))  # 15 days, Centre A only
    session.add_all([
        verified(seeded["alc_a"], "O-1", today - timedelta(days=1), "School Meeting", leads=2),
        verified(seeded["alc_a"], "O-2", today - timedelta(days=6), "School Meeting", leads=50),
        verified(seeded["alc_b"], "O-3", today - timedelta(days=6), "School Meeting", leads=4),
    ])
    await session.commit()
    await as_user(client, ALC_A)
    own = (await client.get("/api/portal/challenge")).json()
    assert (own["period_source"], own["total_days"], own["elapsed_days"]) == (SOURCE_ALC, 15, 3)
    assert (own["name"], own["targets"]["prospects"]) == (None, 12)
    assert own["achieved"]["prospects"] == 2  # 6 days ago is before the override started
    await as_user(client, ALC_B)
    other = (await client.get("/api/portal/challenge")).json()
    assert (other["period_source"], other["total_days"], other["name"]) == (
        SOURCE_GLOBAL, 30, "Global")
    assert other["achieved"]["prospects"] == 4


async def test_expired_override_falls_back_to_the_running_global_challenge(
    client, session, seeded, today
):
    session.add(challenge(today - timedelta(days=9), today + timedelta(days=20), "Global"))
    session.add(row(today - timedelta(days=90), today - timedelta(days=61), seeded["alc_a"].id))
    await session.commit()
    await as_user(client, ALC_A)
    body = (await client.get("/api/portal/challenge")).json()
    assert (body["period_source"], body["status"], body["total_days"]) == (
        SOURCE_GLOBAL, ACTIVE, 30)


async def test_override_without_a_global_challenge_still_works(client, session, seeded, today):
    session.add(row(today - timedelta(days=4), today + timedelta(days=10), seeded["alc_a"].id))
    await session.commit()
    await as_user(client, ALC_A)
    own = (await client.get("/api/portal/challenge")).json()
    assert (own["period_source"], own["status"], own["total_days"]) == (SOURCE_ALC, ACTIVE, 15)
    await as_user(client, ALC_B)
    assert (await client.get("/api/portal/challenge")).json()["status"] == NOT_CONFIGURED


async def test_access_rules_are_unchanged(client, seeded):
    assert (await client.get("/api/portal/challenge")).status_code == 401
    assert (await client.get("/api/admin/challenge")).status_code == 401
    await as_user(client, SBU_4)
    assert (await client.get("/api/portal/challenge")).status_code == 403
    assert (await client.get("/api/admin/challenge")).status_code == 403
    await as_user(client, ALC_A)
    assert (await client.get("/api/admin/challenge")).status_code == 403


# --------------------------------------------------------------------------- #
# Admin overview
# --------------------------------------------------------------------------- #
def three_activities(session, today, *alcs):
    for alc, tag in alcs:
        session.add_all([
            verified(alc, f"{tag}-5", today - timedelta(days=5), "School Meeting", leads=1),
            verified(alc, f"{tag}-20", today - timedelta(days=20), "Pilot Batch", leads=10),
            verified(alc, f"{tag}-45", today - timedelta(days=45), "Pilot Batch", leads=100),
        ])


async def test_admin_overview_uses_the_global_challenge_for_every_alc(
    client, session, seeded, today
):
    alc_a, alc_b, alc_c = seeded["alc_a"], seeded["alc_b"], seeded["alc_c"]
    session.add(challenge(today - timedelta(days=59), today, "Sixty", prospects=80))  # 60 days
    three_activities(session, today, (alc_a, "A"), (alc_b, "B"), (alc_c, "C"))
    await session.commit()
    await as_user(client, ADMIN)
    body = (await client.get("/api/admin/challenge")).json()
    for item in body["items"]:
        assert (item["prospects"], item["meetings"], item["pilots"]) == (111, 1, 2)
    assert body["periods"] == {}  # nobody has an override
    default = body["default_period"]
    assert (default["status"], default["total_days"], default["name"]) == (ACTIVE, 60, "Sixty")
    assert default["period_source"] == SOURCE_GLOBAL
    assert body["targets"] == {**DEFAULT_TARGETS, "prospects": 80}


async def test_admin_overview_measures_an_override_alc_over_its_own_period(
    client, session, seeded, today
):
    alc_a, alc_b, alc_c = seeded["alc_a"], seeded["alc_b"], seeded["alc_c"]
    session.add(challenge(today - timedelta(days=29), today, "Thirty"))  # global: 30 days
    session.add(row(today - timedelta(days=59), today, alc_a.id, prospects=80))  # 60 days
    session.add(row(today - timedelta(days=14), today, alc_b.id))  # 15 days
    three_activities(session, today, (alc_a, "A"), (alc_b, "B"), (alc_c, "C"))
    await session.commit()
    await as_user(client, ADMIN)
    body = (await client.get("/api/admin/challenge")).json()
    rows = {r["alc_code"]: r for r in body["items"]}
    # Centre A: 60-day override -> all three. Centre B: 15-day override -> only the newest.
    # Centre C: no override -> the global 30 days -> the two newest.
    assert (rows["00010001"]["prospects"], rows["00010001"]["pilots"]) == (111, 2)
    assert (rows["00010002"]["prospects"], rows["00010002"]["pilots"]) == (1, 0)
    assert (rows["00010003"]["prospects"], rows["00010003"]["pilots"]) == (11, 1)
    assert all(r["meetings"] == 1 for r in rows.values())

    periods = body["periods"]
    assert set(periods) == {str(alc_a.id), str(alc_b.id)}  # only ALCs with an override
    assert periods[str(alc_a.id)]["total_days"] == 60
    assert periods[str(alc_a.id)]["targets"]["prospects"] == 80
    assert periods[str(alc_a.id)]["period_source"] == SOURCE_ALC
    assert (periods[str(alc_b.id)]["total_days"], periods[str(alc_b.id)]["elapsed_days"]) == (
        15, 15)
    assert set(periods[str(alc_a.id)]) == PERIOD_KEYS
    assert body["default_period"]["total_days"] == 30


async def test_admin_overview_with_nothing_configured_counts_nothing(
    client, session, seeded, today
):
    session.add(verified(seeded["alc_a"], "K-1", today, "School Meeting", leads=2))
    await session.commit()
    await as_user(client, ADMIN)
    body = (await client.get("/api/admin/challenge")).json()
    assert {"items", "page", "page_size", "total", "pages", "targets"} <= set(body)
    assert set(body["items"][0]) == {
        "id", "alc_code", "alc_name", "prospects", "meetings", "pilots", "partnerships"}
    assert len(body["items"]) == 3
    for item in body["items"]:  # no rolling 30-day window is applied
        assert {k: item[k] for k in ZERO} == ZERO
    assert (body["periods"], body["challenges"]) == ({}, [])
    default = body["default_period"]
    assert (default["status"], default["configured"], default["total_days"]) == (
        NOT_CONFIGURED, False, 0)
    assert (default["period_start"], default["period_end"]) == (None, None)
    assert body["targets"] == DEFAULT_TARGETS  # column names only


async def test_admin_overview_override_only_alc_when_no_global_challenge(
    client, session, seeded, today
):
    alc_a, alc_b = seeded["alc_a"], seeded["alc_b"]
    session.add(row(today - timedelta(days=14), today, alc_a.id))
    three_activities(session, today, (alc_a, "A"), (alc_b, "B"))
    await session.commit()
    await as_user(client, ADMIN)
    rows = {r["alc_code"]: r for r in (await client.get("/api/admin/challenge")).json()["items"]}
    assert (rows["00010001"]["prospects"], rows["00010001"]["meetings"]) == (1, 1)
    assert {k: rows["00010002"][k] for k in ZERO} == ZERO  # not configured for Centre B


async def test_admin_overview_freezes_completed_and_zeroes_upcoming(
    client, session, seeded, today
):
    alc_a, alc_b = seeded["alc_a"], seeded["alc_b"]
    ended = today - timedelta(days=10)
    session.add(challenge(ended - timedelta(days=44), ended, "Done"))  # 45 days, completed
    session.add(row(today + timedelta(days=3), today + timedelta(days=32), alc_b.id))  # upcoming
    session.add_all([
        verified(alc_a, "F-1", ended, "Pilot Batch", leads=5),
        verified(alc_a, "F-2", today, "Pilot Batch", leads=70),  # after the challenge ended
        verified(alc_b, "F-3", today, "Pilot Batch", leads=9),  # before its override starts
        verified(alc_b, "F-4", ended, "Pilot Batch", leads=3),  # inside the completed global
    ])
    await session.commit()
    await as_user(client, ADMIN)
    body = (await client.get("/api/admin/challenge")).json()
    rows = {r["alc_code"]: r for r in body["items"]}
    assert (rows["00010001"]["prospects"], rows["00010001"]["pilots"]) == (5, 1)
    # Centre B's upcoming override outranks the completed global challenge.
    assert (rows["00010002"]["prospects"], rows["00010002"]["pilots"]) == (0, 0)
    assert body["default_period"]["status"] == COMPLETED
    assert body["periods"][str(alc_b.id)]["status"] == UPCOMING
    assert body["periods"][str(alc_b.id)]["days_until_start"] == 3
