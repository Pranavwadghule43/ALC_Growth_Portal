# ALC Growth Portal backend performance audit — 2026-11-05

## Scope and evidence

Reviewed the dashboard, yearly activity, regional leaderboard, database/session, rollup,
scope, and frontend leaderboard request paths, plus the migrations and relevant tests. The
current isolated test database was confirmed read-only as `alc_master_test` on host port
55489 at Alembic revision `20261104_0010`. The original database on port 55432 was not used.

The supplied 25 VU measurements are retained below as the user's baseline. Post-change
measurements use the repository's guarded k6 harness, refreshed synthetic sessions, and
the same isolated API/database target. The precise workload used to produce the supplied
baseline was not recorded, so the two sets are shown side by side without claiming a
causal before/after improvement.

## Prioritized findings and actions

| Priority / status | File and function | Problem and evidence | Root cause / recommendation | Risk and expected impact | Regression coverage |
|---|---|---|---|---|---|
| P1 confirmed opportunity, optimized | `app/services/rollups.py:sbu_rollup` | Three grouped rollup queries separately read ALCs, activity metrics, and partners; route-level query-count test and SQL construction confirmed three round trips. | Build three independently aggregated CTEs and join their one-row-per-SBU results. This avoids both repeated round trips and join fanout. | Low; one fewer round trip per SBU dashboard, exact zero/default and scope behavior retained. | `test_dcu_portal.py`; dashboard response tests. |
| P1 confirmed opportunity, optimized | `app/routes/admin.py:dashboard` | Five statements before; trend, activity-type, and metric/partner totals were separate. No per-statement timings were captured. | Combine independent metric aggregates in scalar/grouped subqueries; leave trend and category queries separate. Three statements after. | Low; two fewer round trips. | `test_admin_dashboard_performance.py` asserts response and three-statement shape. |
| P1 confirmed opportunity, optimized | `app/services/yearly_activities.py:list_definitions`, `annual_summary` | Definition listing had a separate introducer lookup; run listing separately aggregated all-run totals/status; annual summary performed three more statements. Up to eight endpoint statements by path. | Join introducer data to the page query, compute run status counts in a single aggregate CTE while preserving role-scoped run rows, and combine annual summary scalars/counts. Four statements in the exercised definitions path. | Medium; authorization-sensitive role scoping and aggregate semantics must remain intact. | `test_yearly_activities.py`, yearly workflow tests. |
| P2 confirmed opportunity, optimized | `app/services/yearly_submissions.py:list_submissions` | Separate total count duplicated the grouped base-status scan. | Derive total from mutually exclusive status buckets (one submission per participation); keep page and annual status queries as before. | Medium; correct only while uniqueness and bucket coverage invariants hold. | `test_yearly_evidence_workflow.py` verifies totals against buckets and page results. |
| P2 confirmed duplicate request, optimized | `apps/web/src/components/leaderboard.tsx` | Top 10 and All Scores requested the same full regional ranking separately. Shared TanStack Query key now deduplicates it; Top 10 derives its first ten rows locally. | Reuse the same query keyed by year; retain the compatible `/region-top10` API. | Low; removes duplicate browser request/ranking transfer per shared screen. | `leaderboard.test.tsx`. |
| P2 confirmed serialization cost opportunity, optimized | `app/routes/leaderboard.py:_response` | Full regional ranking already has a compact response schema; generic recursive response encoding adds work. No isolated serialization microbenchmark was taken. | Convert UUID and datetime values at the HTTP edge and return `JSONResponse`; service Python values and API fields remain the same. | Low; expected lower response-encoding overhead, not separately quantified. | leaderboard API/scoring tests and k6 endpoint measurements. |
| P3 suspected, unchanged | `app/services/leaderboard.py` and leaderboard SQL | Full ranking still must compute all eligible ALC scores. There is no evidence that an index or Redis cache would improve it; the complete ranking is required by the UI. | Do not cache authorization-sensitive data or alter exact `Fraction` scoring/ties. Revisit only with plans and query timing at representative data volume. | Avoids cache invalidation, stale ranking, and arithmetic drift. | `test_yearly_leaderboard.py`, frontend leaderboard tests. |
| P3 unconfirmed | `app/models.py`, migrations, filter/order paths | No new missing index was confirmed. No `EXPLAIN (ANALYZE, BUFFERS)` plan comparison was captured for the endpoint SQL. | No speculative index migration. Collect plans on the synthetic PostgreSQL fixture before adding indexes. | No write amplification or migration lock risk from speculative indexes. | Existing migration tests; add plan-backed migration test only if a missing index is proven. |
| P3 suspected, not confirmed | `get_db`, `stream_csv`, evidence/storage, and API serialization | At 50 VUs PostgreSQL samples briefly showed up to 21 `idle in transaction` states; a later read-only check found 10 idle sessions with no open transaction. Transaction ages were not recorded, so a long-lived transaction is unproven. No per-endpoint response-size or Python profile was collected. S3/boto3 calls and stream reads use `asyncio.to_thread`; password hashing uses the thread pool. | Keep current async offloading. Trace transaction age and slow response serialization if residual latency remains. | No behavior change; avoids unsupported bottleneck claims. | API response, storage, and auth tests. |

## Implemented changes

- Reduced round trips for SBU and Admin dashboards and yearly definitions/submissions.
- Preserved server-side scope, active/eligible populations, workflow state, ranking, exact
  leaderboard scoring, and response field names.
- Reused the full regional ranking request between the two leaderboard widgets and reduced
  leaderboard response encoding work.
- Added stage-by-stage stop behavior to the isolated benchmark harness. It refreshes
  synthetic sessions per stage and records endpoint Trends for admin/portal dashboards,
  yearly definitions/submissions, and regional leaderboard. The harness now enforces the
  latency target per endpoint and requires passing prior-stage evidence before a higher
  stage can start.
- Corrected load resource sampling to use the actual isolated PostgreSQL, Redis, and S3
  container names and sample the isolated API process CPU time and memory.
- Added no SQL indexes and no Redis response cache because neither had plan/invalidation
  evidence to support a safe change.

## Verification

- Regression suites: **92 passed, 2 skipped** (admin dashboard, DCU portal, yearly
  activities/evidence, leaderboard scoring, auth/session, and migration suites).
- Ruff passed for the changed performance and load-harness Python files. A repository-wide
  Ruff scan still reports four pre-existing lint errors in `apps/api/alembic/env.py` and
  `apps/api/alembic/versions/20260916_0001_initial.py`.
- `compileall` and `git diff --check` passed.
- The 25 VU API run exercised refreshed sessions across all roles; it recorded 0% HTTP
  failures and 100% successful checks.
- The 50 VU stage followed a 25 VU run that passed aggregate thresholds; endpoint Trends
  later showed yearly definitions at 510 ms p95, so the strict per-endpoint ordinary target
  was not met at 25 VUs. The 50 VU stage is retained as exploratory evidence; 100 and 200
  VU stages were withheld after latency failures. The runner now requires endpoint-level
  threshold pass evidence before proceeding.
- No database reset, production data mutation, deployment, or access to port 55432 occurred.

## Benchmark results

The API targets are ordinary p95 < 500 ms and dashboard p95 < 1,000 ms; HTTP failure rate
< 1% and successful checks > 99%.

| VUs / capture | Endpoint | Supplied baseline p95 | p50 | p95 | p99 | RPS (all requests) | Result |
|---|---|---:|---:|---:|---:|---:|---|
| 25 / B | Admin dashboard | 1,158 ms | 125 ms | 431 ms | 458 ms | 13.23 | pass |
| 25 / B | Portal dashboard | 927 ms | 140 ms | 621 ms | 859 ms | 13.23 | pass |
| 25 / B | Yearly definitions | 768 ms | 119 ms | 510 ms | 777 ms | 13.23 | pass |
| 25 / B | Yearly submissions | 549 ms | 84 ms | 380 ms | 607 ms | 13.23 | pass |
| 25 / B | Regional leaderboard | 546 ms | 186 ms | 490 ms | 723 ms | 13.23 | pass |
| 25 / C | Admin dashboard | 1,158 ms | 122 ms | 451 ms | 533 ms | 11.96 | ordinary threshold failed |
| 25 / C | Portal dashboard | 927 ms | 247 ms | 742 ms | 1,210 ms | 11.96 | ordinary threshold failed |
| 25 / C | Yearly definitions | 768 ms | 225 ms | 677 ms | 903 ms | 11.96 | ordinary threshold failed |
| 25 / C | Yearly submissions | 549 ms | 129 ms | 652 ms | 784 ms | 11.96 | ordinary threshold failed |
| 25 / C | Regional leaderboard | 546 ms | 295 ms | 653 ms | 765 ms | 11.96 | ordinary threshold failed |
| 50 | Admin dashboard | 1,158 ms | 182 ms | 727 ms | 754 ms | 21.05 | endpoint passed; dashboard aggregate failed |
| 50 | Portal dashboard | 927 ms | 436 ms | 1,780 ms | 2,580 ms | 21.05 | fail |
| 50 | Yearly definitions | 768 ms | 367 ms | 1,530 ms | 2,090 ms | 21.05 | fail |
| 50 | Yearly submissions | 549 ms | 307 ms | 1,420 ms | 1,800 ms | 21.05 | fail |
| 50 | Regional leaderboard | 546 ms | 378 ms | 1,250 ms | 1,530 ms | 21.05 | fail |

At 25 VUs, capture B's console report showed ordinary aggregate p95 458 ms and dashboard
aggregate p95 619 ms, with all checks successful and no failed HTTP requests. A later
25 VU resource capture still had 100% successful checks and 0% request failures, but
ordinary aggregate p95 rose to 649 ms (dashboard p95 739 ms), so it missed the ordinary
latency target. Treat 25 VU latency as variable around the threshold rather than a stable
pass. Capture B's yearly-definitions p95 was also 510 ms, just over the 500 ms target when
that target is applied per endpoint. The 50 VU run had been allowed by the earlier aggregate
threshold; with the endpoint-specific gating now added, such a 25 VU capture cannot advance.
At 50 VUs ordinary aggregate p95 was 1.38 s and dashboard aggregate p95 was 1.77 s; k6 exited with threshold
failure and the runner stopped. The k6 summary-export JSON in this image incorrectly marked
threshold booleans false while the console report showed them passing at 25 VUs; preserve the
human-readable console logs as the acceptance record. Detailed endpoint percentiles are in
`.local/load/stage-25.txt` and `.local/load/stage-50.txt` (ignored local test artifacts).

Resource samples are stored in `.local/load/stage-25-resources.json` and
`.local/load/stage-50-resources.json`. The 25 VU resource capture sampled at most 14
PostgreSQL backends including one monitor connection (2 active; 8 idle in transaction at a
sample), API host-process working set up to 151 MiB, and API CPU-time delta of 46.5 seconds
over 93.6 seconds (about 50% of one logical CPU). Container peaks were PostgreSQL 12.82% CPU
and 146.7 MiB, Redis 0.72% and 5.3 MiB, S3 0.74% and 129 MiB. Redis ping sampling used an
outdated container name and was false for every sample; Docker CPU/memory samples themselves
were collected. The 50 VU resource file came from the earlier monitor version and reports up
to 31 PostgreSQL backends (including its monitor), with up to 21 sampled idle-in-transaction
sessions. A read-only post-run query later showed only 10 idle sessions and no transaction
ages, so persistence/long duration was not established. Do not treat backend states as
SQLAlchemy checkout telemetry. No resource sample is representative of production capacity.

## Remaining bottlenecks and limits

- At 50 VUs, portal dashboard and yearly/leaderboard reads miss the ordinary/dashboard p95
  targets even though authentication/checks succeed; PostgreSQL backend count peaked at 31
  including the monitor at that stage. The next step is
  query-level timing and `EXPLAIN (ANALYZE, BUFFERS)` for these isolated reads at a larger
  synthetic dataset, then targeted optimization.
- 100 and 200 VU capacity is not established; those stages were correctly withheld after
  50 VUs failed.
- The supplied baseline and this k6 run do not have a proven identical request mix and data
  state, so no causal before/after improvement percentage is claimed.
- No Redis cache or index was introduced. Capacity under production-sized data, Python
  process saturation, and long transaction durations remain unproven.
