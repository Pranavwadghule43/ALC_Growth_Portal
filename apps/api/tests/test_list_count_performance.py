"""Phase 4K: paginated activity-list totals are counted with ``COUNT(*)``.

``app.services.activity_lists.activity_total`` counts ``activities`` alone with every list
filter being an ``activities`` column or an ``IN`` subquery (scope, search), and ``id`` is the
NOT NULL primary key, so ``COUNT(*)`` returns exactly what ``COUNT(activities.id)`` did. The
gain is on PostgreSQL: no column value is needed, so a DCU / SBU / ALC scoped total can be read
from ``ix_activities_alc_status`` with an index-only scan instead of visiting every row.

These tests pin the totals (against the rows the list actually returns), the count statement
shape, and — opt-in, with ``ALC_TEST_POSTGRES_URL`` — the PostgreSQL plan.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import insert, select

from app.auth import hash_password
from app.enums import ActivityStatus as S
from app.enums import Role
from app.models import ALC, DCU, RCU, SBU, Activity, User
from tests.test_activity_exports import ActivityLoads, StatementLog, as_user
from tests.test_activity_indexes import DCU_PW, PG_URL, pg, pg_schema  # noqa: F401
from tests.test_list_query_performance import outer_from

T0 = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
QUEUE = (S.SUBMITTED, S.RESUBMITTED, S.UNDER_REVIEW)


@pytest_asyncio.fixture
async def branches(session, seeded):
    """DCU Nashik: SBU 4 (Centres A, C) and SBU 6 (Centre B) — conftest's hierarchy.
    DCU X (second RCU branch): SBU X with Centres D and E. DCU Empty: no SBU at all.
    Every centre has two activities of every status (so two drafts each)."""
    rcu = RCU(code="RCU_K", name="RCU K")
    session.add(rcu)
    await session.flush()
    dcu_x = DCU(code="DCU_KX", name="DCU KX", rcu_id=rcu.id)
    dcu_empty = DCU(code="DCU_KEMPTY", name="DCU K Empty", rcu_id=rcu.id)
    session.add_all([dcu_x, dcu_empty])
    await session.flush()
    sbu_x = SBU(code="SBU KX", name="SBU KX", dcu_id=dcu_x.id)
    session.add(sbu_x)
    await session.flush()
    alc_d = ALC(alc_code="00030001", alc_name="Centre D", sbu_id=sbu_x.id)
    alc_e = ALC(alc_code="00030002", alc_name="Centre E", sbu_id=sbu_x.id)
    session.add_all([alc_d, alc_e])
    await session.flush()
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    session.add_all([
        User(username="dcu-nashik", password_hash=hash_password(DCU_PW), role=Role.DCU,
             dcu_id=nashik.id),
        User(username="dcu-kx", password_hash=hash_password(DCU_PW), role=Role.DCU,
             dcu_id=dcu_x.id),
        User(username="dcu-kempty", password_hash=hash_password(DCU_PW), role=Role.DCU,
             dcu_id=dcu_empty.id),
        User(username="sbu-kx", password_hash=hash_password(DCU_PW), role=Role.SBU,
             sbu_id=sbu_x.id),
    ])
    alcs = {"a": seeded["alc_a"].id, "b": seeded["alc_b"].id, "c": seeded["alc_c"].id,
            "d": alc_d.id, "e": alc_e.id}
    n = 0
    for key, alc_id in alcs.items():
        for status in S:
            for copy in range(2):
                n += 1
                session.add(Activity(
                    activity_number=f"ACT-K-{key}{n:03d}", alc_id=alc_id,
                    activity_type="Seminar", ecosystem="College", activity_date=T0.date(),
                    location="Pune", description="Row", outcome="Done", status=status,
                    submitted_at=None if status == S.DRAFT else T0 + timedelta(hours=n),
                    updated_at=T0 + timedelta(hours=n, minutes=copy), created_at=T0,
                ))
    await session.commit()
    return alcs


async def walk(client, path, page_size=7, **params) -> tuple[list[str], int, int]:
    """Every page concatenated, the total, and the number of pages; also checks that each
    page reports the same total and that asking past the last page returns no items."""
    numbers, page, total = [], 1, None
    while True:
        body = (await client.get(path, params={"page": page, "page_size": page_size,
                                               **params})).json()
        assert total in (None, body["total"]), "total must not vary with the page"
        total = body["total"]
        assert body["page"] == page and body["page_size"] == page_size
        numbers += [(row.get("activity") or row)["activity_number"] for row in body["items"]]
        if page >= max(body["pages"], 1):
            break
        page += 1
    past = (await client.get(path, params={"page": page + 1, "page_size": page_size,
                                           **params})).json()
    assert past["items"] == [] and past["total"] == total
    return numbers, total, body["pages"]


async def check(client, path, expected_total, **params):
    numbers, total, pages = await walk(client, path, **params)
    assert total == expected_total, (path, params, total)
    assert len(numbers) == len(set(numbers)) == total  # items and total agree
    assert pages == (-(-total // 7) if total else 0)
    return numbers


# --------------------------------------------------------------------------- #
# Totals: status rules, queue membership, scope, search, empty
# --------------------------------------------------------------------------- #
async def test_admin_totals(client, branches):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    numbers = await check(client, "/api/admin/activities", 60)  # 5 centres x 6 statuses x 2
    assert not any(n for n in numbers if "DRAFT" in n)
    await check(client, "/api/admin/verification-queue", 30, queue_only="true")
    await check(client, "/api/admin/activities", 10, status="VERIFIED")
    await check(client, "/api/admin/activities", 0, status="DRAFT")  # drafts never listed
    await check(client, "/api/admin/activities", 12, search="Centre D")
    await check(client, "/api/admin/activities", 0, search="no such thing")


async def test_dcu_totals_are_scoped(client, branches):
    await as_user(client, "dcu-nashik", DCU_PW)
    await check(client, "/api/portal/verification", 36)
    await check(client, "/api/portal/verification", 18, queue_only="true")
    await check(client, "/api/portal/activities", 36)
    await as_user(client, "dcu-kx", DCU_PW)
    numbers = await check(client, "/api/portal/verification", 24)
    assert all(n.startswith(("ACT-K-d", "ACT-K-e")) for n in numbers)
    await check(client, "/api/portal/verification", 12, queue_only="true")


async def test_sbu_totals_are_scoped(client, branches):
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    numbers = await check(client, "/api/portal/verification", 24)  # Centres A and C
    assert all(n.startswith(("ACT-K-a", "ACT-K-c")) for n in numbers)
    await check(client, "/api/portal/verification", 12, queue_only="true")
    await as_user(client, "sbu-kx", DCU_PW)
    await check(client, "/api/portal/verification", 24)


async def test_alc_own_total_includes_its_drafts(client, branches):
    await as_user(client, "00010001", "StrongAlcPassA!")
    await check(client, "/api/portal/activities", 14)  # all 7 statuses x 2, drafts included
    await check(client, "/api/portal/activities", 2, status="DRAFT")


async def test_search_never_counts_rows_outside_the_scope(client, branches):
    """The same search text matches centres in both DCUs; each DCU counts only its own."""
    await as_user(client, "dcu-nashik", DCU_PW)
    await check(client, "/api/portal/verification", 36, search="Centre")
    await check(client, "/api/portal/verification", 0, search="Centre D")
    await check(client, "/api/portal/verification", 0, search="ACT-K-d")
    await as_user(client, "dcu-kx", DCU_PW)
    await check(client, "/api/portal/verification", 24, search="Centre")
    await check(client, "/api/portal/verification", 12, search="Centre D")
    await check(client, "/api/portal/verification", 6, search="Centre D", queue_only="true")
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    await check(client, "/api/portal/verification", 0, search="Centre B")  # SBU 6's centre
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    await check(client, "/api/admin/activities", 60, search="Centre")


async def test_empty_scope_and_empty_filters_total_zero(client, branches):
    await as_user(client, "dcu-kempty", DCU_PW)
    for params in ({}, {"queue_only": "true"}, {"search": "Centre"}):
        body = (await client.get("/api/portal/verification", params=params)).json()
        assert (body["total"], body["pages"], body["items"]) == (0, 0, []), params
    body = (await client.get("/api/portal/activities")).json()
    assert body["total"] == 0 and body["items"] == []
    await as_user(client, "dcu-kx", DCU_PW)
    body = (await client.get("/api/portal/verification",
                             params={"date_from": "2030-01-01"})).json()
    assert body["total"] == 0 and body["items"] == []


# --------------------------------------------------------------------------- #
# Count statement shape
# --------------------------------------------------------------------------- #
# Statements per request, identical before and after Phase 4K (measured on e81867b): the
# user lookup and its hierarchy-availability loaders, then count + page + page summaries.
LISTS = [
    (("admin", "StrongAdminPass!", "ADMIN"), "/api/admin/activities", {}, 4),
    (("admin", "StrongAdminPass!", "ADMIN"), "/api/admin/verification-queue",
     {"queue_only": "true", "search": "Centre"}, 4),
    (("dcu-nashik", DCU_PW), "/api/portal/verification", {}, 6),
    (("dcu-nashik", DCU_PW), "/api/portal/verification", {"queue_only": "true"}, 6),
    (("sbu-4", "StrongSbuPass4!"), "/api/portal/verification", {"search": "Centre"}, 7),
    (("dcu-nashik", DCU_PW), "/api/portal/activities", {}, 6),
    (("00010001", "StrongAlcPassA!"), "/api/portal/activities", {}, 8),
]


@pytest.mark.parametrize(("who", "path", "params", "statements"), LISTS)
async def test_count_statement_shape(client, session, branches, who, path, params, statements):
    await as_user(client, *who)
    with StatementLog(session) as log, ActivityLoads() as loads:
        response = await client.get(path, params=params)
    assert response.status_code == 200
    counts = [s for s in log.statements if s.lstrip().lower().startswith("select count(")]
    [count] = counts  # exactly one count statement per list request
    assert count.lstrip().startswith("SELECT count(*)")
    assert outer_from(count) == "activities"  # no join; scope / search stay IN subqueries
    assert loads.count == 0
    # No extra round trips compared with COUNT(activities.id).
    assert len(log.statements) == statements


# --------------------------------------------------------------------------- #
# PostgreSQL plan (opt-in, see tests/test_activity_indexes.py)
# --------------------------------------------------------------------------- #
async def _scoped_count_plans():
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import get_db
    from app.main import app

    engine = create_async_engine(PG_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        nashik = await s.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
        north = await s.scalar(select(DCU).where(DCU.code == "DCU_PUNE_NORTH"))
        ours = SBU(code="SBU K1", name="SBU K1", dcu_id=nashik.id)
        others = SBU(code="SBU K2", name="SBU K2", dcu_id=north.id)
        s.add_all([ours, others])
        await s.flush()
        alcs = [ALC(alc_code=f"8880{i:04d}", alc_name=f"K {i}",
                    sbu_id=(ours if i < 10 else others).id) for i in range(100)]
        s.add_all(alcs)
        await s.flush()
        s.add(User(username="k-dcu", password_hash=hash_password(DCU_PW), role=Role.DCU,
                   dcu_id=nashik.id))
        statuses = list(S)
        await s.execute(insert(Activity), [
            {"id": uuid.uuid4(), "activity_number": f"ACT-KPG-{i:06d}",
             "alc_id": alcs[i % 100].id, "activity_type": "Seminar", "ecosystem": "College",
             "activity_date": T0.date(), "location": "Pune", "description": "Row",
             "outcome": "Done", "status": statuses[i % len(statuses)],
             "submitted_at": T0 + timedelta(minutes=i), "updated_at": T0, "created_at": T0}
            for i in range(30_000)
        ])
        await s.commit()
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.exec_driver_sql("VACUUM ANALYZE activities")

    captured = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("select count("):
            captured.append((statement, parameters))

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/auth/login", json={"identifier": "k-dcu",
                                                          "password": DCU_PW})).status_code == 200
            totals = [(await c.get(url)).json()["total"] for url in (
                "/api/portal/verification", "/api/portal/verification?queue_only=true")]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        app.dependency_overrides.pop(get_db, None)
    out = []
    async with engine.connect() as conn:
        for (sql, params), total in zip(captured, totals, strict=True):
            plan = (await conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql, params)).scalar()
            legacy = sql.replace("count(*)", "count(activities.id)")
            old_total = (await conn.exec_driver_sql(legacy, params)).scalar()
            out.append((json.dumps(plan), total, old_total))
    await engine.dispose()
    return out


@pg
def test_pg_scoped_counts_use_an_index_only_scan(pg_schema):  # noqa: F811
    results = asyncio.run(_scoped_count_plans())
    assert len(results) == 2
    for plan, total, old_total in results:
        assert total == old_total > 0  # identical to COUNT(activities.id)
        assert "Index Only Scan" in plan and "ix_activities_alc_status" in plan
