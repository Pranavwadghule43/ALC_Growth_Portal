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
   server-side. Evidence stays private, and access URLs are short-lived.
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
python -m uvicorn app.main:app --reload --port 8000
```

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

See `.env.example` for all defaults. Important production variables:

| Variable | Purpose |
| --- | --- |
| `APP_ENV=production` | Disables API docs and enforces secure auth configuration. |
| `SECRET_KEY` | Long random JWT signing secret, not the development default. |
| `DATABASE_URL` | Managed PostgreSQL URL using `postgresql+asyncpg://`. |
| `REDIS_URL` | Managed Redis endpoint for login throttling. |
| `CORS_ORIGINS` | Comma-separated exact frontend origins, no wildcard with cookies. |
| `COOKIE_SECURE=true` | HTTPS-only cookies. Required in production. |
| `S3_ENDPOINT_URL` | Empty for AWS S3; set for MinIO or another S3-compatible provider. |
| `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Private evidence bucket credentials. |
| `VITE_API_URL` | Public API base URL ending in `/api`, configured in Vercel. |

Never expose database, Redis, storage, or JWT secrets in `VITE_*` frontend variables. The storage adapter does not rely on a persistent local disk.

## Tests and checks

```powershell
cd apps/api
python -m pytest -q
python -m ruff check app scripts tests

cd ../web
npm run lint
npm run build
```

The test suite uses isolated in-memory SQLite for fast security/workflow checks. Before public rollout, run a staging smoke test against real PostgreSQL and object storage, including upload, signed URL preview, and concurrent submissions.

## Production deployment

1. Create managed PostgreSQL, Redis, and a private S3-compatible bucket. Apply bucket lifecycle, encryption, and least-privilege policy. Do not enable public listing.
2. Deploy `apps/api` to a Python-capable host (for example Render, Fly.io, or a container platform) with HTTPS and environment variables above. Run `python -m alembic upgrade head` as a release migration, not from every web worker.
3. Create the first admin using a strong environment-supplied password through the one-off `python -m scripts.create_admin` command. Import the real ALC master and create unique users through Admin → Users.
4. Deploy the repository root to Vercel using `vercel.json`. Set `VITE_API_URL=https://api.example.org/api` in Vercel and `CORS_ORIGINS=https://portal.example.org` on the API. Ensure both origins share a registrable domain for SameSite cookie behavior.
5. Set `APP_ENV=production`, `COOKIE_SECURE=true`, a random `SECRET_KEY`, and production S3 credentials on the API. Configure health checks at `/api/health`. Review backups, retention, monitoring, error reporting, and provider quotas before launch.

Do not point production at the sample CSV, development passwords, local database, or MinIO default credentials.

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
object storage. The admin challenge overview uses the rolling 30-day period, while
ALC-specific challenge periods can be configured in the database. Do not label the remaining
conveniences as complete features in a public rollout. See `REMAINING-WORK.md` for the
developer-wise breakdown and branch status.
