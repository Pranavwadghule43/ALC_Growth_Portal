# ruff: noqa: F811  (the imported ``hier`` fixture is used as a test argument)
"""Phase 4B1 Admin hierarchy reassignment: ``PATCH /admin/alcs/{id}/sbu`` and
``PATCH /admin/sbus/{id}/dcu``.

Uses the ``hier`` fixture from ``test_dcu_hierarchy`` (RCU Pune, its four DCUs, SBU 4 / 6 / 7
under DCU Nashik with the 199-ALC Nashik master, ``TEST PN`` under DCU Pune North holding
Centres A / B / C, a demo ``SBU 1`` with no DCU, DCU / SBU / ALC logins). A move changes one
foreign key; access follows the live hierarchy; history, users and passwords are untouched.
"""

import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    Activity,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    AuditLog,
    Partner,
    RefreshToken,
    Task,
    User,
)
from tests.test_dcu_hierarchy import (  # noqa: F401  (``hier`` is a fixture)
    NASHIK_ALC_SBU4,
    NASHIK_ALC_SBU7,
    as_user,
    hier,
    logout,
    new_client,
    submit_activity,
)

ADMIN = ("admin", "StrongAdminPass!", "ADMIN")


async def as_admin(client):
    await as_user(client, *ADMIN)


async def move_alc(client, alc_id, sbu_id):
    return await client.patch(f"/api/admin/alcs/{alc_id}/sbu", json={"sbu_id": str(sbu_id)})


async def move_sbu(client, sbu_id, dcu_id):
    return await client.patch(f"/api/admin/sbus/{sbu_id}/dcu", json={"dcu_id": str(dcu_id)})


async def count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def audits(session, action):
    rows = await session.scalars(select(AuditLog).where(AuditLog.action == action))
    return rows.all()


async def fresh(session, model, row_id):
    """Re-read a row from the database (never lazy-loads an expired object)."""
    statement = select(model).where(model.id == row_id).execution_options(populate_existing=True)
    return await session.scalar(statement)


async def history_counts(session, alc_id) -> dict[str, int]:
    activity_ids = select(Activity.id).where(Activity.alc_id == alc_id)
    return {
        "activities": await count(session, Activity, Activity.alc_id == alc_id),
        "evidence": await count(
            session, ActivityEvidence, ActivityEvidence.activity_id.in_(activity_ids)
        ),
        "reviews": await count(
            session, ActivityReview, ActivityReview.activity_id.in_(activity_ids)
        ),
        "revisions": await count(
            session, ActivityRevision, ActivityRevision.activity_id.in_(activity_ids)
        ),
        "partners": await count(session, Partner, Partner.alc_id == alc_id),
        "tasks": await count(session, Task, Task.alc_id == alc_id),
    }


async def user_snapshot(session, **where) -> list[tuple]:
    column, value = next(iter(where.items()))
    rows = await session.execute(
        select(
            User.id,
            User.username,
            User.password_hash,
            User.must_change_password,
            User.is_active,
            User.alc_id,
            User.sbu_id,
            User.dcu_id,
        ).where(getattr(User, column) == value)
    )
    return [tuple(r) for r in rows.all()]


async def build_history(client, session, alc):
    """Submitted activity with partner + evidence, one correction request and a
    resubmission (2 revisions, 1 review), plus a task on the ALC."""
    activity, evidence, partner = await submit_activity(
        client, session, NASHIK_ALC_SBU4, with_partner=True
    )
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    corrected = await client.post(
        f"/api/portal/activities/{activity['id']}/request-correction",
        json={"remark": "Add photos"},
    )
    assert corrected.status_code == 200
    await as_user(client, NASHIK_ALC_SBU4)
    assert (await client.post(f"/api/portal/activities/{activity['id']}/submit")).status_code == 200
    logout(client)
    session.add(Task(alc_id=alc.id, title="Follow up", due_date=date.today() + timedelta(days=7)))
    await session.commit()
    return activity, evidence, partner


# --------------------------------------------------------------------------- #
# ALC -> SBU within one DCU (tests 1-12, 31, 32)
# --------------------------------------------------------------------------- #
async def test_alc_move_preserves_identity_history_and_moves_access(client, session, hier):
    centre = hier["alc4_centre"]
    activity, evidence, partner = await build_history(client, session, centre)
    aid = activity["id"]
    before = (centre.id, centre.alc_code, centre.alc_name, centre.status)
    users_before = await user_snapshot(session, alc_id=centre.id)
    history_before = await history_counts(session, centre.id)
    assert history_before == {
        "activities": 1,
        "evidence": 1,
        "reviews": 1,
        "revisions": 2,
        "partners": 1,
        "tasks": 1,
    }
    alcs_before = await count(session, ALC)

    async with new_client() as old_sbu, new_client() as new_sbu, new_client() as alc_login:
        await as_user(old_sbu, "sbu-4", "StrongSbuPass4!")
        await as_user(new_sbu, "sbu-6", "StrongSbuPass6!")
        await as_user(alc_login, NASHIK_ALC_SBU4)
        alc_tokens = RefreshToken.user_id == hier["alc4"].id
        assert await count(session, RefreshToken, alc_tokens) >= 1
        assert (await old_sbu.get(f"/api/portal/alcs/{centre.id}")).status_code == 200
        assert (await new_sbu.get(f"/api/portal/alcs/{centre.id}")).status_code == 404

        await as_admin(client)
        moved = await move_alc(client, centre.id, hier["sbu6"].id)
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert body["changed"] is True and body["alc"]["id"] == str(centre.id)
        assert body["previous"]["sbu"]["code"] == "SBU 4"
        assert body["current"]["sbu"]["code"] == "SBU 6"
        assert body["previous"]["dcu"]["code"] == body["current"]["dcu"]["code"] == "DCU_NASHIK"
        assert body["current"]["rcu"]["code"] == "RCU_PUNE"

        # 10-12. The old SBU loses the ALC at once (same session, no re-login).
        assert (await old_sbu.get(f"/api/portal/alcs/{centre.id}")).status_code == 404
        assert (await old_sbu.get(f"/api/portal/activities/{aid}")).status_code == 404
        assert (await old_sbu.get(f"/api/portal/verification/{aid}")).status_code == 404
        listed = (await old_sbu.get(f"/api/portal/alcs?search={NASHIK_ALC_SBU4}")).json()
        assert listed["total"] == 0
        # 11. The new SBU gains it, with its full submitted history, and can review it.
        assert (await new_sbu.get(f"/api/portal/alcs/{centre.id}")).status_code == 200
        detail = (await new_sbu.get(f"/api/portal/verification/{aid}")).json()
        assert [r["action"] for r in detail["activity"]["reviews"]] == ["REQUEST_CORRECTION"]
        assert [r["revision_number"] for r in detail["activity"]["revisions"]] == [1, 2]
        assert [e["id"] for e in detail["activity"]["evidence"]] == [str(evidence.id)]
        assert detail["activity"]["partner"]["id"] == partner["id"]
        # The ALC's existing session keeps working: no session revocation on a move.
        assert (await alc_login.get(f"/api/portal/activities/{aid}")).status_code == 200
        revoked = RefreshToken.revoked_at.is_not(None)
        assert await count(session, RefreshToken, alc_tokens, revoked) == 0

    # 2-9. Same row, same code/name/status; same user and password; same history.
    moved_alc = await fresh(session, ALC, centre.id)
    assert (moved_alc.id, moved_alc.alc_code, moved_alc.alc_name, moved_alc.status) == before
    assert moved_alc.sbu_id == hier["sbu6"].id
    assert await count(session, ALC) == alcs_before
    assert await user_snapshot(session, alc_id=centre.id) == users_before
    assert await history_counts(session, centre.id) == history_before
    await as_user(client, NASHIK_ALC_SBU4)  # same ALC Code + password still log in

    # 32. Admin still sees the full history.
    await as_admin(client)
    admin_detail = (await client.get(f"/api/admin/alcs/{centre.id}")).json()
    assert admin_detail["hierarchy"]["sbu"]["code"] == "SBU 6"
    (admin_activity,) = [a for a in admin_detail["activities"] if a["id"] == aid]
    assert len(admin_activity["evidence"]) == 1 and len(admin_activity["revisions"]) == 2
    assert (await client.get(f"/api/admin/activities/{aid}")).status_code == 200

    # 31. One audit entry with old/new hierarchy and the acting admin; no credentials.
    (entry,) = await audits(session, "alc_reassigned")
    assert entry.entity_id == str(centre.id) and entry.actor_role == "ADMIN"
    admin_id = await session.scalar(select(User.id).where(User.username == "admin"))
    assert entry.actor_user_id == admin_id
    meta = entry.audit_metadata
    assert meta["alc_code"] == NASHIK_ALC_SBU4 and meta["cross_dcu"] is False
    assert meta["from"]["sbu"]["id"] == str(hier["sbu4"].id)
    assert meta["to"]["sbu"]["id"] == str(hier["sbu6"].id)
    blob = json.dumps(meta).lower()
    assert "password" not in blob and "$argon2" not in blob


# --------------------------------------------------------------------------- #
# ALC -> SBU across DCUs (tests 13, 14)
# --------------------------------------------------------------------------- #
async def test_cross_dcu_alc_move_switches_dcu_access(client, session, hier):
    centre = hier["alc4_centre"]
    activity, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    aid = activity["id"]
    async with new_client() as nashik, new_client() as pune_north:
        await as_user(nashik, "dcu-nashik")
        await as_user(pune_north, "dcu-pn")
        assert (await nashik.get(f"/api/portal/activities/{aid}")).status_code == 200
        assert (await pune_north.get(f"/api/portal/activities/{aid}")).status_code == 404

        await as_admin(client)
        moved = await move_alc(client, centre.id, hier["test_pn"].id)
        assert moved.status_code == 200, moved.text
        assert moved.json()["current"]["dcu"]["code"] == "DCU_PUNE_NORTH"

        assert (await nashik.get(f"/api/portal/activities/{aid}")).status_code == 404
        assert (await nashik.get(f"/api/portal/alcs/{centre.id}")).status_code == 404
        assert (await pune_north.get(f"/api/portal/activities/{aid}")).status_code == 200
        assert (await pune_north.get(f"/api/portal/alcs/{centre.id}")).status_code == 200
    (entry,) = await audits(session, "alc_reassigned")
    assert entry.audit_metadata["cross_dcu"] is True
    assert entry.audit_metadata["from"]["dcu"]["code"] == "DCU_NASHIK"
    assert entry.audit_metadata["to"]["dcu"]["code"] == "DCU_PUNE_NORTH"


# --------------------------------------------------------------------------- #
# SBU -> DCU (tests 15-20)
# --------------------------------------------------------------------------- #
async def test_sbu_move_carries_all_alcs_without_rewriting_them(client, session, hier):
    sbu7 = hier["sbu7"]
    activity, evidence, _ = await submit_activity(client, session, NASHIK_ALC_SBU7)
    aid = activity["id"]
    alcs_before = {
        a.id: (a.alc_code, a.sbu_id, a.updated_at)
        for a in (await session.scalars(select(ALC).where(ALC.sbu_id == sbu7.id))).all()
    }
    assert len(alcs_before) == 70
    sbu_users_before = await user_snapshot(session, sbu_id=sbu7.id)
    sbus_before = await count(session, SBU)

    async with new_client() as nashik, new_client() as pune_north, new_client() as sbu_login:
        await as_user(nashik, "dcu-nashik")
        await as_user(pune_north, "dcu-pn")
        await as_user(sbu_login, "sbu-7")
        nashik_total = (await nashik.get("/api/portal/alcs?page_size=1")).json()["total"]
        pn_total = (await pune_north.get("/api/portal/alcs?page_size=1")).json()["total"]

        await as_admin(client)
        moved = await move_sbu(client, sbu7.id, hier["dcus"]["DCU_PUNE_NORTH"].id)
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert body["changed"] is True and body["sbu"]["id"] == str(sbu7.id)
        assert body["alc_count"] == 70
        assert body["previous"]["dcu"]["code"] == "DCU_NASHIK"
        assert body["current"]["dcu"]["code"] == "DCU_PUNE_NORTH"

        # 19. The old DCU loses all 70 ALCs; 20. the new DCU gains them.
        after_nashik = (await nashik.get("/api/portal/alcs?page_size=1")).json()["total"]
        after_pn = (await pune_north.get("/api/portal/alcs?page_size=1")).json()["total"]
        assert (after_nashik, after_pn) == (nashik_total - 70, pn_total + 70)
        assert (await nashik.get(f"/api/portal/activities/{aid}")).status_code == 404
        assert (await nashik.get(f"/api/portal/sbus/{sbu7.id}")).status_code == 404
        assert (await pune_north.get(f"/api/portal/activities/{aid}")).status_code == 200
        assert (await pune_north.get(f"/api/portal/sbus/{sbu7.id}")).status_code == 200
        pn_detail = (await pune_north.get(f"/api/portal/verification/{aid}")).json()
        assert pn_detail["activity"]["evidence"][0]["id"] == str(evidence.id)
        # 17. The SBU's own login is unaffected and still sees its ALCs.
        assert (await sbu_login.get(f"/api/portal/activities/{aid}")).status_code == 200

    # 16/18. Same SBU row; every ALC still linked to it and not rewritten (same updated_at).
    assert (await fresh(session, SBU, sbu7.id)).code == "SBU 7"
    assert await count(session, SBU) == sbus_before
    alcs_after = {
        a.id: (a.alc_code, a.sbu_id, a.updated_at)
        for a in (await session.scalars(select(ALC).where(ALC.sbu_id == sbu7.id))).all()
    }
    assert alcs_after == alcs_before
    assert await user_snapshot(session, sbu_id=sbu7.id) == sbu_users_before

    (entry,) = await audits(session, "sbu_reassigned")
    meta = entry.audit_metadata
    assert entry.entity_id == str(sbu7.id) and entry.actor_role == "ADMIN"
    assert meta["sbu_code"] == "SBU 7" and meta["alc_count"] == 70
    assert meta["from"]["dcu"]["code"] == "DCU_NASHIK"
    assert meta["to"]["dcu"]["code"] == "DCU_PUNE_NORTH"
    assert "password" not in json.dumps(meta).lower()

    await as_admin(client)
    detail = (await client.get(f"/api/admin/sbus/{sbu7.id}")).json()
    assert detail["hierarchy"]["dcu"]["code"] == "DCU_PUNE_NORTH"
    assert detail["hierarchy"]["rcu"]["code"] == "RCU_PUNE"
    assert len(detail["alcs"]) == 70


# --------------------------------------------------------------------------- #
# Validation (tests 21-25): nothing changes, nothing is audited
# --------------------------------------------------------------------------- #
@pytest.fixture
async def broken_targets(session, hier):
    """Invalid targets: an inactive SBU, an SBU under an inactive DCU, an SBU under a DCU of
    an inactive RCU, an inactive DCU and a DCU of an inactive RCU."""
    dcus = hier["dcus"]
    dead_rcu = RCU(code="RCU_DEAD", name="RCU Dead", is_active=False)
    session.add(dead_rcu)
    await session.flush()
    dcu_dead_rcu = DCU(code="DCU_ORPHANED", name="DCU Orphaned", rcu_id=dead_rcu.id)
    dcus["DCU_AHILYA_NAGAR"].is_active = False
    session.add(dcu_dead_rcu)
    await session.flush()
    targets = {
        "inactive_sbu": SBU(
            code="PN OFF", name="PN Off", dcu_id=dcus["DCU_PUNE_NORTH"].id, is_active=False
        ),
        "sbu_inactive_dcu": SBU(code="AHN X", name="AHN X", dcu_id=dcus["DCU_AHILYA_NAGAR"].id),
        "sbu_dead_rcu": SBU(code="DEAD X", name="Dead X", dcu_id=dcu_dead_rcu.id),
    }
    session.add_all(targets.values())
    await session.commit()
    return {**targets, "inactive_dcu": dcus["DCU_AHILYA_NAGAR"], "dcu_dead_rcu": dcu_dead_rcu}


async def test_alc_move_rejects_invalid_targets(client, session, hier, broken_targets):
    centre = hier["alc4_centre"]
    await as_admin(client)
    cases = [
        (uuid.uuid4(), "Target SBU not found"),
        (broken_targets["inactive_sbu"].id, "Target SBU is inactive"),
        (hier["sbu1"].id, "Target SBU is not assigned to a DCU"),
        (broken_targets["sbu_inactive_dcu"].id, "Target SBU's DCU is inactive"),
        (broken_targets["sbu_dead_rcu"].id, "Target SBU's DCU does not belong to an active RCU"),
    ]
    for target, detail in cases:
        response = await move_alc(client, centre.id, target)
        assert response.status_code == 422, (detail, response.text)
        assert response.json()["detail"] == detail
    assert (await move_alc(client, uuid.uuid4(), hier["sbu6"].id)).status_code == 404
    missing = await client.patch(f"/api/admin/alcs/{centre.id}/sbu", json={})
    assert missing.status_code == 422
    nulled = await client.patch(f"/api/admin/alcs/{centre.id}/sbu", json={"sbu_id": None})
    assert nulled.status_code == 422
    assert (await fresh(session, ALC, centre.id)).sbu_id == hier["sbu4"].id
    assert await audits(session, "alc_reassigned") == []


async def test_sbu_move_rejects_invalid_targets(client, session, hier, broken_targets):
    sbu7 = hier["sbu7"]
    await as_admin(client)
    cases = [
        (uuid.uuid4(), "Target DCU not found"),
        (broken_targets["inactive_dcu"].id, "Target DCU is inactive"),
        (broken_targets["dcu_dead_rcu"].id, "Target DCU does not belong to an active RCU"),
    ]
    for target, detail in cases:
        response = await move_sbu(client, sbu7.id, target)
        assert response.status_code == 422, (detail, response.text)
        assert response.json()["detail"] == detail
    assert (await move_sbu(client, uuid.uuid4(), hier["dcus"]["DCU_NASHIK"].id)).status_code == 404
    nulled = await client.patch(f"/api/admin/sbus/{sbu7.id}/dcu", json={"dcu_id": None})
    assert nulled.status_code == 422
    assert (await fresh(session, SBU, sbu7.id)).dcu_id == hier["dcus"]["DCU_NASHIK"].id
    assert await audits(session, "sbu_reassigned") == []


# --------------------------------------------------------------------------- #
# Authorization (tests 26-29)
# --------------------------------------------------------------------------- #
async def test_only_admin_can_reassign(client, session, hier):
    centre, sbu7 = hier["alc4_centre"], hier["sbu7"]
    target_sbu, target_dcu = hier["sbu6"].id, hier["dcus"]["DCU_PUNE_NORTH"].id
    for identifier, password in (
        ("dcu-nashik", None),
        ("sbu-4", "StrongSbuPass4!"),
        (NASHIK_ALC_SBU4, None),
    ):
        if password:
            await as_user(client, identifier, password)
        else:
            await as_user(client, identifier)
        assert (await move_alc(client, centre.id, target_sbu)).status_code == 403, identifier
        assert (await move_sbu(client, sbu7.id, target_dcu)).status_code == 403, identifier
    logout(client)
    assert (await move_alc(client, centre.id, target_sbu)).status_code in (401, 403)
    assert (await move_sbu(client, sbu7.id, target_dcu)).status_code in (401, 403)
    assert (await fresh(session, ALC, centre.id)).sbu_id == hier["sbu4"].id
    assert (await fresh(session, SBU, sbu7.id)).dcu_id == hier["dcus"]["DCU_NASHIK"].id
    assert await audits(session, "alc_reassigned") == await audits(session, "sbu_reassigned") == []


async def test_anonymous_without_csrf_is_rejected(client, hier):
    logout(client)
    response = await client.patch(
        f"/api/admin/alcs/{hier['alc4_centre'].id}/sbu", json={"sbu_id": str(hier["sbu6"].id)}
    )
    assert response.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Same target (test 30) and transaction safety
# --------------------------------------------------------------------------- #
async def test_same_target_is_a_no_op(client, session, hier):
    centre, sbu7 = hier["alc4_centre"], hier["sbu7"]
    alc_updated = (await fresh(session, ALC, centre.id)).updated_at  # database values
    sbu_updated = (await fresh(session, SBU, sbu7.id)).updated_at
    await as_admin(client)
    same_alc = await move_alc(client, centre.id, hier["sbu4"].id)
    assert same_alc.status_code == 200 and same_alc.json()["changed"] is False
    assert same_alc.json()["previous"] == same_alc.json()["current"]
    same_sbu = await move_sbu(client, sbu7.id, hier["dcus"]["DCU_NASHIK"].id)
    assert same_sbu.status_code == 200 and same_sbu.json()["changed"] is False
    assert (await fresh(session, ALC, centre.id)).updated_at == alc_updated
    assert (await fresh(session, SBU, sbu7.id)).updated_at == sbu_updated
    assert await audits(session, "alc_reassigned") == await audits(session, "sbu_reassigned") == []


async def test_failure_during_move_rolls_back(client, session, hier, monkeypatch):
    from app.services import hierarchy_reassignment

    async def broken_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    # Plain ids: the rollback expires every ORM object held by the shared test session.
    alc_id, sbu7_id = hier["alc4_centre"].id, hier["sbu7"].id
    sbu4_id, sbu6_id = hier["sbu4"].id, hier["sbu6"].id
    nashik_id, pune_north_id = hier["dcus"]["DCU_NASHIK"].id, hier["dcus"]["DCU_PUNE_NORTH"].id
    await as_admin(client)
    monkeypatch.setattr(hierarchy_reassignment, "record_audit", broken_audit)
    with pytest.raises(RuntimeError):
        await move_alc(client, alc_id, sbu6_id)
    with pytest.raises(RuntimeError):
        await move_sbu(client, sbu7_id, pune_north_id)
    assert (await fresh(session, ALC, alc_id)).sbu_id == sbu4_id
    assert (await fresh(session, SBU, sbu7_id)).dcu_id == nashik_id
    assert await audits(session, "alc_reassigned") == await audits(session, "sbu_reassigned") == []


# --------------------------------------------------------------------------- #
# The generic Admin updates can no longer move or detach anything
# --------------------------------------------------------------------------- #
def rejection_message(response) -> str:
    assert response.status_code == 422, response.text
    return " ".join(d["msg"] for d in response.json()["error"]["details"])


async def test_generic_alc_update_cannot_move_or_detach(client, session, hier):
    centre = hier["alc4_centre"]
    await as_admin(client)
    moved = await client.patch(
        f"/api/admin/alcs/{centre.id}", json={"sbu_id": str(hier["sbu6"].id)}
    )
    assert "use PATCH /api/admin/alcs/{alc_id}/sbu" in rejection_message(moved)
    detached = await client.patch(f"/api/admin/alcs/{centre.id}", json={"sbu_id": None})
    assert "sbu_id cannot be changed here" in rejection_message(detached)
    # A mixed body is rejected as a whole: the status change is not half-applied.
    mixed = await client.patch(
        f"/api/admin/alcs/{centre.id}", json={"status": "INACTIVE", "sbu_id": str(hier["sbu6"].id)}
    )
    assert mixed.status_code == 422
    row = await fresh(session, ALC, centre.id)
    assert (row.sbu_id, row.status.value) == (hier["sbu4"].id, "ACTIVE")
    assert await audits(session, "alc_updated") == await audits(session, "alc_reassigned") == []

    # Ordinary fields are still editable through the generic update.
    toggled = await client.patch(f"/api/admin/alcs/{centre.id}", json={"status": "INACTIVE"})
    assert toggled.status_code == 200 and toggled.json()["status"] == "INACTIVE"
    assert toggled.json()["sbu_id"] == str(hier["sbu4"].id)
    (entry,) = await audits(session, "alc_updated")
    assert entry.audit_metadata == {"status": "INACTIVE"}
    assert await audits(session, "alc_reassigned") == []


async def test_generic_sbu_update_cannot_move_or_detach(client, session, hier):
    sbu7 = hier["sbu7"]
    nashik = hier["dcus"]["DCU_NASHIK"].id
    await as_admin(client)
    moved = await client.patch(
        f"/api/admin/sbus/{sbu7.id}", json={"dcu_id": str(hier["dcus"]["DCU_PUNE_NORTH"].id)}
    )
    assert "use PATCH /api/admin/sbus/{sbu_id}/dcu" in rejection_message(moved)
    detached = await client.patch(f"/api/admin/sbus/{sbu7.id}", json={"dcu_id": None})
    assert "dcu_id cannot be changed here" in rejection_message(detached)
    mixed = await client.patch(
        f"/api/admin/sbus/{sbu7.id}", json={"name": "Renamed SBU 7", "dcu_id": None}
    )
    assert mixed.status_code == 422
    row = await fresh(session, SBU, sbu7.id)
    assert (row.dcu_id, row.name) == (nashik, "Strategic Business Unit 7")
    assert await audits(session, "sbu_updated") == await audits(session, "sbu_reassigned") == []

    edited = await client.patch(
        f"/api/admin/sbus/{sbu7.id}", json={"name": "Nashik SBU 7", "is_active": True}
    )
    assert edited.status_code == 200 and edited.json()["name"] == "Nashik SBU 7"
    assert edited.json()["dcu_id"] == str(nashik)
    (entry,) = await audits(session, "sbu_updated")
    assert entry.audit_metadata == {"name": "Nashik SBU 7", "is_active": True}
    assert await audits(session, "sbu_reassigned") == []


async def test_generic_update_schemas_do_not_expose_hierarchy_fields(client):
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]
    assert set(schemas["AlcStatusPatch"]["properties"]) == {"status"}
    assert set(schemas["SbuPatch"]["properties"]) == {"name", "is_active"}
    assert schemas["AlcReassignIn"]["required"] == ["sbu_id"]
    assert schemas["SbuReassignIn"]["required"] == ["dcu_id"]


async def test_reassignment_audit_only_from_strict_moves(client, session, hier):
    centre, sbu7 = hier["alc4_centre"], hier["sbu7"]
    await as_admin(client)
    # Rejected generic attempts, rejected strict attempts and no-ops leave no reassignment audit.
    await client.patch(f"/api/admin/alcs/{centre.id}", json={"sbu_id": str(hier["sbu6"].id)})
    await client.patch(f"/api/admin/sbus/{sbu7.id}", json={"dcu_id": None})
    await move_alc(client, centre.id, hier["sbu1"].id)  # SBU without DCU: 422
    await move_alc(client, centre.id, hier["sbu4"].id)  # same SBU: no-op
    await move_sbu(client, sbu7.id, hier["dcus"]["DCU_NASHIK"].id)  # same DCU: no-op
    assert await audits(session, "alc_reassigned") == await audits(session, "sbu_reassigned") == []
    # Exactly one entry per real strict move.
    assert (await move_alc(client, centre.id, hier["sbu6"].id)).status_code == 200
    assert (await move_sbu(client, sbu7.id, hier["dcus"]["DCU_PUNE_NORTH"].id)).status_code == 200
    assert len(await audits(session, "alc_reassigned")) == 1
    assert len(await audits(session, "sbu_reassigned")) == 1


# --------------------------------------------------------------------------- #
# SBU creation uses the same DCU placement rule as SBU -> DCU reassignment
# --------------------------------------------------------------------------- #
async def create_sbu(client, code, **extra):
    return await client.post(
        "/api/admin/sbus", json={"code": code, "name": f"{code} name", **extra}
    )


async def test_create_sbu_under_active_dcu_or_unplaced(client, session, hier):
    await as_admin(client)
    pune_north = hier["dcus"]["DCU_PUNE_NORTH"].id
    placed = await create_sbu(client, "PN NEW", dcu_id=str(pune_north))
    assert placed.status_code == 201, placed.text
    assert placed.json()["dcu_id"] == str(pune_north)
    unplaced = await create_sbu(client, "NO DCU YET")  # the DCU stays optional on create
    assert unplaced.status_code == 201 and unplaced.json()["dcu_id"] is None
    codes = [e.audit_metadata["code"] for e in await audits(session, "sbu_created")]
    assert sorted(codes) == ["NO DCU YET", "PN NEW"]


async def test_create_sbu_rejects_broken_dcu_hierarchy(client, session, hier, broken_targets):
    orphan_dcu = DCU(code="DCU_NO_RCU", name="DCU No RCU", rcu_id=uuid.uuid4())
    session.add(orphan_dcu)  # a dangling RCU pointer (SQLite tests do not enforce the FK)
    await session.commit()
    sbus_before = await count(session, SBU)
    await as_admin(client)
    cases = [
        ("BAD 1", uuid.uuid4(), "DCU not found"),
        ("BAD 2", broken_targets["inactive_dcu"].id, "DCU is inactive"),
        ("BAD 3", orphan_dcu.id, "DCU does not belong to an active RCU"),
        ("BAD 4", broken_targets["dcu_dead_rcu"].id, "DCU does not belong to an active RCU"),
    ]
    for code, dcu_id, detail in cases:
        response = await create_sbu(client, code, dcu_id=str(dcu_id))
        assert response.status_code == 422, (code, response.text)
        assert response.json()["detail"] == detail
        assert await count(session, SBU, SBU.code == code) == 0
    assert await count(session, SBU) == sbus_before
    assert await audits(session, "sbu_created") == []
