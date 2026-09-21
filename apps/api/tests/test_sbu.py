"""SBU role: authentication, assigned-ALC scoping, verification, evidence,
partners, password reset, reports, and admin separation."""
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


async def add_evidence(session, activity_id, uploader_username):
    user = await session.scalar(select(User).where(User.username == uploader_username))
    session.add(
        ActivityEvidence(
            activity_id=uuid.UUID(activity_id),
            storage_key=f"private/{uuid.uuid4()}.pdf",
            original_filename="evidence.pdf",
            mime_type="application/pdf",
            file_size=5,
            uploaded_by=user.id,
        )
    )
    await session.commit()


async def alc_submit(client, session, identifier, password, uploader):
    await login(client, identifier, password, "ALC")
    activity = (await client.post("/api/portal/activities", json=payload)).json()
    await add_evidence(session, activity["id"], uploader)
    await client.post(f"/api/portal/activities/{activity['id']}/submit")
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return activity


# --- Authentication -------------------------------------------------------- #
@pytest.mark.asyncio
async def test_admin_sbu_alc_login_and_roles(client):
    admin = await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert admin.status_code == 200 and admin.json()["role"] == "ADMIN"
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    sbu = await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert sbu.status_code == 200 and sbu.json()["role"] == "SBU"
    assert sbu.json()["sbu"]["code"] == "SBU 4"
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    alc = await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert alc.status_code == 200 and alc.json()["role"] == "ALC"


@pytest.mark.asyncio
async def test_invalid_credentials_generic(client):
    resp = await login(client, "sbu-4", "wrong-password", "SBU")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username/ALC code or password"


@pytest.mark.asyncio
async def test_sbu_must_change_password(client, session, seeded):
    seeded["sbu4_user"].must_change_password = True
    await session.commit()
    assert (await login(client, "sbu-4", "StrongSbuPass4!", "SBU")).status_code == 200
    assert (await client.get("/api/portal/dashboard")).status_code == 403
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": "StrongSbuPass4!", "new_password": "EvenStrongerSbuPassword!"},
    )
    assert changed.status_code == 200
    assert (await client.get("/api/portal/dashboard")).status_code == 200


# --- Admin separation ------------------------------------------------------ #
@pytest.mark.asyncio
async def test_admin_api_role_separation(client):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (await client.get("/api/admin/dashboard")).status_code == 403
    assert (await client.get("/api/admin/users")).status_code == 403
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (await client.get("/api/admin/dashboard")).status_code == 403
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await client.get("/api/admin/dashboard")).status_code == 200


@pytest.mark.asyncio
async def test_alc_cannot_use_sbu_endpoints(client):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (await client.get("/api/portal/alcs")).status_code == 403
    assert (await client.get("/api/portal/verification")).status_code == 403


# --- SBU ALC scoping ------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_lists_only_assigned_alcs(client):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    data = (await client.get("/api/portal/alcs")).json()
    codes = {row["alc_code"] for row in data["items"]}
    assert codes == {"00010001", "00010003"}  # Centre A and C, not B


@pytest.mark.asyncio
async def test_sbu_alc_detail_scope(client, seeded):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (await client.get(f"/api/portal/alcs/{seeded['alc_a'].id}")).status_code == 200
    # Centre B belongs to SBU 6 -> not visible to SBU 4
    assert (await client.get(f"/api/portal/alcs/{seeded['alc_b'].id}")).status_code == 404


# --- Activity access ------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_sees_assigned_activities_only(client, session):
    a = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    b = await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    queue = (await client.get("/api/portal/verification?queue_only=true")).json()
    ids = {row["activity"]["id"] for row in queue["items"]}
    assert a["id"] in ids and b["id"] not in ids
    assert (await client.get(f"/api/portal/verification/{a['id']}")).status_code == 200
    assert (await client.get(f"/api/portal/verification/{b['id']}")).status_code == 404


# --- Verification ---------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_verifies_assigned_activity(client, session):
    a = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    verified = await client.post(f"/api/portal/activities/{a['id']}/verify", json={"remark": "ok"})
    assert verified.status_code == 200 and verified.json()["status"] == "VERIFIED"
    reviews = verified.json()["reviews"]
    sbu_user = await session.scalar(select(User).where(User.username == "sbu-4"))
    assert reviews[-1]["action"] == "VERIFY"
    from app.models import ActivityReview

    review = await session.scalar(
        select(ActivityReview).where(ActivityReview.activity_id == uuid.UUID(a["id"]))
    )
    assert review.reviewer_id == sbu_user.id


@pytest.mark.asyncio
async def test_sbu_cannot_verify_other_sbu_activity(client, session):
    b = await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (
        await client.post(f"/api/portal/activities/{b['id']}/verify", json={"remark": "no"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_sbu_correction_and_reject_require_reason(client, session):
    a = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (
        await client.post(f"/api/portal/activities/{a['id']}/reject", json={})
    ).status_code == 422
    corrected = await client.post(
        f"/api/portal/activities/{a['id']}/request-correction", json={"remark": "clarify"}
    )
    assert corrected.status_code == 200 and corrected.json()["status"] == "CORRECTION_REQUIRED"


@pytest.mark.asyncio
async def test_alc_cannot_verify(client, session):
    a = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (
        await client.post(f"/api/portal/activities/{a['id']}/verify", json={"remark": "x"})
    ).status_code == 403


# --- Evidence -------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_evidence_scope(client, session):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    a = (await client.post("/api/portal/activities", json=payload)).json()
    await add_evidence(session, a["id"], "alc-a")
    ev_a = await session.scalar(
        select(ActivityEvidence).where(ActivityEvidence.activity_id == uuid.UUID(a["id"]))
    )
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "00010002", "StrongAlcPassB!", "ALC")
    b = (await client.post("/api/portal/activities", json=payload)).json()
    await add_evidence(session, b["id"], "alc-b")
    ev_b = await session.scalar(
        select(ActivityEvidence).where(ActivityEvidence.activity_id == uuid.UUID(b["id"]))
    )
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (await client.get(f"/api/portal/evidence/{ev_a.id}/access")).status_code == 200
    assert (await client.get(f"/api/portal/evidence/{ev_b.id}/access")).status_code == 404


# --- Partners -------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_partner_scope(client):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    await client.post(
        "/api/portal/partners",
        json={"partner_name": "Alpha School", "partner_type": "School", "ecosystem": "School"},
    )
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "00010002", "StrongAlcPassB!", "ALC")
    await client.post(
        "/api/portal/partners",
        json={"partner_name": "Beta College", "partner_type": "College", "ecosystem": "College"},
    )
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    names = {p["partner_name"] for p in (await client.get("/api/portal/partners")).json()}
    assert "Alpha School" in names and "Beta College" not in names


# --- Password reset -------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_resets_assigned_alc_password(client, session, seeded):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    reset = await client.post(
        f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password",
        json={"password": "BrandNewAlcPass123!"},
    )
    assert reset.status_code == 200
    await session.refresh(seeded["a"])
    assert seeded["a"].must_change_password is True
    # New password works and forces change
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await login(client, "00010001", "BrandNewAlcPass123!", "ALC")).status_code == 200


@pytest.mark.asyncio
async def test_sbu_cannot_reset_other_sbu_alc(client, seeded):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (
        await client.post(
            f"/api/portal/alcs/{seeded['alc_b'].id}/reset-password",
            json={"password": "BrandNewAlcPass123!"},
        )
    ).status_code == 404


# --- Reports --------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_report_scoped(client, session, seeded):
    await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    body = (await client.get("/api/portal/reports/activities.csv")).text
    assert "Centre A" in body and "Centre B" not in body
    # Injecting another SBU's alc_id yields no unauthorized data
    filtered = await client.get(f"/api/portal/reports/activities.csv?alc_id={seeded['alc_b'].id}")
    assert filtered.status_code == 404


# --- Admin SBU management -------------------------------------------------- #
@pytest.mark.asyncio
async def test_admin_creates_sbu_and_user(client, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    created = await client.post("/api/admin/sbus", json={"code": "SBU 9", "name": "Unit 9"})
    assert created.status_code == 201
    sbu_id = created.json()["id"]
    listed = (await client.get("/api/admin/sbus")).json()
    assert any(row["code"] == "SBU 9" for row in listed["items"])
    user = await client.post(
        "/api/admin/users",
        json={"username": "sbu-9", "role": "SBU", "sbu_id": sbu_id, "password": "StrongSbuPass9!"},
    )
    assert user.status_code == 201 and user.json()["sbu_id"] == sbu_id
    # SBU user cannot also carry an ALC
    bad = await client.post(
        "/api/admin/users",
        json={
            "username": "sbu-bad",
            "role": "SBU",
            "alc_id": str(seeded["alc_a"].id),
            "password": "StrongSbuPassX!",
        },
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_admin_assigns_alc_to_sbu(client, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    resp = await client.patch(
        f"/api/admin/alcs/{seeded['alc_b'].id}", json={"sbu_id": str(seeded["sbu4"].id)}
    )
    assert resp.status_code == 200
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    codes = {row["alc_code"] for row in (await client.get("/api/portal/alcs")).json()["items"]}
    assert "00010002" in codes  # now assigned to SBU 4
