# ALC Growth Portal

An evidence-led growth and collaboration portal for Authorized Learning Centres (ALCs). ALC
users record activities and submit supporting evidence; SBU users oversee the ALCs assigned
to their Strategic Business Unit; and administrators oversee everything. Every verification,
correction, and rejection decision is auditable, and only verified outcomes count toward
official performance.

## Roles and portals

| Role | Portal | Login identifier |
| --- | --- | --- |
| `ADMIN` (shown as "Super Admin") | `/admin` | username / email + password |
| `SBU` | `/portal` | username / email + password |
| `ALC` | `/portal` | ALC Code + password |

- **One common login** — there is no role selector. The backend authenticates the account
  and determines its role; the frontend never sends a trusted role.
- **ADMIN** has global access through the Super Admin portal at `/admin`.
- **SBU** and **ALC** share the operational portal at `/portal`; navigation and data are
  scoped by role.

### Data isolation

- **ALC isolation:** an ALC user sees only its own centre's activities, partners, tasks,
  evidence, and reports. Ownership is enforced server-side on every read and write.
- **SBU isolation:** an SBU user sees only the ALCs assigned to its SBU (`ALC.sbu_id`). Any
  attempt to reach another SBU's ALC, activity, or evidence — including by direct ID or URL
  — returns a not-found response.
- Scoping is enforced in the API and the database, never in the frontend.

### SBU ↔ ALC relationship

One SBU has many ALCs (`ALC.sbu_id`); each ALC belongs to at most one SBU. Admins assign and
reassign ALCs to SBUs. SBU users can monitor, verify, request correction on, or reject the
activities of their assigned ALCs, and reset those ALC users' passwords.

### Activity verification workflow

1. An ALC creates a draft with non-negative metrics and an activity date no later than today.
2. Attach at least one JPG/JPEG, PNG, WEBP, or PDF (default limit 10 files, 10 MB each). The
   API checks extension, declared MIME, and file signature; storage keys are generated
   server-side. Evidence stays private: browsers open it only through the authenticated API
   (see [Evidence delivery](#evidence-delivery-private-storage)).
3. The ALC submits, which locks editing. An SBU (for its assigned ALCs) or an ADMIN reviews
   the queue, opens the private evidence, then **verifies**, **requests correction** (reason
   required), or **rejects** (reason required).
4. Correction unlocks the activity for editing and resubmission. Every decision and
   submission snapshot is retained. Only verified reach, leads, and admissions count in
   official performance metrics; draft or pending figures do not. Challenge partnership
   achievement requires a verified partnership activity linked to a partner.

## Architecture

- `apps/web`: React, TypeScript, Vite, Tailwind, TanStack Query, React Hook Form, Zod,
  Recharts.
- `apps/api`: Python 3.11+, FastAPI, SQLAlchemy 2 (async), Alembic, Argon2id password
  hashing, JWT access cookies, and rotating opaque refresh tokens.
- **PostgreSQL** for transactional data; **Redis** for login throttling; local evidence
  storage for development and **MinIO / S3-compatible** private object storage for production.
- The backend API is a separate service; the frontend is prepared for Vercel. Keep the API
  and frontend on the same registrable domain (for example `portal.example.org` and
  `api.example.org`) so SameSite cookies work.

> **Status note:** the common login, the SBU role, and the unified `/portal` experience are
> implemented on the `feature/common-login` and `feature/sbu-operational-portal` branches and
> are not yet merged into `main`. `main` still carries the earlier role-selector login and
> `/alc` routing. See `REMAINING-WORK.md` for the exact branch and completion status.

## Requirements

Python 3.11+, Node.js 20+, npm or pnpm, Docker Compose. PowerShell commands are shown below; on macOS/Linux use `cp` instead of `Copy-Item` and the appropriate activation path.

## Local setup

```powershell
Copy-Item .env.example .env
```

Edit `.env` with unique local passwords and a long random `SECRET_KEY`. The example MinIO and database credentials are development-only. Then start dependencies:

```powershell
docker compose up -d
```

Create a Python environment and install the backend:

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m alembic upgrade head
python -m scripts.import_alcs ../../data/ALC-MASTER.csv
$env:DEV_ADMIN_PASSWORD = "your-unique-12-plus-character-password"
python -m scripts.create_admin --username admin
$env:DEV_ALC_PASSWORD = "another-unique-12-plus-character-password"
python -m scripts.create_alc_users
python -m uvicorn app.main:app --reload --port 8000 --no-proxy-headers
```

`--no-proxy-headers` is required in every environment; see
[Reverse proxy and client IP](#reverse-proxy-and-client-ip).

The development ALC user creator only creates accounts for ALCs lacking one. These accounts require a password change. Do not use shared development credentials in production. For production, use the Admin → Users page to create individual ALC users with unique temporary passwords.

In another terminal:

```powershell
cd apps/web
npm install
npm run dev
```

Open `http://localhost:5173`. The ALC login uses the imported `ALC Code` and the development ALC password. Admin login uses `admin` and the admin password. API docs are available at `http://localhost:8000/docs` in development.

### ALC master import

The importer currently reads `ALC Code,ALC Name`. Codes remain strings, preserving leading
zeroes. Rows are validated, whitespace is trimmed, duplicate codes in the file are reported,
blank/invalid codes are skipped, and existing codes are updated without creating duplicates.
Existing ALCs missing from a later import are never deleted, and passwords are never created
or reset by the import. Use your real master CSV in place of the sample:

```powershell
cd apps/api
python -m scripts.import_alcs ../../data/ALC-MASTER.csv
```

The CLI reports imported/updated and invalid row counts. CSV import through the admin UI is
not yet available.

The real master import will read only `ALC Code`, `ALC Name`, and `SBU` (no Taluka, Mobile,
Email, Area Type, RCU, or DCU); `SBU` handling and the admin upload UI are still in progress
— see `REMAINING-WORK.md`.

## Environment variables

`.env.example` holds the **development** defaults; `.env.production.example` is the production
template (placeholders only — never commit real values). The API reads its variables from the
process environment or a `.env` file; the frontend reads `VITE_*` variables at build time.

`APP_ENV` must be exactly `development`, `test` or `production`. Any other value (including a
typo such as `prodution` or `Production`) stops the API at startup instead of silently running
as development. `STORAGE_BACKEND` must be `s3` or `local` in every environment.

### Production environment variables

With `APP_ENV=production` the API validates its whole critical configuration before it starts.
Anything missing, left at a development default, a placeholder (`change-me`, `example`,
`minioadmin`, …) or otherwise unsafe stops startup with a `ConfigurationError` that lists the
offending **keys** — secret values are never printed. A value counts as configured only when it
is set explicitly (environment variable or `.env`); built-in development defaults never pass.

| Variable | Production rule |
| --- | --- |
| `APP_ENV` | `production`. Disables `/docs`, `/redoc` and `/openapi.json`. |
| `SECRET_KEY` | Required. At least 32 characters, at least 10 distinct characters, not a default/example/placeholder. Signs JWTs and keys the login-limiter counters. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `COOKIE_SECURE` | Must be `true`: auth cookies are `Secure`, HttpOnly (access/refresh) and `SameSite=Lax`, path `/`, so they are sent only over HTTPS. Secure cookies and HSTS are separate controls: HSTS is configured and verified in the HTTPS reverse-proxy deployment phase. |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | Between 1–60 minutes and 1–30 days (defaults 15 and 14). |
| `DATABASE_URL` | Required. `postgresql+asyncpg://…` to a private/loopback PostgreSQL (normally port 5432 on the server); must not use the development password. The port is not fixed by the app (local development may use 55432). |
| `REDIS_URL` | Required. `redis://`, `rediss://` or `unix://` URL to a private/loopback Redis (login rate limiting). |
| `TRUSTED_PROXY_CIDRS` | Required. The reverse proxy address(es), normally `127.0.0.1/32`. Ranges broader than /8 (IPv4) or /32 (IPv6), such as `0.0.0.0/0`, are rejected. Uvicorn must run with `--no-proxy-headers`. |
| `CORS_ORIGINS` | Empty for the same-origin deployment (recommended). Any listed origin must be an exact `https://` origin — no `*`, no localhost, no path. |
| `STORAGE_BACKEND` | Must be `s3` (local disk storage is development-only). |
| `S3_ENDPOINT_URL` | `http(s)://` URL of the private MinIO/S3-compatible endpoint, or empty for AWS S3. Server-side only: FastAPI connects to it (for example `http://127.0.0.1:9000`); browsers never do. |
| `S3_REGION`, `S3_BUCKET` | Bucket is required (keep it private, no anonymous access). |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Required, not `minioadmin`/placeholders; secret at least 16 characters. Use a least-privilege service account, not the MinIO root user. |
| `S3_PRESIGN_SECONDS` | 1–3600 (default 300). Evidence is not viewed through presigned URLs (it is streamed by the API). |
| `LOGIN_IP_LIMIT`, `LOGIN_IP_WINDOW_SECONDS`, `LOGIN_PAIR_FAILURE_LIMIT`, `LOGIN_PAIR_FAILURE_WINDOW_SECONDS`, `LOGIN_FALLBACK_MAX_KEYS` | Login rate limiting (bounded in every environment). The window variable is `LOGIN_PAIR_FAILURE_WINDOW_SECONDS`; the misspelling `LOGIN_PAIR_WINDOW_SECONDS` is rejected in production. |
| `MAX_UPLOAD_FILES`, `MAX_UPLOAD_BYTES`, `PASSWORD_HASH_CONCURRENCY` | Optional tuning (defaults 10, 10 MiB, 2). |
| `LOG_LEVEL` | Optional. `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` (default `INFO`); any other value stops startup. Use `INFO` in production. The log **format** is chosen by `APP_ENV`, not by this variable. |
| `VITE_API_URL` (frontend build) | `/api` for the same-origin deployment. A production build fails if it points to `http://`, localhost or `127.0.0.1`; if unset, a production build uses `/api` (development uses `http://localhost:8000/api`). |

Never expose database, Redis, storage, or JWT secrets in `VITE_*` frontend variables. The storage adapter does not rely on a persistent local disk.

### Production startup model (same-origin)

Documented for the later deployment phase; nothing here is deployed by the repository.

```text
Browser --HTTPS :443--> reverse proxy --+-- /       -> built React files (apps/web/dist)
                                        +-- /api/*  -> Uvicorn 127.0.0.1:8000 -> FastAPI
FastAPI -> PostgreSQL (private/loopback), Redis (private/loopback), MinIO/S3 (private bucket)
```

#### Evidence delivery (private storage)

```text
Browser --HTTPS (session cookies)--> reverse proxy --/api--> FastAPI --private connection--> MinIO/S3
```

- Object storage stays private: no public bucket, no anonymous access, and the MinIO/S3 port and
  console are never exposed to users. `S3_ENDPOINT_URL` is used by FastAPI only.
- Browsers open evidence through the authenticated API —
  `GET /api/portal/evidence/{id}/content` (DCU / SBU / ALC) and
  `GET /api/admin/evidence/{id}/content` (ADMIN). The API applies the same activity scope as the
  rest of the portal (unauthorized or unknown evidence is `404`), then streams the object from
  storage in chunks. The `.../access` check returns that application URL, never a storage URL.
- Responses never include the bucket, object key, filesystem path or storage endpoint; they are
  sent with `Cache-Control: private, no-store`.
- The reverse proxy only has to expose the application over HTTPS (`/` and `/api`); it needs no
  route to MinIO/S3.

```bash
# Frontend (build once per release)
cd apps/web
VITE_API_URL=/api npm run build            # PowerShell: $env:VITE_API_URL = "/api"; npm run build

# Backend (environment from .env.production.example, with real values)
cd apps/api
APP_ENV=production python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

## Tests and checks

```powershell
cd apps/api
python -m pytest -q
python -m ruff check app scripts tests

cd ../web
npm run lint
npm run build
```

The test suite uses isolated in-memory SQLite for fast security/workflow checks. Before public rollout, run a staging smoke test against real PostgreSQL and object storage, including upload, evidence preview through the API, and concurrent submissions.

## Production deployment

1. Create managed PostgreSQL, Redis, and a private S3-compatible bucket. Apply bucket lifecycle, encryption, and least-privilege policy. Do not enable public listing.
2. Deploy `apps/api` to a Python-capable host (for example Render, Fly.io, or a container platform) with HTTPS and environment variables above. Run `python -m alembic upgrade head` as a release migration, not from every web worker.
3. Create the first admin using a strong environment-supplied password through the one-off `python -m scripts.create_admin` command. Import the real ALC master and create unique users through Admin → Users.
4. Preferred: serve the built frontend and `/api` from one origin (see *Production startup model* above, `VITE_API_URL=/api`, `CORS_ORIGINS` empty). Alternative: deploy the repository root to Vercel using `vercel.json`. Set `VITE_API_URL=https://api.example.org/api` in Vercel and `CORS_ORIGINS=https://portal.example.org` on the API. Ensure both origins share a registrable domain for SameSite cookie behavior.
5. Set every variable in *Production environment variables* (the API refuses to start otherwise). Point uptime/monitoring checks at `/api/health/ready` and process-restart checks at `/api/health/live` (see *Health checks, request IDs and logging*). Review backups, retention, monitoring, error reporting, and provider quotas before launch.

Do not point production at the sample CSV, development passwords, local database, or MinIO default credentials.

### Health checks, request IDs and logging

Two public, unauthenticated, read-only endpoints (no deployment or monitoring platform is
configured by this repository):

| Endpoint | Meaning | Result |
| --- | --- | --- |
| `GET /api/health/live` | **Liveness** — the API process is running and answering. It does not touch PostgreSQL, Redis or storage, so an outage of those never makes it fail. `GET /api/health` is kept as an alias. | Always `200` `{"status":"ok"}` while the process is up. |
| `GET /api/health/ready` | **Readiness** — the API can serve normal traffic: PostgreSQL answers `SELECT 1`, Redis answers `PING`, and the evidence bucket answers `HeadBucket`. | `200` `{"status":"ready","checks":{"database":"ok","redis":"ok","storage":"ok"}}`, or `503` `{"status":"not_ready","checks":{…"error"…}}` when any check fails. |

- Use **live** to decide "restart the process?" and **ready** to decide "send traffic / raise
  an alert?". A process manager or reverse proxy may poll them; the reverse proxy only needs
  to pass `/api/health/*` through like the rest of `/api/*`.
- The responses are intentionally minimal: only the words `ok`, `error`, `ready` and
  `not_ready`. They never contain the environment, a version, hosts, URLs, the bucket name,
  credentials or error text. The reason for a failure is in the server log
  (`readiness_check_failed` with the check name and the exception type).
- Each check runs once per request with a 3 second limit: no retries, no caching, nothing is
  written, uploaded, listed or deleted.
- Redis: while Redis is down the login limiter keeps protecting logins from a bounded
  in-process fallback, so the application is *degraded* but still usable. Readiness still
  reports `"redis":"error"` and `503`, because Redis is a required production dependency.

**Request ID.** Every response carries an `X-Request-ID` header (32 hex characters) generated
by the server; an `X-Request-ID` sent by the client is ignored. The same value is on every
log line written for that request, so a user can quote it and the matching log lines can be
found. It is random and contains no user data.

**Logging.** Logs go to stderr. With `APP_ENV=production` each line is one JSON object;
in development and test the lines are human-readable. Each request produces one
`request_completed` line:

```json
{"event":"request_completed","request_id":"…","method":"GET","path":"/api/portal/dashboard","status_code":200,"duration_ms":18.4,"client_ip":"203.0.113.7","user_id":"…","role":"ALC","level":"info","logger":"app.request","timestamp":"2026-10-05T06:35:26.464807Z"}
```

- Logged: request id, method, path **without the query string**, status, duration, client IP
  (resolved with the `TRUSTED_PROXY_CIDRS` rules below) and, for signed-in requests, the user
  id and role. Login events: `login_succeeded`, `login_failed`, `login_refused`,
  `login_rate_limited`.
- Never logged: passwords, the typed login identifier, cookies, JWTs, refresh or CSRF tokens,
  the `Authorization` header, request or response bodies, uploaded files, query strings, or
  any configuration secret/URL. Fields with sensitive names are replaced by `[REDACTED]` as a
  safety net.
- An unexpected error returns a generic `500` (`INTERNAL_ERROR`) with the `X-Request-ID`; the
  exception class and stack trace are written to the server log only (`unhandled_error`).
- This line replaces Uvicorn's own access log, which would print full URLs including query
  strings. Successful health probes are logged at `DEBUG`, so they are hidden at the default
  `INFO`; failed readiness is logged at `WARNING`.
- `LOG_LEVEL=DEBUG` is for short investigations only. Third-party libraries (S3 client,
  database drivers, HTTP clients) stay at `WARNING` even then, because their debug output can
  contain signed headers, URLs and SQL values.

### Reverse proxy and client IP

Login rate limiting counts attempts per client IP. The application decides which forwarding
headers (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`) to believe, and it is the **only**
component allowed to make that decision:

**When application-level `TRUSTED_PROXY_CIDRS` is used, Uvicorn proxy-header processing must
be disabled with `--no-proxy-headers`.**

Uvicorn enables its own proxy-header processing by default and trusts loopback (`127.0.0.1`,
and `::1` in newer versions). If it is left on, Uvicorn replaces the connecting peer address
with a value from `X-Forwarded-For` before the application runs, so the application can no
longer check that the request really came from a trusted proxy. Do not use `--proxy-headers`,
`--forwarded-allow-ips`, or the `FORWARDED_ALLOW_IPS` environment variable to implement the
trust boundary.

Expected production topology (reverse proxy on the same host):

```text
client -> Nginx / Caddy (public HTTPS) -> Uvicorn on 127.0.0.1:8000 -> FastAPI (TRUSTED_PROXY_CIDRS)
```

```bash
# API environment (.env)
TRUSTED_PROXY_CIDRS=127.0.0.1/32
```

```bash
python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --no-proxy-headers
```

- The application never trusts localhost or private ranges on its own; trust comes only from
  `TRUSTED_PROXY_CIDRS`. It is required with `APP_ENV=production` (the API always sits behind
  the reverse proxy); leave it empty only in development when no proxy is in front of the API.
- The proxy must append the real peer to `X-Forwarded-For` (Nginx:
  `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`; Caddy's `reverse_proxy`
  does this by default). The application reads the chain from the right, so values a client
  puts in the header itself are never chosen over the address the proxy saw.
- On a platform whose proxy is not on loopback, set `TRUSTED_PROXY_CIDRS` to that proxy's
  address range instead, and still run Uvicorn with `--no-proxy-headers`.
- Bind Uvicorn to `127.0.0.1` so clients cannot bypass the proxy and connect directly.

## Current limitations

The portal includes the core activity, review, ownership, dashboard, partner, task,
challenge, user, and CSV reporting workflows, plus an SBU operational layer (SBU role, SBU
model, `sbu_id` relations, scoped `/portal` experience, verification, and password reset for
assigned ALCs) that lives on `feature/sbu-operational-portal` and is not yet merged into
`main`. The SBU backend is substantially complete and tested; the SBU frontend is functional
but still needs a refinement pass. Several conveniences remain for a later hardening pass:
the real master import reading `SBU`, the admin CSV upload UI, XLSX exports, an individual
notification inbox, inline image thumbnails and evidence preview, full settings mutation,
more granular performance reports, and broader integration tests against PostgreSQL and
object storage. The Growth Challenge runs for a configured period of any length (for example
15, 30, 45, 60 or 90 days). An Admin sets the name, start date, end date and targets on the
Admin Growth Challenge page; that one global period (`growth_challenges`) applies to every
ALC, including ALCs added later. An ALC with its own `challenge_progress` row uses that as
an override. Both the ALC and Admin views measure progress over the period that applies, and
show "not configured" when there is none (there is no rolling 30-day fallback). Challenge
dates are evaluated in the Asia/Kolkata time zone, whatever the server's time zone is. Do not
label the remaining conveniences as complete features in a public rollout. See
`REMAINING-WORK.md` for the developer-wise breakdown and branch status.
