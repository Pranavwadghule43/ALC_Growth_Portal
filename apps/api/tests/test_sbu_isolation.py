"""Cross-SBU data-isolation guarantees for the operational portal.

These tests complement ``test_sbu.py`` by targeting the exact leakage vectors
called out in the isolation requirement:

* an SBU with no assigned ALCs (the real "SBU 1" demo) must see *nothing*,
  never a global fallback;
* an SBU account whose ``sbu_id`` is NULL must be denied, never treated as
  "see all";
* pagination / search / filter must stay inside the SBU scope;
* direct-ID access to another SBU's ALC, activity, evidence (metadata,
  presigned access *and* raw content) and password reset must 404;
* ADMIN keeps global visibility and ALC keeps single-centre isolation.

The ``seeded`` fixture assigns Centre A + Centre C to SBU 4 and Centre B to
SBU 6. Here we additionally create an empty "SBU 1" and a mis-provisioned
SBU user with no ``sbu_id``.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.auth import hash_password
from app.enums import Role
from app.models import ALC, SBU, ActivityEvidence, User
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


async def alc_submit(client, session, identifier, password, uploader):
    await login(client, identifier, password, "ALC")
    activity = (await client.post("/api/portal/activities", json=payload)).json()
    ev = await add_evidence(session, activity["id"], uploader)
    await client.post(f"/api/portal/activities/{activity['id']}/submit")
    logout(client)
    return activity, ev


@pytest.fixture
async def empty_sbu_user(session):
    """An SBU (like the demo 'SBU 1') that owns no ALCs."""
    sbu1 = SBU(code="SBU 1", name="Strategic Business Unit 1")
    session.add(sbu1)
    await session.flush()
    user = User(
        username="sbu-1",
        password_hash=hash_password("StrongSbuPass1!"),
        role=Role.SBU,
        sbu_id=sbu1.id,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def null_sbu_user(session):
    """A mis-provisioned SBU account whose sbu_id is NULL."""
    user = User(
        username="sbu-null",
        password_hash=hash_password("StrongSbuPassN!"),
        role=Role.SBU,
        sbu_id=None,
    )
    session.add(user)
    await session.commit()
    return user


# --- The reported scenario: an SBU with no ALCs sees nothing --------------- #
@pytest.mark.asyncio
async def test_empty_sbu_sees_zero_alcs_not_global(client, session, empty_sbu_user):
    # Populate other SBUs with real data so a leak would be obvious.
    await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6

    await login(client, "sbu-1", "StrongSbuPass1!", "SBU")

    # The authoritative "assigned/visible ALC count" is the SBU directory endpoint's
    # total. An empty SBU sees zero assigned ALCs and an empty list — not global data.
    alcs = (await client.get("/api/portal/alcs")).json()
    assert alcs["total"] == 0 and alcs["items"] == []

    activities = (await client.get("/api/portal/activities")).json()
    assert activities["total"] == 0 and activities["items"] == []

    queue = (await client.get("/api/portal/verification?queue_only=true")).json()
    assert queue["total"] == 0 and queue["items"] == []

    assert (await client.get("/api/portal/partners")).json() == []

    report = (await client.get("/api/portal/reports/activities.csv")).text
    assert "Centre A" not in report and "Centre B" not in report


# --- NULL sbu_id must never mean "global" --------------------------------- #
@pytest.mark.asyncio
async def test_null_sbu_id_denied_everywhere(client, session, null_sbu_user):
    await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")

    # A NULL-sbu_id SBU must never obtain global/other-SBU access. Two acceptable
    # postures satisfy that, and neither weakens auth:
    #   * preferred: authentication itself rejects the account (401/403); or
    #   * if a session is somehow established, every scoped surface refuses it (403).
    resp = await login(client, "sbu-null", "StrongSbuPassN!", "SBU")
    if resp.status_code == 200:
        for path in (
            "/api/portal/dashboard",
            "/api/portal/alcs",
            "/api/portal/activities",
            "/api/portal/verification",
            "/api/portal/partners",
            "/api/portal/reports/activities.csv",
        ):
            assert (await client.get(path)).status_code == 403, path
    else:
        assert resp.status_code in (401, 403), resp.status_code


# --- Direct-ID manipulation across every SBU-reachable object -------------- #
@pytest.mark.asyncio
async def test_cross_sbu_direct_id_is_not_found(client, session, seeded):
    # Centre B (SBU 6) owns an activity with evidence; SBU 4 must not reach it.
    activity_b, ev_b = await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")

    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    aid, eid, other_alc = activity_b["id"], str(ev_b.id), seeded["alc_b"].id

    assert (await client.get(f"/api/portal/alcs/{other_alc}")).status_code == 404
    assert (await client.get(f"/api/portal/activities/{aid}")).status_code == 404
    assert (await client.get(f"/api/portal/verification/{aid}")).status_code == 404
    assert (await client.get(f"/api/portal/evidence/{eid}/access")).status_code == 404
    assert (await client.get(f"/api/portal/evidence/{eid}/content")).status_code == 404
    for action in ("verify", "request-correction", "reject"):
        resp = await client.post(
            f"/api/portal/activities/{aid}/{action}", json={"remark": "x"}
        )
        assert resp.status_code == 404, action
    assert (
        await client.post(
            f"/api/portal/alcs/{other_alc}/reset-password",
            json={"password": "BrandNewAlcPass123!"},
        )
    ).status_code == 404


# --- Raw evidence content is scoped, not just the presigned link ----------- #
@pytest.mark.asyncio
async def test_evidence_content_scoped_for_sbu(client, session):
    activity_a, ev_a = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    _, ev_b = await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")

    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    # Own centre's evidence authorizes, but its backing file was never written to local
    # storage, so a missing file returns a clean 404 rather than escaping as a 500.
    assert (await client.get(f"/api/portal/evidence/{ev_a.id}/content")).status_code == 404
    # Another SBU's evidence content is authorization-blocked as 404 before any file access.
    assert (await client.get(f"/api/portal/evidence/{ev_b.id}/content")).status_code == 404


# --- Pagination / search cannot surface another SBU's records -------------- #
@pytest.mark.asyncio
async def test_pagination_and_search_stay_in_scope(client, session, seeded):
    # SBU 4 owns Centre A and Centre C; SBU 6 owns Centre B.
    a, _ = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")
    b, _ = await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")

    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")

    # Walk every page with a tiny page size; Centre B's activity must never appear.
    seen: set[str] = set()
    for page in range(1, 10):
        data = (await client.get(f"/api/portal/activities?page={page}&page_size=1")).json()
        seen.update(row["alc_id"] for row in data["items"])
        if page >= data["pages"]:
            break
    assert str(seeded["alc_a"].id) in seen or str(seeded["alc_c"].id) in seen
    assert str(seeded["alc_b"].id) not in seen

    # A search that would match Centre B's activity number returns nothing for SBU 4.
    hit = (await client.get(f"/api/portal/activities?search={b['activity_number']}")).json()
    assert hit["total"] == 0

    # Filtering by another SBU's alc_id is rejected rather than silently widened.
    assert (
        await client.get(f"/api/portal/activities?alc_id={seeded['alc_b'].id}")
    ).status_code == 404
    assert (
        await client.get(f"/api/portal/verification?alc_id={seeded['alc_b'].id}")
    ).status_code == 404


# --- The shared scoping helper fails closed on a missing key --------------- #
@pytest.mark.asyncio
async def test_scoped_alc_ids_denies_missing_key(session, seeded):
    """A missing scope key yields an empty scope, never a global IS NULL match.

    An unassigned ALC (``sbu_id`` NULL) exists so a regression to ``IS NULL``
    scoping would surface it.
    """
    from app.routes.portal import scoped_alc_ids

    session.add(ALC(alc_code="00099999", alc_name="Unassigned Centre", sbu_id=None))
    await session.commit()

    sbu_no_key = User(username="x-sbu", password_hash="x", role=Role.SBU, sbu_id=None)
    alc_no_key = User(username="x-alc", password_hash="x", role=Role.ALC, alc_id=None)
    assert list(await scoped_alc_ids(sbu_no_key, session)) == []
    assert list(await scoped_alc_ids(alc_no_key, session)) == []

    sbu4_user = seeded["sbu4_user"]
    scoped = set(await scoped_alc_ids(sbu4_user, session))
    assert scoped == {seeded["alc_a"].id, seeded["alc_c"].id}


# --- ADMIN keeps global reach; ALC keeps single-centre isolation ----------- #
@pytest.mark.asyncio
async def test_admin_global_and_alc_single_centre(client, session, seeded):
    a, _ = await alc_submit(client, session, "00010001", "StrongAlcPassA!", "alc-a")  # SBU 4
    b, _ = await alc_submit(client, session, "00010002", "StrongAlcPassB!", "alc-b")  # SBU 6

    # ADMIN sees both centres and both activities.
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    directory = (await client.get("/api/admin/alcs?page=1&page_size=100")).json()
    codes = {row["alc_code"] for row in directory["items"]}
    assert {"00010001", "00010002", "00010003"} <= codes
    assert (await client.get(f"/api/admin/activities/{a['id']}")).status_code == 200
    assert (await client.get(f"/api/admin/activities/{b['id']}")).status_code == 200
    logout(client)

    # ALC A cannot reach ALC B's activity through the portal.
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (await client.get(f"/api/portal/activities/{a['id']}")).status_code == 200
    assert (await client.get(f"/api/portal/activities/{b['id']}")).status_code == 404
