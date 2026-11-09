# Security and operational audit evidence

> **Current status — 2026-11-04: NOT READY for production.** Evidence-based Yearly Activities
> replaces manual marking. Auth checkpoint `53731d5` is committed locally. Final gates: 930 backend
> passed / 68 skipped; 139 frontend passed; 17 PostgreSQL and 1 real Redis test passed. Runtime npm/pip
> audits are clear; 12 dev npm entries and 11 baseline Ruff findings remain. Actual 25/50/100/200-VU
> stages missed targets; 400/800 were not run. Bounded uploads and real PG/private-S3 restore passed.
> Source DB remains 0009; production infrastructure/browser UAT remain unverified. See the
> [complete current report](MASTER_IMPLEMENTATION_PRODUCTION_REPORT_20261104.md),
> [workflow](YEARLY_ACTIVITY_EVIDENCE_WORKFLOW.md) and
> [redacted measured results](audit-evidence/MASTER_RUNTIME_RESULTS.json).
>
> The historical sections below retain earlier evidence. Current results above supersede the old
> manual YES/NO workflow, uncommitted-auth status and earlier dependency/load/recovery findings.

## Historical evidence — 2026-11-03

Review date: 2026-11-03. Scope: repository source and isolated automated tests; no deployment, production data mutation, or production penetration testing. The master production-readiness audit records final aggregate test counts and dependency scan results.

Reference standards used to guide the review: [OWASP ASVS 5.0](https://github.com/OWASP/ASVS/tree/v5.0.0/5.0), [OWASP API Security Top 10](https://owasp.org/projects/api-security-project), and [API1:2023 object-level authorization](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/). These are review references, not a claim of ASVS certification or complete adversarial coverage.

## Authentication findings

| Control | Source evidence | Assessment / limit |
| --- | --- | --- |
| Password storage | `app/auth.py`: Argon2id, time cost3, memory65536 KiB, parallelism4; async wrappers use per-event-loop AnyIO capacity limiter default2. | Appropriate primitive present. CPU/RSS and queue latency under representative concurrent login load remain unverified. |
| Login separation | `routes/auth.py`: portal accepts DCU/SBU username/email or ALC code; admin login restricts `User.role == ADMIN`; submitted role is never trusted. | Existing separation, timing and availability tests cover actual API success/failure paths. |
| Unknown-identifier timing | `login_password_hash`: dummy Argon2 hash; exactly one verification before account checks. | Equal hashing work by source/test; no external statistical timing study performed. |
| JWT | HS256/type/expiry/iat/UUID subject and sid required; the existing user query joins a live unexpired persistent family; role/hierarchy still loaded from database. | P1-A revocation acceptance passes; coordinated migration0009/code rollout required. |
| Refresh rotation | `services/sessions.py`: per-user/family locks plus conditional consumption; stable family id across successors. | Replay returns401 and revokes the whole family, including a racing successor. Exactly one consumption winner; family replay requires re-login. |
| Availability | `dependencies.account_available`: user active, and complete active RCU→DCU→SBU→ALC chain appropriate to role; checked login, refresh, every authenticated request. | Fails closed on absent hierarchy; inactive-account regression suite present. |
| Forced password change | Every role guard rejects `must_change_password`; `/auth/me` and change-password remain accessible. Reusing current temporary password rejected. | Existing tests cover forced-change restrictions. |
| Cookies | Access/refresh HttpOnly, SameSite=Lax; production startup requires Secure; CSRF cookie readable for double submit. | Requires HTTPS and correct same-origin/domain deployment; browser/deployment behavior not independently measured here. |
| CSRF | Unsafe `/admin`, `/portal`, `/leaderboard`, yearly writes and logout/change-password use double-submit check. Login and refresh rely on JSON/CORS and SameSite rather than explicit CSRF token. | No credentials in browser localStorage/sessionStorage found in `apps/web/src`. Subdomain trust/cookie injection and hostile same-site origins require deployment review. |
| Rate limit | Redis Lua atomic IP and IP+identifier counters, expiry, HMAC identifier digest, bounded per-process fallback. | Redis outage keeps process-local protection; the fallback budget multiplies by worker count and is not distributed. |
| Reset/change/logout | Current-family logout; all-family reset/change; self-change issues fresh credentials; forced-change preserved. | Copied old JWTs fail persistent validation even though their signatures/exp may still be valid. |

### SEC-01: validation error echoes credential input — P2, fixed

**Module:** `apps/api/app/main.py`, `validation_details`.

**Evidence:** the previous handler returned `_json_safe(error)` for every Pydantic error. A public `POST /api/auth/login` with a password shorter than eight characters produced an HTTP422 error with the password in `error.details[].input`. Model validation can include a complete submitted model in `input`, including password/token fields. Although the caller already supplied the credential, echoing it expands exposure through response capture, monitoring and support tooling and violates the requested no-secret-error-response control.

**Fix:** credential locations replace `input` with `[REDACTED]`; nested model/list credential keys are recursively redacted using the existing sensitive-field naming rules; malformed JSON errors omit unparseable raw input. Useful non-secret validation input and error envelope remain compatible. No credential values were logged during the audit.

**Validation:** new isolated and actual-login regressions in `test_validation_errors.py` cover password/current-password/new-password/token, nested model input, malformed JSON, and existing ordinary-input feedback. See master audit for executed results.

**Deployment impact:** source-only HTTP422 response hardening; no schema or user-data change.

### SEC-02: access JWT revocation window — P1, resolved in P1-A implementation

**Modules:** `app/auth.py`, `dependencies.get_current_user`, `routes/auth.py`, `services/sessions.py`.

**Original root cause:** at phase base85250df, JWTs contained `sub`, `type`, `iat`, `exp` with no stable family identity. The user lookup checked current account/hierarchy but no persistent session. Refresh-row revocation could not invalidate an access JWT; clearing forced-change could restore its operational access. The previous two HTTP200 weakness-evidence tests documented this behavior.

**Implemented fix:** persistent `auth_sessions`, required signed `sid`, and a session join in the existing user lookup. Logout revokes one family; password reset/change revoke all old families and refresh rows. Self-change replaces credentials so the current browser stays signed in. Reused consumed refresh hashes revoke their entire family. User→family→token lock ordering serializes auth mutations across workers/connections. No process-memory revocation set or Redis authority is used.

**Acceptance evidence:** the old weakness tests are now secure HTTP401 acceptance cases. The file has29 tests covering replay after logout, normal change, ADMIN/DCU/SBU reset and after forced-change completion; all old access/refresh credentials fail. Independent-session logout leaves another login valid. Required/malformed claims, expiry, owner binding, ignored role escalation claims, retention and zero-extra-round-trip query checks also pass. Targeted auth/stability/password suite:51 passed in78.38s. Migration and real PostgreSQL race/persistence suite:10 passed in12.34s. See the [P1-A report](SESSION_REVOCATION_REMEDIATION.md) and current [validation record](audit-evidence/VALIDATION_RESULTS.md) for full-suite evidence.

**Deployment impact / limits:** migration0009 after0008, coordinated new-code rollout and fresh sign-in for old browsers; schedule expiry cleanup. Old sid-less access and NULL-family refresh credentials fail closed. Absolute family lifetime equals configured refresh days; rotation does not extend it. Competing refresh replay may sign that family out. A request already validated before revocation may finish; subsequent persistent validation after commit rejects. No migration/deployment on an existing database or real process-restart/load experiment was performed. Overall readiness remains NOT READY for unrelated blockers.

### SEC-03: development services publicly bind — P1 deployment misuse risk

**Module:** `docker-compose.yml`.

**Evidence:** PostgreSQL5432, Redis6379, MinIO9000/9001 publish without a loopback host address, with explicitly development-only credentials and mutable image tags for MinIO. The production runbook instructs private/loopback services, unique secrets, firewall, HTTPS and no public object-storage access.

**Impact:** using the development Compose file unchanged on a public host could expose data services. No evidence that this configuration is deployed was available.

**Remediation:** use the documented production topology; verify actual listening sockets, firewall and private bucket policy before launch. Do not reuse this Compose file as a production deployment. Local development behavior was preserved.

## Authorization review

The [individual route matrix](AUTHORIZATION_ROUTE_MATRIX.md) inventories each baseline route, its role dependency, data scope and CSRF rule. Existing architecture consistently separates identity (`get_current_user`) from role authorization (`require_admin`, `require_supervisor`, `require_alc`, etc.) and object scope (`alc_scope`, `activity_scope`, `scoped_evidence`, `can_access_sbu`).

- Every baseline ADMIN handler has `require_admin`.
- Portal ALC activity/partner/task/evidence writes require `require_alc`; queries constrain owned identifiers. Supervisors cannot mutate through those ALC-only routes.
- DCU/SBU directories, reports, review and resets use current hierarchy scope. `require_alc_in_scope`/`require_sbu_in_scope` reject supplied out-of-scope filters. Missing role scope keys return no scope.
- Notifications list/mark-read require the current user's id.
- Evidence content checks authorized activity/evidence first and returns an application stream, never a storage URL/key. Historical removed evidence is only viewable when submission history permits it.
- Regional leaderboard aggregate fields are intentionally shared with all active operational roles. They do not expand detailed data access.
- Existing hierarchy reassignment locks the moved ALC/SBU, changes one parent key, and audits in one transaction. Business history is retained. Yearly participation needs its independent historical snapshot and current-write-scope checks.

The yearly workflow now locks regional roots before activation and hierarchy reassignment. ALC/SBU reassignment returns409 while **any** snapshotted participation remains in an unfinalized run, including already-marked YES/NO decisions. This preserves the responsible DCU and prevents transferring pending work to a supervisor who cannot edit the old run. After finalization the move preserves history and read access follows the current scoped ALC identity; old DCU write access is locked. This is a conservative operational policy change: administrators must finish existing yearly runs before a hierarchy move. No silent exclusion/reopening is available. Deactivating a required DCU does not remove its barrier participation; an unfinalized inactive DCU requires legitimate restoration or a future explicitly audited organizational exception policy.

No Critical/High BOLA exploit was verified in the reviewed baseline route implementations. This is narrower than proving the entire application secure; direct API regression results and untested combinations remain listed in the master audit.

## Other attack surfaces and reliability

| Surface | Evidence and limit | Priority / remaining work |
| --- | --- | --- |
| SQL injection | ORM expressions and bound parameters; reviewed raw database health query is fixed `SELECT 1`. No string-concatenated attacker SQL found in routes/services. | P2: maintain parameterization; database plans/security against real environment not certified. |
| Browser XSS | React escaping; no `dangerouslySetInnerHTML` usage found. Evidence accepts JPEG/PNG/WEBP/PDF signature+MIME+extension and serves nosniff. PDF is served inline, so browser PDF handling remains a dependency. | P2: consider deployment CSP and file malware scanning if policy requires. No exploit fabricated. |
| SSRF/redirects | Storage endpoint originates from server configuration; no user-controlled URL-fetch route found. Evidence app URL parser validates origin/path. | Not an external-network penetration test. |
| Traversal | Generated storage keys; local `_path` resolves and requires containment; evidence filenames are sanitized before Content-Disposition. | Local storage disallowed by production startup configuration. |
| Upload consumption | Reads each file at most configured limit+1 and validates file count, but accumulates validated files before upload (default up to100MiB/request). Multipart parsing happens before handler; reverse proxy body limit is essential. | P1 operational limit: enforce runbook body/timeouts, monitor concurrent upload RSS and temp disk. No 800-upload test performed. |
| Upload transaction | Object upload precedes database commit; storage failure can leave already uploaded objects not referenced by committed evidence. | P2: later reconciliation/cleanup and idempotent upload design; no production object deletion undertaken. |
| DB pool | PostgreSQL pool10, overflow20 per process, pre-ping, hidden SQL parameters; session context closes/rolls back on exit. Default pool timeout and no application statement timeout configured. | P1 capacity: budget workers×30 against database connection reserve; measure checkout wait and query timeouts. |
| Long reports | Activity CSV uses streaming server-side cursor, batches1000, safe CSV formula prefix, result closed in finally. Cursor holds DB session until response complete. | P2: slow-client report concurrency and proxy timeouts require measurement. |
| Other unbounded reads | Legacy partners/tasks/notifications and some compatibility partner views are unpaginated. New directories/lists are paged. | P2: monitor real cardinality; retain feature compatibility in this patch. |
| Storage outage | Boto connect/read/retry bounds; evidence open maps missing object404/outage503; streams close on complete/error/disconnect. Upload/delete outage currently follows generic500 safety net. | P2: dedicated retry UX and storage-write503 mapping remain; dependent data not treated as successful on error. |
| Health | Public liveness avoids dependencies; readiness parallel DB SELECT1/RedisPING/bucketHEAD with3s outer timeout, no details exposed. | P2: real failure/recovery and infrastructure alarms need staging validation. |
| Logging | Server request ids; route/path without query; request bodies/cookies/passwords omitted; sensitive field processor and hidden SQL params; third-party logs WARNING. | P2: runtime log retention/access/alerting not provisioned by repo. |
| Proxy/CORS | Production validation requires exact HTTPS origins and bounded trusted proxy CIDRs; app trusts forwarded IP only from configured direct peer. Uvicorn must disable its own proxy headers. | P1 operational requirement: verify actual startup command and proxy append behavior. |
| Backups | Integrated backup/manifest/checksum/restore/retention scripts and tests, prior documented nonproduction drill. | NOT VERIFIED for this environment: obtain a fresh restore drill on new yearly schema, off-server encrypted backup, RPO/RTO measurements. |
| Dependency vulnerabilities | npm/pip scans recorded by root audit. Broad semver ranges in backend, npm lock in frontend. | Do not claim dependencies safe if scan unavailable or unresolved advisories remain. |
| CI/CD | No repository workflow found in source file inventory. Commands/runbooks exist. | P2: wire checks and isolated integration tests in organization CI before release. |

## Validation and capacity limits

Historical yearly-phase command: `.venv/Scripts/python.exe -m pytest tests/test_validation_errors.py tests/test_session_revocation_evidence.py -q`:21 passed in6.83s, including two original weakness reproductions. **That is historical evidence, superseded by P1-A's HTTP401 acceptance tests and current results linked above.** Windows asyncio tests use approved loopback access with isolated synthetic fixtures, not the configured database. Current application/tests/cleanup/migration Ruff checks pass;11 unrelated existing script findings remain. k6/load and production infrastructure checks remain unexecuted in this phase.

The test suite uses isolated SQLite for most workflow checks, mock storage/Redis failure tests, and optional isolated PostgreSQL integration. These do not establish production TLS, bucket privacy, firewall, DB resource limits, encrypted backups, alerts, real dependency recovery, or any simultaneous-user capacity. `tests/load` provides guarded repeatable k6 scenarios and measurement instructions; it was not used to load the existing development/production database. Maximum measured concurrency and actual results must be recorded in the master audit, not inferred from800 registered users.
