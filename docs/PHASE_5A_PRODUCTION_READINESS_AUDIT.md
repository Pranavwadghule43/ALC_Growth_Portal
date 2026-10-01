# Phase 5A — Production Readiness Audit

| Field | Value |
| --- | --- |
| Project | ALC Growth Portal |
| Audited Base Commit | `efea81f` — merge: integrate Phase 4G-B security hardening |
| Audit Type | Read-only production readiness audit |
| Repository Changes During Audit | None |
| Application Code Modified | No |
| Database Modified | No |

Phase 5A was intentionally **audit-only**. Its purpose was to establish, from evidence, whether
the integrated Phase 4 application is ready for production-like UAT, real Redis validation, a
database migration dry run, backup/restore validation and an office-server deployment. No
application code, migration, test, configuration file or database was changed during the audit.
Every finding below is a record of the audited state; none has been fixed as part of Phase 5A.

Integrated work covered by the audited baseline:

- Phase 4G-A — login timing hardening
- Phase 4G-B — login rate limiting and trusted-proxy security
- Phase 4H — frontend search/pagination efficiency
- Phase 4I — admin dashboard aggregation optimization
- Phase 4J — non-draft PostgreSQL activity index
- Phase 4K — activity list `COUNT(*)` optimization

Severity scale used throughout:

| Severity | Meaning |
| --- | --- |
| BLOCKER | Must be resolved before production. |
| HIGH | Should be resolved before production unless explicitly accepted. |
| MEDIUM | Can ship with a documented mitigation. |
| LOW | Future improvement. |
| INFO | No action required. |

---

## 1. Executive Summary

The application is functionally strong and well integrated. The audited code was commit
`efea81f`. Verification performed during the audit:

| Check | Result |
| --- | --- |
| Frontend tests | 24 / 24 passed |
| Frontend lint | Passed |
| Frontend production build | Passed |
| Backend full test run (isolated audit environment, opt-in real Redis and PostgreSQL tests enabled) | 478 passed, 1 skipped, 2 initial environment-related failures |
| Re-run of the two initially failing test areas | Passed (54 / 54 in the affected files) |
| Alembic | One linear head (`20260930_0006`) |
| Upgrade from an empty database | Succeeded |
| Upgrade from the older schema containing sample rows | Succeeded |
| Downgrade `0006 → 0003`, then upgrade back to head | Succeeded |
| Real Redis behaviour | Tested and passed |
| Committed secrets | No real secret found in repository content or examined Git history |

The two initial backend failures were caused by the isolated audit environment, not by the
application: one PostgreSQL index test deliberately edits `pg_index` and requires a superuser
role, and one provisioning test requires the checkout to be a Git working tree. Both pass when
those preconditions are met.

For reference, the previously recorded team baseline for the same code was 475 passed, 6 skipped,
0 failed; the difference is that Phase 5A enabled the opt-in real Redis and PostgreSQL tests.

**Conclusion:** the application is suitable for continued UAT. It is **not yet approved for
office-server production deployment**. Two blockers and four high-risk items must be resolved
or explicitly accepted first.

---

## 2. Current Production Readiness

| Target | Status |
| --- | --- |
| Production-like UAT | Ready with conditions (evidence access depends on B1) |
| Real Redis validation | Ready; repeat on the target server |
| Database migration dry run | Ready; still run against a restored copy of the real database |
| Backup/restore validation | Not ready |
| Office-server deployment | Not ready |

---

## 3. Production Blockers

### B1 — Evidence access architecture

**Phase 5A audit finding**

- Production rejects `STORAGE_BACKEND=local` (startup guard in `apps/api/app/main.py`).
- Production therefore uses the S3 backend (MinIO or another S3-compatible service).
- In S3 mode the API returns a **presigned URL** pointing at `S3_ENDPOINT_URL`
  (`/api/portal/evidence/{id}/access`, `/api/admin/evidence/{id}/access`).
- The browser opens that URL **directly** (evidence preview and PDF open in the frontend).
- If MinIO is bound only to `127.0.0.1:9000` on the office server, other users' browsers cannot
  reach it, so evidence cannot be opened during review.
- This worked during same-machine demo usage, where the browser and MinIO ran on the same
  computer. It is not sufficient for a multi-user office deployment.

Phase 5A identified three viable evidence-delivery options:

| Option | Description |
| --- | --- |
| A | Expose only the MinIO S3 API through the reverse proxy over HTTPS (dedicated hostname), set `S3_ENDPOINT_URL` to that public HTTPS address, and keep the MinIO console private. |
| B | Use an external S3-compatible storage provider. |
| C | Authorize the request in FastAPI and stream/proxy the evidence through the API, so MinIO stays fully private. |

None of these options was implemented in Phase 5A.

**Phase 5B project decision / preference (not an audit finding)**

Phase 5A identified three viable evidence-delivery options. For the planned office-server
architecture, the project team currently prefers Option C: authorize the request in FastAPI and
stream/proxy evidence through the API, allowing MinIO to remain private. This is a Phase 5B
architectural decision, not an original Phase 5A audit finding.

### B2 — Backup and restore

- No complete, tested PostgreSQL backup and restore procedure exists yet.
- Backup and restore of the evidence bucket have not been validated either.
- Production deployment must not proceed until both PostgreSQL and evidence backup/restore have
  been performed and a restore has been tested successfully.

A proposed procedure is given in section 21. It is not yet production-validated.

---

## 4. High-Risk Items

### H1 — Production environment detection / startup guard

- The application reads `APP_ENV` only (`apps/api/app/config.py`).
- `ENVIRONMENT=production` is ignored; the application starts in development mode.
- The `APP_ENV` comparison is case-sensitive: `APP_ENV=Production` behaves as development
  (API docs enabled, production guard skipped).
- The `.env` file is loaded from paths relative to the current working directory
  (`../../.env` and `.env`). Starting the API from a different directory silently falls back to
  development defaults.
- `SECRET_KEY` validation is too weak: the production guard rejects only the built-in default
  key. During the audit, the `.env.example` placeholder key and a 3-character key were both
  accepted in production mode.
- A known or weak `SECRET_KEY` would allow access tokens to be forged.

Production startup hardening is required (planned as Phase 5B-1).

### H2 — Frontend production API URL

- A plain `npm run build` can embed `http://localhost:8000/api` in the bundle. This was confirmed
  in the audited build output; it is the fallback in `apps/web/src/lib/api.ts`.
- Vite reads environment configuration from `apps/web`, not from the repository-root `.env`.
- The production frontend must be built with:

  ```
  VITE_API_URL=/api
  ```

### H3 — Trusted proxy configuration

Production behind a local Caddy/Nginx reverse proxy should explicitly use:

```
TRUSTED_PROXY_CIDRS=127.0.0.1/32
```

Uvicorn must continue to run with `--no-proxy-headers`. The application, not Uvicorn, remains the
single authority for trusted-proxy client-IP handling.

Consequence of misconfiguration: if `TRUSTED_PROXY_CIDRS` is left empty behind the proxy, every
request appears to come from `127.0.0.1`. All users then share one client IP for login rate
limiting:

- the site-wide budget of 100 login attempts per 10 minutes is shared by everyone;
- eight failed attempts against any username/ALC code from anyone would block that identifier
  for everyone for 15 minutes.

No startup check currently detects this misconfiguration.

### H4 — Repository visibility / ALC data

- The audit reported the GitHub repository visibility as **public**.
- `data/ALC-MASTER.csv` contains ALC codes, ALC names and the SBU mapping.
- ALC codes are used as ALC login usernames.
- If this is real operational data, repository visibility must be reviewed.
- Removing the current file alone would not erase it from previous Git history.

No GitHub changes were made during Phase 5A.

---

## 5. Medium-Risk Items

| ID | Finding |
| --- | --- |
| M1 | `/api/health` is static. It does not verify PostgreSQL, Redis or storage. |
| M2 | The Redis rate limiter silently falls back to in-process counters when Redis is unavailable; the fallback is not logged. |
| M3 | The application has no global HTTP request-size limit. The reverse proxy must enforce an appropriate body limit while still supporting the configured 10 MiB evidence upload (Nginx's 1 MB default would block uploads). |
| M4 | Production logging, log rotation and alerting are not defined. Unhandled-exception logging records the exception text; database errors can include query values, so user data may appear in logs. |
| M5 | The README deployment documentation is outdated for the planned office server (it describes a Vercel frontend plus a separately hosted API). A production runbook is required. `PASSWORD_HASH_CONCURRENCY` and `LOCAL_STORAGE_PATH` are not fully documented in `.env.example`. |
| M6 | Production does not reject default MinIO credentials such as `minioadmin`. Production must use dedicated, non-default, least-privilege credentials. |

---

## 6. Low-Risk Items

These are post-blocker improvements unless otherwise stated.

| ID | Finding |
| --- | --- |
| L1 | Orphaned evidence files are possible around failed database commits (objects are uploaded before the commit; deletion from storage happens before the commit). There is no orphan clean-up job. |
| L2 | The S3 client uses default botocore timeouts and retries, so a slow storage service can stall requests. |
| L3 | Activity numeric counters (learners reached, leads, admissions) have no application-level upper bound; values above the PostgreSQL `INTEGER` range cause a server error. |
| L4 | The admin ALC import lacks complete archive/size protection (whole-file read with no size cap; no XLSX archive-expansion guard). Admin-only. |
| L5 | Refresh tokens, audit logs and notifications have no retention/purge job. |
| L6 | A stolen access token may remain valid until its 15-minute expiry after logout. Refresh-token family replay hardening could be improved. |
| L7 | CSV download links can show a raw 401 response after the access token expires. |
| L8 | Unknown URLs redirect to the login page (no 404 page); simultaneous token refresh in two tabs can send one tab to login. These need UX improvement. |
| L9 | `apps/api/scripts/demo_smoke.py` is outdated for the current authentication and CSRF model and should not be used for UAT. |
| L10 | The fresh-database schema and the upgraded-database schema differ slightly (a redundant `uq_sbus_code` unique constraint and a server default on `sbus.is_active` on upgraded databases). Future migrations require continued defensive (inspector-guarded) checks. |
| L11 | `/redoc` and `/openapi.json` remain available in production mode (only `/docs` is disabled). |
| L12 | Production should use one Uvicorn worker, because Argon2 resource use and the fallback rate-limit state are per process. |

---

## 7. Informational Observations

- `UNDER_REVIEW` is defined and treated as reviewable, but no workflow transition currently
  produces it.
- Supervisor (DCU/SBU) password reset targets the first ALC login for an ALC.
- The `server: uvicorn` response header is exposed.
- HSTS includes `includeSubDomains`.
- Local-storage evidence URLs are development-only behaviour (local storage is rejected in
  production).
- The provisioning CLI's "inside the repository" protection expects a `.git` directory; on a
  deployment without `.git`, write credential files outside the application directory.
- `apps/api/alembic.ini` contains a development database URL, but `DATABASE_URL` overrides it
  (Alembic's `env.py` uses the application settings).

---

## 8. Environment Variable Audit

All variables have defaults, so none fails startup simply by being absent. Startup does fail
when production checks or validated ranges are violated, as noted below.

| Variable | Required in production | Default | Safe default | Sensitive | Production recommendation | Startup behaviour |
| --- | --- | --- | --- | --- | --- | --- |
| `APP_ENV` | Yes | `development` | No | No | `production` (exact, lower-case) | Missing or mis-cased → silently runs as development |
| `SECRET_KEY` | Yes | development key | No | Yes | 64+ random bytes, unique per environment | Fails only if the built-in default is kept in production |
| `DATABASE_URL` | Yes | development URL with development password | No | Yes | Dedicated non-superuser role on the private server, e.g. `127.0.0.1:5432` | Starts; fails at first database request |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` | Yes (same host) | Yes, if it contains a password | Redis bound to `127.0.0.1` | Starts; unreachable Redis → silent local fallback |
| `COOKIE_SECURE` | Yes | `false` | No | No | `true` | Fails in production if not `true` |
| `TRUSTED_PROXY_CIDRS` | Yes (behind proxy) | empty | Trusts nothing, but incorrect behind a proxy | No | `127.0.0.1/32` | Invalid value fails startup; empty value is not detected |
| `CORS_ORIGINS` | No (same-origin) | `http://localhost:5173` | Yes | No | Explicit portal origin, e.g. `https://portal.example.com` | — |
| `ACCESS_TOKEN_MINUTES` | No | `15` | Yes | No | `15` | — |
| `REFRESH_TOKEN_DAYS` | No | `14` | Yes | No | `14` (or shorter by policy) | — |
| `PASSWORD_HASH_CONCURRENCY` | No | `2` (range 1–32) | Yes | No | `2`; raise only with available memory (~64 MiB per operation) | Out-of-range fails |
| `LOGIN_IP_LIMIT` | No | `100` | Yes | No | `100` | Out-of-range fails |
| `LOGIN_IP_WINDOW_SECONDS` | No | `600` | Yes | No | `600` | Out-of-range fails |
| `LOGIN_PAIR_FAILURE_LIMIT` | No | `8` | Yes | No | `8` | Out-of-range fails |
| `LOGIN_PAIR_FAILURE_WINDOW_SECONDS` | No | `900` | Yes | No | `900` | Out-of-range fails |
| `LOGIN_FALLBACK_MAX_KEYS` | No | `10000` | Yes | No | `10000` | Out-of-range fails |
| `MAX_UPLOAD_FILES` | No | `10` | Yes | No | `10` | — |
| `MAX_UPLOAD_BYTES` | No | `10485760` (10 MiB) | Yes | No | `10485760`; align the proxy body limit | — |
| `STORAGE_BACKEND` | Yes | `s3` | Yes | No | `s3` for the current production architecture | `local` fails in production |
| `LOCAL_STORAGE_PATH` | No | `../../.local/evidence` (working-directory relative) | Development only | No | Not used in production | — |
| `S3_ENDPOINT_URL` | Yes (MinIO) | `http://localhost:9000` | No | No | Depends on the B1 decision | — |
| `S3_REGION` | Yes | `us-east-1` | Yes | No | Keep or match provider | — |
| `S3_BUCKET` | Yes | `alc-evidence` | Yes | No | Private bucket, no public listing | — |
| `S3_ACCESS_KEY` | Yes | `minioadmin` | No | Yes | Non-default, least-privilege, single-bucket key | Not checked at startup |
| `S3_SECRET_KEY` | Yes | `minioadmin` | No | Yes | Non-default, least-privilege | Not checked at startup |
| `S3_PRESIGN_SECONDS` | No | `300` | Yes | No | `300` | — |
| `VITE_API_URL` (frontend build-time) | Yes | `http://localhost:8000/api` (code fallback) | No | No | `/api` | Build does not fail |
| `DEV_ADMIN_PASSWORD` | No (scripts only) | none | — | Yes | Never set or use for production provisioning | — |
| `DEV_ALC_PASSWORD` | No (scripts only) | none | — | Yes | Never set or use for production provisioning | — |

Key production values:

```
APP_ENV=production
COOKIE_SECURE=true
TRUSTED_PROXY_CIDRS=127.0.0.1/32
STORAGE_BACKEND=s3
VITE_API_URL=/api            # frontend build time
SECRET_KEY=<64+ random bytes>
S3_ACCESS_KEY / S3_SECRET_KEY=<non-default, least privilege>
```

`ENVIRONMENT` is not read by the application; use `APP_ENV`. Test-only variables
(`LOGIN_RATE_LIMIT_TEST_REDIS_URL`, `ALC_TEST_POSTGRES_URL`, `AUTH_RACE_DATABASE_URL`) must never
be set in production.

---

## 9. Secret Audit

No real secret was found in tracked repository content or examined Git history during Phase 5A.
The history scan covered all remote branches and found no committed `.env` files, provisioning
credential outputs, database dumps, private keys or cloud access keys.

Expected development placeholders were found in:

| File | Content | Tracked | Action required |
| --- | --- | --- | --- |
| `.env.example` | Placeholder `SECRET_KEY`, development database URL, MinIO default credentials, `DEV_*` placeholders | Yes | No (see H1 and M6 for production use) |
| `docker-compose.yml` | Development PostgreSQL password, MinIO default root credentials | Yes | No (development only) |
| `apps/api/alembic.ini` | Development database URL (overridden by `DATABASE_URL`) | Yes | No |
| `apps/api/app/config.py` | Development defaults | Yes | No |
| `apps/api/tests/*` | Test fixture passwords | Yes | No |

`data/ALC-MASTER.csv` contains usernames and organisational information, not passwords (see H4).

These placeholder and development credentials are not leaked production secrets.

---

## 10. Production Mode Audit

Observed by starting the API with a production configuration in the isolated audit environment:

| Aspect | Observed behaviour |
| --- | --- |
| Debug | FastAPI debug mode is not enabled by the current application configuration. |
| `/docs` | Disabled (404) |
| `/redoc` | Currently accessible (L11) |
| `/openapi.json` | Currently accessible (L11) |
| CORS | Foreign-origin preflight rejected in the tested production configuration |
| Cookies | `Secure` flag depends on `COOKIE_SECURE` |
| Security headers | HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` |
| Local storage | Rejected in production |
| Errors | Generic 500 response to the client; detail logged internally |
| Redis | Fail-open to a bounded local fallback (never a 500) |

Startup guard results: the default `SECRET_KEY`, `COOKIE_SECURE=false` and `STORAGE_BACKEND=local`
are rejected in production. The `.env.example` placeholder key, a very short key and default
MinIO credentials are accepted (H1, M6).

---

## 11. Migration Status

| Item | Status |
| --- | --- |
| Current Alembic head | `20260930_0006` |
| Heads | Single head |
| Branches / orphans | None |
| Phase 4J migration | Included: `20260930_0006_nondraft_submitted_index.py` |

Revision chain:

```
20260916_0001 (initial)
  -> 20260918_0002 (SBU)
  -> 20260920_0003 (user delete set-null)
  -> 20260923_0004 (RCU/DCU hierarchy)
  -> 20260924_0005 (review decision change)
  -> 20260930_0006 (non-draft submitted index)
```

- Upgrade from an empty database succeeded.
- Upgrade from the older schema containing sample rows succeeded, including the reviewer-role
  backfill.
- Downgrade to `20260920_0003` and re-upgrade to head succeeded.
- Every revision has a downgrade. The `0004` downgrade intentionally refuses while DCU users exist.
- `0006` builds `ix_activities_submitted_nondraft` with `CREATE INDEX CONCURRENTLY` and rebuilds an
  invalid index left by an interrupted build.
- `0004` seeds `RCU_PUNE` and four DCUs and links SBU 4/6/7 to DCU Nashik (intended master data).

---

## 12. Database Readiness

- Primary keys, foreign keys and unique constraints were reviewed.
- Authorship history is preserved through nullable user references (`ON DELETE SET NULL`).
- Hierarchy, activity and audit indexes are present.
- Refresh-token hash and expiry indexing is present.
- The Phase 4J partial activity index is retained unchanged.

Remaining concerns: L3, L5, L10.

---

## 13. Redis Readiness

Redis is used for login rate limiting only.

| Rule | Value |
| --- | --- |
| Per client IP | 100 attempts / 600 seconds |
| Per client IP + identifier | 8 failures / 900 seconds |

- The identifier is stored only as an HMAC; raw usernames/ALC codes never appear in keys.
- Key patterns: `login:v1:<scope>:ip:<client-ip>` and `login:v1:<scope>:pair:<client-ip>:<hmac>`.
- Every key has a TTL matching its window (confirmed on real Redis).
- A successful login clears the pair counter.
- The "Account unavailable" flow refunds the reserved failure slot.
- `portal` and `admin` scopes are counted separately.
- The fallback is bounded (least-recently-used, 10,000 keys by default).
- 429 responses include `Retry-After`.
- Redis client timeouts are 0.5 seconds; after a failure the fallback is used for 5 seconds
  before Redis is retried.

Redis persistence is not required for application data; losing Redis rate-limit keys merely
resets counters.

Opt-in real-Redis test (use a disposable Redis database number):

```
cd apps/api
LOGIN_RATE_LIMIT_TEST_REDIS_URL=redis://127.0.0.1:6379/15 python -m pytest -q tests/test_login_rate_limit.py
```

On the target server, also inspect live keys with `redis-cli --scan --pattern 'login:v1:*'` and
`redis-cli ttl <key>`.

---

## 14. Storage Readiness

| Item | Value |
| --- | --- |
| Backends | `local`, `s3` |
| Production backend | `s3` |
| Maximum files per activity | 10 |
| Maximum size per file | 10 MiB |
| Allowed evidence types | JPG, JPEG, PNG, WEBP, PDF |
| Validation | Extension, declared MIME type and file signature must all match |
| Storage keys | Server-generated |
| Presigned URL lifetime | 300 seconds |

Evidence that a reviewer has seen is retained in storage when an ALC removes it; evidence never
sent for review is deleted from storage.

The evidence bucket is stored outside PostgreSQL and requires independent backup.

Related findings: B1, M6, L1, L2.

---

## 15. Authentication / Session Readiness

No authentication blockers were identified.

| Area | Behaviour |
| --- | --- |
| Access token | 15 minutes |
| Refresh token | 14 days |
| Refresh tokens | Rotated on each use (atomic claim); SHA-256 hash stored in the database |
| Logout | Revokes the presented refresh token |
| Password change | Revokes the user's other sessions |
| Password reset | Revokes all sessions and requires a password change |
| Inactive accounts / hierarchy | Checked against the database on every request |
| CSRF | Double-submit token protection on state-changing routes |
| Cookies | Access and refresh cookies are `HttpOnly` and `SameSite=Lax`; `Secure` via `COOKIE_SECURE` |
| Admin vs operational authentication | Separate endpoints, guards and rate-limit scopes |

Related low-risk items: L6, L7, L8.

---

## 16. Role Authorization Matrix

Roles: `ADMIN`, `DCU`, `SBU`, `ALC`.

Hierarchy:

```
RCU
 -> DCU
   -> SBU
     -> ALC
```

Scope rules:

- **ADMIN** has access to all ALCs in the system. The current deployed hierarchy contains only
  RCU Pune, so this presently corresponds to all RCU Pune data. There is no explicit backend
  RCU Pune filter.
- **DCU** — its own DCU, that DCU's SBUs and their ALCs.
- **SBU** — its assigned ALCs.
- **ALC** — its own centre only.

Backend authorization is authoritative; frontend visibility is not relied on for access control.
Out-of-scope IDs return not-found or forbidden responses.

| Area | ADMIN | DCU | SBU | ALC |
| --- | --- | --- | --- | --- |
| Dashboard | Allowed | Scoped | Scoped | Scoped (own) |
| Users (create / edit / delete) | Allowed | Denied | Denied | Denied |
| Password resets for other users | Allowed | Scoped (ALC logins only) | Scoped (ALC logins only) | Denied |
| ALCs | Allowed (including status and import) | Scoped (read) | Scoped (read) | Denied |
| SBUs | Allowed (create / edit / assign DCU) | Scoped (read) | Scoped (own SBU) | Denied |
| DCUs | Allowed (read) | Denied | Denied | Denied |
| Activities — read | Allowed (no drafts) | Scoped (no drafts) | Scoped (no drafts) | Scoped (own, including drafts) |
| Activities — create / edit / submit | Denied | Denied | Denied | Scoped |
| Evidence — upload / delete | Denied | Denied | Denied | Scoped |
| Evidence — view | Allowed | Scoped | Scoped | Scoped |
| Reviews (verify / request correction / reject) | Allowed | Scoped | Scoped | Denied |
| Final-decision changes | Allowed | Scoped | Denied | Denied |
| Reports / exports | Allowed | Scoped | Scoped | Scoped (own) |
| Partners | Allowed (read) | Scoped (read) | Scoped (read) | Scoped (read / create / edit) |
| Audit log | Allowed | Denied | Denied | Denied |
| Reassignment (ALC → SBU, SBU → DCU) | Allowed | Denied | Denied | Denied |
| Profile / own password | Allowed | Allowed | Allowed | Allowed |
| Notifications | Denied (no admin endpoint) | Scoped (own) | Scoped (own) | Scoped (own) |
| Tasks / challenge | Challenge overview only | Denied | Denied | Scoped |

---

## 17. Frontend Readiness

- Routes are lazy-loaded.
- Route guards use `/auth/me`.
- Expired sessions use shared (single-flight) refresh handling, then redirect to login.
- A "Password change required" response redirects to the profile page.
- Static production output is written to `apps/web/dist`.
- The reverse proxy must provide an SPA `index.html` fallback so that deep-link refreshes work.
- The production frontend should use same-origin `/api`.
- Build with `VITE_API_URL=/api` (H2).

---

## 18. CORS / CSRF / Same-Origin

Recommended topology:

```
https://portal.example.com/       (React static build)
https://portal.example.com/api/   (FastAPI via reverse proxy)
```

Same-origin deployment is compatible with the current cookies (path `/`, `SameSite=Lax`,
`Secure`), the CSRF double-submit model, the frontend API client, login and refresh. It also
removes the need for cross-origin requests. Keep `CORS_ORIGINS` configured explicitly for the
portal origin.

---

## 19. Logging / Audit

The audit log currently covers areas such as:

- login
- failed login (client IP and user agent; the attempted identifier is not stored)
- review actions
- password resets
- evidence changes

Passwords, tokens and request bodies are not intentionally logged. Client IPs in the audit log
follow the same trusted-proxy rules as the rate limiter.

Gaps:

- Redis fallback is not logged (M2).
- 429 events are not explicitly audited.
- Production log destination, rotation and alerting are missing (M4).
- No retention policy exists for audit logs and related tables (L5).

---

## 20. Health Checks

Current: `GET /api/health` returns a static status only.

Required future production dependency checks:

- PostgreSQL
- Redis
- MinIO/S3

Until those are implemented, operational checks include:

```
pg_isready -h 127.0.0.1 -p 5432
redis-cli -h 127.0.0.1 ping
curl -f http://127.0.0.1:9000/minio/health/live
```

---

## 21. Backup Requirements

| Item | Backup required | Frequency | Restore test required |
| --- | --- | --- | --- |
| PostgreSQL | Yes | Nightly, and before every migration | Yes |
| Evidence / MinIO bucket | Yes (independent of PostgreSQL) | Nightly mirror or bucket versioning | Yes |
| `.env` / secrets | Yes (encrypted, offline) | After every change | Yes |
| Caddy / Nginx configuration | Yes | After every change | Yes |
| systemd unit files | Yes | After every change | Yes |
| Application source / release tags | Yes (Git tag per release) | Every release | No |
| Redis | No persistent backup required for login-limiter state | — | No |

### Example procedure — PROPOSED / NOT YET PRODUCTION-VALIDATED

Restore only into a new database, never over the live one.

```
# 1. Back up PostgreSQL
pg_dump -Fc -h 127.0.0.1 -p 5432 -U alc -d alc_growth -f alc_growth_$(date +%F_%H%M).dump
pg_restore --list alc_growth_<timestamp>.dump        # confirm the archive is readable

# 2. Create a staging copy
createdb -h 127.0.0.1 -p 5432 -O alc alc_growth_staging
pg_restore -h 127.0.0.1 -p 5432 --no-owner --role=alc -d alc_growth_staging alc_growth_<timestamp>.dump

# 3. Migrate the staging copy (from apps/api)
DATABASE_URL=postgresql+asyncpg://alc:<password>@127.0.0.1:5432/alc_growth_staging python -m alembic current
DATABASE_URL=postgresql+asyncpg://alc:<password>@127.0.0.1:5432/alc_growth_staging python -m alembic upgrade head
DATABASE_URL=postgresql+asyncpg://alc:<password>@127.0.0.1:5432/alc_growth_staging python -m alembic current
# expected: 20260930_0006 (head)

# 4. Back up the evidence bucket (MinIO client)
mc mirror --preserve <alias>/alc-evidence /backup/evidence/
```

`20260930_0006` uses `CREATE INDEX CONCURRENTLY`. If an upgrade is interrupted during that step,
re-running `alembic upgrade head` rebuilds the invalid index.

---

## 22. Production Network Topology

The current Windows demonstration environment (Vite development server, FastAPI/Uvicorn with
reload, local PostgreSQL and local services) is a development setup. It is not the production
architecture. Local development port choices on the Windows machine are development configuration
only and are not part of the production design.

Proposed Linux/Ubuntu office-server topology:

```
Users
  |
HTTPS :443
  |
Caddy / Nginx
  |-- React static build (apps/web/dist)
  |
  +-- /api
        |
        FastAPI 127.0.0.1:8000
        |
        +-- PostgreSQL 127.0.0.1:5432
        +-- Redis      127.0.0.1:6379
        +-- MinIO      127.0.0.1:9000
```

Production PostgreSQL remains private using its normal server configuration, typically
`127.0.0.1:5432`.

| Port | Exposure |
| --- | --- |
| 443 | Public (HTTPS) |
| 80 | Redirect to 443 only |
| 8000 (Uvicorn) | Loopback only |
| 5432 (PostgreSQL) | Loopback only |
| 6379 (Redis) | Loopback only |
| 9000 (MinIO S3 API) | Private (subject to the B1 decision) |
| 9001 (MinIO console) | Never public |
| 22 (SSH) | Restricted; administrator keys only |

---

## 23. Service Management

| Component | Recommendation |
| --- | --- |
| FastAPI | systemd service; single worker; `WorkingDirectory=apps/api`; `EnvironmentFile`; restart policy; enabled at boot |
| PostgreSQL | System service |
| Redis | System service |
| MinIO | System service |
| Frontend | Served statically by Caddy/Nginx |
| Migrations | Run once per release, not automatically from each application worker |

FastAPI command:

```
python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --no-proxy-headers
```

Do not use `--proxy-headers`, `--forwarded-allow-ips` or `FORWARDED_ALLOW_IPS`.

---

## 24. TLS / Domain Requirements

Decisions still needed:

- Portal hostname.
- Storage architecture, and a storage hostname if direct MinIO access is retained (B1).
- Internal-only versus internet-facing deployment.
- DNS ownership.
- Certificate model (public certificate authority such as Let's Encrypt for internet-facing
  deployments, or an internal certificate authority for internal-only deployments).
- HSTS consequences: `includeSubDomains` applies to subdomains of the chosen hostname.

---

## 25. UAT Checklist

### All roles

- [ ] Login works on the correct portal (`/admin` for ADMIN, `/portal` for DCU/SBU/ALC).
- [ ] Login on the incorrect portal is rejected.
- [ ] Rate limiting returns the 429 "try again later" message after repeated failures.
- [ ] Logout ends the session; the browser back button does not restore access.
- [ ] Forced password change after an admin or supervisor reset.
- [ ] Inactive account cannot sign in.
- [ ] Inactive hierarchy (ALC, SBU, DCU or RCU) shows "Account unavailable".
- [ ] Cross-scope URL/ID access is denied (edited IDs in URLs).
- [ ] Deep-link refresh loads the correct page.
- [ ] Concurrent browser tabs remain signed in after the 15-minute access-token expiry.

### ADMIN

- [ ] Dashboard totals.
- [ ] Users: search, pagination, filters, create (ALC / SBU / DCU accounts), deactivate, reset password.
- [ ] ALC and SBU directories with search, pagination and filters.
- [ ] Hierarchy reassignment (ALC → SBU, SBU → DCU).
- [ ] Review workflow: verify, request correction, reject, change a final decision.
- [ ] Evidence preview for images and PDFs.
- [ ] CSV export with filters.
- [ ] Audit log.
- [ ] No drafts appear in the admin review scope.

### DCU

- [ ] Scope isolation: only its own SBUs, ALCs, activities and partners.
- [ ] Review actions, including final-decision changes.
- [ ] Reports and CSV exports with filters.
- [ ] Password reset for an in-scope ALC login.
- [ ] Out-of-scope ALC/SBU/activity is denied.

### SBU

- [ ] Assigned-ALC scope only.
- [ ] Review actions (verify, request correction, reject).
- [ ] Cannot alter a final decision.
- [ ] Reports and CSV exports.
- [ ] Password reset for an assigned ALC login.
- [ ] Cross-SBU access is denied.

### ALC

- [ ] Create a draft.
- [ ] Edit a draft.
- [ ] Evidence upload (type, size and count limits) and delete.
- [ ] Submit (activity becomes locked).
- [ ] Correction and resubmission (previously reviewed evidence remains in history).
- [ ] Final status (verified / rejected) displayed.
- [ ] Notifications.
- [ ] Partners (create / edit).
- [ ] Tasks and challenge.
- [ ] Own reports / CSV exports.
- [ ] Supervisor routes are denied.

---

## 26. Production Deployment Checklist

1. [ ] Resolve the evidence architecture (B1).
2. [ ] Implement and test backup and restore (B2).
3. [ ] Decide repository visibility (H4).
4. [ ] Harden production startup configuration (H1).
5. [ ] Use non-default database, Redis and MinIO credentials where applicable.
6. [ ] Build the frontend with `VITE_API_URL=/api` (H2).
7. [ ] Configure the reverse-proxy body limit and SPA fallback.
8. [ ] Run Uvicorn as one worker on loopback with `--no-proxy-headers`.
9. [ ] Back up the production database and restore it into staging.
10. [ ] Run `alembic upgrade head` against staging and verify the head.
11. [ ] Validate real Redis.
12. [ ] Complete role-based UAT.
13. [ ] Restrict the firewall.
14. [ ] Perform a restore test.
15. [ ] Tag the release.

---

## 27. Recommended Phase 5B

| Priority | Workstream | Scope |
| --- | --- | --- |
| Phase 5B-1 | Production startup / configuration hardening | H1, H3, M6 |
| Phase 5B-2 | Evidence-storage production architecture | B1 (current project preference: Option C) |
| Phase 5B-3 | Health / logging improvements | M1, M2, M4 |
| Phase 5B-4 | Backup / restore scripts and validation | B2 |
| Phase 5B-5 | Production runbook | M5, sections 21–24 |

No Phase 5B changes were implemented as part of Phase 5A.

---

## 28. Mandatory Closing Summary

**WHAT WE REVIEWED**

Application startup, environment configuration, production-mode behaviour, secrets and Git
history, Alembic migrations, database schema, Redis and the login rate limiter, evidence storage,
upload limits, authentication and sessions, role authorization, frontend production behaviour,
CORS/CSRF, logging and audit, health checks, backup requirements, network topology, service
management, TLS/domain needs and UAT readiness — all at commit `efea81f`.

**WHY WE REVIEWED IT**

To determine readiness for production-like UAT, real Redis validation, a database migration dry
run, backup/restore validation and office-server deployment before any production work begins.

**WHAT PROBLEMS WERE FOUND**

- Two blockers: evidence access architecture (B1) and the absence of a tested backup and restore
  procedure (B2).
- Four high-risk items: production startup guard (H1), frontend API URL at build time (H2),
  trusted-proxy configuration (H3) and repository visibility (H4).
- Six medium-risk items (M1–M6), twelve low-risk items (L1–L12) and several informational notes.
- No real committed secrets.

**WHAT WAS NOT CHANGED**

Phase 5A made no application changes. No backend code, frontend code, migrations, tests,
environment or configuration files, or databases were modified.

**RESULT / READINESS STATUS**

Suitable for continued UAT. Ready for real Redis validation and a migration dry run on a restored
copy of the real database. **Not approved for office-server production deployment.**

**FOLLOW-UP ITEMS**

- Confirm the Phase 5B evidence architecture (current preference: Option C).
- Implement and test PostgreSQL and evidence backup and restore.
- Decide repository visibility.
- Harden production startup configuration.
- Prepare the production runbook.
- Execute Phase 5B in the priority order in section 27.
