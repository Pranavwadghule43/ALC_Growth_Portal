"""Migration 0012 accepts both fresh-current and legacy upgrade starting states."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

API_DIR = Path(__file__).resolve().parents[1]
PATH = API_DIR / "alembic" / "versions" / "20261107_0012_yearly_activity_dcu_scope.py"


def migration():
    spec = importlib.util.spec_from_file_location("migration_0012", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_schema_with_dcu_constraint_is_an_upgrade_noop():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE yearly_activity_definitions (
                id VARCHAR(32) PRIMARY KEY,
                rcu_id VARCHAR(32) NOT NULL,
                introduced_by_dcu_id VARCHAR(32) NOT NULL,
                calendar_year INTEGER NOT NULL,
                activity_code VARCHAR(64) NOT NULL,
                CONSTRAINT uq_yearly_activity_definition_dcu_year_code
                    UNIQUE (introduced_by_dcu_id, calendar_year, activity_code)
            )
        """))
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
        constraints = sa.inspect(connection).get_unique_constraints(
            "yearly_activity_definitions"
        )
        assert len(constraints) == 1
        assert set(constraints[0]["column_names"]) == {
            "introduced_by_dcu_id",
            "calendar_year",
            "activity_code",
        }
    engine.dispose()


PG_URL = os.environ.get("ALC_TEST_POSTGRES_URL")
requires_isolated_pg = pytest.mark.skipif(
    not PG_URL
    or sa.engine.make_url(PG_URL).database != "alc_yearly_automated_test_20261107",
    reason="requires the dedicated isolated PostgreSQL migration database",
)


@requires_isolated_pg
async def test_legacy_upgrade_safe_downgrade_and_reupgrade_on_postgres():
    from sqlalchemy.ext.asyncio import create_async_engine

    module = migration()
    schema = f"yearly_scope_test_{uuid.uuid4().hex}"
    engine = create_async_engine(PG_URL, hide_parameters=True)

    def migrate(sync_connection, step):
        with Operations.context(MigrationContext.configure(sync_connection)):
            getattr(module, step)()

    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(sa.text("""
                CREATE TABLE yearly_activity_definitions (
                    id uuid PRIMARY KEY,
                    rcu_id uuid NOT NULL,
                    introduced_by_dcu_id uuid NOT NULL,
                    calendar_year integer NOT NULL,
                    activity_code varchar(64) NOT NULL,
                    CONSTRAINT legacy_rcu_year_code
                        UNIQUE (rcu_id, calendar_year, activity_code)
                )
            """))
            await connection.run_sync(migrate, "upgrade")
            constraints = await connection.run_sync(
                lambda sync: sa.inspect(sync).get_unique_constraints(
                    "yearly_activity_definitions"
                )
            )
            assert {tuple(c["column_names"]) for c in constraints} == {
                ("introduced_by_dcu_id", "calendar_year", "activity_code")
            }

            rcu, first_dcu, second_dcu = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await connection.execute(
                sa.text("""
                    INSERT INTO yearly_activity_definitions
                        (id, rcu_id, introduced_by_dcu_id, calendar_year, activity_code)
                    VALUES (:id1, :rcu, :dcu1, 2026, 'SHARED'),
                           (:id2, :rcu, :dcu2, 2026, 'SHARED')
                """),
                {
                    "id1": uuid.uuid4(),
                    "id2": uuid.uuid4(),
                    "rcu": rcu,
                    "dcu1": first_dcu,
                    "dcu2": second_dcu,
                },
            )
            with pytest.raises(RuntimeError, match="Cannot downgrade"):
                await connection.run_sync(migrate, "downgrade")

            await connection.execute(
                sa.text(
                    "DELETE FROM yearly_activity_definitions "
                    "WHERE introduced_by_dcu_id = :dcu"
                ),
                {"dcu": second_dcu},
            )
            await connection.run_sync(migrate, "downgrade")
            await connection.run_sync(migrate, "upgrade")
    finally:
        async with engine.begin() as connection:
            await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
