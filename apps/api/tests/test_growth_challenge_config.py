"""Growth Challenge: global configuration, authorization, audit and time zone.

* ``POST /api/admin/growth-challenges`` and ``PATCH /api/admin/growth-challenges/{id}``:
  create / update, validation, overlap handling, ADMIN only, CSRF, audit log.
* "Today" is the Asia/Kolkata calendar date, whatever the server's OS time zone is.
"""
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest
from sqlalchemy import func, select

from app.auth import hash_password
from app.enums import Role
from app.models import DCU, AuditLog, ChallengeProgress, GrowthChallenge, User
from app.services import growth_challenge
from app.services.growth_challenge import (
    ACTIVE,
    APP_TIMEZONE_NAME,
    COMPLETED,
    UPCOMING,
)
from tests.conftest import login

TODAY = date(2026, 3, 10)
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
URL = "/api/admin/growth-challenges"
REAL_CURRENT_DATE = growth_challenge.current_date


def payload(days=30, start=TODAY, **extra) -> dict:
    return {
        "name": "Growth Challenge", "start_date": str(start),
        "end_date": str(start + timedelta(days=days - 1)), **extra,
    }


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(growth_challenge, "current_date", lambda: TODAY)
    return TODAY


async def as_user(client, who):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await login(client, *who)).status_code == 200


async def audits(session, action):
    return (await session.scalars(select(AuditLog).where(AuditLog.action == action))).all()


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("days", [15, 30, 45, 60, 90])
async def test_admin_creates_a_challenge_of_any_duration(client, session, seeded, today, days):
    await as_user(client, ADMIN)
    response = await client.post(URL, json=payload(days, name=f"  {days}-day challenge  "))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == f"{days}-day challenge"  # trimmed
    assert (body["start_date"], body["end_date"]) == (
        str(today), str(today + timedelta(days=days - 1)))
    assert (body["total_days"], body["status"]) == (days, ACTIVE)
    assert (body["elapsed_days"], body["remaining_days"]) == (1, days - 1)
    assert body["targets"] == {"prospects": 40, "meetings": 20, "pilots": 10, "partnerships": 5}
    assert body["created_by"] == body["updated_by"] == str(seeded["admin"].id)
    stored = (await session.scalars(select(GrowthChallenge))).one()
    assert (stored.start_date, stored.end_date) == (today, today + timedelta(days=days - 1))
    # One row for everyone: no per-ALC rows are written.
    assert await session.scalar(select(func.count(ChallengeProgress.id))) == 0


async def test_created_challenge_applies_to_alcs_and_admin_overview(client, session, seeded,
                                                                    today):
    await as_user(client, ADMIN)
    created = await client.post(URL, json=payload(
        45, start=today - timedelta(days=4), name="Monsoon", prospects_target=90,
        meetings_target=30, pilots_target=12, partnerships_target=6))
    assert created.status_code == 201
    overview = (await client.get("/api/admin/challenge")).json()
    assert [c["name"] for c in overview["challenges"]] == ["Monsoon"]
    assert overview["challenges"][0]["id"] == created.json()["id"]
    assert overview["targets"] == {"prospects": 90, "meetings": 30, "pilots": 12,
                                   "partnerships": 6}
    assert (overview["default_period"]["total_days"], overview["default_period"]["status"]) == (
        45, ACTIVE)
    assert (overview["today"], overview["timezone"]) == (str(today), "Asia/Kolkata")
    assert "max_days" not in overview  # there is no maximum duration
    await as_user(client, ALC_A)
    mine = (await client.get("/api/portal/challenge")).json()
    assert (mine["name"], mine["total_days"], mine["elapsed_days"]) == ("Monsoon", 45, 5)
    assert mine["targets"]["prospects"] == 90


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
async def test_end_date_before_start_date_is_rejected(client, session, seeded, today):
    await as_user(client, ADMIN)
    bad = {**payload(), "end_date": str(today - timedelta(days=1))}
    response = await client.post(URL, json=bad)
    assert response.status_code == 422
    assert "End date must be on or after the start date" in str(response.json())
    assert await session.scalar(select(func.count(GrowthChallenge.id))) == 0
    assert await audits(session, "growth_challenge_created") == []


async def test_one_day_period_is_allowed(client, seeded, today):
    await as_user(client, ADMIN)
    one_day = await client.post(URL, json=payload(1))
    assert one_day.status_code == 201 and one_day.json()["total_days"] == 1


@pytest.mark.parametrize("days", [366, 367, 400, 730, 1500])
async def test_there_is_no_maximum_duration(client, session, seeded, today, days):
    """A challenge is never rejected only for being long (no 366-day limit)."""
    assert not hasattr(growth_challenge, "MAX_CHALLENGE_DAYS")
    await as_user(client, ADMIN)
    created = await client.post(URL, json=payload(days, start=today - timedelta(days=99)))
    assert created.status_code == 201
    body = created.json()
    assert (body["total_days"], body["status"]) == (days, ACTIVE)
    assert (body["elapsed_days"], body["remaining_days"]) == (100, days - 100)
    # Extending an existing challenge past a year is allowed too.
    longer = await client.patch(
        f"{URL}/{body['id']}",
        json={"end_date": str(today - timedelta(days=99) + timedelta(days=days + 499))})
    assert longer.status_code == 200 and longer.json()["total_days"] == days + 500
    await as_user(client, ALC_A)
    mine = (await client.get("/api/portal/challenge")).json()
    assert (mine["total_days"], mine["status"], mine["elapsed_days"]) == (days + 500, ACTIVE, 100)


@pytest.mark.parametrize("change", [
    {"name": ""}, {"name": " "}, {"name": "x" * 151}, {"start_date": "not-a-date"},
    {"end_date": None}, {"prospects_target": -1}, {"meetings_target": "many"},
    {"pilots_target": 1.5}, {"partnerships_target": 10_000_000},
])
async def test_invalid_fields_are_rejected(client, session, seeded, today, change):
    await as_user(client, ADMIN)
    assert (await client.post(URL, json={**payload(), **change})).status_code == 422
    assert await session.scalar(select(func.count(GrowthChallenge.id))) == 0


async def test_missing_required_fields_are_rejected(client, seeded, today):
    await as_user(client, ADMIN)
    for missing in ("name", "start_date", "end_date"):
        body = payload()
        del body[missing]
        assert (await client.post(URL, json=body)).status_code == 422, missing


# --------------------------------------------------------------------------- #
# Overlap
# --------------------------------------------------------------------------- #
async def test_overlapping_periods_are_rejected_and_adjacent_ones_allowed(
    client, session, seeded, today
):
    await as_user(client, ADMIN)
    first = await client.post(URL, json=payload(30, name="First"))  # today .. today+29
    assert first.status_code == 201
    last_day = today + timedelta(days=29)
    for start, days in (
        (today, 30),  # identical
        (today + timedelta(days=10), 5),  # inside
        (today - timedelta(days=10), 11),  # ends on the first day
        (last_day, 20),  # starts on the last day
        (today - timedelta(days=5), 60),  # surrounds
    ):
        clash = await client.post(URL, json=payload(days, start=start, name="Clash"))
        assert clash.status_code == 409, (start, days)
        assert 'Overlaps Growth Challenge "First"' in clash.json()["detail"]
    assert await session.scalar(select(func.count(GrowthChallenge.id))) == 1
    after = await client.post(URL, json=payload(15, start=last_day + timedelta(days=1),
                                                name="Next"))
    before = await client.post(URL, json=payload(15, start=today - timedelta(days=15),
                                                 name="Previous"))
    assert (after.status_code, before.status_code) == (201, 201)
    overview = (await client.get("/api/admin/challenge")).json()
    assert [c["name"] for c in overview["challenges"]] == ["Next", "First", "Previous"]
    assert [c["status"] for c in overview["challenges"]] == [UPCOMING, ACTIVE, COMPLETED]
    assert overview["default_period"]["name"] == "First"  # the one running today


async def test_database_overlap_rejection_becomes_a_409():
    """PostgreSQL's exclusion constraint (a concurrent request won the race) -> 409."""
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from app.routes.admin import _save_challenge

    class RacingSession:
        rolled_back = False

        async def commit(self):
            raise IntegrityError("INSERT", {}, Exception("ex_growth_challenges_no_overlap"))

        async def rollback(self):
            self.rolled_back = True

    db = RacingSession()
    with pytest.raises(HTTPException) as caught:
        await _save_challenge(db)
    assert (caught.value.status_code, db.rolled_back) == (409, True)
    assert caught.value.detail == "Challenge periods cannot overlap."


# --------------------------------------------------------------------------- #
# Update
# --------------------------------------------------------------------------- #
async def test_admin_updates_name_dates_and_targets(client, session, seeded, today):
    await as_user(client, ADMIN)
    created = (await client.post(URL, json=payload(30))).json()
    response = await client.patch(f"{URL}/{created['id']}", json={
        "name": "Extended challenge", "end_date": str(today + timedelta(days=59)),
        "prospects_target": 100,
    })
    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["total_days"]) == ("Extended challenge", 60)
    assert body["start_date"] == str(today)  # untouched fields keep their value
    assert body["targets"] == {"prospects": 100, "meetings": 20, "pilots": 10, "partnerships": 5}
    stored = (await session.scalars(select(GrowthChallenge))).one()
    await session.refresh(stored)
    assert (stored.name, stored.end_date) == ("Extended challenge", today + timedelta(days=59))
    await as_user(client, ALC_A)
    assert (await client.get("/api/portal/challenge")).json()["total_days"] == 60


async def test_update_validation_overlap_and_unknown_id(client, session, seeded, today):
    await as_user(client, ADMIN)
    first = (await client.post(URL, json=payload(30, name="First"))).json()
    second = (await client.post(
        URL, json=payload(30, start=today + timedelta(days=40), name="Second"))).json()
    # End before the stored start.
    backwards = await client.patch(
        f"{URL}/{first['id']}", json={"end_date": str(today - timedelta(days=1))})
    assert backwards.status_code == 422
    assert backwards.json()["detail"] == "End date must be on or after the start date"
    # Stretching the first challenge into the second.
    clash = await client.patch(
        f"{URL}/{first['id']}", json={"end_date": str(today + timedelta(days=45))})
    assert clash.status_code == 409 and '"Second"' in clash.json()["detail"]
    # A challenge never clashes with itself.
    same = await client.patch(f"{URL}/{first['id']}", json={"end_date": first["end_date"]})
    assert same.status_code == 200
    for bad in ({"name": None}, {"start_date": None}, {"prospects_target": -5}, {"name": "x"}):
        assert (await client.patch(f"{URL}/{second['id']}", json=bad)).status_code == 422, bad
    unknown = await client.patch(f"{URL}/00000000-0000-0000-0000-000000000000", json={"name": "No"})
    assert unknown.status_code == 404
    stored = {c.name: c for c in (await session.scalars(select(GrowthChallenge))).all()}
    assert stored["First"].end_date == today + timedelta(days=29)  # nothing was changed
    assert set(stored) == {"First", "Second"}


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #
async def test_configuration_is_admin_only(client, session, seeded, today):
    await as_user(client, ADMIN)
    created = (await client.post(URL, json=payload(30))).json()
    dcu = (await session.scalars(select(DCU))).first()
    session.add(User(username="dcu-gc", password_hash=hash_password("StrongDcuPassGc!"),
                     role=Role.DCU, dcu_id=dcu.id))
    await session.commit()

    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await client.post(URL, json=payload(15, start=today + timedelta(days=60)))
            ).status_code in (401, 403)
    assert (await client.patch(f"{URL}/{created['id']}", json={"name": "Hacked"})
            ).status_code in (401, 403)
    for who in (ALC_A, SBU_4, ("dcu-gc", "StrongDcuPassGc!", "DCU")):
        await as_user(client, who)
        attempt = await client.post(URL, json=payload(15, start=today + timedelta(days=60)))
        assert attempt.status_code == 403, who
        change = await client.patch(f"{URL}/{created['id']}", json={"name": "Hacked"})
        assert change.status_code == 403, who
    stored = (await session.scalars(select(GrowthChallenge))).all()
    assert [c.name for c in stored] == ["Growth Challenge"]
    assert len(await audits(session, "growth_challenge_created")) == 1
    assert await audits(session, "growth_challenge_updated") == []


async def test_configuration_requires_the_csrf_token(client, session, seeded, today):
    await as_user(client, ADMIN)
    client.headers.pop("X-CSRF-Token", None)
    assert (await client.post(URL, json=payload(30))).status_code == 403
    assert await session.scalar(select(func.count(GrowthChallenge.id))) == 0


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
async def test_create_and_update_are_audit_logged(client, session, seeded, today):
    await as_user(client, ADMIN)
    created = (await client.post(URL, json=payload(30, name="Audited"))).json()
    [entry] = await audits(session, "growth_challenge_created")
    assert (entry.entity_type, entry.entity_id) == ("growth_challenge", created["id"])
    assert (entry.actor_user_id, entry.actor_role) == (seeded["admin"].id, "ADMIN")
    assert entry.audit_metadata == {
        "name": "Audited", "start_date": str(today), "end_date": str(today + timedelta(days=29)),
        "targets": {"prospects": 40, "meetings": 20, "pilots": 10, "partnerships": 5},
    }

    await client.patch(f"{URL}/{created['id']}", json={
        "end_date": str(today + timedelta(days=44)), "pilots_target": 25})
    [update] = await audits(session, "growth_challenge_updated")
    assert (update.entity_id, update.actor_user_id) == (created["id"], seeded["admin"].id)
    assert update.audit_metadata["before"]["end_date"] == str(today + timedelta(days=29))
    assert update.audit_metadata["after"]["end_date"] == str(today + timedelta(days=44))
    assert update.audit_metadata["before"]["targets"]["pilots"] == 10
    assert update.audit_metadata["after"]["targets"]["pilots"] == 25

    # Saving the same values again changes nothing and writes no audit entry.
    unchanged = await client.patch(f"{URL}/{created['id']}", json={"pilots_target": 25})
    assert unchanged.status_code == 200
    assert len(await audits(session, "growth_challenge_updated")) == 1
    # Rejected requests are not logged as changes.
    await client.patch(f"{URL}/{created['id']}", json={"end_date": str(today - timedelta(days=3))})
    assert len(await audits(session, "growth_challenge_updated")) == 1


# --------------------------------------------------------------------------- #
# Time zone: "today" is the Asia/Kolkata date
# --------------------------------------------------------------------------- #
def utc(*parts) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def test_today_follows_asia_kolkata_at_the_date_boundary():
    assert APP_TIMEZONE_NAME == "Asia/Kolkata"
    # 18:29:59 UTC is 23:59:59 IST the same day; 18:30:00 UTC is midnight IST the next day.
    assert REAL_CURRENT_DATE(utc(2026, 3, 9, 18, 29, 59)) == date(2026, 3, 9)
    assert REAL_CURRENT_DATE(utc(2026, 3, 9, 18, 30, 0)) == date(2026, 3, 10)
    assert REAL_CURRENT_DATE(utc(2026, 3, 10, 0, 0, 0)) == date(2026, 3, 10)
    assert REAL_CURRENT_DATE(utc(2026, 3, 10, 18, 29, 59)) == date(2026, 3, 10)
    # Month, year and leap-day boundaries.
    assert REAL_CURRENT_DATE(utc(2026, 12, 31, 18, 30)) == date(2027, 1, 1)
    assert REAL_CURRENT_DATE(utc(2028, 2, 28, 18, 30)) == date(2028, 2, 29)
    assert REAL_CURRENT_DATE(utc(2028, 2, 29, 18, 30)) == date(2028, 3, 1)


def test_today_accepts_any_input_zone_and_naive_utc():
    pacific = timezone(timedelta(hours=-8))
    # 11:00 on 9 March in UTC-8 is 00:30 on 10 March in India.
    assert REAL_CURRENT_DATE(datetime(2026, 3, 9, 11, 0, tzinfo=pacific)) == date(2026, 3, 10)
    assert REAL_CURRENT_DATE(datetime(2026, 3, 9, 18, 30)) == date(2026, 3, 10)  # naive = UTC
    assert REAL_CURRENT_DATE(datetime(2026, 3, 9, 18, 29)) == date(2026, 3, 9)
    ist_now = datetime.now(timezone.utc).astimezone(growth_challenge.app_timezone())
    assert REAL_CURRENT_DATE() in {ist_now.date(), (ist_now + timedelta(seconds=5)).date()}


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is POSIX-only")
@pytest.mark.parametrize("os_zone", ["UTC", "America/Los_Angeles", "Pacific/Kiritimati"])
def test_today_ignores_the_server_os_time_zone(monkeypatch, os_zone):
    monkeypatch.setenv("TZ", os_zone)
    time.tzset()
    try:
        assert REAL_CURRENT_DATE(utc(2026, 3, 9, 18, 30)) == date(2026, 3, 10)
        assert REAL_CURRENT_DATE(utc(2026, 3, 9, 18, 29)) == date(2026, 3, 9)
        expected = datetime.now(timezone.utc).astimezone(growth_challenge.app_timezone()).date()
        assert REAL_CURRENT_DATE() == expected
    finally:
        monkeypatch.undo()  # restore TZ before re-reading it
        time.tzset()


def test_fixed_offset_is_used_when_the_tz_database_is_missing(monkeypatch):
    def missing(_name):
        raise ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(growth_challenge, "ZoneInfo", missing)
    zone = growth_challenge.app_timezone()
    assert zone.utcoffset(None) == timedelta(hours=5, minutes=30)
    assert REAL_CURRENT_DATE(utc(2026, 3, 9, 18, 30)) == date(2026, 3, 10)
    assert REAL_CURRENT_DATE(utc(2026, 3, 9, 18, 29, 59)) == date(2026, 3, 9)


async def test_challenge_status_changes_at_midnight_in_india(client, session, seeded,
                                                             monkeypatch):
    # A 15-day challenge: 10 March .. 24 March 2026 (India dates).
    session.add(GrowthChallenge(name="Boundary", start_date=date(2026, 3, 10),
                                end_date=date(2026, 3, 24)))
    await session.commit()
    await as_user(client, ALC_A)

    async def at(moment: datetime) -> dict:
        monkeypatch.setattr(growth_challenge, "current_date", lambda: REAL_CURRENT_DATE(moment))
        return (await client.get("/api/portal/challenge")).json()

    before = await at(utc(2026, 3, 9, 18, 29, 59))  # 23:59:59 IST on 9 March
    assert (before["status"], before["elapsed_days"], before["days_until_start"]) == (
        UPCOMING, 0, 1)
    first = await at(utc(2026, 3, 9, 18, 30, 0))  # 00:00 IST on 10 March (still 9 March UTC)
    assert (first["status"], first["elapsed_days"], first["remaining_days"]) == (ACTIVE, 1, 14)
    last = await at(utc(2026, 3, 24, 18, 29, 59))  # 23:59:59 IST on the last day
    assert (last["status"], last["elapsed_days"], last["remaining_days"]) == (ACTIVE, 15, 0)
    done = await at(utc(2026, 3, 24, 18, 30, 0))  # 00:00 IST on 25 March
    assert (done["status"], done["elapsed_days"], done["remaining_days"]) == (COMPLETED, 15, 0)
