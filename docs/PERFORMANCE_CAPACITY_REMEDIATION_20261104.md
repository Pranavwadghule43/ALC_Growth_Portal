# ALC Growth Portal — Performance & Capacity Remediation Candidate

Date: 2026-11-04

This document records the focused remediation prepared after the measured 25/50/100/200-VU
failure in the isolated Windows/Docker Desktop rehearsal. It does **not** replace the measured
baseline in `MASTER_IMPLEMENTATION_PRODUCTION_REPORT_20261104.md`, and it does not claim that the
load target has passed until the local guarded k6 stages are rerun.

## Measured baseline retained

The previous isolated run used one API worker, an application pool of 10 + 20 overflow connections,
800 synthetic ALCs and the full evidence/yearly candidate. At 25 VUs overall p95 was about 6.85 s;
50/100/200 VUs progressively saturated all 30 application checkouts and produced timeouts/failures.
PostgreSQL CPU stayed materially below API CPU, so simply increasing the database pool is not a
validated fix.

The subsequent profiler isolated a regional leaderboard call as an important CPU path. The complete
`region-scores` path cost roughly 85 ms CPU in that environment, while the dashboard also requested
`region-top10`, causing the same regional ranking to be calculated twice. Profiling split the
800-row score path into roughly 18 ms aggregate SQL, 15 ms exact-Fraction scoring and 18 ms generic
FastAPI encoding, with remaining time in surrounding application work.

Intentional login-limiter HTTP 429 responses are security behavior and must be reported separately
from transport/5xx capacity failures.

## Code changes in this remediation candidate

1. **Login connection occupancy** — initial identity/hash lookup is intentionally short; the request
   commits/releases that read-only transaction before queued Argon2 verification, then reloads and
   revalidates the user before the existing locked session-issuance step. Argon2 parameters and
   equal-work unknown-account behavior remain unchanged.
2. **Authenticated hierarchy lookup** — scalar User→ALC/SBU/DCU/RCU relationships use joined eager
   loading rather than several `selectinload` round trips. The current database role, hierarchy and
   persistent auth-session state are still checked on every protected request.
3. **One dashboard ranking request** — Region Top 10 now derives its first 10 rows from the same
   TanStack Query cache entry used by All ALC Scores. `/api/leaderboard/region-top10` remains
   available for compatibility.
4. **Exact scorer optimization** — percentile results are precomputed once per distinct metric value,
   and the five equal components are summed before applying the single exact `Fraction(1, 5)` weight.
   No float approximation is introduced.
5. **HTTP encoding path** — leaderboard routes convert only UUID/date-time edge values to JSON-safe
   primitives and return `JSONResponse`, avoiding FastAPI's generic recursive encoder over ~800
   already-known public rows.
6. **Pool overload behavior** — pool size, overflow, timeout and recycle are explicit validated
   environment settings. The single-worker default capacity remains 10 + 20; checkout timeout is
   reduced to 5 s and produces a safe retryable 503 rather than a 30-second server-side hang.
7. **Load-test separation** — the disposable synthetic seed creates real persistent test sessions.
   Normal load stages can use those sessions (`ALC_LOAD_STEADY_STATE=1`) so steady-state portal
   capacity is measured independently of Argon2/login throttling. A separate login-burst mode still
   exercises the real login endpoint and limiter.
8. **Realistic dashboard traffic** — the k6 workflow calls `region-scores` once per screen, matching
   the updated frontend instead of charging the server for both regional endpoints.

## Local microbenchmarks (not production capacity evidence)

Two isolated Python microbenchmarks were used only to validate the CPU-direction of the pure-code
changes. They are not HTTP or concurrency tests.

- Repeated-value exact percentile calculation, 800 values × 2,000 runs: old ~842.85 ms total,
  candidate ~192.20 ms total (same exact `Fraction` outputs).
- Five-metric exact score calculation, 800 synthetic rows × 300 runs: old ~8.72 ms/call,
  candidate ~3.98 ms/call (same exact component/final scores).
- FastAPI generic `jsonable_encoder` on an 800-row representative payload: ~15.47 ms/call;
  targeted primitive conversion: ~0.56 ms/call plus ~2.22 ms/call JSONResponse rendering in this
  sandbox. This mirrors the earlier profiler's ~18 ms generic-encoding observation.

These figures only justify rerunning the real guarded load test; they do not establish a deployment
capacity.

## Security/integrity invariants preserved

- Five leaderboard metrics remain exactly 20% each.
- Regional percentile semantics, full-precision sorting and ALC-code tie ordering are unchanged.
- Only VERIFIED evidence-based yearly completions become the fifth metric after the regional barrier.
- Persistent JWT/session revocation and refresh-family replay protection are unchanged.
- Argon2 cost parameters and login rate limiting are unchanged.
- Private evidence authorization/storage is unchanged.

## Validation prepared

New/updated regressions cover:

- DB transaction released before Argon2 verification.
- One SQL statement for current-user/session/hierarchy reconstruction.
- Safe 503 + `Retry-After` response for SQLAlchemy pool checkout timeout.
- One shared frontend regional ranking request when both dashboard sections render.
- Existing exact scorer/tie/constant-value tests continue to define the scorer contract.

In the ChatGPT sandbox, Python source/test files compile successfully and `git diff --check` passes.
The full repository test environment cannot be reproduced here because the sandbox lacks the
project's `aiosqlite`, `asyncpg`, Ruff, Node dependency tree, Docker and k6 binaries. The repository's
own Windows virtualenv/Docker test environment must therefore run the final acceptance commands.

## Commands to run on the project workstation

After applying the incremental remediation patch to the current master candidate:

```powershell
cd "C:\Users\prana\Downloads\ALC Growth Portal\apps\api"
.\.venv\Scripts\python.exe -m pytest tests/test_auth_stability.py `
  tests/test_session_revocation_evidence.py tests/test_region_leaderboard.py `
  tests/test_yearly_leaderboard.py tests/test_validation_errors.py `
  tests/test_config_hardening.py -q
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m ruff check app tests scripts
```

```powershell
cd "C:\Users\prana\Downloads\ALC Growth Portal\apps\web"
npm run test
npm run lint
$env:VITE_API_URL="/api"
npm run build
```

Then rerun the guarded isolated normal stages using the existing load harness. The normal harness is
now configured for pre-authenticated steady-state traffic. Run the login-burst scenario separately
with `ALC_LOAD_STEADY_STATE=0` so intentional limiter 429s are not confused with dashboard capacity.

Record successful RPS, p50/p95/p99, transport failures, timeouts, 5xx, 429 separately, API CPU/RSS,
PostgreSQL CPU/connections and peak pool checkout for each 25/50/100/200-VU stage. Do not run 400/800
unless 200 VUs is stable with resource headroom.

## Continuation validation — 2026-11-04

- The stale list-query statement-count contract now expects four statements for every role/path:
  auth/session + count + page + page summaries. The assertions still require one `COUNT(*)` and no
  lazy `Activity` collection loads.
- Pure exact-score regression coverage passed: **8 passed** (strictly-lower percentiles, duplicate
  values, all-zero/all-equal inputs, exact 20% weights and full-precision tie ordering).
- The existing browser-facing regression set passed in three bounded runs: **139 passed / 11 files**.
  ESLint and the `VITE_API_URL=/api` TypeScript/Vite build passed.
- `python -m ruff check app tests scripts` now passes; the two import-order and nine line-length
  findings in maintenance scripts were formatted without behavior changes.
- `.github/workflows/quality.yml` now supplies disposable PostgreSQL 17 and Redis 7 CI gates for
  backend regression, migration graph, Ruff, frontend tests, lint and build. It has not yet run on
  a hosted CI worker.

The local command executor ends foreground Python test processes after roughly 30 seconds. The
full backend, PostgreSQL/Redis integration and fresh guarded load runs therefore remain **NOT
VERIFIED after this remediation**; the prior complete run is retained as historical evidence only.

## Release status

Until those repository-native regression and load reruns are completed, production status remains
**NOT READY / PERFORMANCE REMEDIATION NOT YET VERIFIED**. The purpose of this patch is to remove the
measured application-level waste and make the next load run diagnostically meaningful, not to claim
capacity without evidence.
