"""Phase 4F: lean activity lists, join-free counts, id-subquery search and paginate-before-
aggregate ALC directories.

Self-contained hierarchy on top of conftest's ``seeded`` data:

* DCU X owns SBU 4 (Centres A and C, plus 24 filler centres); DCU Y owns SBU 6 (Centre B).
* Activities are inserted directly with known evidence (incl. removed historical evidence),
  reviews and revisions, so every list summary value is known exactly.

Query-shape assertions count SQL statements and inspect their text; nothing depends on
timings.
"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import and_, case, func, or_, select

from app.auth import hash_password
from app.enums import ActivityStatus, AlcStatus, ReviewAction, Role
from app.models import (
    ALC,
    DCU,
    RCU,
    Activity,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    GrowthChallenge,
    Partner,
    User,
)
from app.services import growth_challenge
from app.services.rollups import PENDING_STATUSES, alc_activity_join
from tests.test_activity_exports import ActivityLoads, StatementLog, as_user

PW = "StrongDcuPassword!"
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
SBU4 = ("sbu-4", "StrongSbuPass4!")
ALC_A = ("00010001", "StrongAlcPassA!")
TODAY = date.today()

# Exactly the fields list / queue / dashboard / ALC-detail screens consume.
LEAN_KEYS = {
    "id", "activity_number", "alc_id", "activity_type", "activity_date", "status",
    "submitted_at", "updated_at", "learners_reached", "leads_generated",
    "admissions_generated", "partner_name", "evidence_count", "revision_count",
    "resubmitted_at", "had_correction", "last_remark", "last_remark_action",
}
HEAVY_KEYS = {
    "evidence", "reviews", "revisions", "removed_evidence", "partner", "description",
    "outcome", "location", "collaboration_type",
}
# Column names that only appear when full collection rows (not counts) are fetched.
COLLECTION_ROW_COLUMNS = ("snapshot", "storage_key", "original_filename", "change_summary")


def at(days_ago: int, hour: int = 10) -> datetime:
    day = TODAY - timedelta(days=days_ago)
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def world(session, seeded):
    rcu = RCU(code="RCU_L", name="RCU L")
    session.add(rcu)
    await session.flush()
    dcu_x = DCU(code="DCU_LX", name="DCU LX", rcu_id=rcu.id)
    dcu_y = DCU(code="DCU_LY", name="DCU LY", rcu_id=rcu.id)
    session.add_all([dcu_x, dcu_y])
    await session.flush()
    seeded["sbu4"].dcu_id = dcu_x.id
    seeded["sbu6"].dcu_id = dcu_y.id
    fillers = [
        ALC(alc_code=f"0002{i:04d}", alc_name=f"Filler Centre {i:02d}", sbu_id=seeded["sbu4"].id)
        for i in range(24)
    ]
    session.add_all(fillers)
    session.add_all(
        [
            User(username="dcu-lx", password_hash=hash_password(PW), role=Role.DCU,
                 dcu_id=dcu_x.id),
            User(username="dcu-ly", password_hash=hash_password(PW), role=Role.DCU,
                 dcu_id=dcu_y.id),
        ]
    )
    await session.flush()
    alc_a, alc_b, alc_c = seeded["alc_a"], seeded["alc_b"], seeded["alc_c"]
    college = Partner(alc_id=alc_a.id, partner_name="Nashik College", partner_type="College",
                      ecosystem="College")
    sai = Partner(alc_id=alc_c.id, partner_name="Sai Industries", partner_type="Industry",
                  ecosystem="Corporate")
    pune = Partner(alc_id=alc_b.id, partner_name="Pune Polytechnic", partner_type="College",
                   ecosystem="College")
    session.add_all([college, sai, pune])
    await session.flush()

    def activity(number, alc, status, days_ago, kind, partner=None, eco="College",
                 metrics=(10, 5, 1)):
        submitted = None if status == ActivityStatus.DRAFT else at(days_ago, 9)
        return Activity(
            activity_number=number, alc_id=alc.id, activity_type=kind, ecosystem=eco,
            activity_date=TODAY - timedelta(days=days_ago), location="Nashik",
            learners_reached=metrics[0], leads_generated=metrics[1],
            admissions_generated=metrics[2], description="Structured outreach session.",
            outcome="Done", status=status, submitted_at=submitted,
            partner_id=partner.id if partner else None,
            created_at=at(days_ago, 8), updated_at=at(days_ago, 12),
        )

    acts = {
        "A1": activity("ACT-L-001", alc_a, ActivityStatus.RESUBMITTED, 1, "Partner meeting",
                       college),
        "A2": activity("ACT-L-002", alc_a, ActivityStatus.VERIFIED, 2, "Pilot programme",
                       metrics=(40, 12, 3)),
        "A3": activity("ACT-L-003", alc_c, ActivityStatus.SUBMITTED, 3, "Partnership signing",
                       sai, eco="Corporate"),
        "A4": activity("ACT-L-004", alc_c, ActivityStatus.DRAFT, 4, "Draft visit"),
        "A5": activity("ACT-L-005", alc_a, ActivityStatus.SUBMITTED, 5, "Career awareness"),
        "B1": activity("ACT-L-006", alc_b, ActivityStatus.VERIFIED, 6, "Partnership signing",
                       pune, metrics=(25, 9, 2)),
        "B2": activity("ACT-L-007", alc_b, ActivityStatus.SUBMITTED, 7, "Partner meeting"),
    }
    session.add_all(acts.values())
    await session.flush()
    a1, b1 = acts["A1"], acts["B1"]
    removed_id = uuid.uuid4()

    def evidence(act, name, active=True, evidence_id=None):
        return ActivityEvidence(
            id=evidence_id or uuid.uuid4(), activity_id=act.id, original_filename=name,
            storage_key=f"private/{uuid.uuid4()}.pdf", mime_type="application/pdf",
            file_size=5, is_active=active, uploaded_at=at(10),
        )

    def review(act, action, new_status, remark, days_ago, hour):
        return ActivityReview(
            activity_id=act.id, previous_status=ActivityStatus.SUBMITTED,
            new_status=new_status, action=action, remark=remark, reviewer_role="DCU",
            reviewed_at=at(days_ago, hour),
        )

    def revision(act, number, days_ago, hour, files=()):
        return ActivityRevision(
            activity_id=act.id, revision_number=number, change_summary="Submitted",
            created_at=at(days_ago, hour),
            snapshot={"evidence": [{"id": str(f), "original_filename": "x"} for f in files]},
        )

    session.add_all(
        [
            # A1: 2 current files + 1 removed file seen in submission 1; corrected, resubmitted.
            evidence(a1, "current-1.pdf"), evidence(a1, "current-2.pdf"),
            evidence(a1, "removed.pdf", active=False, evidence_id=removed_id),
            revision(a1, 1, 3, 9, [removed_id]), revision(a1, 2, 1, 9),
            review(a1, ReviewAction.REQUEST_CORRECTION, ActivityStatus.CORRECTION_REQUIRED,
                   "Add attendance sheet", 2, 10),
            # A2: verified once; the verification had an empty remark.
            evidence(acts["A2"], "a2.pdf"), revision(acts["A2"], 1, 2, 9),
            review(acts["A2"], ReviewAction.VERIFY, ActivityStatus.VERIFIED, "", 2, 11),
            evidence(acts["A3"], "a3.pdf"), revision(acts["A3"], 1, 3, 9),
            evidence(acts["A5"], "a5.pdf"), revision(acts["A5"], 1, 5, 9),
            # B1: corrected, resubmitted, then verified with a remark.
            evidence(b1, "b1.pdf"), revision(b1, 1, 8, 9), revision(b1, 2, 7, 9),
            review(b1, ReviewAction.REQUEST_CORRECTION, ActivityStatus.CORRECTION_REQUIRED,
                   "Fix numbers", 8, 10),
            review(b1, ReviewAction.VERIFY, ActivityStatus.VERIFIED, "Looks good", 6, 15),
            evidence(acts["B2"], "b2.pdf"), revision(acts["B2"], 1, 7, 9),
        ]
    )
    await session.commit()
    return {**seeded, "dcu_x": dcu_x, "dcu_y": dcu_y, "acts": acts, "removed_id": removed_id,
            "partners": {"college": college, "sai": sai, "pune": pune}}


def num(world, key):
    return world["acts"][key].activity_number


async def get(client, path, **params):
    response = await client.get(path, params=params)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def queue_numbers(body) -> list[str]:
    return [row["activity"]["activity_number"] for row in body["items"]]


# --------------------------------------------------------------------------- #
# 1–7. Lean list payloads
# --------------------------------------------------------------------------- #
async def list_payloads(client) -> dict[str, list[dict]]:
    """Every activity row returned by every list-type endpoint, per endpoint."""
    out: dict[str, list[dict]] = {}
    await as_user(client, *ADMIN)
    out["admin activities"] = [
        r["activity"] for r in (await get(client, "/api/admin/activities"))["items"]
    ]
    out["admin queue"] = [
        r["activity"]
        for r in (await get(client, "/api/admin/verification-queue", queue_only=True))["items"]
    ]
    alc_a = (await get(client, "/api/admin/alcs", search="Centre A"))["items"][0]["id"]
    out["admin alc detail"] = (await get(client, f"/api/admin/alcs/{alc_a}"))["activities"]
    for who in (("dcu-lx", PW), SBU4):
        await as_user(client, *who)
        tag = who[0]
        out[f"{tag} activities"] = (await get(client, "/api/portal/activities"))["items"]
        out[f"{tag} verification"] = [
            r["activity"] for r in (await get(client, "/api/portal/verification"))["items"]
        ]
        out[f"{tag} dashboard"] = [
            r["activity"] for r in (await get(client, "/api/portal/dashboard"))["recent_activities"]
        ]
        detail = await get(client, f"/api/portal/alcs/{alc_a}")
        out[f"{tag} alc detail"] = detail["activities"]
        out[f"{tag} alc corrections"] = detail["correction_required"]
    await as_user(client, *ALC_A)
    out["alc activities"] = (await get(client, "/api/portal/activities"))["items"]
    out["alc dashboard"] = (await get(client, "/api/portal/dashboard"))["recent_activities"]
    return out


async def test_list_payloads_are_lean_and_complete(client, world):
    payloads = await list_payloads(client)
    for endpoint, rows in payloads.items():
        if endpoint.endswith("corrections"):
            continue  # no activity of Centre A currently awaits correction
        assert rows, endpoint
        for row in rows:
            assert set(row) == LEAN_KEYS, (endpoint, set(row) ^ LEAN_KEYS)
            assert not HEAVY_KEYS & set(row), endpoint
    assert "snapshot" not in str(payloads)


async def test_list_summaries_are_exact(client, world):
    await as_user(client, *ADMIN)
    rows = {
        r["activity"]["activity_number"]: r["activity"]
        for r in (await get(client, "/api/admin/activities"))["items"]
    }
    a1, a2, a3, b1 = (rows[num(world, k)] for k in ("A1", "A2", "A3", "B1"))
    # A1: removed historical evidence is not counted; resubmission = revision 2's time.
    assert (a1["evidence_count"], a1["revision_count"]) == (2, 2)
    assert a1["resubmitted_at"].startswith(at(1, 9).strftime("%Y-%m-%dT%H:%M"))
    assert a1["had_correction"] is True
    assert (a1["last_remark"], a1["last_remark_action"]) == (
        "Add attendance sheet", "REQUEST_CORRECTION",
    )
    assert a1["partner_name"] == "Nashik College"
    # A2: one submission, never corrected; an empty remark is not a remark.
    assert (a2["evidence_count"], a2["revision_count"], a2["resubmitted_at"]) == (1, 1, None)
    assert a2["had_correction"] is False
    assert (a2["last_remark"], a2["last_remark_action"]) == (None, None)
    assert a2["partner_name"] is None
    assert (a2["learners_reached"], a2["leads_generated"], a2["admissions_generated"]) == (
        40, 12, 3,
    )
    # A3: no reviews at all.
    assert (a3["had_correction"], a3["last_remark"]) == (False, None)
    # B1: the latest remark wins even though the earlier review requested a correction.
    assert b1["had_correction"] is True
    assert (b1["last_remark"], b1["last_remark_action"]) == ("Looks good", "VERIFY")
    assert b1["revision_count"] == 2 and b1["resubmitted_at"] is not None


async def test_detail_endpoints_keep_full_activity(client, world):
    a1 = world["acts"]["A1"]
    await as_user(client, *ADMIN)
    admin = (await get(client, f"/api/admin/activities/{a1.id}"))["activity"]
    await as_user(client, "dcu-lx", PW)
    dcu = (await get(client, f"/api/portal/verification/{a1.id}"))["activity"]
    await as_user(client, *ALC_A)
    own = await get(client, f"/api/portal/activities/{a1.id}")
    for full in (admin, dcu, own):
        assert sorted(e["original_filename"] for e in full["evidence"]) == [
            "current-1.pdf", "current-2.pdf",
        ]
        # 27. Historical evidence is still reported on the detail view.
        assert [e["id"] for e in full["removed_evidence"]] == [str(world["removed_id"])]
        assert [r["action"] for r in full["reviews"]] == ["REQUEST_CORRECTION"]
        assert [r["revision_number"] for r in full["revisions"]] == [1, 2]
        assert "snapshot" in full["revisions"][0]
        assert full["partner"]["partner_name"] == "Nashik College"
        assert full["description"] == "Structured outreach session."


async def test_lists_build_no_activity_objects_and_fetch_no_collection_rows(
    client, session, world
):
    with StatementLog(session) as log, ActivityLoads() as loads:
        await list_payloads(client)
    assert loads.count == 0
    for statement in log.statements:
        for column in COLLECTION_ROW_COLUMNS:
            assert column not in statement, (column, statement[:200])


async def test_list_statement_count_does_not_grow_with_page_size(client, session, world):
    await as_user(client, *ADMIN)
    counts = []
    for page_size in (1, 25):
        with StatementLog(session) as log:
            body = await get(client, "/api/admin/activities", page_size=page_size)
        counts.append(len(log.statements))
        assert len(body["items"]) == min(page_size, 6)
    assert counts[0] == counts[1]  # count + page + one summary query: no N+1


# --------------------------------------------------------------------------- #
# 8–12. Counts
# --------------------------------------------------------------------------- #
def outer_from(sql: str) -> str:
    """The top-level FROM clause of ``sql`` (text up to WHERE), ignoring subqueries."""
    depth, top = 0, []
    for char in sql:
        depth += char == "("
        if depth == 0:
            top.append(char)
        depth -= char == ")"
    flat = " ".join("".join(top).split())
    return flat.split(" FROM ", 1)[1].split(" WHERE ", 1)[0]


def count_statements(log: StatementLog) -> list[str]:
    return [s for s in log.statements if "count(" in s.lower() and "activities" in s]


async def test_counts_are_exact_and_join_free_without_search(client, session, world):
    await as_user(client, *ADMIN)
    with StatementLog(session) as log:
        body = await get(client, "/api/admin/activities", page_size=2)
    assert (body["total"], body["pages"], len(body["items"])) == (6, 3, 2)  # drafts excluded
    count_sql = count_statements(log)[0]
    assert "alcs" not in count_sql and "partners" not in count_sql
    assert outer_from(count_sql) == "activities"
    seen = []
    for page in (1, 2, 3):
        seen += queue_numbers(await get(client, "/api/admin/activities", page=page,
                                        page_size=2))
    assert len(seen) == len(set(seen)) == 6

    await as_user(client, "dcu-lx", PW)
    with StatementLog(session) as log:
        body = await get(client, "/api/portal/verification")
    assert body["total"] == 4
    count_sql = count_statements(log)[0]
    assert outer_from(count_sql) == "activities"  # the scope subquery is its own SELECT


async def test_search_counts_still_exact(client, session, world):
    await as_user(client, *ADMIN)
    body = await get(client, "/api/admin/activities", search="00010002")
    assert body["total"] == 2 and set(queue_numbers(body)) == {num(world, "B1"), num(world, "B2")}
    await as_user(client, "dcu-lx", PW)
    with StatementLog(session) as log:
        body = await get(client, "/api/portal/verification", search="Centre C")
    assert (body["total"], queue_numbers(body)) == (1, [num(world, "A3")])
    count_sql = count_statements(log)[0]
    assert outer_from(count_sql) == "activities"  # ALC matches come from an id subquery


async def test_filters_still_apply(client, world):
    await as_user(client, *ADMIN)
    cases = [
        ({"status": "VERIFIED"}, {"A2", "B1"}),
        ({"activity_type": "Partner meeting"}, {"A1", "B2"}),
        ({"ecosystem": "Corporate"}, {"A3"}),
        ({"alc_id": str(world["alc_c"].id)}, {"A3"}),
        ({"queue_only": "true"}, {"A1", "A3", "A5", "B2"}),
        ({"date_from": str(TODAY - timedelta(days=3))}, {"A1", "A2", "A3"}),
        ({"date_to": str(TODAY - timedelta(days=4))}, {"A5", "B1", "B2"}),
    ]
    for params, keys in cases:
        body = await get(client, "/api/admin/activities", **params)
        assert set(queue_numbers(body)) == {num(world, k) for k in keys}, params
        assert body["total"] == len(keys), params
    await as_user(client, "dcu-lx", PW)
    cases = [
        ({"status": "SUBMITTED"}, {"A3", "A5"}),
        ({"activity_type": "Pilot programme"}, {"A2"}),
        ({"sbu_id": str(world["sbu4"].id)}, {"A1", "A2", "A3", "A5"}),
        ({"alc_id": str(world["alc_a"].id)}, {"A1", "A2", "A5"}),
        ({"queue_only": "true"}, {"A1", "A3", "A5"}),
        ({"date_from": str(TODAY - timedelta(days=2)), "date_to": str(TODAY - timedelta(days=1))},
         {"A1", "A2"}),
    ]
    for params, keys in cases:
        body = await get(client, "/api/portal/verification", **params)
        assert set(queue_numbers(body)) == {num(world, k) for k in keys}, params
        assert body["total"] == len(keys), params


async def test_scoped_counts(client, session, world):
    expected = {("dcu-lx", PW): 4, ("dcu-ly", PW): 2, SBU4: 4}
    for who, total in expected.items():
        await as_user(client, *who)
        assert (await get(client, "/api/portal/verification"))["total"] == total, who
        assert (await get(client, "/api/portal/activities"))["total"] == total, who
    await as_user(client, *ALC_A)
    assert (await get(client, "/api/portal/activities"))["total"] == 3


# --------------------------------------------------------------------------- #
# 13–17. Search
# --------------------------------------------------------------------------- #
def legacy_admin_search(term: str):
    """The pre-4F admin search: ILIKE over the ALC / partner join."""
    pattern = f"%{term}%"
    return (
        select(Activity.activity_number)
        .join(ALC, Activity.alc_id == ALC.id)
        .outerjoin(Partner, Activity.partner_id == Partner.id)
        .where(
            Activity.status != ActivityStatus.DRAFT,
            or_(
                Activity.activity_number.ilike(pattern),
                ALC.alc_code.ilike(pattern),
                ALC.alc_name.ilike(pattern),
                Partner.partner_name.ilike(pattern),
            ),
        )
    )


async def test_admin_search_matches_number_alc_and_partner(client, session, world):
    await as_user(client, *ADMIN)
    cases = {
        "ACT-L-003": {"A3"},          # activity number
        "l-00": {"A1", "A2", "A3", "A5", "B1", "B2"},  # case-insensitive, partial
        "00010003": {"A3"},           # ALC code (A4 is a draft)
        "centre a": {"A1", "A2", "A5"},  # ALC name
        "Polytechnic": {"B1"},        # partner name
        "nashik": {"A1"},             # partner name (Centre A's partner)
        "nothing-here": set(),
    }
    for term, keys in cases.items():
        body = await get(client, "/api/admin/activities", search=term)
        got = set(queue_numbers(body))
        assert got == {num(world, k) for k in keys}, term
        assert body["total"] == len(keys), term
        legacy = set(await session.scalars(legacy_admin_search(term)))
        assert got == legacy, term  # identical to the old joined ILIKE semantics


async def test_supervisor_search_and_scope(client, world):
    await as_user(client, "dcu-lx", PW)
    cases = {
        "ACT-L-001": {"A1"},
        "00010001": {"A1", "A2", "A5"},
        "centre c": {"A3"},
        "Nashik College": set(),  # partner names are not part of the supervisor search
    }
    for term, keys in cases.items():
        body = await get(client, "/api/portal/verification", search=term)
        assert set(queue_numbers(body)) == {num(world, k) for k in keys}, term
        assert body["total"] == len(keys), term
    # 17. Out-of-scope matches never leak, even when the search term matches them exactly.
    for term in ("00010002", "Centre B", "ACT-L-006"):
        body = await get(client, "/api/portal/verification", search=term)
        assert (body["total"], body["items"]) == (0, []), term
    await as_user(client, *SBU4)
    body = await get(client, "/api/portal/verification", search="Centre B")
    assert body["total"] == 0
    await as_user(client, "dcu-ly", PW)
    body = await get(client, "/api/portal/verification", search="Centre")
    assert set(queue_numbers(body)) == {num(world, "B1"), num(world, "B2")}


# --------------------------------------------------------------------------- #
# 18–23. Directories: paginate ALC ids, then aggregate
# --------------------------------------------------------------------------- #
def legacy_admin_directory(filters, page, page_size):
    """The pre-4F admin ALC directory query: aggregate every ALC, then OFFSET / LIMIT."""
    return (
        select(
            ALC.id, ALC.alc_code, ALC.alc_name, ALC.status,
            func.count(Activity.id).label("activities"),
            func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label("verified"),
            func.count(case((Activity.status.in_(PENDING_STATUSES), 1))).label("pending"),
            func.coalesce(func.sum(case((Activity.status == ActivityStatus.VERIFIED,
                                         Activity.learners_reached), else_=0)), 0)
            .label("learners"),
            func.max(Activity.activity_date).label("last_activity"),
        )
        .outerjoin(Activity, alc_activity_join())
        .where(*filters)
        .group_by(ALC.id)
        .order_by(ALC.alc_code)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )


def legacy_challenge(filters, page, page_size):
    start = TODAY - timedelta(days=29)
    return (
        select(
            ALC.id, ALC.alc_code, ALC.alc_name,
            func.coalesce(func.sum(Activity.leads_generated), 0).label("prospects"),
            func.count(case((func.lower(Activity.activity_type).like("%meeting%"), 1)))
            .label("meetings"),
            func.count(case((func.lower(Activity.activity_type).like("%pilot%"), 1)))
            .label("pilots"),
            func.count(func.distinct(case((func.lower(Activity.activity_type)
                                           .like("%partnership%"), Activity.partner_id))))
            .label("partnerships"),
        )
        .outerjoin(Activity, and_(ALC.id == Activity.alc_id,
                                  Activity.status == ActivityStatus.VERIFIED,
                                  Activity.activity_date >= start,
                                  Activity.activity_date <= TODAY))
        .where(*filters)
        .group_by(ALC.id)
        .order_by(ALC.alc_code)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )


def jsonish(rows) -> list[dict]:
    return [
        {k: (str(v) if isinstance(v, (uuid.UUID, date)) else
             v.value if isinstance(v, AlcStatus) else v) for k, v in dict(r).items()}
        for r in rows
    ]


async def test_admin_directory_pages_match_legacy_query(client, session, world):
    await as_user(client, *ADMIN)
    cases = [
        ({}, []),
        ({"search": "centre"}, [or_(ALC.alc_code.ilike("%centre%"),
                                    ALC.alc_name.ilike("%centre%"))]),
        ({"search": "0001"}, [or_(ALC.alc_code.ilike("%0001%"), ALC.alc_name.ilike("%0001%"))]),
        ({"status": "ACTIVE"}, [ALC.status == "ACTIVE"]),
    ]
    for params, filters in cases:
        for page in (1, 2, 3, 9):
            body = await get(client, "/api/admin/alcs", page=page, page_size=10, **params)
            legacy = (await session.execute(legacy_admin_directory(filters, page, 10))).mappings()
            assert body["items"] == jsonish(legacy.all()), (params, page)
    first = await get(client, "/api/admin/alcs", page=1, page_size=10)
    assert first["total"] == 27 and first["pages"] == 3
    row_a = next(r for r in first["items"] if r["alc_code"] == "00010001")
    # Centre A: A1 resubmitted + A2 verified + A5 submitted (no drafts).
    assert (row_a["activities"], row_a["verified"], row_a["pending"], row_a["learners"]) == (
        3, 1, 2, 40,
    )
    assert row_a["last_activity"] == str(TODAY - timedelta(days=1))


async def test_supervisor_directory_pages_match_and_stay_in_scope(client, session, world):
    await as_user(client, "dcu-lx", PW)
    everything = []
    for page in (1, 2, 3):
        body = await get(client, "/api/portal/alcs", page=page, page_size=10)
        assert body["total"] == 26  # A, C and 24 fillers; never Centre B
        everything += body["items"]
    codes = [r["alc_code"] for r in everything]
    assert codes == sorted(codes) and len(codes) == len(set(codes)) == 26
    assert "00010002" not in codes
    by_code = {r["alc_code"]: r for r in everything}
    a, c = by_code["00010001"], by_code["00010003"]
    assert (a["activities"], a["verified"], a["pending"], a["corrections"]) == (3, 1, 2, 0)
    assert (a["partners"], a["learners"], a["sbu_code"], a["dcu_code"]) == (
        1, 40, "SBU 4", "DCU_LX",
    )
    assert (c["activities"], c["pending"], c["partners"]) == (1, 1, 1)  # draft excluded
    searched = await get(client, "/api/portal/alcs", search="Centre")
    assert [r["alc_code"] for r in searched["items"]][:2] == ["00010001", "00010003"]
    assert searched["total"] == 26  # "Filler Centre NN" also matches
    assert (await get(client, "/api/portal/alcs", search="Centre B"))["total"] == 0
    await as_user(client, *SBU4)
    assert (await get(client, "/api/portal/alcs", page_size=100))["total"] == 26
    await as_user(client, "dcu-ly", PW)
    only = await get(client, "/api/portal/alcs")
    assert [r["alc_code"] for r in only["items"]] == ["00010002"]
    assert only["items"][0]["activities"] == 2
    empty = await get(client, "/api/portal/alcs", page=5)
    assert (empty["items"], empty["total"]) == ([], 1)


async def test_directories_aggregate_only_the_page(client, session, world):
    await as_user(client, *ADMIN)
    for path in ("/api/admin/alcs", "/api/admin/challenge"):
        with StatementLog(session) as log:
            await get(client, path, page=2, page_size=5)
        [aggregate] = [s for s in log.statements if "GROUP BY" in s]
        assert "LIMIT" not in aggregate and "OFFSET" not in aggregate, path
        placeholders = re.search(r"alcs\.id IN \(([^)]*)\)", aggregate).group(1)
        assert placeholders.count("?") == 5, path  # exactly the page's ALC ids
        assert any("LIMIT" in s and "GROUP BY" not in s and "alcs" in s
                   for s in log.statements), path
    await as_user(client, "dcu-lx", PW)
    with StatementLog(session) as log:
        await get(client, "/api/portal/alcs", page=1, page_size=7)
    [aggregate] = [s for s in log.statements if "GROUP BY" in s]
    placeholders = re.search(r"alcs\.id IN \(([^)]*)\)", aggregate).group(1)
    assert placeholders.count("?") == 7


async def test_challenge_directory_matches_legacy_and_values(
    client, session, world, monkeypatch
):
    # The overview now measures the configured Growth Challenge period (there is no rolling
    # fallback), so configure the 30 days ending today that the legacy query used.
    session.add(GrowthChallenge(name="Thirty days", start_date=TODAY - timedelta(days=29),
                                end_date=TODAY))
    await session.commit()
    monkeypatch.setattr(growth_challenge, "current_date", lambda: TODAY)
    await as_user(client, *ADMIN)
    for params, filters in (({}, []), ({"search": "centre"}, [or_(
            ALC.alc_code.ilike("%centre%"), ALC.alc_name.ilike("%centre%"))])):
        for page in (1, 2, 3):
            body = await get(client, "/api/admin/challenge", page=page, page_size=10, **params)
            legacy = (await session.execute(legacy_challenge(filters, page, 10))).mappings()
            assert body["items"] == jsonish(legacy.all()), (params, page)
    rows = {r["alc_code"]: r for r in (await get(client, "/api/admin/challenge"))["items"]}
    # Verified in the last 30 days: A2 (pilot, 12 leads); B1 (partnership with a partner).
    assert rows["00010001"] == {**rows["00010001"], "prospects": 12, "meetings": 0,
                                "pilots": 1, "partnerships": 0}
    assert rows["00010002"]["partnerships"] == 1 and rows["00010002"]["prospects"] == 9
    assert rows["00010003"]["prospects"] == 0  # submitted / draft only


# --------------------------------------------------------------------------- #
# 24–29. Regression
# --------------------------------------------------------------------------- #
async def test_reviews_still_work_and_lists_follow(client, world):
    a3, a5 = world["acts"]["A3"], world["acts"]["A5"]
    await as_user(client, "dcu-lx", PW)
    verified = await client.post(f"/api/portal/activities/{a3.id}/verify",
                                 json={"remark": "Verified on site"})
    assert verified.status_code == 200 and verified.json()["status"] == "VERIFIED"
    assert [r["action"] for r in verified.json()["reviews"]] == ["VERIFY"]  # full response
    await as_user(client, *SBU4)
    corrected = await client.post(f"/api/portal/activities/{a5.id}/request-correction",
                                  json={"remark": "Upload the photos"})
    assert corrected.status_code == 200 and corrected.json()["status"] == "CORRECTION_REQUIRED"
    rows = {r["activity"]["activity_number"]: r["activity"]
            for r in (await get(client, "/api/portal/verification"))["items"]}
    assert (rows[a3.activity_number]["last_remark"],
            rows[a3.activity_number]["last_remark_action"]) == ("Verified on site", "VERIFY")
    assert rows[a5.activity_number]["had_correction"] is True
    detail = await get(client, f"/api/portal/alcs/{world['alc_a'].id}")
    assert [a["activity_number"] for a in detail["correction_required"]] == [a5.activity_number]
    await as_user(client, *ADMIN)
    assert (await client.get(f"/api/admin/activities/{a3.id}")).status_code == 200


async def test_reassignment_moves_list_and_directory_scope(client, session, world):
    world["sbu6"].dcu_id = world["dcu_x"].id  # SBU 6 (Centre B) moves under DCU X
    await session.commit()
    await as_user(client, "dcu-lx", PW)
    assert (await get(client, "/api/portal/verification"))["total"] == 6
    assert (await get(client, "/api/portal/alcs", page_size=100))["total"] == 27
    await as_user(client, "dcu-ly", PW)
    assert (await get(client, "/api/portal/verification"))["total"] == 0
    assert (await get(client, "/api/portal/alcs"))["total"] == 0
    world["alc_b"].sbu_id = world["sbu4"].id  # Centre B itself moves into SBU 4
    await session.commit()
    await as_user(client, *SBU4)
    body = await get(client, "/api/portal/verification", search="00010002")
    assert set(queue_numbers(body)) == {num(world, "B1"), num(world, "B2")}


async def test_inactive_alc_is_not_filtered_by_this_phase(client, session, world):
    """Inactive-hierarchy behaviour is out of scope here: an inactive ALC's activities keep
    appearing exactly as before, and the directory status filter behaves as before."""
    world["alc_a"].status = AlcStatus.INACTIVE
    await session.commit()
    await as_user(client, "dcu-lx", PW)
    assert (await get(client, "/api/portal/verification"))["total"] == 4
    inactive = await get(client, "/api/portal/alcs", status="INACTIVE")
    assert [r["alc_code"] for r in inactive["items"]] == ["00010001"]
    await as_user(client, *ADMIN)
    assert (await get(client, "/api/admin/activities", search="Centre A"))["total"] == 3


async def test_ordering_is_deterministic(client, session, world):
    # Give two activities identical timestamps: the id tie-breaker keeps pages stable.
    a2, a5 = world["acts"]["A2"], world["acts"]["A5"]
    a5.submitted_at, a5.updated_at = a2.submitted_at, a2.updated_at
    await session.commit()
    await as_user(client, *ADMIN)
    orders = {tuple(queue_numbers(await get(client, "/api/admin/activities"))) for _ in range(3)}
    assert len(orders) == 1
    (order,) = orders
    tied = [n for n in order if n in (a2.activity_number, a5.activity_number)]
    assert tied == sorted(tied, key=lambda n: str(world["acts"]["A2" if n == a2.activity_number
                                                                else "A5"].id), reverse=True)
    await as_user(client, *ALC_A)
    listed = [a["activity_number"] for a in (await get(client, "/api/portal/activities"))["items"]]
    assert listed[0] == num(world, "A1")  # most recently updated first (unchanged)


async def test_scope_module_untouched_by_lists(client, world):
    """Direct out-of-scope access is still rejected."""
    b1 = world["acts"]["B1"]
    await as_user(client, "dcu-lx", PW)
    assert (await client.get(f"/api/portal/verification/{b1.id}")).status_code == 404
    assert (await client.get(f"/api/portal/alcs/{world['alc_b'].id}")).status_code == 404
    assert (await client.get("/api/portal/verification",
                             params={"alc_id": str(world["alc_b"].id)})).status_code == 404
    assert (await client.get("/api/portal/alcs",
                             params={"sbu_id": str(world["sbu6"].id)})).status_code == 404
    await as_user(client, *ALC_A)
    assert (await client.get(f"/api/portal/activities/{b1.id}")).status_code == 404
