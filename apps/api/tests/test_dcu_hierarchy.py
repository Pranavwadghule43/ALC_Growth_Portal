"""RCU → DCU → SBU → ALC hierarchy: seeding, DCU authentication, hierarchy-aware scope,
cross-DCU isolation, fail-closed missing keys, and reassignment without data loss.

The ``hier`` fixture builds a realistic hierarchy on top of ``conftest.seeded``:

* the real ALC master (``data/ALC-MASTER.csv``, 199 ALCs) is imported into SBU 4 / 6 / 7;
* ``ensure_hierarchy`` creates RCU Pune + its four DCUs and links SBU 4 / 6 / 7 → DCU Nashik;
* a test-only SBU ``TEST PN`` is placed under DCU Pune North and receives conftest's
  Centres A, B and C (with their ALC logins) as "another DCU's" data;
* a demo ``SBU 1`` is left without a DCU.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import create_access_token, hash_password
from app.enums import Role
from app.main import app
from app.models import ALC, DCU, RCU, SBU, Activity, ActivityEvidence, User
from app.services import alc_import
from app.services.hierarchy import ensure_hierarchy
from app.services.scope import accessible_alc_ids
from tests.conftest import login

MASTER = Path(__file__).resolve().parents[3] / "data" / "ALC-MASTER.csv"
PW = "StrongDcuPass123!"
PW_HASH = hash_password(PW)
NASHIK_ALC_SBU4 = "57210164"  # Jayesh Computers, SBU 4
NASHIK_ALC_SBU7 = "57210168"  # Shree Computers, SBU 7

payload = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "collaboration_type": "Pilot discussion",
    "activity_date": str(date.today()),
    "location": "Nashik",
    "learners_reached": 25,
    "leads_generated": 10,
    "admissions_generated": 2,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
async def hier(session, seeded):
    sbu7 = SBU(code="SBU 7", name="Strategic Business Unit 7")
    sbu1 = SBU(code="SBU 1", name="Demo Strategic Business Unit 1")
    test_pn = SBU(code="TEST PN", name="Test SBU under Pune North")
    session.add_all([sbu7, sbu1, test_pn])
    await session.commit()

    records = alc_import.parse_source(MASTER.name, MASTER.read_bytes())
    await alc_import.perform(session, records)
    await ensure_hierarchy(session)

    dcus = {d.code: d for d in (await session.scalars(select(DCU))).all()}
    test_pn.dcu_id = dcus["DCU_PUNE_NORTH"].id
    for key in ("alc_a", "alc_b", "alc_c"):
        seeded[key].sbu_id = test_pn.id

    alcs = {
        a.alc_code: a
        for a in (
            await session.scalars(
                select(ALC).where(ALC.alc_code.in_([NASHIK_ALC_SBU4, NASHIK_ALC_SBU7]))
            )
        ).all()
    }

    def user(username, role, **keys):
        return User(username=username, password_hash=PW_HASH, role=role, **keys)

    users = {
        "dcu_nashik": user("dcu-nashik", Role.DCU, dcu_id=dcus["DCU_NASHIK"].id),
        "dcu_pn": user("dcu-pn", Role.DCU, dcu_id=dcus["DCU_PUNE_NORTH"].id),
        "dcu_ahilya": user("dcu-ahilya", Role.DCU, dcu_id=dcus["DCU_AHILYA_NAGAR"].id),
        "dcu_null": user("dcu-null", Role.DCU, dcu_id=None),
        "sbu7_user": user("sbu-7", Role.SBU, sbu_id=sbu7.id),
        "alc4": user("nashik-alc-4", Role.ALC, alc_id=alcs[NASHIK_ALC_SBU4].id),
        "alc7": user("nashik-alc-7", Role.ALC, alc_id=alcs[NASHIK_ALC_SBU7].id),
    }
    session.add_all(users.values())
    await session.commit()
    return {
        **seeded,
        **users,
        "dcus": dcus,
        "sbu7": sbu7,
        "sbu1": sbu1,
        "test_pn": test_pn,
        "alc4_centre": alcs[NASHIK_ALC_SBU4],
        "alc7_centre": alcs[NASHIK_ALC_SBU7],
    }


@asynccontextmanager
async def new_client():
    """An extra client with its own cookie jar (the DB override from ``client`` applies)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


async def as_user(client, identifier, password=PW, portal="PORTAL"):
    logout(client)
    resp = await login(client, identifier, password, portal)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def submit_activity(client, session, alc_identifier, alc_password=PW, with_partner=False):
    """Log in as an ALC, create (optionally with a partner) and submit an activity with
    evidence. Returns (activity_json, evidence, partner_json_or_None)."""
    await as_user(client, alc_identifier, alc_password)
    partner = None
    body = dict(payload)
    if with_partner:
        partner = (
            await client.post(
                "/api/portal/partners",
                json={"partner_name": "Nashik College", "partner_type": "College",
                      "ecosystem": "College"},
            )
        ).json()
        body["partner_id"] = partner["id"]
    activity = (await client.post("/api/portal/activities", json=body)).json()
    uploader = await session.scalar(select(User.id).where(User.role == Role.ALC).limit(1))
    evidence = ActivityEvidence(
        activity_id=uuid.UUID(activity["id"]),
        storage_key=f"private/{uuid.uuid4()}.pdf",
        original_filename="evidence.pdf",
        mime_type="application/pdf",
        file_size=5,
        uploaded_by=uploader,
    )
    session.add(evidence)
    await session.commit()
    submitted = await client.post(f"/api/portal/activities/{activity['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    logout(client)
    return activity, evidence, partner


async def master_counts_by_sbu(session) -> dict[str, int]:
    codes = {r.alc_code for r in alc_import.parse_source(MASTER.name, MASTER.read_bytes())}
    rows = (
        await session.execute(
            select(SBU.code, func.count(ALC.id))
            .join(ALC, ALC.sbu_id == SBU.id)
            .where(ALC.alc_code.in_(codes))
            .group_by(SBU.code)
        )
    ).all()
    return dict(rows)


# --------------------------------------------------------------------------- #
# Seed / backfill
# --------------------------------------------------------------------------- #
async def test_seed_creates_hierarchy_and_links_only_nashik(session, hier):
    rcus = (await session.scalars(select(RCU))).all()
    assert [r.code for r in rcus] == ["RCU_PUNE"]
    dcus = (await session.scalars(select(DCU).order_by(DCU.code))).all()
    assert [d.code for d in dcus] == [
        "DCU_AHILYA_NAGAR", "DCU_NASHIK", "DCU_PUNE_NORTH", "DCU_PUNE_SOUTH",
    ]
    assert {d.rcu_id for d in dcus} == {rcus[0].id}

    nashik = hier["dcus"]["DCU_NASHIK"].id
    placed = dict((await session.execute(select(SBU.code, SBU.dcu_id))).all())
    assert placed["SBU 4"] == placed["SBU 6"] == placed["SBU 7"] == nashik
    assert placed["SBU 1"] is None  # demo SBU is never silently assigned to a real DCU

    # The 199 ALC → SBU mappings are untouched and not duplicated.
    assert await master_counts_by_sbu(session) == {"SBU 4": 71, "SBU 6": 58, "SBU 7": 70}

    # Idempotent: a re-run creates nothing and duplicates nothing.
    counts_before = (
        await session.scalar(select(func.count(SBU.id))),
        await session.scalar(select(func.count(ALC.id))),
    )
    again = await ensure_hierarchy(session)
    await session.commit()
    assert again == {"rcus_created": 0, "dcus_created": 0, "sbus_linked": [], "conflicts": []}
    assert await session.scalar(select(func.count(DCU.id))) == 4
    assert (
        await session.scalar(select(func.count(SBU.id))),
        await session.scalar(select(func.count(ALC.id))),
    ) == counts_before


async def test_seed_never_overrides_an_existing_dcu_assignment(session, hier):
    sbu6 = hier["sbu6"]
    sbu6.dcu_id = hier["dcus"]["DCU_PUNE_SOUTH"].id
    await session.commit()
    summary = await ensure_hierarchy(session)
    await session.commit()
    assert summary["conflicts"] == ["SBU 6"]
    await session.refresh(sbu6)
    assert sbu6.dcu_id == hier["dcus"]["DCU_PUNE_SOUTH"].id


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
async def test_dcu_logs_in_through_portal_login_only(client, hier):
    me = await as_user(client, "dcu-nashik")
    assert me["role"] == "DCU"
    assert me["dcu"]["code"] == "DCU_NASHIK" and me["dcu_id"] == str(hier["dcus"]["DCU_NASHIK"].id)
    assert (await client.get("/api/auth/me")).json()["role"] == "DCU"

    # The admin endpoint never authenticates a DCU, and /login never authenticates ADMIN.
    logout(client)
    assert (await login(client, "dcu-nashik", PW, "ADMIN")).status_code == 401
    assert (await login(client, "admin", "StrongAdminPass!", "PORTAL")).status_code == 401


async def test_dcu_without_dcu_or_with_inactive_dcu_is_denied(client, session, hier):
    # Unlinked DCU login: authentication itself refuses it.
    assert (await login(client, "dcu-null", PW, "PORTAL")).status_code == 401

    # Even with a forged/stale session token, every surface refuses it.
    logout(client)
    client.cookies.set("access_token", create_access_token(hier["dcu_null"].id))
    for path in ("/api/portal/dashboard", "/api/portal/alcs", "/api/portal/activities",
                 "/api/portal/sbus", "/api/portal/reports/activities.csv"):
        assert (await client.get(path)).status_code in (401, 403), path

    # A DCU whose DCU master is deactivated loses its session immediately.
    logout(client)
    await as_user(client, "dcu-ahilya")
    hier["dcus"]["DCU_AHILYA_NAGAR"].is_active = False
    await session.commit()
    assert (await client.get("/api/portal/dashboard")).status_code == 401
    logout(client)
    assert (await login(client, "dcu-ahilya", PW, "PORTAL")).status_code == 401


# --------------------------------------------------------------------------- #
# Admin: global view of the hierarchy, DCU user provisioning
# --------------------------------------------------------------------------- #
async def test_admin_sees_all_dcus_sbus_and_alcs(client, hier):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    dcus = {d["code"]: d for d in (await client.get("/api/admin/dcus")).json()["items"]}
    assert set(dcus) == {"DCU_AHILYA_NAGAR", "DCU_NASHIK", "DCU_PUNE_NORTH", "DCU_PUNE_SOUTH"}
    assert (dcus["DCU_NASHIK"]["sbus"], dcus["DCU_NASHIK"]["alcs"]) == (3, 199)
    assert (dcus["DCU_PUNE_NORTH"]["sbus"], dcus["DCU_PUNE_NORTH"]["alcs"]) == (1, 3)
    assert dcus["DCU_PUNE_SOUTH"]["alcs"] == 0 and dcus["DCU_NASHIK"]["rcu_code"] == "RCU_PUNE"

    detail = (await client.get(f"/api/admin/dcus/{dcus['DCU_NASHIK']['id']}")).json()
    assert {s["code"]: s["assigned_alcs"] for s in detail["sbus"]} == {
        "SBU 4": 71, "SBU 6": 58, "SBU 7": 70,
    }
    assert [u["username"] for u in detail["users"]] == ["dcu-nashik"]

    sbu_rows = (await client.get("/api/admin/sbus?page_size=100")).json()["items"]
    sbus = {s["code"]: s for s in sbu_rows}
    assert set(sbus) == {"SBU 1", "SBU 4", "SBU 6", "SBU 7", "TEST PN"}
    assert sbus["SBU 1"]["dcu_id"] is None and sbus["SBU 4"]["dcu_code"] == "DCU_NASHIK"
    assert (await client.get("/api/admin/alcs?page_size=1")).json()["total"] == 202


async def test_admin_creates_dcu_user_linked_to_exactly_one_dcu(client, hier):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    nashik = str(hier["dcus"]["DCU_NASHIK"].id)
    base = {"username": "dcu-new", "role": "DCU", "password": "BrandNewDcuPass123!"}
    assert (await client.post("/api/admin/users", json=base)).status_code == 422
    assert (
        await client.post("/api/admin/users", json={**base, "dcu_id": str(uuid.uuid4())})
    ).status_code == 422
    for extra in ({"sbu_id": str(hier["sbu4"].id)}, {"alc_id": str(hier["alc_a"].id)}):
        resp = await client.post("/api/admin/users", json={**base, "dcu_id": nashik, **extra})
        assert resp.status_code == 422, extra
    # SBU / ALC / ADMIN accounts cannot carry a dcu_id.
    for role, key in (("SBU", "sbu_id"), ("ALC", "alc_id")):
        ref = str(hier["sbu4"].id) if role == "SBU" else str(hier["alc_a"].id)
        resp = await client.post(
            "/api/admin/users",
            json={**base, "username": f"x-{role}", "role": role, key: ref, "dcu_id": nashik},
        )
        assert resp.status_code == 422, role
    created = await client.post("/api/admin/users", json={**base, "dcu_id": nashik})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "DCU" and body["dcu"]["code"] == "DCU_NASHIK"
    assert body["sbu_id"] is None and body["alc_id"] is None


# --------------------------------------------------------------------------- #
# DCU scope: own region only
# --------------------------------------------------------------------------- #
async def test_dcu_nashik_sees_exactly_its_three_sbus_and_199_alcs(client, hier):
    await as_user(client, "dcu-nashik")
    sbus = (await client.get("/api/portal/sbus")).json()
    assert sorted(s["code"] for s in sbus["items"]) == ["SBU 4", "SBU 6", "SBU 7"]
    for sbu in sbus["items"]:
        detail = await client.get(f"/api/portal/sbus/{sbu['id']}")
        assert detail.status_code == 200
        assert len(detail.json()["alcs"]) == sbu["assigned_alcs"]

    directory = (await client.get("/api/portal/alcs?page_size=100")).json()
    assert directory["total"] == 199
    codes: set[str] = set()
    for page in range(1, directory["pages"] + 1):
        items = (await client.get(f"/api/portal/alcs?page={page}&page_size=100")).json()["items"]
        codes.update(row["alc_code"] for row in items)
        assert {row["sbu_code"] for row in items} <= {"SBU 4", "SBU 6", "SBU 7"}
    assert len(codes) == 199 and "00010001" not in codes

    by_sbu = (await client.get(f"/api/portal/alcs?sbu_id={hier['sbu7'].id}")).json()
    assert by_sbu["total"] == 70

    dash = (await client.get("/api/portal/dashboard")).json()
    assert dash["role"] == "DCU" and dash["assigned_alcs"] == 199 and dash["sbus"] == 3
    assert (await client.get(f"/api/portal/alcs/{hier['alc4_centre'].id}")).status_code == 200


async def test_dcu_cannot_reach_another_dcus_records(client, session, hier):
    # Pune North data: Centre B (ALC login 00010002) with partner, activity and evidence.
    other, evidence, partner = await submit_activity(
        client, session, "00010002", "StrongAlcPassB!", with_partner=True
    )
    other_alc, other_sbu = hier["alc_b"].id, hier["test_pn"].id
    aid, eid = other["id"], evidence.id

    await as_user(client, "dcu-nashik")
    # Another DCU's SBU and ALC.
    assert (await client.get(f"/api/portal/sbus/{other_sbu}")).status_code == 404
    assert (await client.get(f"/api/portal/sbus/{hier['sbu1'].id}")).status_code == 404
    assert (await client.get(f"/api/portal/alcs/{other_alc}")).status_code == 404
    assert (await client.get(f"/api/portal/alcs?sbu_id={other_sbu}")).status_code == 404
    # Its activity (read, review detail, every review decision).
    assert (await client.get(f"/api/portal/activities/{aid}")).status_code == 404
    assert (await client.get(f"/api/portal/verification/{aid}")).status_code == 404
    for action in ("verify", "request-correction", "reject"):
        resp = await client.post(f"/api/portal/activities/{aid}/{action}", json={"remark": "x"})
        assert resp.status_code == 404, action
    # Client-supplied filters never widen scope.
    for path in ("activities", "verification", "reports/activities.csv", "reports/partners.csv"):
        assert (
            await client.get(f"/api/portal/{path}?alc_id={other_alc}")
        ).status_code == 404, path
    for path in ("activities", "verification", "reports/activities.csv"):
        assert (
            await client.get(f"/api/portal/{path}?sbu_id={other_sbu}")
        ).status_code == 404, path
    listing = (await client.get("/api/portal/activities?page_size=100")).json()
    assert aid not in {a["id"] for a in listing["items"]}
    # Its evidence (presigned access and raw content).
    assert (await client.get(f"/api/portal/evidence/{eid}/access")).status_code == 404
    assert (await client.get(f"/api/portal/evidence/{eid}/content")).status_code == 404
    # Its partners.
    assert partner["id"] not in {p["id"] for p in (await client.get("/api/portal/partners")).json()}
    assert partner["id"] not in {
        p["id"] for p in (await client.get("/api/portal/sbu/partners")).json()
    }
    # Its reports.
    for path in ("activities.csv", "partners.csv", "verification-status.csv"):
        report = (await client.get(f"/api/portal/reports/{path}")).text
        assert "Centre B" not in report and "00010002" not in report, path
        assert other["activity_number"] not in report
    # Its ALC login.
    assert (
        await client.post(
            f"/api/portal/alcs/{other_alc}/reset-password", json={"password": "BrandNewAlcPass1!"}
        )
    ).status_code == 404

    # The owning DCU does see it.
    await as_user(client, "dcu-pn")
    assert (await client.get(f"/api/portal/activities/{aid}")).status_code == 200
    assert (await client.get("/api/portal/alcs?page_size=100")).json()["total"] == 3


async def test_dcu_reviews_and_manages_its_own_region(client, session, hier):
    activity, evidence, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    await as_user(client, "dcu-nashik")
    queue = (await client.get("/api/portal/verification?queue_only=true")).json()
    assert activity["id"] in {row["activity"]["id"] for row in queue["items"]}
    assert (await client.get(f"/api/portal/evidence/{evidence.id}/access")).status_code == 200
    report = (await client.get("/api/portal/reports/activities.csv")).text
    assert activity["activity_number"] in report

    verified = await client.post(
        f"/api/portal/activities/{activity['id']}/verify", json={"remark": "Looks good"}
    )
    assert verified.status_code == 200 and verified.json()["status"] == "VERIFIED"
    stored = await session.scalar(select(Activity).where(Activity.id == uuid.UUID(activity["id"])))
    assert stored.verified_by == hier["dcu_nashik"].id

    reset = await client.post(
        f"/api/portal/alcs/{hier['alc4_centre'].id}/reset-password",
        json={"password": "BrandNewAlcPass123!"},
    )
    assert reset.status_code == 200
    await session.refresh(hier["alc4"])
    assert hier["alc4"].must_change_password is True


async def test_dcu_has_no_admin_or_alc_write_surfaces(client, hier):
    await as_user(client, "dcu-nashik")
    for path in ("/api/admin/dashboard", "/api/admin/audit-logs", "/api/admin/users",
                 "/api/admin/dcus", "/api/admin/sbus", "/api/admin/alcs", "/api/admin/settings",
                 "/api/admin/activities", "/api/admin/reports/activities.csv"):
        assert (await client.get(path)).status_code == 403, path
    assert (
        await client.post(
            "/api/admin/users",
            json={"username": "evil", "role": "ADMIN", "password": "BrandNewPass1234!"},
        )
    ).status_code == 403
    assert (
        await client.patch(f"/api/admin/sbus/{hier['sbu1'].id}",
                           json={"dcu_id": str(hier["dcus"]["DCU_NASHIK"].id)})
    ).status_code == 403
    # DCU supervises; it never authors ALC records.
    assert (await client.post("/api/portal/activities", json=payload)).status_code == 403
    assert (await client.get("/api/portal/tasks")).status_code == 403


async def test_dcu_with_no_sbus_sees_nothing(client, session, hier):
    await submit_activity(client, session, NASHIK_ALC_SBU4)
    await as_user(client, "dcu-ahilya")
    assert (await client.get("/api/portal/sbus")).json()["items"] == []
    assert (await client.get("/api/portal/alcs")).json()["total"] == 0
    assert (await client.get("/api/portal/activities")).json()["total"] == 0
    assert (await client.get("/api/portal/partners")).json() == []
    assert (await client.get("/api/portal/dashboard")).json()["assigned_alcs"] == 0


# --------------------------------------------------------------------------- #
# Existing SBU / ALC scopes are unchanged; missing keys fail closed
# --------------------------------------------------------------------------- #
async def test_scope_helper_per_role_and_missing_keys(session, hier):
    total = await session.scalar(select(func.count(ALC.id)))
    assert len(await accessible_alc_ids(session, hier["admin"])) == total == 202
    assert len(await accessible_alc_ids(session, hier["dcu_nashik"])) == 199
    assert len(await accessible_alc_ids(session, hier["dcu_pn"])) == 3
    assert len(await accessible_alc_ids(session, hier["sbu4_user"])) == 71
    assert len(await accessible_alc_ids(session, hier["sbu7_user"])) == 70
    assert set(await accessible_alc_ids(session, hier["alc4"])) == {hier["alc4_centre"].id}

    # An unassigned ALC, and an ALC under the DCU-less demo SBU 1, exist so an ``IS NULL``
    # regression (``ALC.sbu_id IS NULL`` / ``SBU.dcu_id IS NULL``) would surface them.
    session.add(ALC(alc_code="00099999", alc_name="Unassigned Centre", sbu_id=None))
    session.add(ALC(alc_code="00099998", alc_name="Demo Centre", sbu_id=hier["sbu1"].id))
    await session.commit()
    for role, key in ((Role.DCU, "dcu_id"), (Role.SBU, "sbu_id"), (Role.ALC, "alc_id")):
        ghost = User(username=f"ghost-{role}", password_hash="x", role=role, **{key: None})
        assert list(await accessible_alc_ids(session, ghost)) == [], role
    # A DCU key is ignored for the other roles, and vice versa: scope follows the role.
    confused = User(username="c", password_hash="x", role=Role.SBU,
                    dcu_id=hier["dcus"]["DCU_NASHIK"].id)
    assert list(await accessible_alc_ids(session, confused)) == []


async def test_existing_sbu_and_alc_isolation_still_hold(client, session, hier):
    a4, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    a7, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU7)

    await as_user(client, "sbu-4", "StrongSbuPass4!")
    assert (await client.get("/api/portal/alcs?page_size=1")).json()["total"] == 71
    assert (await client.get(f"/api/portal/activities/{a4['id']}")).status_code == 200
    assert (await client.get(f"/api/portal/activities/{a7['id']}")).status_code == 404
    assert (await client.get("/api/portal/sbus")).json()["items"][0]["code"] == "SBU 4"
    assert (await client.get(f"/api/portal/sbus/{hier['sbu7'].id}")).status_code == 404

    await as_user(client, NASHIK_ALC_SBU4)
    assert (await client.get(f"/api/portal/activities/{a4['id']}")).status_code == 200
    assert (await client.get(f"/api/portal/activities/{a7['id']}")).status_code == 404
    assert (await client.get("/api/portal/alcs")).status_code == 403
    assert (await client.get("/api/portal/sbus")).status_code == 403


# --------------------------------------------------------------------------- #
# Reassignment: pointers move, history stays, access follows immediately
# --------------------------------------------------------------------------- #
async def test_alc_reassignment_moves_access_and_preserves_history(client, session, hier):
    centre = hier["alc4_centre"]
    activity, evidence, partner = await submit_activity(
        client, session, NASHIK_ALC_SBU4, with_partner=True
    )
    aid = activity["id"]
    # Review cycle to build history: correction requested, then resubmitted.
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    corrected = await client.post(
        f"/api/portal/activities/{aid}/request-correction", json={"remark": "Add photos"}
    )
    assert corrected.status_code == 200
    await as_user(client, NASHIK_ALC_SBU4)
    assert (await client.post(f"/api/portal/activities/{aid}/submit")).status_code == 200
    logout(client)

    # The resubmission notified the Nashik DCU (current hierarchy), not other DCUs.
    async def notified(username):
        await as_user(client, username)
        notes = (await client.get("/api/portal/notifications")).json()
        return any(n["entity_id"] == aid for n in notes)

    assert await notified("dcu-nashik")
    assert not await notified("dcu-pn")

    async with new_client() as old_sbu:
        await as_user(old_sbu, "sbu-4", "StrongSbuPass4!")
        assert (await old_sbu.get(f"/api/portal/activities/{aid}")).status_code == 200

        alcs_before = await session.scalar(select(func.count(ALC.id)))
        await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
        moved = await client.patch(
            f"/api/admin/alcs/{centre.id}", json={"sbu_id": str(hier["sbu6"].id)}
        )
        assert moved.status_code == 200 and moved.json()["id"] == str(centre.id)

        # Same session, no re-login: the old SBU loses access immediately.
        assert (await old_sbu.get(f"/api/portal/activities/{aid}")).status_code == 404
        assert (await old_sbu.get(f"/api/portal/alcs/{centre.id}")).status_code == 404
        assert (await old_sbu.get(f"/api/portal/evidence/{evidence.id}/access")).status_code == 404
        assert (await old_sbu.get("/api/portal/alcs?page_size=1")).json()["total"] == 70

    await as_user(client, "sbu-6", "StrongSbuPass6!")
    detail = (await client.get(f"/api/portal/verification/{aid}")).json()
    assert detail["alc"]["id"] == str(centre.id)
    history = detail["activity"]
    assert [r["action"] for r in history["reviews"]] == ["REQUEST_CORRECTION"]
    assert [r["revision_number"] for r in history["revisions"]] == [1, 2]
    assert [e["id"] for e in history["evidence"]] == [str(evidence.id)]
    assert history["partner"]["id"] == partner["id"]
    assert (await client.get(f"/api/portal/evidence/{evidence.id}/access")).status_code == 200
    verified = await client.post(f"/api/portal/activities/{aid}/verify", json={"remark": "ok"})
    assert verified.status_code == 200

    # No duplicate ALC; the ALC login keeps working on the same centre and activity.
    assert await session.scalar(select(func.count(ALC.id))) == alcs_before
    same_code = select(func.count(ALC.id)).where(ALC.alc_code == NASHIK_ALC_SBU4)
    assert await session.scalar(same_code) == 1
    await as_user(client, NASHIK_ALC_SBU4)
    assert (await client.get(f"/api/portal/activities/{aid}")).json()["status"] == "VERIFIED"
    # The DCU is unaffected by a move between two of its own SBUs.
    await as_user(client, "dcu-nashik")
    assert (await client.get(f"/api/portal/activities/{aid}")).status_code == 200


async def test_sbu_dcu_reassignment_moves_access_and_preserves_history(client, session, hier):
    sbu7 = hier["sbu7"]
    activity, evidence, partner = await submit_activity(
        client, session, NASHIK_ALC_SBU7, with_partner=True
    )
    aid = activity["id"]
    sbus_before = await session.scalar(select(func.count(SBU.id)))
    alc_ids_before = set(
        (await session.scalars(select(ALC.id).where(ALC.sbu_id == sbu7.id))).all()
    )
    assert len(alc_ids_before) == 70

    async with new_client() as nashik, new_client() as pune_north:
        await as_user(nashik, "dcu-nashik")
        await as_user(pune_north, "dcu-pn")
        assert (await nashik.get(f"/api/portal/activities/{aid}")).status_code == 200
        assert (await pune_north.get(f"/api/portal/activities/{aid}")).status_code == 404

        await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
        moved = await client.patch(
            f"/api/admin/sbus/{sbu7.id}", json={"dcu_id": str(hier["dcus"]["DCU_PUNE_NORTH"].id)}
        )
        assert moved.status_code == 200 and moved.json()["id"] == str(sbu7.id)
        # A non-existent DCU is rejected.
        assert (
            await client.patch(f"/api/admin/sbus/{sbu7.id}", json={"dcu_id": str(uuid.uuid4())})
        ).status_code == 422

        # Existing sessions: the old DCU loses access at once, the new DCU gains it.
        assert (await nashik.get(f"/api/portal/activities/{aid}")).status_code == 404
        assert (await nashik.get(f"/api/portal/sbus/{sbu7.id}")).status_code == 404
        assert (await nashik.get(f"/api/portal/evidence/{evidence.id}/access")).status_code == 404
        assert (await nashik.get("/api/portal/alcs?page_size=1")).json()["total"] == 129
        assert partner["id"] not in {
            p["id"] for p in (await nashik.get("/api/portal/partners")).json()
        }

        detail = (await pune_north.get(f"/api/portal/verification/{aid}")).json()
        assert detail["activity"]["evidence"][0]["id"] == str(evidence.id)
        assert detail["activity"]["partner"]["id"] == partner["id"]
        assert [r["revision_number"] for r in detail["activity"]["revisions"]] == [1]
        assert (await pune_north.get("/api/portal/alcs?page_size=1")).json()["total"] == 73
        assert (await pune_north.get(f"/api/portal/sbus/{sbu7.id}")).status_code == 200

    # The SBU and its ALCs are the same rows: nothing duplicated, no ALC re-pointed.
    assert await session.scalar(select(func.count(SBU.id))) == sbus_before
    assert set(
        (await session.scalars(select(ALC.id).where(ALC.sbu_id == sbu7.id))).all()
    ) == alc_ids_before
    # SBU 7's own login is unaffected by the DCU move.
    await as_user(client, "sbu-7")
    assert (await client.get(f"/api/portal/activities/{aid}")).status_code == 200

    # Explicit null detaches the SBU from any DCU: the region DCU fails closed.
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (
        await client.patch(f"/api/admin/sbus/{sbu7.id}", json={"dcu_id": None})
    ).status_code == 200
    await as_user(client, "dcu-pn")
    assert (await client.get(f"/api/portal/activities/{aid}")).status_code == 404
