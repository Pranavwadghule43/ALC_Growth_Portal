"""The regional finalization barrier and annual fifth metric on the shared ranking."""
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    DCU,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
)
from app.services import leaderboard
from tests.test_activity_exports import StatementLog, as_user
from tests.test_region_leaderboard import activity, partner


@pytest_asyncio.fixture
async def world(session, seeded):
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    return {**seeded, "nashik": nashik}


async def annual(session, world, code="OUTREACH", year=2026, *, finalized=False,
                 completed=True):
    definition = YearlyActivityDefinition(
        rcu_id=world["nashik"].rcu_id, calendar_year=year, activity_code=code,
        name="Annual outreach", introduced_by_dcu_id=world["nashik"].id,
    )
    session.add(definition)
    await session.flush()
    dcus = (await session.scalars(select(DCU).where(
        DCU.rcu_id == definition.rcu_id, DCU.is_active.is_(True)))).all()
    runs = []
    for dcu in dcus:
        run = YearlyActivityDCURun(
            yearly_activity_id=definition.id, dcu_id=dcu.id,
            status="FINALIZED" if finalized else "AVAILABLE",
            scheduled_start_date=date(year, 1, 1) if finalized else None,
            scheduled_end_date=date(year, 1, 1) if finalized else None,
            finalized_at=datetime.now(timezone.utc) if finalized else None,
        )
        session.add(run)
        runs.append(run)
    await session.flush()
    own = next(run for run in runs if run.dcu_id == world["nashik"].id)
    completion = YearlyActivityCompletion(
        yearly_activity_id=definition.id, yearly_activity_dcu_run_id=own.id,
        alc_id=world["alc_a"].id, snapshot_sbu_id=world["sbu4"].id,
        completed=completed,
    )
    session.add(completion)
    await session.commit()
    return definition, runs, completion


async def test_yearly_only_alc_is_eligible_only_after_all_snapshot_dcus_finalize(session, world):
    _, runs, _ = await annual(session, world)
    own = next(run for run in runs if run.dcu_id == world["nashik"].id)
    own.status = "FINALIZED"
    own.scheduled_start_date = own.scheduled_end_date = date(2026, 1, 1)
    own.finalized_at = datetime.now(timezone.utc)
    await session.commit()
    assert (await leaderboard.region_scores(session, 2026))["items"] == []
    for run in runs:
        run.status = "FINALIZED"
        run.scheduled_start_date = run.scheduled_end_date = date(2026, 1, 1)
        run.finalized_at = datetime.now(timezone.utc)
    await session.commit()
    payload = await leaderboard.region_scores(session, 2026)
    assert payload["eligible_yearly_activities"] == 1
    assert [(r["alc_code"], r["yearly_completed_count"], r["score"])
            for r in payload["items"]] == [("00010001", 1, 20.0)]


@pytest.mark.parametrize("completed", [False, None])
async def test_no_and_not_marked_never_award_points_and_empty_population_is_accurate(
    session, world, completed
):
    await annual(session, world, finalized=True, completed=completed)
    payload = await leaderboard.region_scores(session, 2026)
    assert payload["items"] == []
    assert payload["eligible_yearly_activities"] == 1


async def test_selected_year_changes_only_annual_metric_and_preserves_lifetime(session, world):
    await annual(session, world, finalized=True)
    session.add_all([activity(world["alc_a"].id, leads=7, admissions=3),
                     partner(world["alc_a"].id)])
    await session.commit()
    current = (await leaderboard.region_scores(session, 2026))["items"][0]
    following = (await leaderboard.region_scores(session, 2027))["items"][0]
    assert current["score"] == 100
    assert following["score"] == 80
    for key in ("verified_leads", "verified_admissions", "activities_done", "partners"):
        assert following[key] == current[key]
    assert current["yearly_completed_count"] == 1
    assert following["yearly_completed_count"] == 0


async def test_dynamic_annual_counts_are_distinct_and_one_sql_statement(session, world):
    for number in range(11):
        await annual(session, world, code=f"EVENT-{number}", finalized=True)
    await annual(session, world, code="PENDING")
    with StatementLog(session) as statements:
        payload = await leaderboard.region_scores(session, 2026)
    assert len(statements.statements) == 1
    assert payload["items"][0]["yearly_completed_count"] == 11
    assert payload["eligible_yearly_activities"] == 11
    assert (await leaderboard.region_top10(session, 2026))["items"] == payload["items"][:10]


async def test_inactive_snapshotted_dcu_still_blocks_and_new_dcu_does_not_rewrite_history(
    session, world
):
    definition, runs, _ = await annual(session, world, finalized=True)
    other = next(run for run in runs if run.dcu_id != world["nashik"].id)
    other.status, other.finalized_at = "AVAILABLE", None
    dcu = await session.get(DCU, other.dcu_id)
    dcu.is_active = False
    await session.commit()
    assert (await leaderboard.region_scores(session, 2026))["items"] == []
    other.status, other.finalized_at = "FINALIZED", datetime.now(timezone.utc)
    other.scheduled_start_date = other.scheduled_end_date = date(2026, 1, 1)
    session.add(DCU(code="NEW_DCU", name="Created later", rcu_id=definition.rcu_id))
    await session.commit()
    assert (await leaderboard.region_scores(session, 2026))["items"][0][
        "yearly_completed_count"] == 1


async def test_default_annual_year_is_application_date_and_query_year_is_validated(
    client, session, world, monkeypatch
):
    await annual(session, world, finalized=True)
    monkeypatch.setattr(leaderboard, "current_date", lambda: date(2027, 1, 1))
    await as_user(client, "00010001", "StrongAlcPassA!")
    default = (await client.get("/api/leaderboard/region-scores")).json()
    assert default["activity_year"] == 2027 and default["items"] == []
    selected = (await client.get("/api/leaderboard/region-scores?year=2026")).json()
    assert selected["activity_year"] == 2026 and len(selected["items"]) == 1
    assert (await client.get("/api/leaderboard/region-scores?year=9999")).status_code == 422
