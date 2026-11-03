# Security and operational audit evidence

Review date: 2026-11-03. Scope: repository source and isolated automated tests; no deployment, production data mutation, or production penetration testing. The master production-readiness audit records final aggregate test counts and dependency scan results.

Reference standards used to guide the review: [OWASP ASVS 5.0](https://github.com/OWASP/ASVS/tree/v5.0.0/5.0), [OWASP API Security Top 10](https://owasp.org/projects/api-security-project), and [API1:2023 object-level authorization](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/). These are review references, not a claim of ASVS certification or complete adversarial coverage.

## Authentication findings

| Control | Source evidence | Assessment / limit |
| --- | --- | --- |
| Password storage | `app/auth.py`: Argon2id, time cost3, memory65536 KiB, parallelism4; async wrappers use per-event-loop AnyIO capacity limiter default2. | Appropriate primitive present. CPU/RSS and queue latency under representative concurrent login load remain unverified. |
| Login separation | `routes/auth.py`: portal accepts DCU/SBU username/email or ALC code; admin login restricts `User.role == ADMIN`; submitted role is never trusted. | Existing separation, timing and availability tests cover actual API success/failure paths. |
| Unknown-identifier timing | `login_password_hash`: dummy Argon2 hash; exactly one verification before account checks. | Equal hashing work by source/test; no external statistical timing study performed. |
| JWT | HS256 allowlist, expiration validation, UUID subject, access type check; role/scope loaded from database on every request. | Short-lived stateless access token; see residual revocation below. |
| Refresh rotation | `services/sessions.py`: conditional atomic `UPDATE ... RETURNING` revokes only an unexpired active token before issuing successor. | Old-token replay returns401; existing race test expects exactly one winner. Family-wide compromise detection/revocation is absent. |
| Availability | `dependencies.account_available`: user active, and complete active RCU→DCU→SBU→ALC chain appropriate to role; checked login, refresh, every authenticated request. | Fails closed on absent hierarchy; inactive-account regression suite present. |
| Forced password change | Every role guard rejects `must_change_password`; `/auth/me` and change-password remain accessible. Reusing current temporary password rejected. | Existing tests cover forced-change restrictions. |
| Cookies | Access/refresh HttpOnly, SameSite=Lax; production startup requires Secure; CSRF cookie readable for double submit. | Requires HTTPS and correct same-origin/domain deployment; browser/deployment behavior not independently measured here. |
| CSRF | Unsafe `/admin`, `/portal`, `/leaderboard`, yearly writes and logout/change-password use double-submit check. Login and refresh rely on JSON/CORS and SameSite rather than explicit CSRF token. | No credentials in browser localStorage/sessionStorage found in `apps/web/src`. Subdomain trust/cookie injection and hostile same-site origins require deployment review. |
| Rate limit | Redis Lua atomic IP and IP+identifier counters, expiry, HMAC identifier digest, bounded per-process fallback. | Redis outage keeps process-local protection; the fallback budget multiplies by worker count and is not distributed. |
| Reset/change/logout | Refresh token revocation and cookie clearing; password reset sets forced-change flag. | Copied access tokens remain cryptographically valid until expiry. |

### SEC-01: validation error echoes credential input — P2, fixed

**Module:** `apps/api/app/main.py`, `validation_details`.

**Evidence:** the previous handler returned `_json_safe(error)` for every Pydantic error. A public `POST /api/auth/login` with a password shorter than eight characters produced an HTTP422 error with the password in `error.details[].input`. Model validation can include a complete submitted model in `input`, including password/token fields. Although the caller already supplied the credential, echoing it expands exposure through response capture, monitoring and support tooling and violates the requested no-secret-error-response control.

**Fix:** credential locations replace `input` with `[REDACTED]`; nested model/list credential keys are recursively redacted using the existing sensitive-field naming rules; malformed JSON errors omit unparseable raw input. Useful non-secret validation input and error envelope remain compatible. No credential values were logged during the audit.

**Validation:** new isolated and actual-login regressions in `test_validation_errors.py` cover password/current-password/new-password/token, nested model input, malformed JSON, and existing ordinary-input feedback. See master audit for executed results.

**Deployment impact:** source-only HTTP422 response hardening; no schema or user-data change.

### SEC-02: access JWT revocation window — P1 policy decision / unresolved

**Modules:** `app/auth.py`, `dependencies.get_current_user`, `routes/auth.py`, `services/sessions.py`.

**Source evidence:** access JWT contains `sub`, `type`, `iat`, `exp`; `get_current_user` validates the JWT and current account/hierarchy but does not bind it to an active refresh session or password version. Logout only revokes the refresh token; password change revokes other refresh tokens. Password reset's forced-change flag blocks operational guards until a valid password change clears the flag. Thus a previously copied access JWT can remain usable during its configured lifetime (default15 minutes, production validation allows up to60), including after forced-change is later cleared.

**Reproduction on synthetic test accounts only:** `apps/api/tests/test_session_revocation_evidence.py` signs in to a synthetic ALC account and preserves its access cookie, logs out normally, then restores the copied access cookie to a cleared cookie jar. It also resets through an authorized synthetic supervisor, verifies the old access cookie is blocked by forced-change, changes the temporary password through a new legitimate login, then resubmits the old access cookie. Both flows explicitly expect dashboard200 and revoked refresh401. These assertions document the unresolved weakness and must not be presented as successful access revocation. See validation results below. No production account or data is used.

**Impact:** stolen access-token containment depends on expiry; refresh revocation is immediate but access revocation is not. Inactive user/hierarchy still blocks immediately because availability is reloaded.

**Remediation:** explicit organizational acceptance of the short access lifetime, or a separately designed persistent session identifier/token version checked server-side and invalidated by logout/reset/change. A refresh-token-family design should also specify stolen-token replay behavior. A cross-cutting authentication schema change was not introduced in the yearly-activity implementation. This remains a launch decision; do not describe logout as immediate revocation of every copied JWT.

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

Executed isolated security command: `.venv/Scripts/python.exe -m pytest tests/test_validation_errors.py tests/test_session_revocation_evidence.py -q` from `apps/api`: **21 passed in6.83 seconds**. The last two tests reproduce the unresolved SEC-02 weakness; their green result does not indicate secure revocation. The Windows sandbox stalled asyncio's local event-loop socket creation, so the run used the permitted unsandboxed command with in-memory SQLite synthetic fixtures, not the project's configured database. Ruff passed on `app/main.py` and both changed/new security test files. `node --check tests/load/portal.k6.js` passed. k6 itself and representative infrastructure load measurements were not run. `git diff --check` passed at this checkpoint.

The test suite uses isolated SQLite for most workflow checks, mock storage/Redis failure tests, and optional isolated PostgreSQL integration. These do not establish production TLS, bucket privacy, firewall, DB resource limits, encrypted backups, alerts, real dependency recovery, or any simultaneous-user capacity. `tests/load` provides guarded repeatable k6 scenarios and measurement instructions; it was not used to load the existing development/production database. Maximum measured concurrency and actual results must be recorded in the master audit, not inferred from800 registered users.
