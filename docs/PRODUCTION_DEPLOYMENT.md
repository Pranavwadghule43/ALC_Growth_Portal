# ALC Growth Portal — Production Deployment Runbook

## 1. Purpose

This document defines the approved production deployment procedure for the ALC Growth Portal on the office Ubuntu server.

It covers:

- Ubuntu server preparation
- PostgreSQL
- Redis
- private MinIO/S3-compatible object storage
- FastAPI
- React production build
- systemd
- Nginx
- HTTPS
- firewall
- production secrets
- Alembic migrations
- health checks
- logging
- backups
- release procedure
- rollback procedure
- disaster recovery
- production verification

This runbook does **not** perform deployment automatically.

---

# 2. Approved Production Architecture

```text
Internet / Office Users
        |
      HTTPS
        |
        v
+-----------------------+
| Nginx                 |
| Public: 80 / 443      |
+-----------------------+
       |          |
       | /        | /api/*
       v          v
React static     FastAPI
files            127.0.0.1:8000
                    |
          +---------+---------+
          |         |         |
          v         v         v
     PostgreSQL   Redis     MinIO
     localhost    localhost private
```

Production requirements:

- Nginx is the only public application service.
- FastAPI binds only to `127.0.0.1:8000`.
- PostgreSQL must not be publicly accessible.
- Redis must not be publicly accessible.
- MinIO must not be publicly accessible.
- MinIO evidence must continue to be delivered through authenticated FastAPI endpoints.
- Browser clients must never connect directly to MinIO.
- `VITE_API_URL=/api`.
- Same-origin deployment is preferred.
- `CORS_ORIGINS` should therefore remain empty.
- Uvicorn must run with `--no-proxy-headers`.
- `TRUSTED_PROXY_CIDRS=127.0.0.1/32`.

---

# 3. Production Server Preconditions

Before beginning Phase 6, confirm:

- Ubuntu server is patched and supported.
- Server has a stable LAN IP.
- Public deployment has a static public IP or suitable DNS/NAT arrangement.
- The chosen domain resolves to the production server.
- TCP 80 and 443 can reach the server.
- SSH access is confirmed before firewall rules are enabled.
- Server clock/time synchronization is working.
- Adequate disk capacity is available for:
  - application files
  - PostgreSQL
  - MinIO
  - logs
  - backups
- Off-server backup storage is available or planned.

Record:

```text
Production domain:
Server public IP:
Server private/LAN IP:
Ubuntu version:
Python version:
Node version:
PostgreSQL version:
Redis version:
MinIO version:
Nginx version:
Deployment commit:
Deployment date:
Operator:
```

Do not deploy from an unrecorded Git commit.

---

# 4. Recommended Filesystem Layout

Use:

```text
/opt/alc-growth/
    releases/
        <git-commit>/
    current -> releases/<git-commit>

/etc/alc-growth/
    alc-growth.env

/var/backups/alc-growth/

/var/log/
    handled primarily through systemd journal
```

Application service user:

```text
alcportal
```

Do not run FastAPI as `root`.

---

# 5. Base Ubuntu Packages

During Phase 6 install the required operating-system packages.

Typical requirements:

```bash
sudo apt update
sudo apt upgrade

sudo apt install \
  git \
  curl \
  ca-certificates \
  nginx \
  postgresql \
  redis-server \
  python3 \
  python3-venv \
  python3-pip \
  build-essential
```

Before continuing:

```bash
python3 --version
psql --version
redis-server --version
nginx -v
```

Python must satisfy the application's Python 3.11+ requirement.

Use a supported Node.js LTS release for the React production build.

---

# 6. Dedicated Application User

Create a non-login or restricted service account:

```bash
sudo adduser \
  --system \
  --group \
  --home /opt/alc-growth \
  alcportal
```

Create application directories:

```bash
sudo mkdir -p /opt/alc-growth/releases
sudo mkdir -p /etc/alc-growth
sudo mkdir -p /var/backups/alc-growth
```

Set appropriate ownership and permissions.

Production secrets must not be committed to Git.

---

# 7. PostgreSQL

PostgreSQL is the authoritative transactional database.

Production should normally use loopback/private networking only.

Example target:

```text
Host: 127.0.0.1
Port: 5432
Database: alc_growth
Application role: alc_growth_app
```

Create a dedicated application role and database.

Do not use the PostgreSQL superuser from the application.

Example:

```sql
CREATE ROLE alc_growth_app LOGIN;
CREATE DATABASE alc_growth OWNER alc_growth_app;
```

Assign a strong unique password interactively.

Production `DATABASE_URL` will have the form:

```text
postgresql+asyncpg://alc_growth_app:<URL-ENCODED-PASSWORD>@127.0.0.1:5432/alc_growth
```

Do not use:

- the Windows development database
- port 55432 assumptions
- development passwords
- PostgreSQL superuser credentials

The Windows development PostgreSQL configuration is not the production configuration.

---

# 8. Redis

Redis is required for production login throttling.

Bind Redis only to loopback/private networking.

Recommended:

```text
127.0.0.1:6379
```

Keep:

```text
protected-mode yes
```

Authentication should be enabled where practical.

Example application setting:

```text
REDIS_URL=redis://:<PASSWORD>@127.0.0.1:6379/0
```

Never expose TCP 6379 to the public internet.

A Redis outage causes `/api/health/ready` to report not-ready even though the bounded in-process login limiter fallback continues protecting authentication.

---

# 9. Private MinIO

Production evidence storage uses MinIO/S3-compatible storage.

Recommended local endpoints:

```text
S3 API:
127.0.0.1:9000

MinIO administration console:
127.0.0.1:9001
```

Do not expose either port through the public firewall or Nginx.

If console access is needed, use controlled administrative access such as an SSH tunnel.

Create a dedicated private bucket:

```text
alc-evidence
```

Create a dedicated application service account.

Do not use:

```text
minioadmin
```

and do not use MinIO root credentials from FastAPI.

The application service account should receive only the permissions required for its evidence bucket, such as:

```text
Bucket:
- bucket metadata/readiness permission

Objects:
- GetObject
- PutObject
- DeleteObject
```

No anonymous/public access.

Recommended application configuration:

```text
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_REGION=us-east-1
S3_BUCKET=alc-evidence
S3_ACCESS_KEY=<SERVICE-ACCOUNT-ACCESS-KEY>
S3_SECRET_KEY=<SERVICE-ACCOUNT-SECRET>
S3_CONNECT_TIMEOUT_SECONDS=1
S3_READ_TIMEOUT_SECONDS=2
S3_MAX_ATTEMPTS=2
```

Evidence remains:

```text
Browser
   |
 HTTPS
   |
 FastAPI
   |
 private S3 connection
   |
 MinIO
```

Never change this into:

```text
Browser -> public MinIO URL
```

---

# 10. Production Environment File

Use:

```text
/etc/alc-growth/alc-growth.env
```

Start from the repository's:

```text
.env.production.example
```

Replace every placeholder with real production values.

Critical values include:

```text
APP_ENV=production
LOG_LEVEL=INFO

SECRET_KEY=<GENERATED-SECRET>

COOKIE_SECURE=true

DATABASE_URL=<PRODUCTION-DATABASE-URL>
REDIS_URL=<PRODUCTION-REDIS-URL>

TRUSTED_PROXY_CIDRS=127.0.0.1/32
CORS_ORIGINS=

STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_REGION=us-east-1
S3_BUCKET=alc-evidence
S3_ACCESS_KEY=<SERVICE-ACCOUNT-KEY>
S3_SECRET_KEY=<SERVICE-ACCOUNT-SECRET>

S3_CONNECT_TIMEOUT_SECONDS=1
S3_READ_TIMEOUT_SECONDS=2
S3_MAX_ATTEMPTS=2
```

Generate the application secret with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Recommended permissions:

```bash
sudo chown root:alcportal /etc/alc-growth/alc-growth.env
sudo chmod 640 /etc/alc-growth/alc-growth.env
```

Never:

- commit this file
- paste it into issue trackers
- place secrets in frontend `VITE_*` variables
- store secrets inside Nginx configuration

---

# 11. Release Checkout

Each production release should correspond to an exact Git commit.

Example:

```bash
cd /opt/alc-growth/releases

sudo -u alcportal git clone <REPOSITORY-URL> <COMMIT-OR-RELEASE-DIRECTORY>
```

Checkout the intended release commit.

Verify:

```bash
git rev-parse HEAD
git status --short
```

Working tree must be clean.

Record the commit SHA in the deployment log.

---

# 12. Python Environment

Inside:

```text
apps/api
```

create the release-specific virtual environment:

```bash
python3 -m venv .venv
```

Then install the production application dependencies.

Do not install development/debugging tooling unless it is operationally required.

Verify import/startup before systemd activation.

---

# 13. React Production Build

Production frontend configuration:

```text
VITE_API_URL=/api
```

Build:

```bash
cd apps/web

VITE_API_URL=/api npm run build
```

The output is:

```text
apps/web/dist
```

Never compile production frontend with:

```text
http://localhost:8000/api
```

or a development API URL.

---

# 14. Pre-Release Backup

Before every release containing:

- database migrations
- schema changes
- significant application changes

take a verified backup.

Use the existing Phase 5B-4 backup tooling.

The release must not continue if the required backup fails verification.

Backup should include:

- PostgreSQL
- evidence object storage
- manifest
- SHA256 checksums

Recommended operational targets:

```text
RPO <= 24 hours
RTO <= 4 hours
```

These are operational targets, not contractual SLAs.

---

# 15. Alembic Migration Procedure

Run Alembic once as a release operation.

Never run migrations automatically from every Uvicorn worker.

Before migration:

```bash
python -m alembic current
python -m alembic heads
```

Then:

```bash
python -m alembic upgrade head
```

Verify:

```bash
python -m alembic current
```

Record the resulting revision.

If migration fails:

- do not start the new application release
- preserve logs
- determine whether migration was transactional
- follow rollback/disaster-recovery procedure as appropriate

Never manually alter production tables simply to force a migration to succeed.

---

# 16. FastAPI systemd Service

Recommended service:

```ini
[Unit]
Description=ALC Growth Portal API
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
Type=simple

User=alcportal
Group=alcportal

WorkingDirectory=/opt/alc-growth/current/apps/api

EnvironmentFile=/etc/alc-growth/alc-growth.env

ExecStart=/opt/alc-growth/current/apps/api/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers

Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

UMask=0027

[Install]
WantedBy=multi-user.target
```

Install as:

```text
/etc/systemd/system/alc-growth-api.service
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable alc-growth-api
sudo systemctl start alc-growth-api
```

Verify:

```bash
sudo systemctl status alc-growth-api
```

FastAPI must listen only on:

```text
127.0.0.1:8000
```

Do not bind it publicly to:

```text
0.0.0.0:8000
```

---

# 17. Application Logs

Production application logs are structured JSON written to stderr and therefore captured by systemd journal.

View recent logs:

```bash
sudo journalctl -u alc-growth-api -n 100
```

Follow logs:

```bash
sudo journalctl -u alc-growth-api -f
```

Every application response includes a server-generated:

```text
X-Request-ID
```

Use that ID when investigating user-reported failures.

Do not enable verbose third-party S3/database HTTP debug logging in production.

Default:

```text
LOG_LEVEL=INFO
```

---

# 18. Nginx

Nginx provides:

- public HTTPS
- React static files
- `/api/*` reverse proxy
- real client IP forwarding

Example configuration:

```nginx
server {
    listen 80;
    server_name <DOMAIN>;

    root /opt/alc-growth/current/apps/web/dist;
    index index.html;

    client_max_body_size 110m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;

        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

The 110 MB request limit allows the application-level default maximum of up to ten 10 MB evidence files plus multipart overhead.

Do not create Nginx routes for:

```text
:5432 PostgreSQL
:6379 Redis
:9000 MinIO
:9001 MinIO Console
```

Validate before reload:

```bash
sudo nginx -t
```

Then:

```bash
sudo systemctl reload nginx
```

---

# 19. Proxy Trust Boundary

The application—not Uvicorn—decides whether forwarded client-IP headers are trusted.

Required:

```text
TRUSTED_PROXY_CIDRS=127.0.0.1/32
```

Required Uvicorn option:

```text
--no-proxy-headers
```

Nginx must set:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP $remote_addr;
```

Never enable Uvicorn proxy-header processing at the same time.

---

# 20. HTTPS

Production must use HTTPS before users are permitted to log in.

The domain must resolve correctly before certificate issuance.

After TLS is configured verify:

```text
https://<DOMAIN>/
https://<DOMAIN>/api/health/live
https://<DOMAIN>/api/health/ready
```

HTTP should redirect to HTTPS.

Cookies require:

```text
COOKIE_SECURE=true
```

Do not enable HSTS until HTTPS has been fully validated.

After validation, add HSTS deliberately.

Recommended initial production header:

```text
Strict-Transport-Security: max-age=31536000
```

Only add:

```text
includeSubDomains
```

after confirming every relevant subdomain is HTTPS-only.

Do not enable browser preload during the initial deployment.

---

# 21. Firewall

Before enabling UFW, verify working SSH access.

Recommended public rules:

```text
22/tcp    SSH
80/tcp    HTTP / certificate redirect
443/tcp   HTTPS
```

Do not permit publicly:

```text
5432
6379
8000
9000
9001
```

Verify actual listening sockets using:

```bash
sudo ss -lntp
```

Expected public application listeners:

```text
0.0.0.0:80
0.0.0.0:443
```

Expected private listeners include:

```text
127.0.0.1:8000
127.0.0.1:5432
127.0.0.1:6379
127.0.0.1:9000
127.0.0.1:9001
```

depending on installed service configuration.

---

# 22. Health Checks

### Liveness

```text
GET /api/health/live
```

Expected:

```json
{"status":"ok"}
```

Purpose:

```text
Is the FastAPI process alive?
```

It intentionally does not check PostgreSQL, Redis, or storage.

### Readiness

```text
GET /api/health/ready
```

Healthy:

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "storage": "ok"
  }
}
```

Dependency failure returns HTTP 503.

Use readiness for:

- uptime monitoring
- dependency outage alerts
- deployment verification

Do not automatically restart FastAPI merely because PostgreSQL, Redis, or MinIO is temporarily unavailable.

---

# 23. Production Smoke Test

Before opening production to all users verify:

### Infrastructure

- PostgreSQL reachable.
- Redis reachable.
- MinIO reachable only privately.
- Nginx healthy.
- FastAPI service healthy.
- HTTPS valid.
- HTTP redirects to HTTPS.

### Application

Test:

- ADMIN login
- DCU login
- SBU login
- ALC login
- role authorization
- Growth Challenge
- Region Top 10
- All ALC Scores
- activities
- partners
- tasks
- reports

### Evidence

Perform a real MinIO test:

1. Login as an ALC.
2. Create activity.
3. Upload evidence.
4. Submit activity.
5. Login as authorized reviewer.
6. Open evidence.
7. Confirm browser retrieves it through `/api/.../evidence/.../content`.
8. Confirm MinIO URL is never exposed to the browser.
9. Verify correction/resubmission.
10. Verify activity.

---

# 24. Storage Failure Test

In staging or a controlled maintenance window:

1. Confirm `/api/health/ready` is healthy.
2. Temporarily make MinIO unavailable.
3. Call `/api/health/live`.

Expected:

```text
200
```

4. Call `/api/health/ready`.

Expected:

```text
503
storage = error
```

5. Attempt evidence access.

Expected:

```text
503
Evidence is temporarily unavailable
```

6. Confirm:
   - MinIO endpoint is not leaked.
   - bucket name is not leaked.
   - object key is not leaked.
   - credentials are not leaked.

7. Restore MinIO.

8. Confirm readiness returns to `200`.

---

# 25. Backup Scheduling

Use the Phase 5B-4 backup tooling and supplied systemd timer examples.

Production backups should cover:

- PostgreSQL
- private object storage
- backup manifest
- SHA256 verification

Recommended retention:

```text
Daily:   7
Weekly:  4
Monthly: 3
```

Retention should remain dry-run until the real backup directory has been reviewed.

At least one backup copy must eventually be stored away from the production server.

A backup is not considered proven until it has been restored successfully.

---

# 26. Recovery Test

Never perform a restore drill over the live production database.

Restore into a separate temporary database.

Verify:

- restore succeeds
- table counts are correct
- Alembic revision matches
- evidence objects/checksums match
- application can read restored data

Only then remove the temporary recovery database.

---

# 27. Release Procedure

For every production release:

1. Confirm target Git commit.
2. Confirm CI/local validation is clean.
3. Create release directory.
4. Checkout exact commit.
5. Create/install Python environment.
6. Build frontend with `VITE_API_URL=/api`.
7. Take verified pre-release backup.
8. Record current Alembic revision.
9. Run `alembic upgrade head`.
10. Record new Alembic revision.
11. Point `/opt/alc-growth/current` to the new release.
12. Restart `alc-growth-api`.
13. Reload Nginx only if configuration changed.
14. Test `/api/health/live`.
15. Test `/api/health/ready`.
16. Perform login smoke tests.
17. Perform evidence smoke test if storage-related code changed.
18. Record deployment commit and timestamp.

Do not delete the previous release immediately.

---

# 28. Rollback Procedure

If the new release fails and no incompatible database migration occurred:

1. Stop/restart application as necessary.
2. Point:

```text
/opt/alc-growth/current
```

back to the previous known-good release.

3. Restart:

```bash
sudo systemctl restart alc-growth-api
```

4. Verify:

```text
/api/health/live
/api/health/ready
```

5. Run login/application smoke tests.

If an incompatible migration has already modified production data/schema, simply switching code backwards may be unsafe.

In that case:

- enter maintenance mode
- assess migration reversibility
- use the verified pre-release backup when necessary
- follow the disaster-recovery procedure

Never manually force the database into an assumed previous state.

---

# 29. Service Restart Test

Before go-live:

```bash
sudo systemctl restart alc-growth-api
```

Verify:

```bash
sudo systemctl is-active alc-growth-api
```

Then verify both health endpoints.

---

# 30. Server Reboot Test

Before final production acceptance:

```bash
sudo reboot
```

After reconnecting verify:

```text
PostgreSQL
Redis
MinIO
alc-growth-api
Nginx
```

all started successfully.

Then verify:

```text
/api/health/live
/api/health/ready
```

and perform login/evidence smoke tests.

A deployment is not complete until it survives a server reboot.

---

# 31. Monitoring

At minimum monitor:

- HTTPS reachability
- `/api/health/live`
- `/api/health/ready`
- disk usage
- PostgreSQL health
- Redis health
- MinIO health
- backup completion
- certificate expiry
- application service status

Successful health requests are intentionally low-noise.

Readiness failures should generate alerts.

---

# 32. Security Verification

Before public go-live confirm:

- `APP_ENV=production`
- `/docs` unavailable
- `/redoc` unavailable
- `/openapi.json` unavailable
- HTTPS enabled
- `COOKIE_SECURE=true`
- strong `SECRET_KEY`
- production PostgreSQL password
- production Redis configuration
- dedicated MinIO service account
- no default MinIO credentials
- MinIO bucket private
- MinIO ports private
- PostgreSQL private
- Redis private
- FastAPI bound to loopback
- Nginx is the only public web endpoint
- Uvicorn uses `--no-proxy-headers`
- `TRUSTED_PROXY_CIDRS=127.0.0.1/32`
- `CORS_ORIGINS=` for same-origin deployment
- production frontend uses `/api`
- firewall checked
- backup restore proven
- no secrets committed to Git

---

# 33. Final UAT

Perform role-by-role UAT.

### ADMIN

Verify:

- login
- user management
- activity review
- dashboard
- reports
- challenge management
- hierarchy views

### DCU

Verify:

- scoped dashboard
- scoped ALC/SBU access
- activity review
- reports

### SBU

Verify:

- assigned ALC scope
- activities
- verification workflow
- partner/task visibility
- password-reset permissions where applicable

### ALC

Verify:

- login
- dashboard
- personal performance
- activity creation
- evidence upload
- submission
- correction/resubmission
- partners
- tasks
- reports

---

# 34. Production Acceptance Criteria

Production deployment is accepted only when all of the following are true:

```text
[ ] Exact Git commit recorded
[ ] Working tree clean
[ ] Production configuration accepted
[ ] PostgreSQL operational
[ ] Redis operational
[ ] MinIO private and operational
[ ] Alembic at expected head
[ ] FastAPI systemd service active
[ ] React production build served
[ ] HTTPS valid
[ ] HTTP redirects to HTTPS
[ ] Firewall verified
[ ] /api/health/live = 200
[ ] /api/health/ready = 200
[ ] ADMIN UAT passed
[ ] DCU UAT passed
[ ] SBU UAT passed
[ ] ALC UAT passed
[ ] Real MinIO evidence test passed
[ ] Backup completed
[ ] Backup verification passed
[ ] Restore rehearsal passed
[ ] Application restart test passed
[ ] Server reboot test passed
[ ] Logs contain request IDs
[ ] No secrets exposed
[ ] Previous release retained for rollback
[ ] Deployment record completed
```

---

# 35. Items Intentionally Deferred Until Phase 6

The following require the real office environment and cannot be proven during documentation-only Phase 5B-5:

- actual Ubuntu version
- actual network/NAT configuration
- production domain
- real TLS certificate
- real PostgreSQL credentials
- real Redis credentials
- real MinIO credentials
- real MinIO evidence smoke test
- real firewall rules
- off-server backup destination
- backup timer installation
- monitoring/alerting provider
- full disaster-recovery rehearsal
- service restart validation
- server reboot validation

These must be completed and recorded during Phase 6.

---

# 36. Deployment Safety Rules

Never:

- expose MinIO merely to make evidence preview work
- expose PostgreSQL or Redis publicly
- bind FastAPI publicly when Nginx is the intended entry point
- commit production secrets
- put secrets in frontend variables
- run development credentials in production
- run migrations automatically from every worker
- restore a recovery test over the live production database
- force-push production release history
- deploy an unrecorded Git commit
- claim disaster recovery is proven until an actual restore succeeds
- claim production readiness before real MinIO and reboot tests are completed

---

## Production Flow Summary

```text
Validated Git Release
        |
        v
Pre-release Backup
        |
        v
Production Configuration
        |
        v
PostgreSQL + Redis + Private MinIO
        |
        v
Alembic Migration
        |
        v
FastAPI systemd
127.0.0.1:8000
        |
        v
React /api Build
        |
        v
Nginx
        |
        v
HTTPS + Firewall
        |
        v
Health Checks
        |
        v
Role UAT
        |
        v
Real Evidence Test
        |
        v
Backup/Restore Validation
        |
        v
Restart + Reboot Test
        |
        v
Production Acceptance
```