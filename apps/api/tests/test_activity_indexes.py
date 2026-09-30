"""Phase 4J: ``ix_activities_submitted_nondraft`` (partial index for newest-first lists).

The index is ``activities (submitted_at DESC) WHERE status <> 'DRAFT'``. It serves the
Admin list / review queue, the DCU & SBU lists / queues and the supervisor "recent activity"
query, which all read ``WHERE status <> 'DRAFT' ORDER BY submitted_at DESC, updated_at DESC,
id ...``. It must never change what those queries return.

* The SQLite tests (always run) pin the list semantics the index must preserve: membership,
  draft handling, scope, ordering including both tie-breakers, and pagination.
* The PostgreSQL tests are opt-in: set ``ALC_TEST_POSTGRES_URL`` to a THROWAWAY database whose
  name contains "test" (its ``public`` schema is dropped and rebuilt), e.g.
  ``postgresql+asyncpg://user@localhost/alc_index_test``. They run the real Alembic chain,
  the upgrade / downgrade / re-upgrade cycle, check the query plans the app's prepared
  statements get, and compare results with and without the index.
"""
import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateIndex

from app.auth import hash_password
from app.enums import ActivityStatus as S
from app.enums import Role
from app.models import ALC, DCU, SBU, Activity, User
from tests.test_activity_exports import as_user

INDEX = "ix_activities_submitted_nondraft"
API_DIR = Path(__file__).resolve().parents[1]
DCU_PW = "StrongDcuPassword!"
T0 = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
QUEUE = {"SUBMITTED", "RESUBMITTED", "UNDER_REVIEW"}


# --------------------------------------------------------------------------- #
# Definition
# --------------------------------------------------------------------------- #
def test_model_declares_the_partial_index():
    index = next(i for i in Activity.__table__.indexes if i.name == INDEX)
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect())).strip()
    assert ddl == (
        f"CREATE INDEX {INDEX} ON activities (submitted_at DESC) WHERE status <> 'DRAFT'"
    )


def test_migration_is_the_single_head_after_0005():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260930_0006"]
    assert script.get_revision("20260930_0006").down_revision == "20260924_0005"


# --------------------------------------------------------------------------- #
# List semantics the index must preserve (SQLite)
# --------------------------------------------------------------------------- #
def uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


# (id, alc key, status, submitted_at offset in hours or None, updated_at offset in hours)
ROWS = [
    # Three activities tied on BOTH submitted_at and updated_at: only the id breaks the tie.
    (11, "a", S.SUBMITTED, 50, 60), (12, "c", S.VERIFIED, 50, 60), (13, "b", S.RESUBMITTED, 50, 60),
    # Tied on submitted_at only: updated_at decides.
    (21, "a", S.UNDER_REVIEW, 40, 41), (22, "b", S.REJECTED, 40, 45),
    (31, "c", S.CORRECTION_REQUIRED, 30, 31), (32, "a", S.VERIFIED, 20, 90),
    (33, "b", S.SUBMITTED, 10, 11), (34, "c", S.RESUBMITTED, 5, 6),
    # Drafts: no submitted_at; the newest updated_at of all.
    (41, "a", S.DRAFT, None, 99), (42, "b", S.DRAFT, None, 98), (43, "c", S.DRAFT, None, 97),
]


@pytest_asyncio.fixture
async def listing(session, seeded):
    """Centres A, C (SBU 4) and B (SBU 6), all under DCU Nashik (conftest's hierarchy)."""
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    session.add(User(username="dcu-nashik", password_hash=hash_password(DCU_PW), role=Role.DCU,
                     dcu_id=nashik.id))
    alcs = {"a": seeded["alc_a"].id, "b": seeded["alc_b"].id, "c": seeded["alc_c"].id}
    for n, alc, status, submitted, updated in ROWS:
        session.add(Activity(
            id=uid(n), activity_number=f"ACT-IX-{n}", alc_id=alcs[alc], activity_type="Seminar",
            ecosystem="College", activity_date=T0.date(), location="Pune", description="Row",
            outcome="Done", status=status,
            submitted_at=None if submitted is None else T0 + timedelta(hours=submitted),
            updated_at=T0 + timedelta(hours=updated), created_at=T0,
        ))
    await session.commit()
    return {"alcs": alcs}


def expected(rows, id_desc: bool) -> list[str]:
    """Reference ordering: submitted_at DESC, updated_at DESC, then id (DESC or ASC)."""
    return [f"ACT-IX-{r[0]}" for r in sorted(
        rows, key=lambda r: (-r[3], -r[4], -r[0] if id_desc else r[0]))]


def visible(alc_keys="abc", statuses=None):
    return [r for r in ROWS if r[2] != S.DRAFT and r[1] in alc_keys
            and (statuses is None or r[2].value in statuses)]


async def walk(client, path, page_size=2, **params) -> tuple[list[str], int]:
    """Every page of a list endpoint, concatenated; plus the reported total."""
    numbers, page, total = [], 1, None
    while True:
        body = (await client.get(path, params={"page": page, "page_size": page_size,
                                               **params})).json()
        total = body["total"]
        numbers += [(row.get("activity") or row)["activity_number"] for row in body["items"]]
        if page >= body["pages"]:
            return numbers, total
        page += 1


async def test_admin_list_order_ties_drafts_and_pagination(client, listing):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    numbers, total = await walk(client, "/api/admin/activities")
    assert numbers == expected(visible(), id_desc=True)
    assert total == len(numbers) == 9  # drafts never listed
    # The id tie-breaker: 13 > 12 > 11 for identical submitted_at and updated_at.
    assert numbers[:3] == ["ACT-IX-13", "ACT-IX-12", "ACT-IX-11"]


async def test_admin_review_queue_membership_and_order(client, listing):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    numbers, total = await walk(client, "/api/admin/verification-queue", queue_only="true")
    assert numbers == expected(visible(statuses=QUEUE), id_desc=True)
    assert total == 5
    assert {r[2].value for r in visible(statuses=QUEUE)} == QUEUE  # all three queue statuses


async def test_dcu_list_and_queue_use_ascending_id_tie_break(client, listing):
    await as_user(client, "dcu-nashik", DCU_PW)
    numbers, total = await walk(client, "/api/portal/verification")
    assert numbers == expected(visible(), id_desc=False)
    assert numbers[:3] == ["ACT-IX-11", "ACT-IX-12", "ACT-IX-13"] and total == 9
    queue, total = await walk(client, "/api/portal/verification", queue_only="true")
    assert queue == expected(visible(statuses=QUEUE), id_desc=False) and total == 5


async def test_sbu_list_is_scoped_and_ordered(client, listing):
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    numbers, total = await walk(client, "/api/portal/verification")
    assert numbers == expected(visible("ac"), id_desc=False)
    assert total == 6 and "ACT-IX-13" not in numbers  # Centre B is SBU 6


async def test_alc_own_list_keeps_drafts_and_updated_order(client, listing):
    await as_user(client, "00010001", "StrongAlcPassA!")
    numbers, total = await walk(client, "/api/portal/activities")
    own = [r for r in ROWS if r[1] == "a"]
    assert numbers == [f"ACT-IX-{r[0]}" for r in sorted(own, key=lambda r: (-r[4], -r[0]))]
    assert numbers[0] == "ACT-IX-41" and total == 4  # its own draft first (newest update)


async def test_supervisor_recent_activity_order(client, listing):
    await as_user(client, "dcu-nashik", DCU_PW)
    recent = (await client.get("/api/portal/dashboard")).json()["recent_activities"]
    assert [r["activity"]["activity_number"] for r in recent] == expected(
        visible(), id_desc=True)[:8]


async def test_pages_do_not_overlap_or_skip(client, listing):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    for size in (1, 2, 4, 25):
        numbers, total = await walk(client, "/api/admin/activities", page_size=size)
        assert len(numbers) == len(set(numbers)) == total == 9, size


# --------------------------------------------------------------------------- #
# PostgreSQL (opt-in)
# --------------------------------------------------------------------------- #
PG_URL = os.environ.get("ALC_TEST_POSTGRES_URL")
pg = pytest.mark.skipif(
    not PG_URL or "test" not in (make_url(PG_URL).database or ""),
    reason="set ALC_TEST_POSTGRES_URL to a throwaway PostgreSQL database named *test*",
)


def _alembic(*args):
    """Run an Alembic command against PG_URL (env.py reads app.config.settings)."""
    from alembic.config import Config

    from alembic import command
    from app.config import settings

    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    previous, settings.database_url = settings.database_url, PG_URL
    try:
        getattr(command, args[0])(config, *args[1:])
    finally:
        settings.database_url = previous


async def _pg(*statements: str, **params):
    """Run statements in one transaction; rows of the last one (if it returns rows)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(PG_URL)
    try:
        async with engine.begin() as conn:
            for statement in statements:
                result = await conn.execute(text(statement), params)
            return result.all() if result.returns_rows else None
    finally:
        await engine.dispose()


def pg_index() -> tuple | None:
    rows = asyncio.run(_pg(
        "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name", name=INDEX))
    return tuple(rows[0]) if rows else None


def pg_version() -> str:
    return asyncio.run(_pg("SELECT version_num FROM alembic_version"))[0][0]


EXPECTED_DEF = (f"CREATE INDEX {INDEX} ON public.activities USING btree (submitted_at DESC) "
                "WHERE ((status)::text <> 'DRAFT'::text)")


@pytest.fixture(scope="module")
def pg_schema():
    """Fresh schema built by the real migration chain."""
    asyncio.run(_pg("DROP SCHEMA public CASCADE", "CREATE SCHEMA public"))
    _alembic("upgrade", "head")
    yield
    asyncio.run(_pg("DROP SCHEMA public CASCADE", "CREATE SCHEMA public"))


@pg
def test_pg_upgrade_downgrade_reupgrade(pg_schema):
    assert pg_version() == "20260930_0006" and pg_index() == (True, EXPECTED_DEF)
    _alembic("downgrade", "-1")
    assert pg_version() == "20260924_0005" and pg_index() is None
    _alembic("upgrade", "head")
    assert pg_version() == "20260930_0006" and pg_index() == (True, EXPECTED_DEF)


@pg
def test_pg_upgrade_rebuilds_an_invalid_leftover_index(pg_schema):
    _alembic("stamp", "20260924_0005")
    asyncio.run(_pg(
        f"UPDATE pg_index SET indisvalid = false WHERE indexrelid = '{INDEX}'::regclass"))
    assert pg_index()[0] is False
    _alembic("upgrade", "head")
    assert pg_index() == (True, EXPECTED_DEF)


async def _seed_and_check():
    """Seed, capture the app's real list SQL, then check plans and results on PostgreSQL."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import get_db
    from app.main import app

    engine = create_async_engine(PG_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        nashik = await s.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
        sbu = SBU(code="SBU IX", name="SBU IX", dcu_id=nashik.id)
        s.add(sbu)
        await s.flush()
        alcs = [ALC(alc_code=f"9990{i:04d}", alc_name=f"IX {i}", sbu_id=sbu.id) for i in range(20)]
        s.add_all(alcs)
        await s.flush()
        s.add_all([
            User(username="ix-admin", password_hash=hash_password("StrongAdminPass!"),
                 role=Role.ADMIN),
            User(username="ix-dcu", password_hash=hash_password(DCU_PW), role=Role.DCU,
                 dcu_id=nashik.id),
        ])
        statuses = list(S)
        rows = []
        for i in range(24_000):
            status = statuses[i % len(statuses)]
            submitted = None if status == S.DRAFT else T0 + timedelta(minutes=i // 3)
            rows.append({
                "activity_number": f"ACT-PG-{i:06d}", "alc_id": alcs[i % 20].id,
                "activity_type": "Seminar", "ecosystem": "College", "activity_date": T0.date(),
                "location": "Pune", "description": "Row", "outcome": "Done", "status": status,
                "submitted_at": submitted, "created_at": T0,
                # Ties on submitted_at every 3 rows; updated_at ties every 6 rows.
                "updated_at": T0 + timedelta(minutes=i // 6),
            })
        await s.execute(insert(Activity), rows)
        await s.commit()
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.exec_driver_sql("ANALYZE activities")

    captured: list = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "LIMIT" in statement and "activities.activity_number" in statement:
            captured.append((statement, parameters))

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            for who, path, password, urls in (
                ("ix-admin", "/api/auth/admin-login", "StrongAdminPass!",
                 ["/api/admin/activities?page=1", "/api/admin/activities?page=40",
                  "/api/admin/verification-queue?queue_only=true&page=3"]),
                ("ix-dcu", "/api/auth/login", DCU_PW,
                 ["/api/portal/verification?page=1", "/api/portal/verification?page=25",
                  "/api/portal/verification?queue_only=true&page=2"]),
            ):
                c.cookies.clear()
                assert (await c.post(path, json={"identifier": who,
                                                 "password": password})).status_code == 200
                for url in urls:
                    assert (await c.get(url)).status_code == 200, url
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        app.dependency_overrides.pop(get_db, None)
    assert len(captured) == 6

    results = []
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        for n, (sql, params) in enumerate(captured):
            # The app runs these as asyncpg prepared statements: execute one repeatedly, the
            # way a busy connection does, then look at the plan PostgreSQL settled on.
            literals = ", ".join(
                "NULL" if p is None else f"'{p}'" if isinstance(p, (str, uuid.UUID)) else str(p)
                for p in params)
            await raw.execute(f"PREPARE ix_q{n} AS {sql}")
            for _ in range(8):
                await raw.execute(f"EXECUTE ix_q{n}({literals})")
            plan = await raw.fetchval(f"EXPLAIN (FORMAT JSON) EXECUTE ix_q{n}({literals})")
            plan = json.loads(plan) if isinstance(plan, str) else plan  # codec may decode it
            with_index = await raw.fetch(f"EXECUTE ix_q{n}({literals})")
            # Same statement without the index (dropped inside a rolled-back transaction).
            transaction = raw.transaction()
            await transaction.start()
            try:
                await raw.execute(f"DROP INDEX {INDEX}")
                without_index = await raw.fetch(sql, *params)
            finally:
                await transaction.rollback()
            results.append((json.dumps(plan), [r["id"] for r in with_index],
                            [r["id"] for r in without_index]))
    await engine.dispose()
    return results


@pg
def test_pg_list_queries_use_the_index_and_return_identical_pages(pg_schema):
    results = asyncio.run(_seed_and_check())
    assert pg_index() == (True, EXPECTED_DEF)  # the rolled-back DROP left it in place
    # Identical rows, in identical order, with and without the index: all six queries.
    for _plan, with_index, without_index in results:
        assert with_index == without_index and len(with_index) == 25
    # The unscoped Admin list and review queue use the index even after repeated prepared
    # executions (the app's asyncpg path). The DCU-scoped queries (last three) may pick
    # another plan when, as in this small fixture, the DCU's scope is the whole table.
    for plan, _with_index, _without_index in results[:3]:
        assert INDEX in plan
