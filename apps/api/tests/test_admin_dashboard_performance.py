"""Phase 4I: Admin dashboard query consolidation.

Allowed dashboard query shape (besides the authenticated-user lookup):

1. ALC totals                                   -- ``alcs``
2. Activity metrics + per-status counts          -- ONE unfiltered pass over ``activities``
3. Active partners                               -- ``partners``
4. Submission trend (latest 30 activity dates)   -- ``activities`` via ``activity_date``
5. Top 8 verified activity types                 -- ``activities`` WHERE status = VERIFIED

The status distribution used to be a second, separate ``GROUP BY status`` scan of
``activities``; it is now counted in the same pass as the scalar metrics. Every response
value must match the former implementation (``legacy_dashboard`` below is a verbatim copy of
its queries, kept here only as a test oracle).
"""
import random
from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import case, func, insert, select

from app.auth import hash_password
from app.enums import ActivityStatus as S
from app.enums import AlcStatus, Role
from app.models import ALC, DCU, RCU, SBU, Activity, Partner, User
from app.services.scope import submitted_workflow
from tests.test_activity_exports import ActivityLoads, StatementLog, as_user

ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
DCU_PW = "StrongDcuPassword!"
BASE = date(2026, 3, 1)
SUBMITTED_ON = datetime(2026, 5, 20, 9, tzinfo=timezone.utc)  # deliberately NOT the activity date

RESPONSE_TYPES = {
    "total_alcs": int, "active_alcs": int, "activities_submitted": int,
    "pending_verification": int, "verified_activities": int, "correction_required": int,
    "rejected_activities": int, "verified_learners": int, "verified_leads": int,
    "verified_admissions": int, "active_partnerships": int, "status_distribution": list,
    "submission_trend": list, "verified_categories": list,
}

# (status, alc key, learners, leads, admissions, activity type, day offset from BASE)
ROWS = [
    (S.DRAFT, "a", 500, 500, 500, "Workshop", 40),   # newest date of all, but a draft
    (S.DRAFT, "d", 7, 7, 7, "Workshop", 2),
    (S.SUBMITTED, "a", 10, 1, 0, "Partner meeting", 0),
    (S.SUBMITTED, "b", 0, 0, 0, "Partner meeting", 1),
    (S.UNDER_REVIEW, "c", 3, 3, 3, "Seminar", 2),
    (S.CORRECTION_REQUIRED, "d", 4, 4, 4, "Seminar", 3),
    (S.RESUBMITTED, "b", 5, 5, 5, "Workshop", 4),
    (S.REJECTED, "a", 6, 6, 6, "Workshop", 5),
    (S.VERIFIED, "a", 20, 5, 2, "Workshop", 6),
    (S.VERIFIED, "b", 30, 4, 1, "Workshop", 7),
    (S.VERIFIED, "d", 0, 0, 0, "Workshop", 8),       # verified with zero metrics
    (S.VERIFIED, "c", 15, 0, 3, "Pilot programme", 9),
]

EXPECTED = {
    "total_alcs": 4,
    "active_alcs": 3,                  # Centre D is inactive
    "activities_submitted": 10,        # every non-draft activity
    "pending_verification": 4,         # SUBMITTED x2 + UNDER_REVIEW + RESUBMITTED
    "verified_activities": 4,
    "correction_required": 1,
    "rejected_activities": 1,
    "verified_learners": 65,           # 20 + 30 + 0 + 15 (drafts' 500s never count)
    "verified_leads": 9,
    "verified_admissions": 6,
    "active_partnerships": 2,
    "status_distribution": [
        {"name": "CORRECTION_REQUIRED", "value": 1},
        {"name": "REJECTED", "value": 1},
        {"name": "RESUBMITTED", "value": 1},
        {"name": "SUBMITTED", "value": 2},
        {"name": "UNDER_REVIEW", "value": 1},
        {"name": "VERIFIED", "value": 4},
    ],
    # By activity date (not submission date), oldest first; the drafts' dates never appear.
    "submission_trend": [{"date": str(BASE + timedelta(days=n)), "count": 1} for n in range(10)],
    "verified_categories": [
        {"name": "Workshop", "value": 3},
        {"name": "Pilot programme", "value": 1},
    ],
}


def make_activity(number, alc_id, status, learners, leads, admissions, kind, day):
    return Activity(
        activity_number=number, alc_id=alc_id, activity_type=kind, ecosystem="College",
        activity_date=BASE + timedelta(days=day), location="Nashik",
        learners_reached=learners, leads_generated=leads, admissions_generated=admissions,
        description="Structured outreach session.", outcome="Done", status=status,
        submitted_at=None if status == S.DRAFT else SUBMITTED_ON,
    )


@pytest_asyncio.fixture
async def branches(session, seeded):
    """Centres A, C (SBU 4) and B (SBU 6) under DCU Nashik (conftest), plus inactive Centre D
    in a second RCU / DCU / SBU branch. A DCU login exists for DCU Nashik."""
    rcu = RCU(code="RCU_X", name="RCU X")
    session.add(rcu)
    await session.flush()
    dcu = DCU(code="DCU_X", name="DCU X", rcu_id=rcu.id)
    session.add(dcu)
    await session.flush()
    sbu = SBU(code="SBU X", name="SBU X", dcu_id=dcu.id)
    session.add(sbu)
    await session.flush()
    alc_d = ALC(alc_code="00020001", alc_name="Centre D", sbu_id=sbu.id, status=AlcStatus.INACTIVE)
    session.add(alc_d)
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    session.add(User(username="dcu-nashik", password_hash=hash_password(DCU_PW), role=Role.DCU,
                     dcu_id=nashik.id))
    await session.flush()
    return {"a": seeded["alc_a"].id, "b": seeded["alc_b"].id, "c": seeded["alc_c"].id,
            "d": alc_d.id}


@pytest_asyncio.fixture
async def dataset(session, branches):
    for i, (status, alc, learners, leads, admissions, kind, day) in enumerate(ROWS, 1):
        session.add(make_activity(f"ACT-DASH-{i:03d}", branches[alc], status, learners, leads,
                                  admissions, kind, day))
    session.add_all(
        [
            Partner(alc_id=branches["a"], partner_name="Active 1", partner_type="College",
                    ecosystem="College", status="ACTIVE"),
            Partner(alc_id=branches["d"], partner_name="Active 2", partner_type="College",
                    ecosystem="College", status="ACTIVE"),
            Partner(alc_id=branches["b"], partner_name="Paused", partner_type="College",
                    ecosystem="College", status="INACTIVE"),
        ]
    )
    await session.commit()
    return branches


async def dashboard(client) -> dict:
    await as_user(client, *ADMIN)
    response = await client.get("/api/admin/dashboard")
    assert response.status_code == 200, response.text
    return response.json()


async def legacy_dashboard(session) -> dict:
    """The pre-4I dashboard queries, verbatim (test oracle only)."""
    alc_counts = (await session.execute(select(
        func.count(ALC.id).label("total_alcs"),
        func.count(case((ALC.status == "ACTIVE", 1))).label("active_alcs"),
    ))).mappings().one()

    def verified_sum(column):
        return func.coalesce(func.sum(case((Activity.status == S.VERIFIED, column), else_=0)), 0)

    metrics = (await session.execute(select(
        func.count(case((Activity.status != S.DRAFT, 1))).label("activities_submitted"),
        func.count(case((Activity.status.in_([S.SUBMITTED, S.RESUBMITTED, S.UNDER_REVIEW]), 1)))
        .label("pending_verification"),
        func.count(case((Activity.status == S.VERIFIED, 1))).label("verified_activities"),
        func.count(case((Activity.status == S.CORRECTION_REQUIRED, 1)))
        .label("correction_required"),
        func.count(case((Activity.status == S.REJECTED, 1))).label("rejected_activities"),
        verified_sum(Activity.learners_reached).label("verified_learners"),
        verified_sum(Activity.leads_generated).label("verified_leads"),
        verified_sum(Activity.admissions_generated).label("verified_admissions"),
    ))).mappings().one()
    partners = await session.scalar(
        select(func.count(Partner.id)).where(Partner.status == "ACTIVE")) or 0
    status_rows = (await session.execute(
        select(Activity.status, func.count(Activity.id))
        .where(submitted_workflow()).group_by(Activity.status))).all()
    trend_rows = (await session.execute(
        select(Activity.activity_date, func.count(Activity.id)).where(submitted_workflow())
        .group_by(Activity.activity_date).order_by(Activity.activity_date.desc()).limit(30))).all()
    categories = (await session.execute(
        select(Activity.activity_type, func.count(Activity.id))
        .where(Activity.status == S.VERIFIED).group_by(Activity.activity_type)
        .order_by(func.count(Activity.id).desc()).limit(8))).all()
    return {
        **alc_counts, **metrics, "active_partnerships": partners,
        "status_distribution": [{"name": s.value, "value": c} for s, c in status_rows],
        "submission_trend": [{"date": str(d), "count": c} for d, c in reversed(trend_rows)],
        "verified_categories": [{"name": n, "value": c} for n, c in categories],
    }


# --------------------------------------------------------------------------- #
# Response contract and exact values
# --------------------------------------------------------------------------- #
async def test_response_schema_unchanged(client, dataset):
    body = await dashboard(client)
    assert set(body) == set(RESPONSE_TYPES)
    for key, kind in RESPONSE_TYPES.items():
        assert isinstance(body[key], kind), key
    for entry in body["status_distribution"] + body["verified_categories"]:
        assert set(entry) == {"name", "value"} and isinstance(entry["value"], int)
    for entry in body["submission_trend"]:
        assert set(entry) == {"date", "count"} and isinstance(entry["count"], int)


async def test_every_metric_matches_expected_values(client, dataset):
    body = await dashboard(client)
    assert body == EXPECTED


async def test_drafts_are_excluded_everywhere(client, session, dataset):
    """DRAFT counts nowhere: not in totals, status distribution, outcome sums or the trend,
    even when a draft carries the largest numbers and the newest activity date."""
    before = await dashboard(client)
    session.add(make_activity("ACT-DASH-DRAFT", dataset["b"], S.DRAFT, 999, 999, 999,
                              "Pilot programme", 60))
    await session.commit()
    after = await dashboard(client)
    assert after == before
    assert "DRAFT" not in {e["name"] for e in after["status_distribution"]}
    assert str(BASE + timedelta(days=60)) not in {e["date"] for e in after["submission_trend"]}


async def test_each_status_is_counted_in_its_own_bucket(client, session, dataset):
    body = await dashboard(client)
    distribution = {e["name"]: e["value"] for e in body["status_distribution"]}
    assert distribution == {"SUBMITTED": 2, "UNDER_REVIEW": 1, "CORRECTION_REQUIRED": 1,
                            "RESUBMITTED": 1, "VERIFIED": 4, "REJECTED": 1}
    assert sum(distribution.values()) == body["activities_submitted"]
    # One more of each workflow status moves exactly its own bucket and the derived totals.
    for i, status in enumerate(s for s in S if s != S.DRAFT):
        session.add(make_activity(f"ACT-DASH-X{i}", dataset["c"], status, 1, 1, 1, "Seminar", 11))
    await session.commit()
    body = await dashboard(client)
    distribution = {e["name"]: e["value"] for e in body["status_distribution"]}
    assert distribution == {"SUBMITTED": 3, "UNDER_REVIEW": 2, "CORRECTION_REQUIRED": 2,
                            "RESUBMITTED": 2, "VERIFIED": 5, "REJECTED": 2}
    assert (body["pending_verification"], body["verified_activities"],
            body["correction_required"], body["rejected_activities"],
            body["activities_submitted"]) == (7, 5, 2, 2, 16)  # pending: 4 + 3 new
    assert (body["verified_learners"], body["verified_leads"],
            body["verified_admissions"]) == (66, 10, 7)


async def test_absent_statuses_are_omitted_not_zero(client, session, branches):
    session.add(make_activity("ACT-DASH-ONLY", branches["a"], S.SUBMITTED, 1, 1, 1, "Seminar", 0))
    await session.commit()
    body = await dashboard(client)
    assert body["status_distribution"] == [{"name": "SUBMITTED", "value": 1}]


async def test_multiple_alcs_and_hierarchy_branches_are_all_aggregated(client, session, dataset):
    """Admin is global: every ALC in every DCU / SBU branch counts, active or not."""
    body = await dashboard(client)
    # Centre D (second branch, inactive ALC) contributes a verified activity with zero metrics,
    # a correction-required activity and an active partner.
    assert body["correction_required"] == 1 and body["active_partnerships"] == 2
    detached = await session.get(ALC, dataset["d"])
    detached.sbu_id = None  # hierarchy placement does not change Admin's totals
    await session.commit()
    assert await dashboard(client) == body


async def test_zero_valued_outcomes_sum_to_zero_not_null(client, session, branches):
    session.add(make_activity("ACT-DASH-Z", branches["a"], S.VERIFIED, 0, 0, 0, "Seminar", 0))
    await session.commit()
    body = await dashboard(client)
    assert (body["verified_learners"], body["verified_leads"], body["verified_admissions"]) == (
        0, 0, 0)
    assert body["verified_categories"] == [{"name": "Seminar", "value": 1}]


async def test_empty_database(client, seeded):
    body = await dashboard(client)
    assert body == {
        "total_alcs": 3, "active_alcs": 3, "activities_submitted": 0, "pending_verification": 0,
        "verified_activities": 0, "correction_required": 0, "rejected_activities": 0,
        "verified_learners": 0, "verified_leads": 0, "verified_admissions": 0,
        "active_partnerships": 0, "status_distribution": [], "submission_trend": [],
        "verified_categories": [],
    }


async def test_trend_and_categories_keep_limits_order_and_date_basis(client, session, branches):
    """Trend: the latest 30 distinct activity dates with non-draft activity, oldest first,
    by activity_date (never submitted_at). Categories: top 8 verified types by count."""
    rows = []
    for n in range(35):  # 35 distinct activity dates, all submitted on one other day
        rows.append(make_activity(f"ACT-T-{n:03d}", branches["a"], S.SUBMITTED, 1, 1, 1,
                                  "Seminar", n))
    for rank in range(10):  # 10 verified types with distinct counts 10, 9, ... 1
        for k in range(10 - rank):
            rows.append(make_activity(f"ACT-C-{rank}-{k}", branches["b"], S.VERIFIED, 1, 1, 1,
                                      f"Type {rank}", 0))
    session.add_all(rows)
    await session.commit()
    body = await dashboard(client)
    assert [e["date"] for e in body["submission_trend"]] == [
        str(BASE + timedelta(days=n)) for n in range(5, 35)]
    assert body["submission_trend"][-1] == {"date": str(BASE + timedelta(days=34)), "count": 1}
    assert str(SUBMITTED_ON.date()) not in {e["date"] for e in body["submission_trend"]}
    assert body["verified_categories"] == [
        {"name": f"Type {rank}", "value": 10 - rank} for rank in range(8)]


async def test_matches_legacy_implementation_on_varied_data(client, session, dataset):
    """Semantic equivalence with the former queries on a larger deterministic dataset."""
    rng = random.Random(4)
    statuses = list(S)
    kinds = ["Workshop", "Seminar", "Pilot programme", "Partner meeting", "Training programme"]
    alcs = list(dataset.values())
    await session.execute(insert(Activity), [
        {
            "activity_number": f"ACT-R-{i:04d}", "alc_id": rng.choice(alcs),
            "activity_type": rng.choice(kinds), "ecosystem": "College",
            "activity_date": BASE - timedelta(days=rng.randrange(90)), "location": "Pune",
            "learners_reached": rng.randrange(0, 40), "leads_generated": rng.randrange(0, 9),
            "admissions_generated": rng.randrange(0, 4), "description": "Random row",
            "outcome": "Done", "status": statuses[i % len(statuses)],
        }
        for i in range(400)
    ])
    await session.commit()
    assert await dashboard(client) == await legacy_dashboard(session)


async def test_expected_dataset_matches_legacy_implementation(client, session, dataset):
    assert await legacy_dashboard(session) == EXPECTED
    assert await dashboard(client) == EXPECTED


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
def dashboard_statements(log: StatementLog) -> list[str]:
    """Statements issued by the dashboard itself (not the authenticated-user lookup)."""
    return [s for s in log.statements if "FROM users" not in s]


async def test_query_shape_is_consolidated(client, session, dataset):
    await as_user(client, *ADMIN)
    with StatementLog(session) as log, ActivityLoads() as loads:
        assert (await client.get("/api/admin/dashboard")).status_code == 200
    statements = dashboard_statements(log)
    activity_statements = [s for s in statements if "FROM activities" in s]
    assert len(statements) == 5
    assert len(activity_statements) == 3
    # Exactly one unfiltered pass over activities (the scalar + status aggregate).
    unfiltered = [s for s in activity_statements if "WHERE" not in s]
    assert len(unfiltered) == 1
    assert "GROUP BY" not in unfiltered[0]
    # The former separate status scan is gone.
    assert not any("GROUP BY activities.status" in s for s in statements)
    # The remaining activity queries are the trend (by activity_date) and the categories.
    grouped = sorted(s.split("GROUP BY", 1)[1].split()[0] for s in activity_statements
                     if "GROUP BY" in s)
    assert grouped == ["activities.activity_date", "activities.activity_type"]
    assert loads.count == 0  # no Activity ORM objects are built


async def test_statement_count_is_independent_of_data_volume(client, session, dataset):
    await as_user(client, *ADMIN)
    with StatementLog(session) as small:
        await client.get("/api/admin/dashboard")
    await session.execute(insert(Activity), [
        {"activity_number": f"ACT-BULK-{i:04d}", "alc_id": dataset["a"], "activity_type": "Bulk",
         "ecosystem": "College", "activity_date": BASE - timedelta(days=i % 50),
         "location": "Pune", "description": "Bulk", "outcome": "Done",
         "status": list(S)[i % len(S)]}
        for i in range(500)
    ])
    await session.commit()
    with StatementLog(session) as large:
        await client.get("/api/admin/dashboard")
    assert len(dashboard_statements(large)) == len(dashboard_statements(small)) == 5


# --------------------------------------------------------------------------- #
# Authorization (unchanged)
# --------------------------------------------------------------------------- #
async def test_only_admin_can_open_the_dashboard(client, dataset):
    await as_user(client, *ADMIN)
    assert (await client.get("/api/admin/dashboard")).status_code == 200
    for who in (("dcu-nashik", DCU_PW), ("sbu-4", "StrongSbuPass4!"),
                ("00010001", "StrongAlcPassA!")):
        await as_user(client, *who)
        assert (await client.get("/api/admin/dashboard")).status_code == 403, who
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await client.get("/api/admin/dashboard")).status_code == 401
