"""Region Top 10 leaderboard (``GET /api/leaderboard/region-top10``).

Approved rules: lifetime, VERIFIED-only activity metrics, ACTIVE partner rows, ACTIVE ALCs
with a complete ALC -> SBU -> DCU -> RCU chain and at least one non-zero metric, regional
percentile normalisation with equal 25% weights, ranked by exact score then ALC code. The
same regional result for ADMIN, DCU, SBU and ALC. One aggregate statement, no fan-out.
"""
import itertools
import uuid
from datetime import date
from fractions import Fraction

import pytest_asyncio
from sqlalchemy import select

from app.auth import hash_password
from app.enums import ActivityStatus as S
from app.enums import AlcStatus, Role
from app.models import ALC, DCU, RCU, SBU, Activity, Partner, User
from app.services import leaderboard
from app.services.leaderboard import (
    METRICS,
    WEIGHTS,
    LeaderboardRow,
    display_score,
    percentile_scores,
    rank_rows,
    region_top10,
    score_rows,
)
from tests.conftest import login
from tests.test_activity_exports import ActivityLoads, StatementLog, as_user

URL = "/api/leaderboard/region-top10"
DCU_PW = "StrongDcuPassword!"
ROLES = (
    ("admin", "StrongAdminPass!", "ADMIN"),
    ("dcu-nashik", DCU_PW, "PORTAL"),
    ("sbu-4", "StrongSbuPass4!", "PORTAL"),
    ("00010001", "StrongAlcPassA!", "PORTAL"),
)
ITEM_KEYS = {
    "rank", "alc_id", "alc_code", "alc_name", "sbu", "dcu", "verified_leads",
    "verified_admissions", "activities_done", "partners", "score",
}
_numbers = itertools.count(1)


def activity(alc_id, status=S.VERIFIED, leads=0, admissions=0) -> Activity:
    n = next(_numbers)
    return Activity(
        activity_number=f"ACT-LB-{n:05d}", alc_id=alc_id, activity_type="Workshop",
        ecosystem="College", activity_date=date(2026, 6, 1), location="Nashik",
        learners_reached=0, leads_generated=leads, admissions_generated=admissions,
        description="Leaderboard test activity.", outcome="Done", status=status,
    )


def partner(alc_id, status="ACTIVE", name="Partner") -> Partner:
    return Partner(alc_id=alc_id, partner_name=name, partner_type="College",
                   ecosystem="College", status=status)


async def add_alc(session, code, sbu_id, status=AlcStatus.ACTIVE) -> ALC:
    alc = ALC(alc_code=code, alc_name=f"Centre {code}", sbu_id=sbu_id, status=status)
    session.add(alc)
    await session.flush()
    return alc


async def add_scored_alc(session, code, sbu_id, leads=0, admissions=0, done=0, partners=0):
    """An ALC with exactly these metric values (``done`` verified activities)."""
    alc = await add_alc(session, code, sbu_id)
    for i in range(done):
        session.add(activity(alc.id, leads=leads if i == 0 else 0,
                             admissions=admissions if i == 0 else 0))
    if done == 0 and (leads or admissions):
        raise ValueError("leads/admissions need at least one verified activity")
    session.add_all([partner(alc.id) for _ in range(partners)])
    await session.flush()
    return alc


@pytest_asyncio.fixture
async def world(session, seeded):
    """Seeded Centres A, C (SBU 4) and B (SBU 6) under DCU Nashik / RCU Pune, a DCU login."""
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    session.add(User(username="dcu-nashik", password_hash=hash_password(DCU_PW),
                     role=Role.DCU, dcu_id=nashik.id))
    await session.commit()
    return {**seeded, "nashik": nashik}


async def fetch(client):
    response = await client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def by_code(payload) -> dict:
    return {item["alc_code"]: item for item in payload["items"]}


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #
async def test_unauthenticated_is_401(client, world):
    response = await client.get(URL)
    assert response.status_code == 401


async def test_every_operational_role_gets_the_same_regional_result(client, session, world):
    # Centre B is outside SBU 4's and ALC A's normal scope but must still be ranked.
    session.add_all([
        activity(world["alc_a"].id, leads=5),
        activity(world["alc_b"].id, leads=9, admissions=2),
        partner(world["alc_c"].id),
    ])
    await session.commit()
    results = []
    for identifier, password, portal in ROLES:
        await as_user(client, identifier, password, portal)
        payload = await fetch(client)
        assert payload["period"] == "LIFETIME"
        assert payload["generated_at"]
        results.append(payload["items"])
    assert all(items == results[0] for items in results)
    assert {item["alc_code"] for item in results[0]} == {"00010001", "00010002", "00010003"}


async def test_must_change_password_is_denied(client, session, world):
    for username, (identifier, password, portal) in (("admin", ROLES[0]), ("alc-a", ROLES[3])):
        user = await session.scalar(select(User).where(User.username == username))
        user.must_change_password = True
        await session.commit()
        client.cookies.clear()
        client.headers.pop("X-CSRF-Token", None)
        assert (await login(client, identifier, password, portal)).status_code == 200
        response = await client.get(URL)
        assert response.status_code == 403
        assert "Password change required" in response.text


async def test_existing_guards_are_not_widened(client, world):
    await as_user(client, "00010001", "StrongAlcPassA!")
    assert (await client.get("/api/admin/dashboard")).status_code == 403
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    assert (await client.get("/api/admin/dashboard")).status_code == 403
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await client.get("/api/portal/dashboard")).status_code == 403
    assert (await client.get(URL)).status_code == 200


async def test_response_exposes_only_leaderboard_fields(client, session, world):
    session.add_all([activity(world["alc_a"].id, leads=3),
                     partner(world["alc_a"].id, name="Secret Partner")])
    await session.commit()
    await as_user(client, "00010001", "StrongAlcPassA!")
    payload = await fetch(client)
    assert set(payload) == {"period", "generated_at", "items"}
    item = payload["items"][0]
    assert set(item) == ITEM_KEYS
    assert set(item["sbu"]) == {"code", "name"} and set(item["dcu"]) == {"code", "name"}
    assert item["sbu"] == {"code": "SBU 4", "name": "Strategic Business Unit 4"}
    assert item["dcu"] == {"code": "DCU_NASHIK", "name": "DCU Nashik"}
    assert "Secret Partner" not in str(payload) and "ACT-LB" not in str(payload)


# --------------------------------------------------------------------------- #
# Metric rules
# --------------------------------------------------------------------------- #
async def test_only_verified_activities_count(client, session, world):
    alc = world["alc_a"].id
    for status in (S.DRAFT, S.SUBMITTED, S.RESUBMITTED, S.UNDER_REVIEW,
                   S.CORRECTION_REQUIRED, S.REJECTED):
        session.add(activity(alc, status=status, leads=1000, admissions=1000))
    session.add_all([activity(alc, leads=7, admissions=2), activity(alc, leads=3, admissions=1)])
    await session.commit()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    row = by_code(await fetch(client))["00010001"]
    assert row["verified_leads"] == 10
    assert row["verified_admissions"] == 3
    assert row["activities_done"] == 2


async def test_only_non_verified_activity_means_not_ranked(client, session, world):
    for status in (S.DRAFT, S.SUBMITTED, S.RESUBMITTED, S.UNDER_REVIEW,
                   S.CORRECTION_REQUIRED, S.REJECTED):
        session.add(activity(world["alc_a"].id, status=status, leads=50, admissions=5))
    await session.commit()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await fetch(client))["items"] == []


async def test_active_partner_rows_count_inactive_do_not(client, session, world):
    alc = world["alc_a"].id
    session.add_all([
        partner(alc, name="Same Name"), partner(alc, name="Same Name"),  # not de-duplicated
        partner(alc, status="INACTIVE"), partner(alc, status="Paused"),
        partner(world["alc_b"].id, status="INACTIVE"),
    ])
    await session.commit()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    rows = by_code(await fetch(client))
    assert rows["00010001"]["partners"] == 2
    assert "00010002" not in rows  # only an inactive partner: all metrics zero


async def test_activities_and_partners_do_not_fan_out(client, session, world):
    alc = world["alc_a"].id
    session.add_all([activity(alc, leads=10, admissions=1) for _ in range(3)])
    session.add_all([partner(alc, name=f"P{i}") for i in range(4)])
    await session.commit()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    row = by_code(await fetch(client))["00010001"]
    assert (row["verified_leads"], row["verified_admissions"]) == (30, 3)
    assert (row["activities_done"], row["partners"]) == (3, 4)


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #
async def test_eligibility_requires_active_alc_and_complete_hierarchy(client, session, world):
    sbu4 = world["sbu4"].id
    inactive = await add_alc(session, "00090001", sbu4, status=AlcStatus.INACTIVE)
    unplaced = await add_alc(session, "00090002", None)
    sbu_without_dcu = SBU(code="SBU 1", name="Demo SBU")
    dcu_without_rcu = DCU(code="DCU_ORPHAN", name="Orphan DCU", rcu_id=uuid.uuid4())
    session.add_all([sbu_without_dcu, dcu_without_rcu])
    await session.flush()
    sbu_of_orphan_dcu = SBU(code="SBU ORPHAN", name="Orphan SBU", dcu_id=dcu_without_rcu.id)
    session.add(sbu_of_orphan_dcu)
    await session.flush()
    demo = await add_alc(session, "00090003", sbu_without_dcu.id)
    orphan = await add_alc(session, "00090004", sbu_of_orphan_dcu.id)
    for alc in (inactive, unplaced, demo, orphan, world["alc_a"]):
        session.add_all([activity(alc.id, leads=100, admissions=10), partner(alc.id)])
    await session.commit()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    payload = await fetch(client)
    # Centre B / C have no metrics (all-zero) and the four ineligible ALCs are excluded.
    assert [item["alc_code"] for item in payload["items"]] == ["00010001"]
    assert payload["items"][0]["score"] == 100.0


async def _branch(session, tag, *, rcu_active=True, dcu_active=True, sbu_active=True):
    """A separate RCU -> DCU -> SBU chain with one ACTIVE, performing ALC."""
    rcu = RCU(code=f"RCU_{tag}", name=f"RCU {tag}", is_active=rcu_active)
    session.add(rcu)
    await session.flush()
    dcu = DCU(code=f"DCU_{tag}", name=f"DCU {tag}", rcu_id=rcu.id, is_active=dcu_active)
    session.add(dcu)
    await session.flush()
    sbu = SBU(code=f"SBU {tag}", name=f"SBU {tag}", dcu_id=dcu.id, is_active=sbu_active)
    session.add(sbu)
    await session.flush()
    return await add_scored_alc(session, f"0004{tag}", sbu.id, leads=50, admissions=5, done=2,
                                partners=3)


async def test_active_alc_under_inactive_sbu_is_excluded(session, world):
    await _branch(session, "0001", sbu_active=False)
    await session.commit()
    assert (await region_top10(session))["items"] == []


async def test_active_alc_under_inactive_dcu_is_excluded(session, world):
    await _branch(session, "0002", dcu_active=False)
    await session.commit()
    assert (await region_top10(session))["items"] == []


async def test_active_alc_under_inactive_rcu_is_excluded(session, world):
    await _branch(session, "0003", rcu_active=False)
    await session.commit()
    assert (await region_top10(session))["items"] == []


async def test_fully_active_hierarchy_with_performance_is_eligible(session, world):
    await _branch(session, "0004")
    await _branch(session, "0005", sbu_active=False)
    await _branch(session, "0006", dcu_active=False)
    await _branch(session, "0007", rcu_active=False)
    await session.commit()
    items = (await region_top10(session))["items"]
    assert [i["alc_code"] for i in items] == ["00040004"]
    assert items[0]["sbu"]["code"] == "SBU 0004" and items[0]["dcu"]["code"] == "DCU_0004"
    # The only eligible ALC: every non-zero component is 100.
    assert items[0]["score"] == 100.0


async def test_inactive_units_do_not_affect_normalisation(session, world):
    # An excluded ALC under an inactive SBU must not count in the percentile population.
    await add_scored_alc(session, "00030001", world["sbu4"].id, leads=10, done=1)
    await add_scored_alc(session, "00030002", world["sbu4"].id, leads=20, done=1)
    await _branch(session, "0008", sbu_active=False)  # leads 50 would otherwise top leads
    await session.commit()
    items = (await region_top10(session))["items"]
    assert [i["alc_code"] for i in items] == ["00030002", "00030001"]
    assert [i["score"] for i in items] == [50.0, 25.0]


async def test_all_zero_alcs_are_excluded_and_not_used_for_normalisation(session, world):
    await add_scored_alc(session, "00080001", world["sbu4"].id, leads=10, done=1)
    await add_scored_alc(session, "00080002", world["sbu4"].id, leads=20, done=1)
    await session.commit()
    items = (await region_top10(session))["items"]
    # Seeded Centres A, B, C have nothing: only two eligible ALCs, so the percentile
    # denominator is 1 (not 4) and the higher-leads ALC scores 100 for leads.
    assert [i["alc_code"] for i in items] == ["00080002", "00080001"]
    # leads 100 / 0, admissions both 0 -> 0, activities both 1 -> 100, partners 0 -> 0.
    assert [i["score"] for i in items] == [50.0, 25.0]


# --------------------------------------------------------------------------- #
# Percentiles and scores
# --------------------------------------------------------------------------- #
def test_percentile_formula_counts_strictly_lower_values():
    assert percentile_scores([10, 20, 30, 40, 50]) == [0, 25, 50, 75, 100]
    assert percentile_scores([3, 1, 2]) == [100, 0, 50]


def test_percentile_ties_share_a_score():
    assert percentile_scores([5, 5, 10]) == [0, 0, 100]
    third = Fraction(100, 3)
    assert percentile_scores([1, 5, 5, 10]) == [0, third, third, 100]


def test_percentile_all_zero_scores_zero():
    assert percentile_scores([0, 0, 0]) == [0, 0, 0]


def test_percentile_same_non_zero_value_scores_hundred():
    assert percentile_scores([7, 7, 7]) == [100, 100, 100]


def test_percentile_single_alc():
    assert percentile_scores([5]) == [100]
    assert percentile_scores([0]) == [0]
    assert percentile_scores([]) == []


def test_different_scales_contribute_equally():
    # Leads in the thousands and partners in single digits: the same relative position
    # gives the same component score, so neither metric dominates the total.
    rows = score_rows([
        _row("A", leads=5000, admissions=1, done=1, partners=1),
        _row("B", leads=50, admissions=40, done=9, partners=3),
        _row("C", leads=900, admissions=10, done=4, partners=2),
    ])
    scores = {r.alc_code: r for r in rows}
    assert scores["A"].components == {
        "verified_leads": 100, "verified_admissions": 0, "activities_done": 0, "partners": 0,
    }
    assert scores["A"].score == 25
    assert scores["B"].score == 75  # 0 + 100 + 100 + 100, each x 0.25
    assert scores["C"].score == 50  # 50 + 50 + 50 + 50, each x 0.25


def test_equal_weights_of_25_percent():
    assert WEIGHTS == {metric: Fraction(1, 4) for metric in METRICS}
    assert sum(WEIGHTS.values()) == 1
    for metric in METRICS:
        values = {m: 0 for m in METRICS}
        values[metric] = 1
        top, _ = score_rows([_row("X", **_short(values)), _row("Y")])
        assert top.score == 25, metric


def test_single_eligible_alc_end_to_end_components():
    (row,) = score_rows([_row("A", leads=5, admissions=0, done=1, partners=0)])
    assert row.components == {
        "verified_leads": 100, "verified_admissions": 0, "activities_done": 100, "partners": 0,
    }
    assert row.score == 50


def test_ranking_uses_full_precision_before_alc_code():
    # Both display as 66.67, but the exact score decides, not the (smaller) ALC code.
    low = _row("00000001", score=Fraction(66665, 1000))
    high = _row("00000002", score=Fraction(666661, 10000))
    assert display_score(low.score) == display_score(high.score) == 66.67
    assert [r.alc_code for r in rank_rows([low, high])] == ["00000002", "00000001"]


def test_equal_exact_scores_break_ties_by_alc_code():
    rows = [_row(code, score=Fraction(50)) for code in ("00000300", "00000100", "00000200")]
    assert [r.alc_code for r in rank_rows(rows)] == ["00000100", "00000200", "00000300"]


def test_display_score_rounds_half_up_to_two_decimals():
    assert display_score(Fraction(12125, 1000)) == 12.13
    assert display_score(Fraction(100, 3)) == 33.33
    assert display_score(Fraction(0)) == 0.0


def _short(values: dict) -> dict:
    return {"leads": values["verified_leads"], "admissions": values["verified_admissions"],
            "done": values["activities_done"], "partners": values["partners"]}


def _row(code, leads=0, admissions=0, done=0, partners=0, score=None) -> LeaderboardRow:
    row = LeaderboardRow(
        alc_id=uuid.uuid4(), alc_code=code, alc_name=f"Centre {code}", sbu_code="SBU 4",
        sbu_name="SBU", dcu_code="DCU_NASHIK", dcu_name="DCU Nashik", verified_leads=leads,
        verified_admissions=admissions, activities_done=done, partners=partners,
    )
    if score is not None:
        row.score = score
    return row


# --------------------------------------------------------------------------- #
# Top 10 size and normalisation over the whole region
# --------------------------------------------------------------------------- #
async def _region(session, sbu_id, count):
    """``count`` ALCs whose leads are 1..count (one verified activity each)."""
    for i in range(1, count + 1):
        await add_scored_alc(session, f"0007{i:04d}", sbu_id, leads=i, done=1)
    await session.commit()


async def test_fewer_than_ten_returns_fewer(session, world):
    await _region(session, world["sbu4"].id, 3)
    items = (await region_top10(session))["items"]
    assert [i["rank"] for i in items] == [1, 2, 3]


async def test_exactly_ten(session, world):
    await _region(session, world["sbu4"].id, 10)
    items = (await region_top10(session))["items"]
    assert [i["rank"] for i in items] == list(range(1, 11))
    assert items[0]["alc_code"] == "00070010" and items[-1]["alc_code"] == "00070001"


async def test_more_than_ten_normalises_over_every_eligible_alc(session, world):
    await _region(session, world["sbu4"].id, 12)
    items = (await region_top10(session))["items"]
    assert len(items) == 10 and [i["rank"] for i in items] == list(range(1, 11))
    assert [i["alc_code"] for i in items] == [f"0007{i:04d}" for i in range(12, 2, -1)]
    # Leads percentile uses all 12 ALCs (denominator 11); activities are all 1 -> 100.
    # The 10th row (leads 3) has 2 lower values: 100 * 2 / 11.
    tenth = Fraction(100 * 2, 11) / 4 + Fraction(100, 4)
    assert items[-1]["score"] == display_score(tenth) == 29.55
    assert items[0]["score"] == 50.0


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
async def _grow(session, sbu_id, prefix, alcs, activities, partners):
    for i in range(alcs):
        alc = await add_alc(session, f"{prefix}{i:04d}", sbu_id)
        session.add_all([activity(alc.id, leads=i + j, admissions=j % 2)
                         for j in range(activities)])
        session.add_all([activity(alc.id, status=S.SUBMITTED, leads=99)])
        session.add_all([partner(alc.id, name=f"P{j}") for j in range(partners)])
    await session.commit()


async def test_single_statement_regardless_of_data_volume(session, world):
    await _grow(session, world["sbu4"].id, "0006", alcs=3, activities=2, partners=1)
    with StatementLog(session) as small, ActivityLoads() as loads:
        await region_top10(session)
    await _grow(session, world["sbu4"].id, "0005", alcs=25, activities=6, partners=4)
    with StatementLog(session) as large, ActivityLoads() as more_loads:
        payload = await region_top10(session)
    assert len(small.statements) == len(large.statements) == 1
    assert len(large.touching("activities")) == 1 and len(large.touching("partners")) == 1
    assert loads.count == more_loads.count == 0  # no Activity rows hydrated
    assert len(payload["items"]) == 10


async def test_endpoint_statement_count_is_constant(client, session, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    await _grow(session, world["sbu4"].id, "0006", alcs=2, activities=1, partners=1)
    with StatementLog(session) as small:
        await fetch(client)
    await _grow(session, world["sbu4"].id, "0005", alcs=20, activities=5, partners=3)
    with StatementLog(session) as large:
        await fetch(client)
    assert len(small.statements) == len(large.statements)
    assert len(large.touching("activities")) == 1


def test_query_preaggregates_before_joining():
    sql = str(leaderboard.regional_metrics_query())
    assert sql.count("GROUP BY") == 2  # one per pre-aggregated subquery, none outside
    assert "verified_activity" in sql and "active_partners" in sql
