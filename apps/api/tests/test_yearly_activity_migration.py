"""Additive upgrade/downgrade and constraints, including an isolated PostgreSQL chain."""
import importlib.util
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models import YearlyActivityCompletion, YearlyActivityDCURun, YearlyActivityDefinition

API_DIR = Path(__file__).resolve().parents[1]
REVISION = "20261103_0008"
TABLES = [YearlyActivityDefinition.__table__, YearlyActivityDCURun.__table__,
          YearlyActivityCompletion.__table__]
NAMES = {table.name for table in TABLES}
spec = importlib.util.spec_from_file_location(
    "yearly_migration", API_DIR / "alembic" / "versions" / f"{REVISION}_yearly_activities.py")
MIGRATION = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MIGRATION)


def run(connection, step):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(MIGRATION, step)()


@pytest.fixture
def legacy():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=[table for table in Base.metadata.sorted_tables
                                               if table.name not in NAMES])
        conn.execute(sa.insert(Base.metadata.tables["alcs"]), {
            "id": uuid.uuid4(), "alc_code": "MIGRATION-ALC", "alc_name": "Existing Centre",
            "status": "ACTIVE", "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)})
    yield engine
    engine.dispose()


def test_revision_upgrade_downgrade_preserves_unrelated_data(legacy):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [REVISION]
    assert MIGRATION.down_revision == "20261002_0007"
    with legacy.begin() as conn:
        before = set(sa.inspect(conn).get_table_names())
        run(conn, "upgrade")
        run(conn, "upgrade")
        assert set(sa.inspect(conn).get_table_names()) == before | NAMES
        for table in TABLES:
            assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 0
            inspector = sa.inspect(conn)
            assert {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)} == {
                c.name: c.nullable for c in table.columns}
            assert {i["name"] for i in inspector.get_indexes(table.name)} == {
                i.name for i in table.indexes}
            assert {c["name"] for c in inspector.get_check_constraints(table.name)} == {
                c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)}
        run(conn, "downgrade")
        assert set(sa.inspect(conn).get_table_names()) == before
        assert conn.execute(sa.text("SELECT alc_name FROM alcs")).scalar() == "Existing Centre"
        run(conn, "upgrade")
        assert set(sa.inspect(conn).get_table_names()) == before | NAMES


def test_constraints_reject_duplicates_and_bad_schedule(legacy):
    now = datetime.now(timezone.utc)
    definition_id, run_id, alc_id, dcu_id = [uuid.uuid4() for _ in range(4)]
    definition = dict(id=definition_id, rcu_id=uuid.uuid4(), calendar_year=2026,
                      activity_code="CODE-A", name="Regional Activity",
                      introduced_by_dcu_id=dcu_id, created_at=now, updated_at=now)
    run_row = dict(id=run_id, yearly_activity_id=definition_id, dcu_id=dcu_id,
                   status="SCHEDULED", scheduled_start_date=date(2026, 1, 1),
                   scheduled_end_date=date(2026, 1, 2), created_at=now, updated_at=now)
    with legacy.begin() as conn:
        run(conn, "upgrade")
        conn.execute(sa.insert(TABLES[0]), definition)
        conn.execute(sa.insert(TABLES[1]), run_row)
    bad_rows = [
        (TABLES[0], {**definition, "id": uuid.uuid4()}),
        (TABLES[0], {**definition, "id": uuid.uuid4(), "activity_code": "lower"}),
        (TABLES[1], {**run_row, "id": uuid.uuid4()}),
        (TABLES[1], {**run_row, "id": uuid.uuid4(), "dcu_id": uuid.uuid4(),
                    "scheduled_end_date": date(2025, 1, 1)}),
    ]
    for table, row in bad_rows:
        with pytest.raises(IntegrityError), legacy.begin() as conn:
            conn.execute(sa.insert(table), row)
    decision = dict(id=uuid.uuid4(), yearly_activity_id=definition_id,
                    yearly_activity_dcu_run_id=run_id, alc_id=alc_id,
                    snapshot_sbu_id=uuid.uuid4(), created_at=now, updated_at=now)
    with legacy.begin() as conn:
        conn.execute(sa.insert(TABLES[2]), decision)
    with pytest.raises(IntegrityError), legacy.begin() as conn:
        conn.execute(sa.insert(TABLES[2]), {**decision, "id": uuid.uuid4()})


PG_URL = os.environ.get("ALC_TEST_POSTGRES_URL")
requires_pg = pytest.mark.skipif(
    not PG_URL or "test" not in (make_url(PG_URL).database or ""),
    reason="set ALC_TEST_POSTGRES_URL to a throwaway PostgreSQL database named *test*",
)


@requires_pg
async def test_postgres_real_chain_and_composite_foreign_keys():
    """Create a unique schema, test there only, remove only the schema created by this test."""
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    schema = f"alc_yearly_test_{uuid.uuid4().hex}"
    engine = create_async_engine(PG_URL)
    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))

    def migrate(connection, method, target):
        config.attributes["connection"] = connection
        getattr(command, method)(config, target)

    created = False
    try:
        async with engine.begin() as conn:
            # No IF NOT EXISTS: a collision never adopts or deletes any existing schema.
            await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with engine.begin() as conn:
            await conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.run_sync(migrate, "upgrade", "head")
            assert await conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == REVISION
            await conn.execute(sa.text("CREATE TABLE migration_sentinel (value text)"))
            await conn.execute(sa.text("INSERT INTO migration_sentinel VALUES ('preserved')"))
            constraints = await conn.execute(sa.text("SELECT contype::text FROM pg_constraint "
                "WHERE conrelid = 'yearly_activity_completions'::regclass"))
            assert sum(kind == "f" for (kind,) in constraints) == 6
            await conn.run_sync(migrate, "downgrade", "20261002_0007")
            for table in TABLES:
                assert not await conn.scalar(sa.text("SELECT to_regclass(:name) IS NOT NULL"),
                                             {"name": table.name})
            await conn.run_sync(migrate, "upgrade", "head")
            assert await conn.scalar(sa.text("SELECT value FROM migration_sentinel")) == "preserved"
            assert await conn.scalar(
                sa.text("SELECT count(*) FROM yearly_activity_definitions")) == 0
            ids = [uuid.uuid4() for _ in range(7)]
            rcu_id, dcu_id, sbu_id, alc_id, definition_id, other_definition_id, run_id = ids
            for table, values in (
                ("rcus", {"id": rcu_id, "code": "TEST", "name": "Test RCU"}),
                ("dcus", {"id": dcu_id, "code": "TEST", "name": "Test DCU", "rcu_id": rcu_id}),
                ("sbus", {"id": sbu_id, "code": "TEST", "name": "Test SBU", "dcu_id": dcu_id}),
                ("alcs", {"id": alc_id, "alc_code": "TEST", "alc_name": "Test Centre",
                          "sbu_id": sbu_id}),
                ("yearly_activity_definitions", {
                    "id": definition_id, "rcu_id": rcu_id, "calendar_year": 2026,
                    "activity_code": "TEST-A", "name": "Test A", "introduced_by_dcu_id": dcu_id}),
                ("yearly_activity_definitions", {
                    "id": other_definition_id, "rcu_id": rcu_id, "calendar_year": 2026,
                    "activity_code": "TEST-B", "name": "Test B", "introduced_by_dcu_id": dcu_id}),
                ("yearly_activity_dcu_runs", {
                    "id": run_id, "yearly_activity_id": definition_id, "dcu_id": dcu_id}),
            ):
                await conn.execute(sa.insert(Base.metadata.tables[table]), values)
            # Both parent identities exist, but this run belongs to A, never B.
            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.execute(sa.insert(TABLES[2]), {
                        "yearly_activity_id": other_definition_id,
                        "yearly_activity_dcu_run_id": run_id,
                        "alc_id": alc_id, "snapshot_sbu_id": sbu_id})
    finally:
        if created:
            async with engine.begin() as conn:
                await conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
