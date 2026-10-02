"""Growth Challenge: migration ``20261002_0007`` (creates ``growth_challenges``).

* SQLite (always run): the revision's own ``upgrade`` / ``downgrade`` on a database that
  looks like production before the change (no ``growth_challenges`` table, existing
  ``challenge_progress`` data): upgrade, constraints, data preserved, downgrade, re-upgrade,
  and idempotency on a brand-new database (the initial revision builds every model table).
* PostgreSQL (opt-in, same switch as ``test_activity_indexes``): set
  ``ALC_TEST_POSTGRES_URL`` to a THROWAWAY database whose name contains "test" (its ``public``
  schema is dropped and rebuilt). Runs the real Alembic chain: upgrade, downgrade,
  re-upgrade, the legacy path, and the no-overlap exclusion constraint.
"""
import asyncio
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
from app.models import GrowthChallenge

API_DIR = Path(__file__).resolve().parents[1]
REVISION = "20261002_0007"
PREVIOUS = "20260930_0006"
TABLE = "growth_challenges"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)


def load_migration():
    path = API_DIR / "alembic" / "versions" / f"{REVISION}_growth_challenges.py"
    spec = importlib.util.spec_from_file_location("migration_0007", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = load_migration()


def run(connection, step: str) -> None:
    """Run the revision's ``upgrade`` / ``downgrade`` on ``connection``."""
    with Operations.context(MigrationContext.configure(connection)):
        getattr(MIGRATION, step)()


def tables(connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def challenge_row(start, end, **values) -> dict:
    return {
        "id": uuid.uuid4(), "name": "Growth Challenge", "start_date": start, "end_date": end,
        "prospects_target": 40, "meetings_target": 20, "pilots_target": 10,
        "partnerships_target": 5, "created_at": NOW, "updated_at": NOW, **values,
    }


@pytest.fixture
def legacy():
    """A database as it is before this revision: every table except ``growth_challenges``,
    with one ALC and its existing ``challenge_progress`` row."""
    engine = sa.create_engine("sqlite://")
    before = [t for t in Base.metadata.sorted_tables if t.name != TABLE]
    with engine.begin() as connection:
        Base.metadata.create_all(connection, tables=before)
        alc_id = uuid.uuid4()
        connection.execute(sa.insert(Base.metadata.tables["alcs"]).values(
            id=alc_id, alc_code="00010001", alc_name="Centre A", status="ACTIVE",
            created_at=NOW, updated_at=NOW))
        connection.execute(sa.insert(Base.metadata.tables["challenge_progress"]).values(
            id=uuid.uuid4(), alc_id=alc_id, challenge_period_start=date(2026, 9, 1),
            challenge_period_end=date(2026, 10, 15), prospects_target=55, meetings_target=20,
            pilots_target=10, partnerships_target=5, created_at=NOW, updated_at=NOW))
    yield engine
    engine.dispose()


def existing_data(connection) -> list[tuple]:
    progress = Base.metadata.tables["challenge_progress"]
    alcs = Base.metadata.tables["alcs"]
    return [
        tuple(r) for r in connection.execute(
            sa.select(alcs.c.alc_code, progress.c.challenge_period_start,
                      progress.c.challenge_period_end, progress.c.prospects_target)
            .join(alcs, alcs.c.id == progress.c.alc_id))
    ]


EXISTING = [("00010001", date(2026, 9, 1), date(2026, 10, 15), 55)]


# --------------------------------------------------------------------------- #
# Revision chain
# --------------------------------------------------------------------------- #
def test_revision_is_the_single_head_after_0006():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert (MIGRATION.revision, MIGRATION.down_revision) == (REVISION, PREVIOUS)


# --------------------------------------------------------------------------- #
# SQLite: upgrade / downgrade
# --------------------------------------------------------------------------- #
def test_upgrade_creates_the_table_and_preserves_existing_data(legacy):
    with legacy.begin() as connection:
        assert TABLE not in tables(connection)
        before = tables(connection)
        run(connection, "upgrade")
        assert tables(connection) == before | {TABLE}  # nothing else added or removed
        assert existing_data(connection) == EXISTING  # challenge_progress untouched
        table = Base.metadata.tables[TABLE]
        assert connection.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 0


def test_upgraded_table_matches_the_model(legacy):
    with legacy.begin() as connection:
        run(connection, "upgrade")
        inspector = sa.inspect(connection)
        migrated = {c["name"]: c["nullable"] for c in inspector.get_columns(TABLE)}
        model = {c.name: c.nullable for c in GrowthChallenge.__table__.columns}
        assert migrated == model
        assert set(migrated) == {
            "id", "name", "start_date", "end_date", "prospects_target", "meetings_target",
            "pilots_target", "partnerships_target", "created_by", "updated_by", "created_at",
            "updated_at",
        }
        assert {i["name"] for i in inspector.get_indexes(TABLE)} == {
            i.name for i in GrowthChallenge.__table__.indexes}
        assert {c["name"] for c in inspector.get_check_constraints(TABLE)} == {
            "ck_growth_challenges_period_order", "ck_growth_challenges_targets_non_negative"}
        foreign_keys = {
            fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys(TABLE)}
        assert set(foreign_keys) == {"created_by", "updated_by"}
        for fk in foreign_keys.values():
            assert fk["referred_table"] == "users"
            assert fk["options"].get("ondelete") == "SET NULL"


def test_upgraded_table_enforces_its_constraints(legacy):
    table = Base.metadata.tables[TABLE]
    with legacy.begin() as connection:
        run(connection, "upgrade")
        connection.execute(sa.insert(table).values(
            challenge_row(date(2026, 10, 1), date(2026, 10, 1))))  # one-day period is valid
    for bad in (
        challenge_row(date(2026, 11, 10), date(2026, 11, 9)),  # end before start
        challenge_row(date(2026, 12, 1), date(2026, 12, 30), pilots_target=-1),
    ):
        with pytest.raises(IntegrityError), legacy.begin() as connection:
            connection.execute(sa.insert(table).values(bad))
    with legacy.begin() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 1


def test_downgrade_removes_only_the_new_table(legacy):
    table = Base.metadata.tables[TABLE]
    with legacy.begin() as connection:
        before = tables(connection)
        run(connection, "upgrade")
        connection.execute(sa.insert(table).values(
            challenge_row(date(2026, 10, 1), date(2026, 10, 30))))
        run(connection, "downgrade")
        assert tables(connection) == before
        assert existing_data(connection) == EXISTING
        run(connection, "downgrade")  # already gone: no error
        assert tables(connection) == before


def test_upgrade_downgrade_reupgrade_cycle(legacy):
    table = Base.metadata.tables[TABLE]
    with legacy.begin() as connection:
        run(connection, "upgrade")
        run(connection, "downgrade")
        run(connection, "upgrade")
        assert TABLE in tables(connection)
        connection.execute(sa.insert(table).values(
            challenge_row(date(2026, 10, 1), date(2026, 11, 14))))
        assert connection.execute(sa.select(table.c.end_date)).scalar() == date(2026, 11, 14)
        assert existing_data(connection) == EXISTING


def test_upgrade_is_idempotent_on_a_database_built_from_the_current_models():
    """The initial revision creates every model table, so the table may already exist."""
    engine = sa.create_engine("sqlite://")
    table = Base.metadata.tables[TABLE]
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        connection.execute(sa.insert(table).values(
            challenge_row(date(2026, 10, 1), date(2026, 10, 30))))
        run(connection, "upgrade")
        run(connection, "upgrade")
        assert connection.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 1
    engine.dispose()


# --------------------------------------------------------------------------- #
# PostgreSQL: real Alembic chain (opt-in)
# --------------------------------------------------------------------------- #
PG_URL = os.environ.get("ALC_TEST_POSTGRES_URL")
requires_pg = pytest.mark.skipif(
    not PG_URL or "test" not in (make_url(PG_URL).database or ""),
    reason="set ALC_TEST_POSTGRES_URL to a throwaway PostgreSQL database named *test*",
)
NO_OVERLAP = "ex_growth_challenges_no_overlap"


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
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(PG_URL)
    try:
        async with engine.begin() as conn:
            for statement in statements:
                result = await conn.execute(sa.text(statement), params)
            return result.all() if result.returns_rows else None
    finally:
        await engine.dispose()


def pg(*statements: str, **params):
    return asyncio.run(_pg(*statements, **params))


def pg_state() -> tuple[str, bool, bool]:
    """(alembic version, table exists, exclusion constraint exists)."""
    version = pg("SELECT version_num FROM alembic_version")[0][0]
    table = pg("SELECT to_regclass('public.growth_challenges') IS NOT NULL")[0][0]
    constraint = bool(pg("SELECT 1 FROM pg_constraint WHERE conname = :n", n=NO_OVERLAP))
    return version, table, constraint


INSERT = (
    "INSERT INTO growth_challenges (id, name, start_date, end_date, prospects_target, "
    "meetings_target, pilots_target, partnerships_target, created_at, updated_at) VALUES "
    "(gen_random_uuid(), :name, :start, :end, 40, 20, 10, 5, now(), now())"
)


@pytest.fixture
def pg_schema():
    """Fresh schema built by the real migration chain, with existing per-ALC data."""
    pg("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
    _alembic("upgrade", "head")
    pg("INSERT INTO alcs (id, alc_code, alc_name, status, created_at, updated_at) VALUES "
       "('11111111-1111-1111-1111-111111111111', '00010001', 'Centre A', 'ACTIVE', now(), now())",
       "INSERT INTO challenge_progress (id, alc_id, challenge_period_start, "
       "challenge_period_end, prospects_target, meetings_target, pilots_target, "
       "partnerships_target, created_at, updated_at) VALUES (gen_random_uuid(), "
       "'11111111-1111-1111-1111-111111111111', '2026-09-01', '2026-10-15', 55, 20, 10, 5, "
       "now(), now())")
    yield
    pg("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")


def pg_existing() -> list[tuple]:
    return [tuple(r) for r in pg(
        "SELECT a.alc_code, p.challenge_period_start, p.challenge_period_end, p.prospects_target "
        "FROM challenge_progress p JOIN alcs a ON a.id = p.alc_id")]


@requires_pg
def test_pg_upgrade_downgrade_reupgrade(pg_schema):
    assert pg_state() == (REVISION, True, True)
    assert pg_existing() == EXISTING
    pg(INSERT, name="Configured", start=date(2026, 10, 1), end=date(2026, 10, 30))

    _alembic("downgrade", "-1")
    assert pg_state() == (PREVIOUS, False, False)
    assert pg_existing() == EXISTING  # per-ALC data survives the downgrade

    _alembic("upgrade", "head")
    assert pg_state() == (REVISION, True, True)
    assert pg_existing() == EXISTING
    assert pg("SELECT count(*) FROM growth_challenges")[0][0] == 0


@requires_pg
def test_pg_upgrade_from_a_database_without_the_table(pg_schema):
    """The production path: at 20260930_0006 with no ``growth_challenges`` table."""
    _alembic("stamp", PREVIOUS)
    pg("DROP TABLE growth_challenges")
    _alembic("upgrade", "head")
    assert pg_state() == (REVISION, True, True)
    assert pg_existing() == EXISTING
    columns = {r[0]: r[1] for r in pg(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'growth_challenges'")}
    assert columns == {
        c.name: "YES" if c.nullable else "NO" for c in GrowthChallenge.__table__.columns}
    constraints = {r[0] for r in pg(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'growth_challenges'::regclass")}
    assert constraints == {
        "pk_growth_challenges", "ck_growth_challenges_period_order",
        "ck_growth_challenges_targets_non_negative", "fk_growth_challenges_created_by_users",
        "fk_growth_challenges_updated_by_users", NO_OVERLAP,
    }


@requires_pg
def test_pg_upgrade_adds_a_missing_exclusion_constraint_only(pg_schema):
    """A database built from the models has the table but not the PostgreSQL-only constraint."""
    _alembic("stamp", PREVIOUS)
    pg(f"ALTER TABLE growth_challenges DROP CONSTRAINT {NO_OVERLAP}")
    pg(INSERT, name="Kept", start=date(2026, 10, 1), end=date(2026, 10, 30))
    _alembic("upgrade", "head")
    assert pg_state() == (REVISION, True, True)
    assert pg("SELECT name FROM growth_challenges")[0][0] == "Kept"  # rows preserved


@requires_pg
def test_pg_constraints_reject_bad_and_overlapping_periods(pg_schema):
    pg(INSERT, name="First", start=date(2026, 10, 1), end=date(2026, 10, 30))
    pg(INSERT, name="Adjacent", start=date(2026, 10, 31), end=date(2026, 11, 14))  # next day: ok
    for start, end in (
        (date(2026, 10, 30), date(2026, 11, 20)),  # shares the last day of "First"
        (date(2026, 9, 1), date(2026, 10, 1)),  # shares the first day
        (date(2026, 10, 10), date(2026, 10, 12)),  # inside
    ):
        with pytest.raises(IntegrityError, match=NO_OVERLAP):
            pg(INSERT, name="Clash", start=start, end=end)
    with pytest.raises(IntegrityError, match="ck_growth_challenges_period_order"):
        pg(INSERT, name="Backwards", start=date(2027, 1, 10), end=date(2027, 1, 9))
    assert pg("SELECT count(*) FROM growth_challenges")[0][0] == 2
