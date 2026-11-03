"""Repeatable PostgreSQL aggregate plan/timing experiment with synthetic data only.

Creates a unique schema, never touches public application tables, then drops ONLY that
schema. Requires explicit isolation assertion and a loopback database named *test*.
This is a query microbenchmark, not a concurrent-user capacity test.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    Activity,
    Partner,
    YearlyActivityCompletion,
    YearlyActivityDCURun,
    YearlyActivityDefinition,
)
from app.services.leaderboard import region_scores, regional_metrics_query


async def main():
    url = os.environ.get("ALC_TEST_POSTGRES_URL", "")
    parsed = make_url(url)
    if (os.environ.get("ALC_ISOLATED_TEST") != "1"
            or parsed.host not in {"127.0.0.1", "localhost"}
            or "test" not in (parsed.database or "").lower()):
        raise SystemExit(
            "Requires ALC_ISOLATED_TEST=1 and loopback ALC_TEST_POSTGRES_URL named *test*")
    schema = f"yearly_measure_{uuid.uuid4().hex}"
    admin = create_async_engine(url, hide_parameters=True)
    engine = create_async_engine(url, hide_parameters=True,
                                 connect_args={"server_settings": {"search_path": schema}})
    now = datetime.now(timezone.utc)
    year = 2026
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            rcu_id = uuid.uuid4()
            await conn.execute(insert(RCU), [{"id": rcu_id, "code": "MEASURE_RCU",
                                              "name": "Synthetic RCU", "is_active": True}])
            dcus = [uuid.uuid4() for _ in range(4)]
            await conn.execute(insert(DCU), [
                {"id": key, "rcu_id": rcu_id, "code": f"MEASURE_DCU_{n}",
                 "name": f"Synthetic DCU {n}", "is_active": True} for n, key in enumerate(dcus)])
            sbus = [uuid.uuid4() for _ in range(20)]
            await conn.execute(insert(SBU), [
                {"id": key, "dcu_id": dcus[n % 4], "code": f"MEASURE_SBU_{n}",
                 "name": f"Synthetic SBU {n}", "is_active": True} for n, key in enumerate(sbus)])
            alcs = [uuid.uuid4() for _ in range(800)]
            await conn.execute(insert(ALC), [
                {"id": key, "sbu_id": sbus[n % 20], "alc_code": f"T{n:07d}",
                 "alc_name": f"Synthetic ALC {n}", "status": "ACTIVE"}
                for n, key in enumerate(alcs)])
            await conn.execute(insert(Activity), [
                {"id": uuid.uuid4(), "alc_id": key, "activity_number": f"MEASURE_{n}_{j}",
                 "activity_type": "Workshop", "ecosystem": "College",
                 "activity_date": date(year, 1, 1), "location": "Synthetic",
                 "learners_reached": n % 50, "leads_generated": n % 23,
                 "admissions_generated": n % 7, "description": "Synthetic",
                 "outcome": "Synthetic", "status": "VERIFIED"}
                for n, key in enumerate(alcs) for j in range(5)])
            await conn.execute(insert(Partner), [
                {"id": uuid.uuid4(), "alc_id": key, "partner_name": "Synthetic partner",
                 "partner_type": "College", "ecosystem": "College", "status": "ACTIVE"}
                for key in alcs for _ in range(2)])
            definitions = [uuid.uuid4() for _ in range(10)]
            await conn.execute(insert(YearlyActivityDefinition), [
                {"id": key, "rcu_id": rcu_id, "calendar_year": year,
                 "activity_code": f"EVENT-{n}", "name": f"Synthetic event {n}",
                 "introduced_by_dcu_id": dcus[0]} for n, key in enumerate(definitions)])
            runs = {(definition, dcu): uuid.uuid4() for definition in definitions for dcu in dcus}
            await conn.execute(insert(YearlyActivityDCURun), [
                {"id": key, "yearly_activity_id": definition, "dcu_id": dcu,
                 "status": "FINALIZED", "scheduled_start_date": date(year, 1, 1),
                 "scheduled_end_date": date(year, 1, 1), "finalized_at": now}
                for (definition, dcu), key in runs.items()])
            await conn.execute(insert(YearlyActivityCompletion), [
                {"id": uuid.uuid4(), "yearly_activity_id": definition,
                 "yearly_activity_dcu_run_id": runs[definition, dcus[n % 4]],
                 "alc_id": key, "snapshot_sbu_id": sbus[n % 20], "completed": n % 3 != 0}
                for definition in definitions for n, key in enumerate(alcs)])
            await conn.execute(text("ANALYZE"))
            sql = str(regional_metrics_query(year).compile(
                dialect=conn.dialect, compile_kwargs={"literal_binds": True}))
            plan = (await conn.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")))
            plan = plan.scalar_one()[0]
        maker = async_sessionmaker(engine, expire_on_commit=False)
        timings = []
        async with maker() as db:
            for _ in range(12):
                start = time.perf_counter()
                payload = await region_scores(db, year)
                timings.append((time.perf_counter() - start) * 1000)
                assert len(payload["items"]) == 800
                assert payload["eligible_yearly_activities"] == 10
        print(json.dumps({
            "experiment": "single-client aggregate query, NOT load capacity",
            "synthetic_alcs": 800, "activities": 4000, "partners": 1600,
            "annual_completions": 8000, "samples": len(timings),
            "cold_ms": timings[0], "warm_p50_ms": statistics.median(timings[2:]),
            "warm_max_ms": max(timings[2:]), "explain": plan,
        }, indent=2))
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            # schema is generated above from a UUID, never external or an existing schema.
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
