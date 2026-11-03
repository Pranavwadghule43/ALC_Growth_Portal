"""Real scoped API workflows: registry, lifecycle, regional barrier and membership history."""
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.auth import hash_password
from app.enums import Role
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    AuditLog,
    User,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
)
from app.services import alc_import, master_import
from app.services import yearly_activities as service
from tests.conftest import login

ROOT = "/api/yearly-activities"


@pytest.fixture
async def yearly(session, seeded, monkeypatch):
    monkeypatch.setattr(service, "local_today", lambda: date(2026, 11, 3))
    # Reduce only this synthetic fixture's participation population to two active DCUs.
    dcus = (await session.scalars(select(DCU))).all()
    primary = await session.get(DCU, seeded["sbu4"].dcu_id)
    other = next(d for d in dcus if d.id != primary.id)
    for dcu in dcus:
        dcu.is_active = dcu.id in {primary.id, other.id}
    sbu = SBU(code="YEARLY-OTHER", name="Other SBU", dcu_id=other.id)
    session.add(sbu)
    await session.flush()
    alc = ALC(alc_code="00019999", alc_name="Other Centre", sbu_id=sbu.id)
    users = [User(username=f"yearly-dcu-{index}", role=Role.DCU, dcu_id=dcu.id,
                  password_hash=hash_password("StrongDcuPass!"))
             for index, dcu in enumerate((primary, other))]
    session.add_all([alc, *users])
    await session.commit()
    return {"primary": primary, "other": other, "other_sbu": sbu, "other_alc": alc,
            "users": users}


async def dcu_login(client, index=0):
    response = await login(client, f"yearly-dcu-{index}", "StrongDcuPass!", "DCU")
    assert response.status_code == 200


async def register(client, code="activity_a", year=2026):
    response = await client.post(f"{ROOT}/definitions", json={
        "activity_code": code, "calendar_year": year,
        "name": "Common Annual Activity", "description": "Regional instructions",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def listing(client, **params):
    response = await client.get(f"{ROOT}/definitions", params={"year": 2026, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def activate(client, run_id, *, start="2026-10-01", end="2026-10-02"):
    for payload in ({"scheduled_start_date": start, "scheduled_end_date": end},
                    {"status": "ACTIVE"}, {"status": "MARKING"}):
        response = await client.patch(f"{ROOT}/runs/{run_id}", json=payload)
        assert response.status_code == 200, response.text


async def run_rows(client, run_id, **params):
    response = await client.get(f"{ROOT}/runs/{run_id}/completions", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def mark_all(client, run_id, completed=True):
    for row in (await run_rows(client, run_id))["items"]:
        response = await client.patch(f"{ROOT}/runs/{run_id}/completions/{row['alc_id']}",
                                      json={"completed": completed})
        assert response.status_code == 200, response.text


async def test_shared_identity_normalization_year_separation_and_dynamic_count(
    client, session, yearly,
):
    await dcu_login(client)
    first = await register(client)
    assert (await listing(client))["items"][0]["activity_code"] == "ACTIVITY-A"
    duplicate = await client.post(f"{ROOT}/definitions", json={
        "activity_code": " Activity A ", "calendar_year": 2026, "name": "Different label",
    })
    assert duplicate.status_code == 409
    assert await session.scalar(select(func.count(YearlyActivityDefinition.id))) == 1
    await dcu_login(client, 1)
    result = await listing(client)
    assert result["items"][0]["id"] == first
    assert result["items"][0]["participant_count"] == 2
    await register(client, year=2027)
    for index in range(11):
        await register(client, code=f"OTHER-{index}")
    assert (await listing(client))["total"] == 12
    invalid = await client.post(f"{ROOT}/definitions", json={
        "activity_code": "!!", "calendar_year": 2026, "name": "Activity",
    })
    assert invalid.status_code == 422


async def test_complete_workflow_tri_state_audits_and_regional_barrier(client, session, yearly):
    await dcu_login(client)
    definition_id = await register(client)
    primary_run = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, primary_run)
    rows = await run_rows(client, primary_run)
    assert rows["summary"] == {"yes": 0, "no": 0, "not_marked": 3}
    assert all(row["completed"] is None for row in rows["items"])
    assert (await client.post(f"{ROOT}/runs/{primary_run}/finalize")).status_code == 409
    alc_id = rows["items"][0]["alc_id"]
    for decision in (True, False, None, True):
        response = await client.patch(f"{ROOT}/runs/{primary_run}/completions/{alc_id}",
                                      json={"completed": decision})
        assert response.json()["completed"] is decision
    assert await session.scalar(select(func.count(YearlyActivityCompletion.id))) == 3
    await mark_all(client, primary_run)
    assert (await client.post(f"{ROOT}/runs/{primary_run}/finalize")).status_code == 200
    assert (await listing(client))["items"][0]["regional_status"] == "PENDING"
    assert (await session.scalars(service.eligible_activity_ids(2026))).all() == []
    assert (await client.patch(f"{ROOT}/runs/{primary_run}/completions/{alc_id}",
                               json={"completed": False})).status_code == 409
    assert (await client.patch(f"{ROOT}/runs/{primary_run}",
                               json={"status": "ACTIVE"})).status_code == 409
    await dcu_login(client, 1)
    other_run = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, other_run, start="2026-10-15", end="2026-10-16")
    await mark_all(client, other_run, completed=False)
    assert (await client.post(f"{ROOT}/runs/{other_run}/finalize")).status_code == 200
    assert (await listing(client))["items"][0]["regional_status"] == "ELIGIBLE"
    assert (await session.scalars(service.eligible_activity_ids(2026))).all() == [
        uuid.UUID(definition_id)]
    assert await session.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == "yearly_activity_completion_marked")) == 8


async def test_scope_readonly_roles_and_csrf(client, seeded, yearly):
    await dcu_login(client)
    await register(client)
    run_id = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, run_id)
    await mark_all(client, run_id)
    alc_id = str(seeded["alc_a"].id)
    await dcu_login(client, 1)
    for path, body in ((f"runs/{run_id}", {"status": "MARKING"}),
                       (f"runs/{run_id}/completions/{alc_id}", {"completed": True})):
        assert (await client.patch(f"{ROOT}/{path}", json=body)).status_code == 404
    assert (await client.post(f"{ROOT}/runs/{run_id}/finalize")).status_code == 404
    assert (await client.get(f"{ROOT}/runs/{run_id}/completions")).status_code == 404
    for identifier, password, portal, allowed in (
        ("00010001", "StrongAlcPassA!", "ALC", {str(seeded["alc_a"].id)}),
        ("sbu-4", "StrongSbuPass4!", "SBU",
         {str(seeded["alc_a"].id), str(seeded["alc_c"].id)}),
        ("admin", "StrongAdminPass!", "ADMIN", None),
    ):
        assert (await login(client, identifier, password, portal)).status_code == 200
        visible = await run_rows(client, run_id)
        if allowed:
            assert {row["alc_id"] for row in visible["items"]} == allowed
        assert all(not row["can_edit"] for row in visible["items"])
        assert (await client.post(f"{ROOT}/definitions", json={
            "activity_code": "DENIED", "calendar_year": 2026, "name": "Denied",
        })).status_code == 403
        assert (await client.patch(f"{ROOT}/runs/{run_id}/completions/{alc_id}",
                                   json={"completed": False})).status_code == 403
        assert (await client.post(f"{ROOT}/runs/{run_id}/finalize")).status_code == 403
    await dcu_login(client)
    client.headers.pop("X-CSRF-Token")
    assert (await client.patch(f"{ROOT}/runs/{run_id}/completions/{alc_id}",
                               json={"completed": False})).status_code == 403


async def test_schedule_and_state_validation(client, yearly):
    await dcu_login(client)
    await register(client)
    run_id = (await listing(client))["items"][0]["runs"][0]["id"]
    for payload, code in (
        ({"status": "ACTIVE"}, 409),
        ({"scheduled_start_date": "2026-10-02", "scheduled_end_date": "2026-10-01"}, 422),
        ({"scheduled_start_date": "2027-01-01", "scheduled_end_date": "2027-01-02"}, 422),
        ({"scheduled_start_date": "2026-12-01", "scheduled_end_date": "2026-12-02"}, 200),
        ({"status": "ACTIVE"}, 409),
        ({"scheduled_start_date": "2026-10-01", "scheduled_end_date": "2026-12-01"}, 200),
        ({"status": "ACTIVE"}, 200),
        ({"status": "MARKING"}, 409),
        ({"scheduled_start_date": "2026-10-01", "scheduled_end_date": "2026-10-02"}, 409),
    ):
        response = await client.patch(f"{ROOT}/runs/{run_id}", json=payload)
        assert response.status_code == code, response.text


async def test_search_sbu_filter_pagination(client, seeded, yearly):
    await dcu_login(client)
    await register(client)
    run_id = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, run_id)
    rows = await run_rows(client, run_id, page_size=1, page=2)
    assert rows["total"] == 3 and len(rows["items"]) == 1 and rows["pages"] == 3
    assert len((await run_rows(client, run_id, q="Centre A"))["items"]) == 1
    assert len((await run_rows(client, run_id, sbu_id=str(seeded["sbu4"].id)))["items"]) == 2
    assert (await client.get(f"{ROOT}/definitions", params={"page_size": 101})).status_code == 422


async def test_participation_snapshot_late_and_inactive_dcu_policy(client, session, yearly):
    await dcu_login(client)
    await register(client)
    first = (await listing(client))["items"][0]
    session.add(DCU(code="LATE-DCU", name="Late DCU", rcu_id=yearly["primary"].rcu_id))
    yearly["other"].is_active = False
    await session.commit()
    item = (await listing(client))["items"][0]
    assert item["participant_count"] == first["participant_count"] == 2
    assert item["regional_status"] == "PENDING"


async def test_reassignment_blocked_until_finalization_and_history_not_duplicated(
    client, session, seeded, yearly,
):
    await dcu_login(client)
    await register(client)
    first_run = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, first_run)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    response = await client.patch(f"/api/admin/alcs/{seeded['alc_a'].id}/sbu",
                                  json={"sbu_id": str(yearly["other_sbu"].id)})
    assert response.status_code == 409
    response = await client.patch(f"/api/admin/sbus/{seeded['sbu4'].id}/dcu",
                                  json={"dcu_id": str(yearly["other"].id)})
    assert response.status_code == 409
    await dcu_login(client)
    await mark_all(client, first_run)
    assert (await client.post(f"{ROOT}/runs/{first_run}/finalize")).status_code == 200
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await client.patch(f"/api/admin/alcs/{seeded['alc_a'].id}/sbu",
                                json={"sbu_id": str(yearly["other_sbu"].id)})).status_code == 200
    await dcu_login(client, 1)
    second_run = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, second_run)
    # The transferred centre keeps its original finalized result; one definition counts once.
    assert {row["alc_id"] for row in (await run_rows(client, second_run))["items"]} == {
        str(yearly["other_alc"].id)}
    assert await session.scalar(select(func.count(YearlyActivityCompletion.id)).where(
        YearlyActivityCompletion.alc_id == seeded["alc_a"].id)) == 1
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    historic = (await listing(client))["items"][0]["runs"]
    assert first_run in {row["id"] for row in historic}
    assert (await run_rows(client, first_run))["items"][0]["completed"] is True


@pytest.mark.parametrize("canonical", [False, True])
async def test_bulk_import_cannot_bypass_pending_membership_or_partially_write(
    client, session, seeded, yearly, canonical,
):
    await dcu_login(client)
    await register(client)
    run_id = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, run_id)
    before_count = await session.scalar(select(func.count(ALC.id)))
    original_alc_id = seeded["alc_a"].id
    original_sbu = seeded["alc_a"].sbu_id
    original_name = seeded["alc_a"].alc_name
    if canonical:
        records = [
            master_import.MasterRecord(2, "RCU Pune", "DCU Nashik", "SBU 4",
                                       "99990001", "New Centre", "Yes"),
            master_import.MasterRecord(3, "RCU Pune", "DCU Nashik", "SBU 6",
                                       "00010001", "Renamed Centre", "Yes"),
        ]
        with pytest.raises(HTTPException) as error:
            await master_import.perform(session, records, allow_nashik_change=True)
    else:
        records = [alc_import.MasterRow(2, "99990001", "New Centre", "SBU 4"),
                   alc_import.MasterRow(3, "00010001", "Renamed Centre", "SBU 6")]
        with pytest.raises(HTTPException) as error:
            await alc_import.perform(session, records)
    assert error.value.status_code == 409
    assert await session.scalar(select(func.count(ALC.id))) == before_count
    alc = await session.get(ALC, original_alc_id)
    assert (alc.sbu_id, alc.alc_name) == (original_sbu, original_name)


async def test_per_alc_annual_totals_use_the_runs_region_population(
    client, session, seeded, yearly,
):
    await dcu_login(client)
    await register(client)
    run_id = (await listing(client))["items"][0]["runs"][0]["id"]
    await activate(client, run_id)
    await mark_all(client, run_id)
    other_rcu = RCU(code="HISTORIC-REGION", name="Historical Region")
    session.add(other_rcu)
    await session.flush()
    other_dcu = DCU(code="HISTORIC-DCU", name="Historical DCU", rcu_id=other_rcu.id)
    session.add(other_dcu)
    await session.flush()
    definition = YearlyActivityDefinition(rcu_id=other_rcu.id, calendar_year=2026,
        activity_code="HISTORICAL", name="Historical Activity", introduced_by_dcu_id=other_dcu.id)
    session.add(definition)
    await session.flush()
    run = YearlyActivityDCURun(yearly_activity_id=definition.id, dcu_id=other_dcu.id,
        status="FINALIZED", scheduled_start_date=date(2026, 1, 1),
        scheduled_end_date=date(2026, 1, 2), finalized_at=datetime.now(timezone.utc))
    session.add(run)
    await session.flush()
    session.add(YearlyActivityCompletion(yearly_activity_id=definition.id,
        yearly_activity_dcu_run_id=run.id, alc_id=seeded["alc_a"].id,
        snapshot_sbu_id=seeded["sbu4"].id, completed=True))
    await session.commit()
    rows = (await run_rows(client, run_id))["items"]
    alc_a = next(row for row in rows if row["alc_id"] == str(seeded["alc_a"].id))
    assert (alc_a["yearly_completed_count"], alc_a["yearly_total_applicable"]) == (1, 1)
