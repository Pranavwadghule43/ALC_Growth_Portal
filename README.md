# ALC Growth Portal

An evidence-led growth and collaboration portal for Authorized Learning Centres. ALC users see only their own records; administrators review submitted evidence and make auditable verification, correction, or rejection decisions.

## Architecture

- `apps/web`: React 18, TypeScript, Vite, Tailwind, TanStack Query, React Hook Form, Zod, Recharts.
- `apps/api`: Python 3.11+, FastAPI, SQLAlchemy 2 async, Alembic, Argon2id, JWT access cookies, rotating opaque refresh tokens.
- PostgreSQL for transactional data; Redis for login throttling; private S3-compatible object storage (MinIO locally).
- The backend API is a separate service; the frontend is prepared for Vercel. Keep the API and frontend on the same registrable domain (for example `portal.example.org` and `api.example.org`) so SameSite cookies work.

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

The CSV must contain `ALC Code,ALC Name`. Codes remain strings, preserving leading zeroes. Rows are validated, whitespace is trimmed, duplicate codes in the file are reported, and existing codes are updated without creating duplicates. Use your real master CSV in place of the sample:

```powershell
cd apps/api
python -m scripts.import_alcs ../../data/ALC-MASTER.csv
```

The CLI reports imported/updated and invalid row counts. CSV import through the admin UI is not yet available.

## Workflow

1. An ALC creates a draft with non-negative metrics and an activity date no later than today.
2. Attach at least one JPG/JPEG, PNG, WEBP, or PDF. The default limit is 10 files and 10 MB per file. The API checks extension, declared MIME, and file signature; keys are generated server-side. Evidence stays private, and access URLs are short-lived.
3. Submit to lock editing. Admin reviews the queue, can open private evidence, then verifies, requests correction (reason required), or rejects (reason required).
4. Correction unlocks the activity for editing and resubmission. Every decision and submission snapshot is retained. Verified reach, leads, and admissions count in official performance metrics; draft or pending figures do not. Challenge partnership achievement requires a verified partnership activity linked to a partner.

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

The portal includes the core activity, review, ownership, dashboard, partner, task, challenge, user, and CSV reporting workflows. Some requested conveniences remain for a later hardening pass: admin CSV upload UI, XLSX exports, individual notification inbox, inline image thumbnails, full settings mutation, more granular performance reports, and broader integration tests. The admin challenge overview uses the rolling 30-day period, while ALC-specific challenge periods can be configured in the database. Do not label the remaining conveniences as complete features in a public rollout.
