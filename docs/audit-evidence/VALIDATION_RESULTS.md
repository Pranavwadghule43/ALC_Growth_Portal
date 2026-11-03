# Validation evidence — 2026-11-03

All application tests use synthetic fixtures. No test ran against the configured development/production database. Counts below overlap; do not add them together as independent test coverage.

| Check | Actual result | Scope / limitation |
| --- | --- | --- |
| Complete backend `.venv/Scripts/python.exe -m pytest -q` |869 passed,60 skipped in572.48s | SQLite/mocks; existing optional platform/infrastructure skips, no failures; raw output in backend-pytest.txt |
| Final affected yearly/import/hierarchy regression |60 passed in54.82s | Latest import guard and per-RCU denominator changes; includes test_yearly_activities, test_alc_import, test_master_import, test_hierarchy_reassignment |
| Scoring + annual leaderboard regression |49 passed in46.45s | Full precision/ties/metrics/year/barrier/one statement; no production DB |
| Final combined migration/concurrency tests |5 passed in4.91s |2 SQLite migration tests +1 actual PostgreSQL full-chain/constraints +2 PostgreSQL concurrency tests; unique schemas only |
| Validation + revocation-evidence tests |21 passed in6.83s | Two passing tests reproduce the unresolved copied-JWT weakness; not security acceptance |
| Actual Redis atomic limiting/expiry integration |1 passed in0.27s | Disposable verified loopback Redis7;50 competing operations, TTL repair/refund/clear; service removed afterward |
| Frontend `npm run test` |136 passed,10 files,21.01s | jsdom/client/component tests with mocked API, not browser E2E |
| Frontend `npm run lint` | Passed, no diagnostics | Latest frontend source |
| Frontend `VITE_API_URL=/api npm run build` | Passed | TypeScript project checks + Vite production bundle; plain build rejected inherited unsafe development URL as designed |
| Ruff `app tests scripts/measure_yearly_queries.py` | Passed | All application/tests plus new performance script |
| Full Ruff `app scripts tests` | Failed:11 pre-existing findings | Unchanged scripts/create_admin.py, create_alc_users.py import order;9 long lines in demo_smoke.py; errors not suppressed/fixed outside scope |
| `node --check tests/load/portal.k6.js` | Passed | Syntax only; k6 not installed or executed |
| npm full advisory scan |21 affected packages:16 moderate,3 high,2 critical | Full raw JSON included; mostly dev tooling |
| npm production-only advisory scan |1 high: Zod | Requires tested major-version migration/triage |
| Python pip-audit on installed pinned packages |16 reported advisory entries across5 packages | Some duplicate IDs; installed runtime/dev environment, not16 reproduced application exploits |
| Synthetic PostgreSQL aggregate experiment |800 ALCs,12 serial samples; SQL22.686ms; warm scoring median68.64ms/max128.95ms | Single-client microbenchmark; NOT requests/sec or simultaneous-user capacity |
| Diff/patch integrity | `git diff --check` and real-index `git diff --cached --check` passed | Final export also checks reverse application against working files and forward application against baseline in a temporary index; details in delivered patch metadata |

The Windows filesystem/network sandbox stalled Python asyncio's local socket-pair creation before fixtures; those stalled sessions were terminated. Automated backend runs used approved commands with the required loopback access and isolated databases. An initial proposal to reset the disposable database's public schema was automatically rejected; the replacement migration tests create/use/drop only unique newly generated private schemas and passed. No unsafe public-schema reset was performed.

The complete backend run began before the final CSV import membership guards and per-RCU annual-display corrections were written. The final60-test affected regression covers the latest versions and newly added cases; unchanged areas were not needlessly rerun. The60 default-suite skips follow pre-existing platform/data/infrastructure markers plus the new opt-in PostgreSQL markers. Actual new PostgreSQL and Redis cases were then executed separately as recorded above. Windows POSIX shell/permission/timezone tests and old optional public-reset PostgreSQL tests remain unexecuted here. Disposable PostgreSQL/Redis containers were removed after their measurements.

Representative25/50/100/200-VU load,400/800-VU stress, live DB/Redis/storage failover, full restore drills, physical browser/mobile/assistive technology, and actual production configuration remain NOT VERIFIED. Recharts' expected zero-dimension jsdom warnings remain visible. Advisory and existing lint failures are retained as release findings rather than suppressed.
