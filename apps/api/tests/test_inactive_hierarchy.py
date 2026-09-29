"""Phase 4E: an operational user is available only while its whole chain is active.

    ADMIN: User.is_active
    DCU:   User -> DCU -> RCU
    SBU:   User -> SBU -> DCU -> RCU
    ALC:   User -> ALC -> SBU -> DCU -> RCU

A missing link fails closed. The check runs on login, on refresh and on every request, so an
existing session stops working as soon as anything above it is deactivated. Deactivation
never deletes data: Admin can still see everything.

Self-contained fixtures: ``seeded`` places SBU 4 / SBU 6 under DCU Nashik, RCU Pune.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import hash_password
from app.enums import AlcStatus, Role
from app.main import app
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    AuditLog,
    User,
)
from app.models import Activity as ActivityModel
from tests.conftest import login

ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
DCU_N = ("dcu-test-nashik", "StrongDcuPass123!", "DCU")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
UNAVAILABLE = "Account unavailable"

ACTIVITY = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "activity_date": str(date.today()),
    "location": "Nashik",
    "learners_reached": 12,
    "leads_generated": 3,
    "admissions_generated": 1,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}


@pytest.fixture
async def chain(session, seeded):
    """The active chain above Centre A, plus a DCU login for DCU Nashik."""
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    rcu = await session.scalar(select(RCU).where(RCU.id == nashik.rcu_id))
    session.add(
        User(
            username=DCU_N[0],
            password_hash=hash_password(DCU_N[1]),
            role=Role.DCU,
            dcu_id=nashik.id,
            must_change_password=False,
        )
    )
    await session.commit()
    return {
        "alc": seeded["alc_a"],
        "sbu": seeded["sbu4"],
        "dcu": nashik,
        "rcu": rcu,
        "alc_user": seeded["a"],
        "sbu_user": seeded["sbu4_user"],
    }


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


@asynccontextmanager
async def admin_client():
    """A separate browser signed in as Admin (the test DB override applies to the app)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await login(c, *ADMIN)).status_code == 200
        yield c


async def works(client) -> bool:
    """Can this session still use a protected operational endpoint?"""
    resp = await client.get("/api/portal/activities")
    return resp.status_code == 200


async def assert_cut_off(client):
    for path in ("/api/portal/activities", "/api/auth/me"):
        resp = await client.get(path)
        assert resp.status_code == 401 and resp.json()["detail"] == UNAVAILABLE, (path, resp.text)


async def assert_login_unavailable(client, who):
    logout(client)
    resp = await login(client, *who)
    assert resp.status_code == 401 and resp.json()["detail"] == UNAVAILABLE, resp.text
    assert "refresh_token" not in resp.cookies  # no session was created


# --------------------------------------------------------------------------- #
# Login: every level of the chain must be active
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_inactive_user_cannot_login(client, session, chain):
    for user, who in ((chain["alc_user"], ALC_A), (chain["sbu_user"], SBU_4)):
        user.is_active = False
        await session.commit()
        logout(client)
        resp = await login(client, *who)
        assert resp.status_code == 401  # generic invalid-credentials response, as before


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["dcu", "rcu"])
async def test_dcu_login_needs_active_dcu_and_rcu(client, session, chain, level):
    chain[level].is_active = False
    await session.commit()
    await assert_login_unavailable(client, DCU_N)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["sbu", "dcu", "rcu"])
async def test_sbu_login_needs_active_sbu_dcu_and_rcu(client, session, chain, level):
    chain[level].is_active = False
    await session.commit()
    await assert_login_unavailable(client, SBU_4)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["alc", "sbu", "dcu", "rcu"])
async def test_alc_login_needs_active_alc_sbu_dcu_and_rcu(client, session, chain, level):
    if level == "alc":
        chain["alc"].status = AlcStatus.INACTIVE
    else:
        chain[level].is_active = False
    await session.commit()
    await assert_login_unavailable(client, ALC_A)


# --------------------------------------------------------------------------- #
# Missing links fail closed (never "unrestricted")
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_alc_without_sbu_fails_closed(client, session, chain):
    orphan = ALC(alc_code="00099001", alc_name="Orphan Centre", sbu_id=None)
    session.add(orphan)
    await session.flush()
    session.add(
        User(username="alc-orphan", password_hash=hash_password("OrphanAlcPass1!"),
             role=Role.ALC, alc_id=orphan.id, must_change_password=False)
    )
    await session.commit()
    await assert_login_unavailable(client, ("00099001", "OrphanAlcPass1!", "ALC"))


@pytest.mark.asyncio
async def test_sbu_without_dcu_fails_closed(client, session, chain):
    loose = SBU(code="SBU 99", name="SBU without DCU", dcu_id=None)
    session.add(loose)
    await session.flush()
    session.add(
        User(username="sbu-loose", password_hash=hash_password("LooseSbuPass1!"),
             role=Role.SBU, sbu_id=loose.id, must_change_password=False)
    )
    await session.commit()
    await assert_login_unavailable(client, ("sbu-loose", "LooseSbuPass1!", "SBU"))


@pytest.mark.asyncio
async def test_dcu_without_rcu_fails_closed(client, session, chain):
    # rcu_id is NOT NULL in the schema, so the realistic broken link is a DCU whose RCU row is
    # gone (SQLite in tests does not enforce the foreign key, which lets us build that).
    dangling = DCU(code="DCU_DANGLING", name="DCU with missing RCU", rcu_id=uuid.uuid4())
    session.add(dangling)
    await session.flush()
    session.add(
        User(username="dcu-dangling", password_hash=hash_password("DanglingDcu1!"),
             role=Role.DCU, dcu_id=dangling.id, must_change_password=False)
    )
    await session.commit()
    await assert_login_unavailable(client, ("dcu-dangling", "DanglingDcu1!", "DCU"))


@pytest.mark.asyncio
async def test_alc_under_sbu_without_dcu_fails_closed(client, session, chain):
    chain["sbu"].dcu_id = None  # break the chain one level above the ALC's SBU
    await session.commit()
    await assert_login_unavailable(client, ALC_A)


# --------------------------------------------------------------------------- #
# Existing sessions stop working on the next request
# --------------------------------------------------------------------------- #
async def deactivate(session, admin, chain, level):
    """Deactivate one level: through the Admin API where one exists, otherwise in the DB
    (there is no Admin endpoint to deactivate a DCU or an RCU)."""
    if level in ("user_alc", "user_sbu"):
        user = chain["alc_user"] if level == "user_alc" else chain["sbu_user"]
        resp = await admin.patch(f"/api/admin/users/{user.id}", json={"is_active": False})
    elif level == "alc":
        resp = await admin.patch(f"/api/admin/alcs/{chain['alc'].id}", json={"status": "INACTIVE"})
    elif level == "sbu":
        resp = await admin.patch(f"/api/admin/sbus/{chain['sbu'].id}", json={"is_active": False})
    else:
        chain[level].is_active = False
        await session.commit()
        return
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["user_alc", "alc", "sbu", "dcu", "rcu"])
async def test_active_alc_session_is_cut_off(client, session, chain, level):
    await login(client, *ALC_A)
    assert await works(client)
    async with admin_client() as admin:
        await deactivate(session, admin, chain, level)
    await assert_cut_off(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["user_sbu", "sbu", "dcu", "rcu"])
async def test_active_sbu_session_is_cut_off(client, session, chain, level):
    await login(client, *SBU_4)
    assert await works(client)
    async with admin_client() as admin:
        await deactivate(session, admin, chain, level)
    await assert_cut_off(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["dcu", "rcu"])
async def test_active_dcu_session_is_cut_off(client, session, chain, level):
    await login(client, *DCU_N)
    assert await works(client)
    async with admin_client() as admin:
        await deactivate(session, admin, chain, level)
    await assert_cut_off(client)


# --------------------------------------------------------------------------- #
# Refresh cannot restore access
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("who,level", [(ALC_A, "sbu"), (SBU_4, "dcu"), (DCU_N, "rcu")])
async def test_refresh_cannot_restore_access(client, session, chain, who, level):
    await login(client, *who)
    refresh_token = client.cookies.get("refresh_token")
    chain[level].is_active = False
    await session.commit()

    # The access token "expires": only the refresh cookie is left.
    logout(client)
    resp = await client.post(
        "/api/auth/refresh", headers={"Cookie": f"refresh_token={refresh_token}"}
    )
    assert resp.status_code == 401 and resp.json()["detail"] == "Session expired"
    assert "access_token" not in resp.cookies
    assert (await client.get("/api/auth/me")).status_code == 401


# --------------------------------------------------------------------------- #
# Admin keeps full historical visibility; nothing is deleted
# --------------------------------------------------------------------------- #
async def counts(session) -> dict:
    models = (ActivityModel, ActivityEvidence, ActivityReview, ActivityRevision, ALC, SBU, DCU, RCU)
    return {m.__name__: await session.scalar(select(func.count()).select_from(m)) for m in models}


@pytest.mark.asyncio
async def test_admin_still_sees_inactive_hierarchy_and_history(client, session, chain):
    # History: ALC submits with evidence, SBU asks for a correction.
    await login(client, *ALC_A)
    activity = (await client.post("/api/portal/activities", json=ACTIVITY)).json()
    session.add(
        ActivityEvidence(
            activity_id=uuid.UUID(activity["id"]), storage_key=f"private/{uuid.uuid4()}.pdf",
            original_filename="attendance.pdf", mime_type="application/pdf", file_size=10,
            uploaded_by=chain["alc_user"].id,
        )
    )
    await session.commit()
    assert (await client.post(f"/api/portal/activities/{activity['id']}/submit")).status_code == 200
    logout(client)
    await login(client, *SBU_4)
    resp = await client.post(
        f"/api/portal/activities/{activity['id']}/request-correction", json={"remark": "Add photos"}
    )
    assert resp.status_code == 200
    before = await counts(session)

    # Everything above the ALC is switched off.
    chain["alc"].status = AlcStatus.INACTIVE
    for level in ("sbu", "dcu", "rcu"):
        chain[level].is_active = False
    await session.commit()

    async with admin_client() as admin:
        detail = (await admin.get(f"/api/admin/activities/{activity['id']}")).json()["activity"]
        assert [e["original_filename"] for e in detail["evidence"]] == ["attendance.pdf"]
        assert [r["action"] for r in detail["reviews"]] == ["REQUEST_CORRECTION"]
        assert [r["revision_number"] for r in detail["revisions"]] == [1]
        for path in (
            f"/api/admin/alcs/{chain['alc'].id}",
            f"/api/admin/sbus/{chain['sbu'].id}",
            f"/api/admin/dcus/{chain['dcu'].id}",
        ):
            assert (await admin.get(path)).status_code == 200, path
        listed = (await admin.get("/api/admin/activities")).json()
        assert activity["id"] in [row["activity"]["id"] for row in listed["items"]]
        audit = (await admin.get("/api/admin/audit-logs")).json()
        assert audit["total"] > 0

    assert await counts(session) == before  # deactivation deleted nothing
    audit_rows = await session.scalar(select(func.count()).select_from(AuditLog))
    assert audit_rows > 0


@pytest.mark.asyncio
async def test_admin_login_does_not_depend_on_any_hierarchy(client, session, chain):
    chain["alc"].status = AlcStatus.INACTIVE
    for level in ("sbu", "dcu", "rcu"):
        chain[level].is_active = False
    await session.commit()
    resp = await login(client, *ADMIN)
    assert resp.status_code == 200 and resp.json()["role"] == "ADMIN"
    assert (await client.get("/api/admin/sbus")).status_code == 200


# --------------------------------------------------------------------------- #
# Regression: a fully active chain works for every operational role
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("who,role", [(DCU_N, "DCU"), (SBU_4, "SBU"), (ALC_A, "ALC")])
async def test_fully_active_chain_works(client, chain, who, role):
    resp = await login(client, *who)
    assert resp.status_code == 200 and resp.json()["role"] == role
    assert await works(client)
    assert (await client.post("/api/auth/refresh")).status_code == 200
    assert (await client.get("/api/auth/me")).json()["role"] == role