"""Read-only query-plan and request/transaction profiler for the isolated rehearsal DB."""

import asyncio
import json
import time
from contextvars import ContextVar
from pathlib import Path

from isolated_drill import LOCAL, check_database_identity, configure, refresh_sessions

ROOT = Path(__file__).resolve().parents[2]
OUT = LOCAL / "phase1b-query-profile.json"
active_request: ContextVar[dict | None] = ContextVar("active_request", default=None)
capture_queries: ContextVar[bool] = ContextVar("capture_queries", default=True)


def plan_nodes(node):
    output = []
    if node.get("Node Type", "").endswith("Scan"):
        output.append(
            {
                "node": node.get("Node Type"),
                "relation": node.get("Relation Name"),
                "index": node.get("Index Name"),
                "plan_rows": node.get("Plan Rows"),
                "actual_rows": node.get("Actual Rows"),
                "loops": node.get("Actual Loops"),
                "actual_ms": node.get("Actual Total Time"),
                "shared_hit_blocks": node.get("Shared Hit Blocks"),
                "shared_read_blocks": node.get("Shared Read Blocks"),
            }
        )
    for child in node.get("Plans", []):
        output.extend(plan_nodes(child))
    return output


async def main():
    configure()
    await check_database_identity()
    await refresh_sessions()

    from app.database import SessionLocal, engine
    from app.main import app
    from app.performance_diagnostics import snapshot
    from app.routes.leaderboard import _response
    from app.services.leaderboard import eligible_rows, leaderboard_payload, score_rows
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import event

    captured: list[dict] = []

    def before(connection, _cursor, statement, parameters, _context, _executemany):
        req = active_request.get()
        pending = {
            "started": time.perf_counter(),
            "request": req,
            "captured": False,
        }
        if capture_queries.get() and statement.lstrip().upper().startswith(("SELECT", "WITH")):
            item = {"statement": statement, "parameters": parameters, "request": req}
            captured.append(item)
            pending["captured_item"] = item
            pending["captured"] = True
        connection.info["profile_pending"] = pending

    def after(connection, _cursor, _statement, _parameters, _context, _executemany):
        pending = connection.info.pop("profile_pending", None)
        if pending is None:
            return
        elapsed = (time.perf_counter() - pending["started"]) * 1000
        req = pending["request"]
        if req is not None:
            req["sql_ms"] += elapsed
            req["sql_count"] += 1
        if pending["captured"]:
            pending["captured_item"]["execute_ms"] = round(elapsed, 3)

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(engine.sync_engine, "after_cursor_execute", after)

    accounts = json.loads((LOCAL / "master-accounts.json").read_text(encoding="utf-8"))
    by_role = {account["role"]: account for account in accounts}
    dcu = by_role["DCU"]
    alc = next(account for account in accounts if account["role"] == "ALC" and account.get("run_id"))
    targets = [
        ("admin_dashboard", by_role["ADMIN"], "/api/admin/dashboard"),
        ("portal_dashboard", dcu, "/api/portal/dashboard"),
        (
            "yearly_definitions",
            dcu,
            "/api/yearly-activities/definitions?year=2026&page=1&page_size=25",
        ),
        (
            "yearly_submissions",
            alc,
            f"/api/yearly-activities/submissions?year=2026&run_id={alc['run_id']}&page=1&page_size=25",
        ),
        ("regional_leaderboard", dcu, "/api/leaderboard/region-scores?year=2026"),
    ]
    requests = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://isolated.test") as client:
        for name, account, path in targets:
            for sample_number in range(1, 4):
                request_metrics = {
                    "name": name,
                    "sample": sample_number,
                    "sql_ms": 0.0,
                    "sql_count": 0,
                }
                request_token = active_request.set(request_metrics)
                capture_token = capture_queries.set(sample_number == 1)
                started = time.perf_counter()
                response = await client.get(
                    path,
                    cookies={
                        "access_token": account["access_token"],
                        "csrf_token": account["csrf_token"],
                    },
                )
                request_metrics["response_ms"] = round(
                    (time.perf_counter() - started) * 1000, 3
                )
                request_metrics["status"] = response.status_code
                request_metrics["response_bytes"] = len(response.content)
                request_metrics["pool_after"] = snapshot(engine)
                requests.append(request_metrics)
                capture_queries.reset(capture_token)
                active_request.reset(request_token)

    plans = []
    for item in captured:
        statement = item["statement"].lstrip()
        if not statement.upper().startswith(("SELECT", "WITH")):
            continue
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                result = await connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
                    item["parameters"],
                )
                explain = result.scalar_one()
                if isinstance(explain, str):
                    explain = json.loads(explain)
                root = explain[0]["Plan"]
                plans.append(
                    {
                        "request": item.get("request", {}).get("name"),
                        "execute_ms": item.get("execute_ms"),
                        "planning_ms": explain[0].get("Planning Time"),
                        "execution_ms": explain[0].get("Execution Time"),
                        "root_actual_rows": root.get("Actual Rows"),
                        "root_loops": root.get("Actual Loops"),
                        "shared_hit_blocks": root.get("Shared Hit Blocks"),
                        "shared_read_blocks": root.get("Shared Read Blocks"),
                        "scans": plan_nodes(root),
                        "sql": statement,
                    }
                )
            finally:
                await transaction.rollback()

    async with SessionLocal() as db:
        started = time.perf_counter()
        ranking_rows = await eligible_rows(db, 2026)
        fetched_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        ranked = score_rows(ranking_rows)
        scoring_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        payload = leaderboard_payload(ranked, 2026)
        built_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        encoded = _response(payload).body
        encode_ms = (time.perf_counter() - started) * 1000
        leaderboard_stages = {
            "eligible_alcs": len(ranked),
            "query_and_row_build_ms": round(fetched_ms, 3),
            "exact_scoring_ms": round(scoring_ms, 3),
            "payload_build_ms": round(built_ms, 3),
            "json_encode_ms": round(encode_ms, 3),
            "response_bytes": len(encoded),
        }

    event.remove(engine.sync_engine, "before_cursor_execute", before)
    event.remove(engine.sync_engine, "after_cursor_execute", after)
    await engine.dispose()
    report = {
        "requests": requests,
        "plans": plans,
        "leaderboard_stages": leaderboard_stages,
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"profile_file": str(OUT), **report}))


if __name__ == "__main__":
    asyncio.run(main())
