# Isolated portal load tests

Production capacity is **NOT VERIFIED**. The 2026-11-04 local rehearsal uses separate disposable infrastructure; measured results and limits are in [the current master report](../../docs/MASTER_IMPLEMENTATION_PRODUCTION_REPORT_20261104.md). 800 registered accounts does not mean 800 concurrent sessions.

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

Optional writes require `ALC_LOAD_SYNTHETIC_WRITES=1`. An ALC's `submission_id` must identify its own editable synthetic draft; the suite saves metadata at a controlled rate. A DCU's `review_submission_id` must identify its own submitted evidence fixture; the suite reads the current revision and verifies only a pending submission. No manual YES/NO completion endpoint is used. Any role can supply an authorized `run_id` for paginated submission/status queues. The scenario never finalizes runs or uploads large media. Invalid or conflicting requests remain failures in the metrics.

The repository includes narrowly guarded rehearsal tooling:

- `isolated_drill.py`: configure/migrate/seed 800 synthetic ALCs and serve one monitored loopback API. Requires explicitly created PostgreSQL on 55489, Redis on 56389 and private S3 on 19090. It refuses both project port 55432 and unrelated port 5432. Credentials and fixtures remain under ignored `.local`.
- `run_isolated_load.py`: invoke Docker k6 at 25/50/100/200 VUs, collect PostgreSQL/Redis/container samples, and preserve per-stage exports. This local experiment uses a 30-second ramp, 30-second hold and 15-second ramp-down; it is not a soak test. `--stress` requires all four normal stages to pass before 400/800, and stops stress after a failed stage. Server samples include RSS, CPU seconds, pool checkouts and spool bytes; checkout wait duration is still uninstrumented.
- `upload_capacity.py`: separate 1×1 MiB, 4×5 MiB and 4×10 MiB playable synthetic MP4 uploads, oversize rejection and actual disposable-storage outage/recovery. No upload size or production limit is raised.
- `recovery_drill.py`: real PostgreSQL custom archive, table/count/record-hash/constraint/index comparison, private-object checksum backup/restore, restored API authorization/range tests and revocation of restored sessions before startup. It refuses an existing restore target. Backup artifacts are retained under ignored `.local/master-recovery-complete`.

These scripts require the application virtual environment plus isolated test tooling (`psutil`, `imageio-ffmpeg`) under `apps/api/.deps/drill`; neither is an application dependency. The official MinIO images could not be pulled in this environment, so the current drill uses RustFS through the existing S3 adapter. Provision the real production MinIO/IAM/TLS configuration and repeat before release. The synthetic seed uses one randomly generated test-only password hash; real API logins still perform normal Argon2 verification.

The local shared-NAT login budget is explicitly 10,000 attempts/600 seconds in the disposable process only. Production defaults and code are unchanged. Results must disclose this setting rather than claim the normal 100-attempt shared-IP policy was exercised at every stage. Default k6 durations remain available for longer independent testing through `ALC_LOAD_RAMP`, `ALC_LOAD_DURATION` and `ALC_LOAD_DOWN`.

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

The final post-upload recovery used `--artifacts master-recovery-complete`. A Growth Challenge and notification were populated. The target must be a new disposable database/bucket; preserve previous backup artifacts before cleaning a prior test target.

The initial status-0 counter was reconciled with raw k6 warnings: 200 VUs had 760 request timeouts and 1668 TCP refusals. The current script separates transport failures and timeout code1050 ([official k6 codes](https://grafana.com/docs/k6/latest/javascript-api/error-codes/)).

## Capacity measurement modes

Normal 25/50/100/200-VU stages use `ALC_LOAD_STEADY_STATE=1`: the isolated seed creates
short-lived synthetic access/CSRF cookies so dashboard capacity is measured independently
from Argon2 and login throttling. This does not bypass authentication in application code;
the cookies reference real persistent synthetic `auth_sessions` in the disposable database.

Run a separate login-burst experiment by omitting `ALC_LOAD_STEADY_STATE` (or setting it to
`0`). Intentional HTTP 429 responses from the login limiter must be reported separately from
transport/5xx capacity failures. The application limiter must never be disabled to make a
load test pass.
