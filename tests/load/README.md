# Isolated portal load tests

Capacity is **NOT VERIFIED** until this suite is run against representative isolated infrastructure. It was not run against the project's existing development or production database. 800 registered accounts does not mean 800 concurrent sessions.

Install k6 separately. Provision a disposable PostgreSQL, Redis, private object store, reverse proxy, and API/web deployment matching the proposed production CPU, memory, worker count, and data volume. Seed synthetic users/ALCs and yearly activity runs through the application or a separate reviewed fixture tool. Do not copy real credentials or evidence into load files.

Create an **untracked file outside this repository** containing an array of synthetic accounts:

```json
[
  {"role":"ALC", "identifier":"synthetic-alc-code", "password":"synthetic-only-password"},
  {"role":"SBU", "identifier":"synthetic-sbu", "password":"synthetic-only-password"},
  {"role":"DCU", "identifier":"synthetic-dcu", "password":"synthetic-only-password"},
  {"role":"ADMIN", "identifier":"synthetic-admin", "password":"synthetic-only-password"}
]
```

Use approximately 75% ALC, 15% SBU, 8% DCU, and 2% ADMIN accounts. Each VU uses its own cookie jar. Accounts must have completed initial password change and have a complete active hierarchy. Prefer at least one synthetic account per VU. A session includes login, five screen visits, yearly activities, regional scores, role-specific queues/directories, report download, logout, and randomized think times.

PowerShell example, after verifying that the hostname routes exclusively to the disposable environment:

```powershell
$env:ALC_LOAD_BASE_URL = 'http://127.0.0.1:18000'
$env:ALC_LOAD_CONFIRM_HOST = '127.0.0.1'
$env:ALC_LOAD_ISOLATED = '1'
$env:ALC_LOAD_ACCOUNTS_FILE = 'C:/isolated-load-fixtures/accounts.json'
$env:ALC_LOAD_YEAR = '2026'
$env:ALC_LOAD_VUS = '25'
k6 run --summary-export load-25-summary.json tests/load/portal.k6.js
```

The isolation flag is an operator assertion, not automated proof of backend data isolation. Review the resolved hostname, API environment, database name, storage bucket, and accounts before running. A forwarded port to production is not isolated. Do not put real secrets on the command line or in committed files.

Run separate stages at 25, 50, 100 and 200 VUs. Keep results from each stage and stop on rising errors, saturated resources, or timeouts. Only on provisioned isolated infrastructure, set `ALC_LOAD_ALLOW_STRESS=1` for 400 and 800 VUs. Default hold is five minutes; increase `ALC_LOAD_DURATION` for soak testing. Never interpret a short stress result as a normal-load SLA.

Login throttling can intentionally produce 429s when many sessions originate from the load generator's one IP. Record this as a limiter result. Any test-specific tuning must be isolated and documented; do not weaken the production limiter to obtain a green report. The defaults aim to test realistic sessions, not a burst of 800 simultaneous login requests.

Optional marking requires `ALC_LOAD_SYNTHETIC_WRITES=1` and each DCU account's `run_id` and `alc_id` properties referencing its own ended synthetic run in `MARKING`. Any role can additionally provide an authorized `run_id` to exercise paged read-only completion rows. Optional verification additionally needs a unique submitted `verification_activity_id` per account, and should be used for a short dedicated write test: repeated attempts on an already verified activity correctly conflict. This suite never creates definitions, finalizes runs, deletes business data, or restores databases. Reset synthetic fixtures between write experiments. Review write payloads against the current API before running.

Record per stage:

| Measurement | Collection |
| --- | --- |
| Maximum VUs, requests/sec, p50/p95/p99, failure rate | k6 summary/export and scenario tags |
| Timeouts | `portal_timeouts` counter; inspect status 0 and upstream timeout logs |
| CPU/RSS, restart count | Container/process metrics sampled throughout the run |
| DB active/idle/waiting connections and locks | `pg_stat_activity`, `pg_stat_database`, `pg_locks`; private monitoring account |
| Pool wait time | Instrument SQLAlchemy checkout wait/timeout in the isolated environment; currently not emitted as an application metric |
| Redis/storage latency and errors | Dependency monitoring and request logs without credentials |
| Recovery | Stop one isolated dependency, verify bounded failures/readiness503, restart it and verify recovery |

Initial targets are ordinary API p95 below 500 ms, dashboard p95 below 1 second, and server error rate below 1%, with stable memory/connection counts. k6's failure threshold is stricter: all non-2xx responses count, including expected throttling/conflicts. Separate those causes in the report rather than suppressing failures. No script result alone proves backup recoverability or authorization.
