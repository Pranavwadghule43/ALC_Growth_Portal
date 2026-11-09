"""Additive evidence schema and conservative legacy YES preservation."""

import importlib.util
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
    YearlyActivityEvidence,
    YearlyActivityReview,
    YearlyActivitySubmission,
)

API = Path(__file__).resolve().parents[1]
NEW = {"yearly_activity_submissions", "yearly_activity_evidence", "yearly_activity_reviews"}
spec = importlib.util.spec_from_file_location(
    "yearly_evidence_migration", API / "alembic/versions/20261104_0010_yearly_evidence.py"
)
MIGRATION = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MIGRATION)


def run(conn, direction):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(MIGRATION, direction)()


def seed_legacy(conn):
    with Session(conn) as db:
        rcu = RCU(code="LEGACY", name="Migration sentinel")
        db.add(rcu)
        db.flush()
        dcu = DCU(code="LEGACY", name="Migration sentinel", rcu_id=rcu.id)
        db.add(dcu)
        db.flush()
        sbu = SBU(code="LEGACY", name="Migration sentinel", dcu_id=dcu.id)
        definition = YearlyActivityDefinition(
            rcu_id=rcu.id,
            calendar_year=2026,
            activity_code="LEGACY",
            name="Historical manual activity",
            introduced_by_dcu_id=dcu.id,
        )
        db.add_all([sbu, definition])
        db.flush()
        run_row = YearlyActivityDCURun(
            yearly_activity_id=definition.id,
            dcu_id=dcu.id,
            status="FINALIZED",
            finalized_at=datetime.now(timezone.utc),
            scheduled_start_date=date(2026, 1, 1),
            scheduled_end_date=date(2026, 1, 1),
        )
        db.add(run_row)
        db.flush()
        for index, value in enumerate([True, False, None]):
            alc = ALC(alc_code=f"LEGACY{index}", alc_name="Historical participant", sbu_id=sbu.id)
            db.add(alc)
            db.flush()
            db.add(
                YearlyActivityCompletion(
                    yearly_activity_id=definition.id,
                    yearly_activity_dcu_run_id=run_row.id,
                    alc_id=alc.id,
                    snapshot_sbu_id=sbu.id,
                    completed=value,
                )
            )
        db.flush()


def legacy_values(conn):
    return list(
        conn.scalars(
            sa.select(YearlyActivityCompletion.completed)
            .join(ALC, ALC.id == YearlyActivityCompletion.alc_id)
            .order_by(ALC.alc_code)
        )
    )


def test_additive_tables_match_models_and_downgrade_preserves_legacy_rows():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        Base.metadata.create_all(
            conn, tables=[t for t in Base.metadata.sorted_tables if t.name not in NEW]
        )
        conn.execute(sa.text("CREATE TABLE legacy_sentinel (value text)"))
        conn.execute(sa.text("INSERT INTO legacy_sentinel VALUES ('manual YES stays historical')"))
        before = set(sa.inspect(conn).get_table_names())
        seed_legacy(conn)
        run(conn, "upgrade")
        assert set(sa.inspect(conn).get_table_names()) == before | NEW
        for model in (YearlyActivitySubmission, YearlyActivityEvidence, YearlyActivityReview):
            table = model.__table__
            assert {c["name"]: c["nullable"] for c in sa.inspect(conn).get_columns(table.name)} == {
                c.name: c.nullable for c in table.columns
            }
            assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 0
        run(conn, "downgrade")
        assert set(sa.inspect(conn).get_table_names()) == before
        assert legacy_values(conn) == [True, False, None]
        assert (
            conn.execute(sa.text("SELECT value FROM legacy_sentinel"))
            .scalar()
            .startswith("manual YES")
        )
        run(conn, "upgrade")
        run(conn, "upgrade")
    engine.dispose()


@pytest.mark.skipif(
    os.environ.get("ALC_ISOLATED_TEST") != "1" or not os.environ.get("ALC_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL opt-in",
)
async def test_postgres_new_head_rollback_reupgrade_and_legacy_boolean_retention():
    from alembic.config import Config
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    url = os.environ["ALC_TEST_POSTGRES_URL"]
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and "test" in parsed.database
    schema = f"proof_migration_test_{uuid.uuid4().hex}"
    engine = create_async_engine(url, hide_parameters=True)
    config = Config()
    config.set_main_option("script_location", str(API / "alembic"))

    def migrate(conn, operation, target):
        config.attributes["connection"] = conn
        getattr(command, operation)(config, target)

    created = False
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with engine.begin() as conn:
            await conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.run_sync(migrate, "upgrade", "head")
            assert await conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20261107_0012"
            )
            await conn.execute(sa.text("CREATE TABLE legacy_sentinel (completed boolean)"))
            await conn.execute(
                sa.text("INSERT INTO legacy_sentinel VALUES (true), (false), (NULL)")
            )
            await conn.run_sync(migrate, "downgrade", "20261103_0009")
            await conn.run_sync(seed_legacy)
            assert await conn.scalar(sa.text("SELECT count(*) FROM legacy_sentinel")) == 3
            await conn.run_sync(migrate, "upgrade", "head")
            assert await conn.run_sync(legacy_values) == [True, False, None]
            assert (
                await conn.scalar(sa.text("SELECT count(*) FROM yearly_activity_submissions")) == 0
            )
            assert await conn.scalar(sa.text("SELECT count(*) FROM auth_sessions")) == 0
            assert (
                await conn.scalar(sa.text("SELECT count(*) FROM yearly_activity_completions")) == 3
            )
    finally:
        if created:
            async with engine.begin() as conn:
                await conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
