"""Migration 0011 adds publication metadata without changing existing visibility."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

API_DIR = Path(__file__).resolve().parents[1]
PATH = API_DIR / "alembic" / "versions" / "20261107_0011_growth_challenge_visibility.py"


def migration():
    spec = importlib.util.spec_from_file_location("migration_0011", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_challenges_are_published_and_description_is_empty():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE growth_challenges (
                id VARCHAR(32) PRIMARY KEY, name VARCHAR(150) NOT NULL,
                start_date DATE NOT NULL, end_date DATE NOT NULL,
                prospects_target INTEGER NOT NULL, meetings_target INTEGER NOT NULL,
                pilots_target INTEGER NOT NULL, partnerships_target INTEGER NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )
        """))
        connection.execute(sa.text("""
            INSERT INTO growth_challenges VALUES (
                '00000000000000000000000000000001', 'Existing', '2026-01-01', '2026-01-31',
                40, 20, 10, 5, '2026-01-01', '2026-01-01'
            )
        """))
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
        row = connection.execute(sa.text(
            "SELECT name, description, is_published FROM growth_challenges"
        )).one()
        assert tuple(row) == ("Existing", "", 1)
    engine.dispose()


def test_fresh_schema_objects_make_upgrade_idempotent():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE growth_challenges (
                id VARCHAR(32) PRIMARY KEY, description TEXT NOT NULL DEFAULT '',
                is_published BOOLEAN NOT NULL DEFAULT 1
            )
        """))
        connection.execute(sa.text(
            "CREATE INDEX ix_growth_challenges_is_published "
            "ON growth_challenges (is_published)"
        ))
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
        inspector = sa.inspect(connection)
        assert [c["name"] for c in inspector.get_columns("growth_challenges")].count(
            "description"
        ) == 1
        assert [c["name"] for c in inspector.get_columns("growth_challenges")].count(
            "is_published"
        ) == 1
        assert [i["name"] for i in inspector.get_indexes("growth_challenges")].count(
            "ix_growth_challenges_is_published"
        ) == 1
    engine.dispose()
