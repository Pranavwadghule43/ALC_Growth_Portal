# Validation evidence — 2026-11-03

> **Current status — 2026-11-04: NOT READY for production.** Evidence-based Yearly Activities
> replaces manual marking. Auth checkpoint `53731d5` is committed locally. Final gates: 930 backend
> passed / 68 skipped; 139 frontend passed; 17 PostgreSQL and 1 real Redis test passed. Runtime npm/pip
> audits are clear; 12 dev npm entries remain. Full Ruff is now clean and a disposable service-backed
> CI workflow has been added but not yet run remotely. Actual 25/50/100/200-VU
> stages missed targets; 400/800 were not run. Bounded uploads and real PG/private-S3 restore passed.
> Source DB remains 0009; production infrastructure/browser UAT remain unverified. See the
> [complete current report](../MASTER_IMPLEMENTATION_PRODUCTION_REPORT_20261104.md),
> [workflow](../YEARLY_ACTIVITY_EVIDENCE_WORKFLOW.md) and
> [redacted measured results](MASTER_RUNTIME_RESULTS.json).
>
> The historical sections below retain earlier evidence. Current results above supersede the old
> manual YES/NO workflow, uncommitted-auth status and earlier dependency/load/recovery findings.

## Current completed validation — 2026-11-04

| Gate | Actual final result |
| --- | --- |
| Complete backend `pytest -q -rs` | **930 passed, 68 skipped, 0 failed**, 1 warning, 1046.83 s |
| Final auth/yearly PostgreSQL migration/race group | **17 passed**, 32.44 s; new private schemas only |
| Real Redis atomic limiter test | **1 passed**, 31 deselected, 1.11 s |
| Frontend Vitest | **139 passed / 11 files**, 0 failed, 103.35 s |
| ESLint | Passed |
| TypeScript + Vite (`VITE_API_URL=/api`) | Passed; build 18.76 s |
| Application/tests/new migration/auth cleanup Ruff | Passed |
| Load/recovery Python tooling Ruff | Passed |
| Required full `ruff check app tests scripts` | **11 pre-existing findings**, no new findings |
| `git diff --check` | Passed; line-ending normalization notices only |

## Continuation validation — 2026-11-04

| Gate | Actual result / limitation |
| --- | --- |
| List-count contract update | Updated seven stale expected values to four statements; runtime test remains NOT VERIFIED in this foreground-limited executor |
| Exact leaderboard scoring | **8 passed**, 34 deselected; duplicate/all-equal/strictly-lower/Fraction contracts exercised |
| Frontend Vitest | **139 passed / 11 files** in three bounded runs |
| ESLint | Passed |
| TypeScript + Vite (`VITE_API_URL=/api`) | Passed; Vite emitted its existing native-config future warning |
| Full `ruff check app tests scripts` | **Passed** after mechanical formatting of the 11 previously reported script findings |
| Alembic graph | One head: `20261104_0010` follows `20261103_0009` |
| CI definition | Added local GitHub Actions workflow with PostgreSQL 17/Redis 7 services; hosted CI execution is NOT VERIFIED |
| Full backend/PostgreSQL/Redis/load rerun | NOT VERIFIED after the performance candidate: local foreground test execution is terminated at ~30 seconds; no pass claim is made |

## Historical evidence — 2026-11-03

All application tests use synthetic fixtures. No test ran against the configured development/production database. Counts below overlap; do not add them together as independent test coverage.

## Current P1-A session revocation remediation

Phase base is85250df on `dev1/production-blockers-remediation`. Yearly Activities is already
committed at this base and remains preserved. New finding status: **SEC-02 resolved in the
tested implementation**, not deployed. Overall portal readiness remains **NOT READY**.

| Check | Actual result | Scope / limitation |
| --- | --- | --- |
| Targeted revocation acceptance |29 passed | Replaces2 old weakness-evidence cases; includes logout, both devices reset/change, forced-change non-resurrection, replay, required claims, ownership, expiry, cleanup and query path |
| Auth stability/password-security regression |22 passed |15 stability +7 password-security cases; old refresh expectations updated for secure family replay/replacement policy |
| Combined targeted run |51 passed in78.38s |1 deliberate wrong-algorithm JWT-construction warning; no error/warning suppression |
| New migration tests |4 passed |3 SQLite graph/legacy upgrade/downgrade +1 real PostgreSQL full chain, existing user/hash/refresh and Yearly preservation |
| New concurrency/persistence tests |6 passed |Real PostgreSQL separate transactions; refresh/logout/reset/change, competing refresh, separate engine and login/reset |
| Combined migration/concurrency run |10 passed in12.34s |Only newly generated private schemas in a new loopback test container; schemas/container removed afterward |
| Full backend regression |902 passed,67 skipped,1 warning in612.32s |All source changes were complete before this run; raw skip reasons included; new PostgreSQL cases passed separately above |
| Application/tests/new cleanup script/new migration Ruff | Passed |No changed-source lint findings |
| Full repository Ruff | Failed:11 unchanged baseline findings |Existing create_admin.py/create_alc_users.py import order and9 demo_smoke.py long lines; not suppressed |
| Frontend tests/lint/build | Not rerun in P1-A |No frontend files/API JSON contracts changed; prior phase results below remain historical |
| Auth validation query count |0 additional round trips |Measured comparison with original user/hierarchy load; session join appears in exactly1 existing statement |
| Git preservation/integrity |Both working-tree and real-index diff checks passed; index empty |No checkout/reset/stash/commit/push/deploy; phase-only P1A manifest/status separate from Yearly history |

Raw P1-A logs and phase-only manifest use the `P1A_` prefix in this directory. Migration
head0009 follows0008. No production/dev data or configuration was modified. New code rejects
legacy sid-less access and NULL-family refresh credentials; coordinated rollout/fresh sign-in
and scheduled expiry cleanup are operational prerequisites, not performed here. No actual
process restart/multi-host deployment/load test was conducted; independent persistent engine
visibility and real database lock behavior were tested.

## Historical Yearly Activities phase

The results below precede P1-A. In particular, the two former HTTP200 weakness reproductions
are retained only as historical evidence and have now been replaced by secure acceptance
tests; they no longer describe current token-validation behavior.

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
