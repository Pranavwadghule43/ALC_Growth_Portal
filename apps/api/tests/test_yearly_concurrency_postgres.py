"""Real PostgreSQL lock experiments, exclusively in a newly generated private schema."""
import asyncio
import os
import uuid
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.database import Base
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    AuditLog,
    User,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
)
from app.services import yearly_activities as service
from app.yearly_schemas import CompletionIn

PG_URL = os.environ.get("ALC_TEST_POSTGRES_URL")
SAFE = bool(PG_URL and os.environ.get("ALC_ISOLATED_TEST") == "1"
            and make_url(PG_URL).host in {"127.0.0.1", "localhost"}
            and "test" in (make_url(PG_URL).database or "").lower())
pytestmark = pytest.mark.skipif(
    not SAFE, reason="requires explicit isolated loopback test database")


@pytest_asyncio.fixture
async def postgres_world():
    schema = f"yearly_lock_test_{uuid.uuid4().hex}"
    admin = create_async_engine(PG_URL, hide_parameters=True)
    engine = create_async_engine(PG_URL, hide_parameters=True,
                                 connect_args={"server_settings": {"search_path": schema}})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            rcu = RCU(code="LOCK_RCU", name="Synthetic lock RCU")
            db.add(rcu)
            await db.flush()
            dcu = DCU(code="LOCK_DCU", name="Synthetic lock DCU", rcu_id=rcu.id)
            db.add(dcu)
            await db.flush()
            sbu = SBU(code="LOCK_SBU", name="Synthetic lock SBU", dcu_id=dcu.id)
            actor = User(username="lock-actor", role="DCU", dcu_id=dcu.id,
                         password_hash="unused-test-only-no-authentication")
            db.add_all([sbu, actor])
            await db.flush()
            alc = ALC(alc_code="LOCK0001", alc_name="Synthetic lock ALC", sbu_id=sbu.id)
            definition = YearlyActivityDefinition(
                rcu_id=rcu.id, calendar_year=2026, activity_code="LOCK-ACTIVITY",
                name="Lock experiment", introduced_by_dcu_id=dcu.id)
            db.add_all([alc, definition])
            await db.flush()
            run = YearlyActivityDCURun(
                yearly_activity_id=definition.id, dcu_id=dcu.id, status="MARKING",
                scheduled_start_date=date(2026, 1, 1), scheduled_end_date=date(2026, 1, 1))
            db.add(run)
            await db.flush()
            db.add(YearlyActivityCompletion(
                yearly_activity_id=definition.id, yearly_activity_dcu_run_id=run.id,
                alc_id=alc.id, snapshot_sbu_id=sbu.id, completed=True))
            await db.commit()
            identifiers = actor.id, run.id, alc.id, definition.id
        yield maker, identifiers
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            # Only this newly generated schema is removed, never public or a supplied name.
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def request():
    return Request({"type": "http", "method": "PATCH", "path": "/synthetic-lock-test",
                    "headers": [], "client": ("127.0.0.1", 0)})


async def test_waiting_mark_is_rejected_after_finalization_commits(postgres_world):
    maker, (actor_id, run_id, alc_id, definition_id) = postgres_world
    async with maker() as finalizer, maker() as marker:
        await finalizer.scalar(select(YearlyActivityDCURun).where(
            YearlyActivityDCURun.id == run_id).with_for_update())
        actor = await marker.get(User, actor_id)

        async def mark():
            try:
                await service.mark_completion(marker, actor, run_id, alc_id,
                                              CompletionIn(completed=False), request())
            except HTTPException as exc:
                await marker.rollback()
                return exc.status_code
            return 200

        waiting = asyncio.create_task(mark())
        try:
            await asyncio.sleep(0.05)
            assert not waiting.done(), "mark must wait for the locked run"
            assert (await finalizer.scalars(service.eligible_activity_ids(2026))).all() == []
            final_actor = await finalizer.get(User, actor_id)
            await service.finalize_run(finalizer, final_actor, run_id, request())
            assert await asyncio.wait_for(waiting, timeout=5) == 409
            assert (await finalizer.scalars(service.eligible_activity_ids(2026))).all() == [
                definition_id]
            assert await finalizer.scalar(select(YearlyActivityCompletion.completed)) is True
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)


async def test_parallel_marks_keep_one_row_and_two_attributed_audits(postgres_world):
    maker, (actor_id, run_id, alc_id, _) = postgres_world

    async def mark(value):
        async with maker() as db:
            actor = await db.get(User, actor_id)
            return await service.mark_completion(db, actor, run_id, alc_id,
                                                 CompletionIn(completed=value), request())

    results = await asyncio.gather(mark(False), mark(True))
    assert {row["completed"] for row in results} == {False, True}
    async with maker() as db:
        assert await db.scalar(select(func.count(YearlyActivityCompletion.id))) == 1
        assert await db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "yearly_activity_completion_marked")) == 2
        audits = (await db.scalars(select(AuditLog).order_by(AuditLog.created_at))).all()
        value = await db.scalar(select(YearlyActivityCompletion.completed))
        assert value == audits[-1].audit_metadata["completed"]
        assert all(audit.actor_user_id == actor_id for audit in audits)
