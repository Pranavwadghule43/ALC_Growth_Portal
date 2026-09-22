"""Backend coverage for the activity review entry points (SBU + Admin).

The UI adds "View"/"Review" actions that open the existing review pages; these
tests assert the endpoints those actions call behave correctly and stay scoped:

* SBU can open an assigned ALC's activity + evidence; never another SBU's.
* SBU can Verify / Request Correction / Reject an assigned activity.
* Admin (global) can open any ALC's activity, sees the assigned SBU, previews
  evidence, and can Verify / Request Correction / Reject.
* A processed (VERIFIED) activity remains viewable via its detail endpoint.
* Direct-URL manipulation across SBUs is rejected (404).
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models import ActivityEvidence, User
from tests.conftest import login

payload = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "collaboration_type": "Pilot discussion",
    "activity_date": str(date.today()),
    "location": "Pune",
    "learners_reached": 25,
    "leads_generated": 10,
    "admissions_generated": 2,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


async def add_evidence(session, activity_id, uploader_username):
    user = await session.scalar(select(User).where(User.username == uploader_username))
    ev = ActivityEvidence(
        activity_id=uuid.UUID(activity_id),
        storage_key=f"private/{uuid.uuid4()}.pdf",
        original_filename="evidence.pdf",
        mime_type="application/pdf",
        file_size=5,
        uploaded_by=user.id,
    )
    session.add(ev)
    await session.commit()
    return ev


async def submit(client, session, code, password, uploader):
    await login(client, code, password, "ALC")
    activity = (await client.post("/api/portal/activities", json=payload)).json()
    ev = await add_evidence(session, activity["id"], uploader)
    await client.post(f"/api/portal/activities/{activity['id']}/submit")
    logout(client)
    return activity, ev


# --- SBU entry points ------------------------------------------------------ #
@pytest.mark.asyncio
async def test_sbu_opens_assigned_activity_not_other(client, session):
    a, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    b, _ = await submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (await client.get(f"/api/portal/verification/{a['id']}")).status_code == 200
    assert (await client.get(f"/api/portal/verification/{b['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_sbu_evidence_open_scoped(client, session):
    a, ev_a = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    _, ev_b = await submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (await client.get(f"/api/portal/evidence/{ev_a.id}/access")).status_code == 200
    assert (await client.get(f"/api/portal/evidence/{ev_b.id}/access")).status_code == 404


@pytest.mark.asyncio
async def test_sbu_decisions_on_assigned_activity(client, session):
    verify, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    correct, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    reject, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (
        await client.post(f"/api/portal/activities/{verify['id']}/verify", json={"remark": "ok"})
    ).json()["status"] == "VERIFIED"
    assert (
        await client.post(
            f"/api/portal/activities/{correct['id']}/request-correction", json={"remark": "fix"}
        )
    ).json()["status"] == "CORRECTION_REQUIRED"
    assert (
        await client.post(f"/api/portal/activities/{reject['id']}/reject", json={"remark": "no"})
    ).json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_sbu_cannot_act_on_other_sbu_activity(client, session):
    b, _ = await submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    for action in ("verify", "request-correction", "reject"):
        assert (
            await client.post(f"/api/portal/activities/{b['id']}/{action}", json={"remark": "x"})
        ).status_code == 404


# --- Admin entry points (global) ------------------------------------------- #
@pytest.mark.asyncio
async def test_admin_opens_any_activity_with_assigned_sbu(client, session):
    a, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    b, _ = await submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    da = (await client.get(f"/api/admin/activities/{a['id']}")).json()
    db_ = (await client.get(f"/api/admin/activities/{b['id']}")).json()
    assert da["alc"]["alc_code"] == "00010001" and da["sbu"]["code"] == "SBU 4"
    assert db_["alc"]["alc_code"] == "00010002" and db_["sbu"]["code"] == "SBU 6"


@pytest.mark.asyncio
async def test_admin_previews_evidence(client, session):
    _, ev = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await client.get(f"/api/admin/evidence/{ev.id}/access")).status_code == 200


@pytest.mark.asyncio
async def test_admin_decisions(client, session):
    verify, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    correct, _ = await submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    reject, _ = await submit(client, session, "00010003", "StrongAlcPassC!", "alc-c")  # SBU 4
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (
        await client.post(f"/api/admin/activities/{verify['id']}/verify", json={"remark": "ok"})
    ).json()["status"] == "VERIFIED"
    assert (
        await client.post(
            f"/api/admin/activities/{correct['id']}/request-correction", json={"remark": "fix"}
        )
    ).json()["status"] == "CORRECTION_REQUIRED"
    assert (
        await client.post(f"/api/admin/activities/{reject['id']}/reject", json={"remark": "no"})
    ).json()["status"] == "REJECTED"


# --- Processed activity stays viewable ------------------------------------- #
@pytest.mark.asyncio
async def test_processed_activity_remains_viewable(client, session):
    a, _ = await submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (
        await client.post(f"/api/portal/activities/{a['id']}/verify", json={"remark": "ok"})
    ).json()["status"] == "VERIFIED"
    # Still openable by the SBU after processing (View remains available).
    assert (await client.get(f"/api/portal/verification/{a['id']}")).status_code == 200
    logout(client)
    # And by the admin.
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    detail = await client.get(f"/api/admin/activities/{a['id']}")
    assert detail.status_code == 200 and detail.json()["activity"]["status"] == "VERIFIED"
