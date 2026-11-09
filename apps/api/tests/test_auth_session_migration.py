"""Upgrade/downgrade compatibility with existing users and legacy refresh rows."""
import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url

from app.database import Base
from app.models import AuthSession, RefreshToken, User

API_DIR = Path(__file__).resolve().parents[1]
REVISION = "20261103_0009"
spec = importlib.util.spec_from_file_location(
    "auth_session_migration", API_DIR / "alembic" / "versions" / f"{REVISION}_auth_sessions.py")
MIGRATION = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MIGRATION)


def run(connection, step):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(MIGRATION, step)()


@pytest.fixture
def legacy():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    refresh = sa.Table("refresh_tokens", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), index=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables
            if t.name not in {"auth_sessions", "refresh_tokens"}])
        refresh.create(conn)
        user_id = uuid.uuid4()
        conn.execute(sa.insert(User.__table__), {"id": user_id, "username": "legacy-user",
            "password_hash": "unchanged-test-hash", "role": "ADMIN"})
        conn.execute(refresh.insert(), {"id": uuid.uuid4(), "user_id": user_id,
            "token_hash": "a" * 64, "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1)})
    yield engine
    engine.dispose()


def test_upgrade_preserves_legacy_credentials_but_requires_new_session_link(legacy):
    with legacy.begin() as conn:
        before = set(sa.inspect(conn).get_table_names())
        run(conn, "upgrade")
        assert set(sa.inspect(conn).get_table_names()) == before | {"auth_sessions"}
        assert conn.execute(sa.text("SELECT password_hash FROM users")).scalar() == (
            "unchanged-test-hash")
        assert conn.execute(sa.text("SELECT token_hash, session_id FROM refresh_tokens")).one() == (
            "a" * 64, None)
        assert conn.execute(sa.select(sa.func.count()).select_from(AuthSession)).scalar() == 0
        inspector = sa.inspect(conn)
        assert {c["name"]: c["nullable"] for c in inspector.get_columns("auth_sessions")} == {
            c.name: c.nullable for c in AuthSession.__table__.columns}
        assert {i["name"] for i in inspector.get_indexes("auth_sessions")} == {
            i.name for i in AuthSession.__table__.indexes}
        assert "ix_refresh_tokens_session_id" in {
            i["name"] for i in inspector.get_indexes("refresh_tokens")}
        run(conn, "upgrade")  # current-metadata initial migration/fresh-install compatibility


def test_downgrade_reupgrade_preserves_users_and_old_refresh_rows(legacy):
    with legacy.begin() as conn:
        run(conn, "upgrade")
        run(conn, "downgrade")
        assert "auth_sessions" not in sa.inspect(conn).get_table_names()
        assert "session_id" not in {
            c["name"] for c in sa.inspect(conn).get_columns("refresh_tokens")}
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar() == 1
        assert conn.execute(sa.text("SELECT token_hash FROM refresh_tokens")).scalar() == "a" * 64
        run(conn, "upgrade")
        assert conn.execute(sa.text("SELECT session_id FROM refresh_tokens")).scalar() is None


def test_revision_graph_is_linear():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20261107_0012"]
    assert script.get_revision(REVISION).down_revision == "20261103_0008"


PG_URL = os.environ.get("ALC_TEST_POSTGRES_URL")
SAFE = bool(PG_URL and os.environ.get("ALC_ISOLATED_TEST") == "1"
            and make_url(PG_URL).host in {"127.0.0.1", "localhost"}
            and "test" in (make_url(PG_URL).database or "").lower())


@pytest.mark.skipif(not SAFE, reason="explicit isolated loopback PostgreSQL test required")
async def test_postgres_full_chain_and_legacy_upgrade_downgrade():
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    schema = f"auth_migration_test_{uuid.uuid4().hex}"
    engine = create_async_engine(PG_URL, hide_parameters=True)
    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))

    def migrate(connection, method, target):
        config.attributes["connection"] = connection
        getattr(command, method)(config, target)

    created = False
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with engine.begin() as conn:
            await conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.run_sync(migrate, "upgrade", REVISION)
            assert await conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == REVISION
            user_id = uuid.uuid4()
            await conn.execute(sa.insert(User.__table__), {"id": user_id,
                "username": "migration-admin", "password_hash": "unchanged", "role": "ADMIN"})
            await conn.execute(sa.insert(RefreshToken.__table__), {"user_id": user_id,
                "token_hash": "b" * 64,
                "expires_at": datetime.now(timezone.utc) + timedelta(days=1)})
            await conn.run_sync(migrate, "downgrade", "20261103_0008")
            assert await conn.scalar(sa.text("SELECT password_hash FROM users")) == "unchanged"
            assert await conn.scalar(sa.text("SELECT token_hash FROM refresh_tokens")) == "b" * 64
            assert await conn.scalar(sa.text("SELECT to_regclass('auth_sessions')")) is None
            await conn.run_sync(migrate, "upgrade", REVISION)
            assert await conn.scalar(sa.text("SELECT session_id FROM refresh_tokens")) is None
            fk = await conn.execute(sa.text(
                "SELECT confdeltype::text FROM pg_constraint WHERE conrelid = "
                "'refresh_tokens'::regclass AND conname = :name"), {"name": MIGRATION.FK})
            assert fk.scalar() == "c"  # cascade, no orphaned refresh state
            assert await conn.scalar(sa.text(
                "SELECT count(*) FROM yearly_activity_definitions")) == 0
    finally:
        if created:
            async with engine.begin() as conn:
                await conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
