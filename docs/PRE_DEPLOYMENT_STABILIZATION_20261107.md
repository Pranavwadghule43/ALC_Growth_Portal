# Pre-deployment stabilization report — 2026-11-07

## Assessment

**READY WITH CONDITIONS.** Repository-level migration blockers are repaired; the complete backend
suite is certified on Python 3.11; the pinned production Python dependency audit is clear; and the
frontend, static, migration-unit, and isolated PostgreSQL concurrency checks pass. Deployment remains
conditional on a staging rehearsal from a restored copy of the real database revision, production
infrastructure verification, capacity acceptance, and human authorization.

No production migration, deployment, DNS change, credential reset, or production-data mutation was
performed. The recovered local database and databases of unconfirmed purpose were not used for tests.

## Resolved release blockers

1. Migration 0011 now handles both legacy schemas and fresh schemas created from current metadata.
   It creates only missing Growth Challenge columns/indexes and validates its prerequisite table.
2. Migration 0012 now has explicit deterministic states:
   - legacy RCU constraint only: replace it with the DCU/year/code constraint;
   - current DCU constraint only: fresh-install no-op;
   - both: remove only the obsolete legacy constraint;
   - neither: fail with a precise integrity error.
3. Migration 0012 downgrade checks for cross-DCU duplicate codes before changing constraints. It
   fails before destructive DDL when the old regional uniqueness rule cannot be restored.
4. Yearly Activity ownership is enforced by the authoritative originating DCU for definition,
   run, participation, submission, review, evidence, dashboard, aggregate and direct-ID access.
   Historical fan-out rows remain stored but cannot bypass definition ownership.
5. Production configuration remains fail-closed for unsafe secrets, cookies, CORS, proxy trust,
   PostgreSQL driver, Redis and private S3 storage.

## Verification evidence

| Check | Result |
| --- | --- |
| Migration compatibility/unit tests | 6 passed, 2 PostgreSQL-only cases skipped in that run |
| Isolated PostgreSQL 13 migration-chain and concurrency tests | 8 passed |
| Alembic graph | single head `20261107_0012` |
| Ruff | passed |
| Python compilation | passed |
| Frontend tests | 141 passed |
| TypeScript and production Vite build | passed |
| Frontend ESLint | passed |
| npm production dependency audit | 0 vulnerabilities |
| npm complete audit | 7 moderate, development-only `minimatch` findings; no high/critical findings |
| Python installed-environment consistency | `pip check` passed |
| Complete backend suite, Python 3.11 | 963 passed, 69 skipped, 0 failed in 793.79s |
| PostgreSQL-specific suite (`alc_yearly_automated_test_20261107`) | 57 passed, 0 failed across 9 modules |
| Python Ruff (`app` and `tests`) | passed |
| Production Python dependency resolution | 45 pinned runtime packages; `uv pip check` passed |
| Production Python vulnerability audit | `pip-audit 2.10.1`: no known vulnerabilities |

The earlier interpreter discrepancy is resolved. The existing `apps/api/.venv/Scripts/python.exe`
is Python 3.11.5 (Anaconda), not Python 3.13. A separate environment was created without overwriting
it, using the managed CPython 3.11.15 interpreter at
`C:\Users\prana\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe`.
The certified test interpreter is `apps/api/.venv311-clean/Scripts/python.exe` (Python 3.11.15,
pytest 9.2.0). The apparent async fixture stall reproduced only in the restricted execution context:
even a minimal `asyncio.to_thread` wake-up stalled there, while the same interpreter and fixtures
completed normally outside that restriction. It was not accepted as an application defect.

The full backend command was `APP_ENV=test`,
`DATABASE_URL=sqlite+aiosqlite:///:memory:`, `ALC_ISOLATED_TEST=1`, then
`.venv311-clean/Scripts/python.exe -m pytest -q`. PostgreSQL-only modules were run separately with
`ALC_TEST_POSTGRES_URL` restricted to `alc_yearly_automated_test_20261107`; disposable roles/schemas
were removed afterward. The production dependency environment was independently resolved from
`constraints-runtime.txt` and audited with
`uvx pip-audit -r constraints-runtime.txt --no-deps --disable-pip --progress-spinner off`.
The exact-pinned audit completed with “No known vulnerabilities found.” No CVE suppression was used.
The constraints are not hash-pinned, which remains supply-chain hardening work but is not an unresolved
critical/high vulnerability.

## Required production environment

Use `.env.production.example` as the key list; store actual values in the deployment secret manager,
never in Git or frontend `VITE_*` values. Required controls include:

- `APP_ENV=production`, strong `SECRET_KEY`, `COOKIE_SECURE=true`;
- private `postgresql+asyncpg` database URL and a least-privilege migration role;
- private Redis URL, authentication/TLS where supported;
- exact HTTPS CORS origins or empty CORS for same-origin hosting;
- explicit trusted reverse-proxy CIDRs and Uvicorn `--no-proxy-headers`;
- private S3-compatible bucket and least-privilege service credentials;
- frontend built with `VITE_API_URL=/api` for the recommended same-origin topology.

## Backup, migration and rollback plan

1. Put writes into a documented maintenance window. Record the current production Alembic revision.
2. Take a PostgreSQL custom-format backup plus roles/grants metadata, and take/version the private
   evidence bucket. Copy both to storage independent of the application host.
3. Verify the database backup by restoring it under a new staging database name. Verify representative
   evidence hashes/objects; do not treat successful backup commands alone as restore proof.
4. Run the exact release code against that restored copy. Upgrade from its recorded revision to
   `20261107_0012`, run schema/constraint checks, then execute authentication, role-scope, Yearly
   Activity, evidence and leaderboard smoke tests.
5. Before production upgrade, query for duplicate `(rcu_id, calendar_year, activity_code)` groups and
   confirm every Yearly definition has a valid `introduced_by_dcu_id`. Preserve historical runs.
6. Stop application workers, run `alembic upgrade 20261107_0012` once with the migration role, then
   start workers with the matching code. Never run migrations independently from every worker.
7. Prefer forward fixes. Do not downgrade 0012 after different DCUs have reused the same code in one
   RCU/year; the migration intentionally refuses that unsafe downgrade. For a failed release, stop
   workers, preserve failure logs, restore the verified database and evidence backup, and redeploy the
   previous code/revision as one coordinated rollback.

## Deployment and post-deployment checklist

- [x] Clean Python 3.11 environment passes the complete backend suite and runtime `pip-audit`.
- [ ] Staging restore and migration rehearsal from the actual production revision passes.
- [ ] PostgreSQL 13 compatibility, role privileges, TLS, connection budget and backups are verified.
- [ ] Redis persistence/ACL/TLS and outage behavior are verified.
- [ ] S3 bucket is private; IAM, encryption, lifecycle, capacity and restore are verified.
- [ ] Reverse proxy serves HTTPS, forwards `/api`, limits upload size, and does not independently trust
      arbitrary forwarding headers.
- [ ] Frontend assets are built with the reviewed API URL and deployed atomically.
- [ ] `/api/health/live` and `/api/health/ready` are monitored with alert ownership.
- [ ] Smoke test login/logout/refresh for ADMIN, DCU, SBU and ALC; ambiguous ALC centre login fails.
- [ ] Verify cross-DCU definition, run, submission and evidence IDs return 404.
- [ ] Verify Yearly submission/review/finalization and exact leaderboard results on staging fixtures.
- [ ] Verify request IDs, structured logs, audit records and secret redaction.
- [ ] Obtain explicit human approval before production migration or deployment.

## Remaining risks and blockers

- Python 3.11 backend certification gate: **PASS** (963 passed, 69 skipped, 0 failed).
- Production Python dependency vulnerability gate: **PASS** (no known vulnerabilities; no unresolved
  critical/high findings). Hash-pin the constraints in a future supply-chain hardening change.
- The actual production revision and schema were not inspected in this task; the rollout path must be
  selected from a restored copy, not assumed from local databases.
- Production proxy, TLS, PostgreSQL, Redis, storage, monitoring, worker count and capacity are unverified.
- Development tooling retains moderate transitive `minimatch` advisories. They do not enter the
  production bundle, but should be cleared when the TypeScript ESLint dependency publishes/resolves a
  non-vulnerable tree.
