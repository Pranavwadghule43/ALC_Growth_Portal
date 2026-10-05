# Backup and Recovery — ALC Growth Portal

Operational runbook for backing up and restoring the portal's data: the PostgreSQL application
database and the private evidence files in S3-compatible object storage (MinIO in production).

> Status: the scripts are implemented and tested (automated tests, and a full backup → verify →
> restore drill against PostgreSQL 16 and an S3-compatible test server). A restore drill against
> the real development database and against a real MinIO server must still be run — see
> [Recovery drill](#recovery-drill). Nothing here is deployed or scheduled automatically.

## Contents

1. [Purpose](#purpose)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Required tools](#required-tools)
5. [Environment variables](#environment-variables)
6. [Manual backup](#manual-backup)
7. [Verify a backup](#verify-a-backup)
8. [Retention](#retention)
9. [Database restore](#database-restore)
10. [Storage restore](#storage-restore)
11. [Complete disaster recovery](#complete-disaster-recovery)
12. [Alembic after a restore](#alembic-after-a-restore)
13. [Maintenance / write freeze](#maintenance--write-freeze)
14. [Off-server copy](#off-server-copy)
15. [Security](#security)
16. [Scheduling](#scheduling)
17. [Recovery drill](#recovery-drill)
18. [Troubleshooting](#troubleshooting)
19. [RPO / RTO](#rpo--rto)
20. [What not to do](#what-not-to-do)

---

## Purpose

The portal's state lives in two places that must be backed up **together**:

| Data | Where | Backed up by |
| --- | --- | --- |
| Users, hierarchy, activities, reviews, partners, Growth Challenge, audit log, evidence *metadata* | PostgreSQL | `pg_dump --format=custom` |
| Evidence files (images / PDFs) | private bucket (`S3_BUCKET`), keys `evidence/<alc>/<activity>/<evidence>.<ext>` | object copy with exact keys |

Every `activity_evidence` row stores the object key of its file, so a database without its
objects (or objects without the database) is an incomplete recovery.

Not backed up by these scripts (back them up separately, encrypted, after every change): the
production environment file / secrets, reverse-proxy configuration, systemd units. Redis holds
only login rate-limit counters and needs no backup. Application code is recovered from its git
release tag.

## Architecture

```text
apps/api/scripts/backup/
  backup.sh             orchestrator: lock → dump → validate → copy storage → verify → manifest
  verify_backup.sh      verify an existing set without restoring
  restore_postgres.sh   restore a dump (new/empty database, or confirmed replacement)
  restore_storage.sh    restore evidence objects under their exact keys (never deletes)
  retention.sh          daily / weekly / monthly retention inside BACKUP_ROOT
  common.sh             shared logging, guards and locking (sourced)
  backup_tool.py        Python helper used by the scripts (root validation, storage copy,
                        checksums, manifest, verification, retention). Stdlib + boto3.
```

One backup run produces one **backup set**:

```text
<BACKUP_ROOT>/
  .backup.lock                         run lock (flock)
  20261005T020000Z/                    set id = UTC start time
    INCOMPLETE                         present until every step succeeded (removed last)
    database/alc_growth_db_20261005T020000Z.dump
    storage/objects/evidence/...       evidence files, keys unchanged
    storage/inventory.json             key, size, SHA256, MD5, content type of every object
    manifest.json                      written last; status "complete"
```

`manifest.json` (non-secret) records: format version, set id, start/finish times (UTC), host,
application git commit (or `APP_RELEASE`), database name, dump file / size / SHA256, the
**Alembic revision read from the dump itself**, PostgreSQL server and `pg_dump` versions, storage
backend and bucket name, object count / total bytes, inventory SHA256, how many objects were
also matched against the server ETag, an evidence reference check (active evidence rows in the
dump whose object is missing from the copy), tool versions, warnings and the consistency note.
It never contains `DATABASE_URL`, passwords, `SECRET_KEY`, S3 keys, Redis URLs or tokens.

### Why a Python copy instead of `mc mirror`

`mc mirror --preserve` is a good tool and remains a valid manual alternative (see
[Troubleshooting](#troubleshooting)). The scripts use the application's own `boto3` instead
because it:

* reads the **same** `S3_*` variables as the API (no second credential store such as
  `MC_HOST_*` / `~/.mc/config.json`) and needs no extra binary on the server;
* produces a per-object SHA256 inventory while copying, so a set can be verified offline later;
* checks each download against the server's ETag (MD5 for single-part uploads, which is how the
  API uploads evidence);
* works identically for the `local` storage backend used in development.

### What the checksums prove

| Check | Proves |
| --- | --- |
| `pg_restore --list` right after `pg_dump` | the archive is complete and readable (a truncated dump fails) |
| dump SHA256 in the manifest | the dump has not changed since the backup |
| server ETag = MD5 of downloaded bytes | the copied object equals what the object store holds (single-part, unencrypted objects; others rely on size + SHA256) |
| per-object SHA256 in the inventory, re-read before the set is marked complete | every stored file is exactly what was downloaded |
| inventory SHA256 in the manifest | the object list itself has not been altered |
| reference check | every *active* evidence row in the dump has its object in the copy |

`verify_backup.sh` repeats every offline check (manifest, dump SHA256 + `pg_restore --list`,
inventory SHA256, each object's size + SHA256, no unexpected files). It does **not** prove the
dump restores cleanly into a server — only a restore does; that is what the drill is for.

## Prerequisites

* A dedicated backup directory on a separate disk/volume, e.g. `/srv/alc-growth/backups`, owned
  by a dedicated `alc-backup` user, mode `0700`. It must not be `/`, a system directory, a home
  directory or the repository — the scripts refuse those.
* A PostgreSQL role that can read every application table: the application's owner role, or a
  dedicated role granted `pg_read_all_data` (PostgreSQL 14+).
* Object-storage credentials for the backup: **read-only** on the evidence bucket for backups;
  a separate read-write key is used only when restoring.
* Free space for at least two full sets (database dump + all evidence) plus retention.

## Required tools

| Tool | Version | Used for |
| --- | --- | --- |
| Bash | 4.4+ | the scripts (Ubuntu's bash 5 is fine) |
| PostgreSQL client (`pg_dump`, `pg_restore`, `psql`, `createdb`, `dropdb`) | **same major version as the server or newer** | dump / restore |
| Python | 3.11+ with `boto3` (use the API's virtualenv: `BACKUP_PYTHON=/opt/alc-growth/apps/api/.venv/bin/python`) | `backup_tool.py` |
| `flock` (util-linux) | any | run lock (an atomic `mkdir` lock is used where `flock` is missing, e.g. Git Bash) |

The repository's `docker-compose.yml` runs PostgreSQL 16; the Windows development machine runs
PostgreSQL 18. A dump made by a newer `pg_dump` cannot be read by an older `pg_restore`.

## Environment variables

The scripts read only the environment (nothing is hard-coded). Never put a password on the
command line.

| Variable | Used by | Meaning |
| --- | --- | --- |
| `BACKUP_ROOT` | backup, retention | dedicated backup directory (validated) |
| `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` | backup, restore_postgres | the database (all four required; URLs are refused) |
| `PGPASSFILE` (recommended) / `~/.pgpass` / `PGPASSWORD` | PostgreSQL tools | password, never printed |
| `STORAGE_BACKEND` | backup, restore_storage | `s3` (production) or `local` (development) |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | storage | same names and meaning as for the API |
| `LOCAL_STORAGE_PATH` | storage (`local`) | the API's local evidence directory |
| `BACKUP_PYTHON` | all | Python with boto3 (default `python3`) |
| `APP_RELEASE` | backup | release label recorded when the server has no git checkout |
| `BACKUP_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY` | retention | default 7 / 4 / 3 |
| `RESTORE_CONFIRM` | restores | must equal the target database / bucket name for a destructive restore |
| `PGMAINTENANCE_DB`, `RESTORE_DB_OWNER`, `RESTORE_ROLE`, `RESTORE_VERIFY_TABLES` | restore_postgres | see the script header |
| `BACKUP_SKIP_ETAG_CHECK=1` | backup | only if the store's ETags are not MD5 (e.g. server-side encryption) |

The `DATABASE_URL` used by the API is **not** read; set the four `PG*` variables instead (they
are the same host / port / user / database). A sample file is in
[`docs/examples/backup/backup.env.example`](examples/backup/backup.env.example).

Load an environment file without printing it:

```bash
set -a; . /etc/alc-growth/backup.env; set +a
```

## Manual backup

```bash
cd /opt/alc-growth
set -a; . /etc/alc-growth/backup.env; set +a
apps/api/scripts/backup/backup.sh
```

Logged steps: `backup started` → `database dump started` → `database dump completed and
readable by pg_restore` → `storage backup started` / `completed: N objects` → `verification
completed; manifest written` → `backup finished: <set>`. Exit status `0` only when the set is
complete and verified; `75` when another backup/retention run holds the lock; anything else is
a failure — the set keeps its `INCOMPLETE` marker (with `failed_step=`) and must not be used.

`pg_dump` takes a consistent snapshot of the database without downtime. For a database dump
and storage copy that match each other, run it inside a
[write freeze](#maintenance--write-freeze) (always do so before migrations or upgrades).

## Verify a backup

```bash
apps/api/scripts/backup/verify_backup.sh /srv/alc-growth/backups/20261005T020000Z
```

Exit status `0` only when every check in [What the checksums prove](#what-the-checksums-prove)
passes. Run it on the off-server copy too.

## Retention

Recommended starting policy — an **operational recommendation, not a client contractual
requirement**: keep the newest valid set of each of the last **7 days**, **4 ISO weeks** and
**3 months**.

```bash
apps/api/scripts/backup/retention.sh            # dry run: prints keep / would delete
apps/api/scripts/backup/retention.sh --apply    # deletes
```

Safety rules (enforced by `backup_tool.py`, tested):

* only directories directly inside the validated `BACKUP_ROOT` whose name is a set id
  (`YYYYMMDDTHHMMSSZ`) are considered; other names, files and symlinks are ignored;
* only complete, structurally valid sets count as backups, and the newest valid set is
  **always** kept; with no valid set nothing is deleted;
* `INCOMPLETE` sets are deleted only when a newer valid set exists;
* every deletion re-checks that the path is a set directory inside `BACKUP_ROOT`;
* retention takes the same lock as `backup.sh`, so it never races a running backup.

## Database restore

**Always restore into a new database first.** Replacing a live database is a separate,
confirmed step.

### A. Into a new or empty database (non-destructive)

```bash
export PGDATABASE=alc_growth_restore      # a NEW name
apps/api/scripts/backup/restore_postgres.sh --create /srv/alc-growth/backups/20261005T020000Z
```

The set is verified first (manifest, SHA256, `pg_restore --list`). The script prints the target
(host, port, database, user — never the password), restores with `pg_restore --no-owner
--no-privileges --exit-on-error --single-transaction`, then verifies: connection,
`alembic_version` present and equal to the backup's revision, the required tables
(`rcus dcus sbus alcs users activities activity_evidence partners`, configurable through
`RESTORE_VERIFY_TABLES`) exist, and prints row counts (optional tables such as
`growth_challenges` are reported as `(absent)` instead of failing).

Objects are owned by the connecting user. Connect as the application's owner role, or connect
as an administrator with `RESTORE_ROLE=<app role>` and `RESTORE_DB_OWNER=<app role>`.

`restore_postgres.sh --check` verifies and counts an existing database without restoring; run
it on the source and the restored database to compare them.

### B. Replace an existing database (DESTRUCTIVE)

Refused unless **both** `--force` is given **and** `RESTORE_CONFIRM` equals the database name:

```bash
# 1. stop the API (systemctl stop alc-growth-api) — the drop is refused while sessions exist
# 2. take a fresh backup of the current state if it is still readable
export PGDATABASE=alc_growth
RESTORE_CONFIRM=alc_growth apps/api/scripts/backup/restore_postgres.sh --force <set>
```

The database is dropped and recreated, then restored and verified as above. `postgres`,
`template0` and `template1` are never accepted as targets. A bare `.dump` file may be given
instead of a set directory; it is checked with `pg_restore --list` but has no SHA256 check.

## Storage restore

```bash
export S3_BUCKET=alc-evidence                  # target bucket (must exist, private)
apps/api/scripts/backup/restore_storage.sh --deep-verify <set>
```

* objects missing from the bucket are uploaded under their **exact** keys, with their recorded
  content type;
* objects already identical are skipped (the restore can be re-run safely);
* if an object exists with different content the restore stops **before writing anything**;
  overwriting needs `--force` and `RESTORE_CONFIRM=<bucket name>`;
* objects in the bucket that are not in the backup are **never deleted or modified**;
* afterwards every restored object is checked (size and ETag; with `--deep-verify` it is
  downloaded again and its SHA256 compared).

To practise, restore into a new private bucket (`S3_BUCKET=alc-evidence-restore-test`). For the
local development backend set `STORAGE_BACKEND=local` and `LOCAL_STORAGE_PATH`.

## Complete disaster recovery

1. Provision a clean Ubuntu server; restore OS hardening and firewall (only SSH + HTTPS open).
2. Install PostgreSQL (same major version as the backup or newer), Redis, MinIO, Python 3.11+
   and Node (frontend build).
3. Check out the application release recorded in the manifest (`application.git_commit`), or
   the release you intend to run.
4. Restore the secure environment configuration (`.env` / `/etc/alc-growth/*.env`) from its
   encrypted off-server copy.
5. Create the database role(s); create the empty target database.
6. Copy the chosen backup set from off-server storage and run `verify_backup.sh` on it.
7. `restore_postgres.sh` into the database (mode A).
8. Check the restored Alembic revision (printed by the script) — see
   [Alembic after a restore](#alembic-after-a-restore).
9. Create the private bucket (no anonymous access) and run `restore_storage.sh --deep-verify`.
10. Check a few evidence objects: the manifest's reference check, and `restore_storage.sh`
    reporting every object verified.
11. Apply only the migrations newer than the restored revision, if the release needs them.
12. Start PostgreSQL / Redis / MinIO (if not already running).
13. Start the API (FastAPI / Uvicorn).
14. Check `/api/health`; once Phase 5B-3 (health and logging) is integrated, check
    `/health/live` and then `/health/ready`.
15. Start the frontend / reverse proxy.
16. Smoke-test as ADMIN, DCU, SBU and ALC: sign in, open dashboards, open an activity and view
    its evidence (images and a PDF), check a report export.
17. Take a fresh backup of the recovered system.

## Alembic after a restore

Do **not** run `alembic upgrade head` blindly.

1. Inspect the restored revision: `psql -XAt -c 'SELECT version_num FROM alembic_version'`
   (also printed by `restore_postgres.sh` and recorded in the manifest).
2. Compare it with the release: `cd apps/api && python -m alembic heads` and
   `python -m alembic history`.
3. Equal → nothing to do. Older → apply only the forward migrations of that release
   (`python -m alembic upgrade head` against the restored database), after taking a backup if
   the migration is significant. Newer than the release → the code is too old for this
   database: deploy the matching release instead of downgrading.
4. `python -m alembic current` must then equal the release head.

## Maintenance / write freeze

The database dump and the storage copy are taken **one after the other, not atomically**. A
file uploaded or deleted between them can leave the pair inconsistent. They form a consistent
recovery point only when application writes are frozen for the whole run:

1. Announce the maintenance window to users.
2. Stop new writes/uploads: stop the API (`systemctl stop alc-growth-api`) or put the reverse
   proxy in maintenance mode for `/api`.
3. Create the PostgreSQL dump (`backup.sh` does steps 3–6).
4. Back up object storage.
5. Verify both.
6. Write the final manifest.
7. Copy the set off-server and verify the copy.
8. Resume writes.

Nightly backups without a freeze are still useful (the dump itself is always consistent), but
evidence uploaded during the run may be missing from the copy; the manifest's reference check
reports active evidence rows whose object is missing.

## Off-server copy

A backup that only lives on the production server is lost with that server. Keep a second,
independent copy:

* a NAS or backup server inside the office network (pull model: the backup server copies with
  read-only access, e.g. `rsync -a --delete-after` of complete sets over SSH);
* encrypted removable storage rotated off-site;
* or any secure cloud object storage — no specific vendor is required.

Rules: copy only complete sets (no `INCOMPLETE` marker); run `verify_backup.sh` on the copy;
encrypt at rest (LUKS / BitLocker / the storage's server-side encryption); keep at least one
copy that the production server cannot delete (pull model or object lock).

## Security

* Dedicated `alc-backup` user; `BACKUP_ROOT` owned by it, mode `0700` (`backup.sh` and
  `restore_postgres.sh` run with `umask 077`, so backup files are `0600`).
* Limit who can log in as that user; backups contain personal data.
* Read-only credentials for backups; write credentials only for restores.
* No secrets in scripts or manifests: passwords come from `PGPASSFILE` (mode `0600`), S3 keys
  from the environment file (mode `0600`, root-owned, read by systemd).
* Encrypt off-server copies at rest with standard tools (LUKS, BitLocker, storage encryption) —
  never homemade encryption.
* Never publish backups: no public bucket, no web-served directory, no public URLs.
* MinIO stays private (loopback / private network); its console is not exposed.

## Scheduling

Recommended on Ubuntu: a systemd **service + timer** (samples in
[`docs/examples/backup/`](examples/backup/) — copy, adapt, then `systemctl enable --now
alc-growth-backup.timer`; nothing is installed by the repository).

* nightly at about 02:15 (with a random delay), `Persistent=true` so a missed run happens on boot;
* the service runs `backup.sh` and then `retention.sh --apply`;
* a failed run leaves the unit `failed` — alert on it (and on a manifest older than 26 hours)
  once monitoring from Phase 5B-3 is available;
* weekly: copy the newest set off-server and verify it;
* quarterly: a restore drill.

## Recovery drill

### Drill in this repository (cloud) — done

Run against a throwaway PostgreSQL 16 cluster (non-superuser owner role, password
authentication) and an S3-compatible test server on `127.0.0.1:9000` (moto, not MinIO), with
data created through the real API: backup → verify → restore into a new database → identical
row counts and Alembic revision → refused destructive restores → confirmed replacement →
storage restore into a new bucket with exact keys, identical ETags and content types, an
unrelated object left untouched, conflicts refused without `--force` + `RESTORE_CONFIRM`.

### PostgreSQL drill on the Windows development machine — to be run by Pranav

Uses the PostgreSQL 18 tools, never touches `alc_growth` except to read it, and restores into
`alc_growth_restore_test`, which is dropped at the end. Stop the local API first so the counts
do not change between the dump and the comparison. Authentication uses the standard mechanisms:
either let the tools prompt for the password, or create `%APPDATA%\postgresql\pgpass.conf`
(one line: `127.0.0.1:55432:*:alc:<password>`).

```powershell
$pg = "C:\Program Files\PostgreSQL\18\bin"
$env:PGHOST = "127.0.0.1"; $env:PGPORT = "55432"; $env:PGUSER = "alc"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$dir = "C:\alc-backup-drill\$stamp"
New-Item -ItemType Directory -Force $dir | Out-Null
$dump = "$dir\alc_growth_db_$stamp.dump"

# 1. Back up alc_growth (read-only for the source database)
& "$pg\pg_dump.exe" --format=custom --dbname=alc_growth --file="$dump"
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
& "$pg\pg_restore.exe" --list "$dump" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "dump is not readable" }
Get-FileHash -Algorithm SHA256 $dump

# 2. Create the separate restore database (must not exist yet)
& "$pg\psql.exe" -X -At -d postgres -c "SELECT datname FROM pg_database WHERE datname = 'alc_growth_restore_test'"
& "$pg\createdb.exe" alc_growth_restore_test
#    (if role alc may not create databases:  & "$pg\createdb.exe" -U postgres -O alc alc_growth_restore_test)

# 3. Restore the dump into alc_growth_restore_test only
& "$pg\pg_restore.exe" --no-owner --no-privileges --exit-on-error --single-transaction --dbname=alc_growth_restore_test "$dump"
if ($LASTEXITCODE -ne 0) { throw "restore failed" }

# 4. Compare row counts of the application tables
$q = "SELECT 'rcus', count(*) FROM rcus UNION ALL SELECT 'dcus', count(*) FROM dcus UNION ALL SELECT 'sbus', count(*) FROM sbus UNION ALL SELECT 'alcs', count(*) FROM alcs UNION ALL SELECT 'users', count(*) FROM users UNION ALL SELECT 'activities', count(*) FROM activities UNION ALL SELECT 'activity_evidence', count(*) FROM activity_evidence UNION ALL SELECT 'activity_reviews', count(*) FROM activity_reviews UNION ALL SELECT 'activity_revisions', count(*) FROM activity_revisions UNION ALL SELECT 'partners', count(*) FROM partners UNION ALL SELECT 'growth_challenges', count(*) FROM growth_challenges UNION ALL SELECT 'challenge_progress', count(*) FROM challenge_progress UNION ALL SELECT 'tasks', count(*) FROM tasks UNION ALL SELECT 'notifications', count(*) FROM notifications UNION ALL SELECT 'audit_logs', count(*) FROM audit_logs"
& "$pg\psql.exe" -X -At -d alc_growth -c $q | Set-Content "$dir\source-counts.txt"
& "$pg\psql.exe" -X -At -d alc_growth_restore_test -c $q | Set-Content "$dir\restored-counts.txt"
Get-Content "$dir\restored-counts.txt"
if (Compare-Object (Get-Content "$dir\source-counts.txt") (Get-Content "$dir\restored-counts.txt")) { "COUNTS DIFFER" } else { "COUNTS IDENTICAL" }

# 5. Alembic revision must be identical
& "$pg\psql.exe" -X -At -d alc_growth -c "SELECT version_num FROM alembic_version"
& "$pg\psql.exe" -X -At -d alc_growth_restore_test -c "SELECT version_num FROM alembic_version"

# 6. Destroy ONLY the restore test database
& "$pg\dropdb.exe" alc_growth_restore_test
```

Expected: `COUNTS IDENTICAL`, the same Alembic revision twice (currently `20261002_0007`), no
errors. Optional: with Git for Windows the shell scripts themselves can be run from Git Bash
(PostgreSQL `bin` on `PATH`, `BACKUP_PYTHON` pointing at the API virtualenv's `python.exe`,
`BACKUP_ROOT=/c/alc-backup-drill`, `STORAGE_BACKEND=local` or the local MinIO); they use the
`mkdir` lock there because Git Bash has no `flock`.

### MinIO drill — still to be run against a real MinIO

On a machine with the development MinIO (`docker compose up -d minio minio-init`) and the
scripts (Ubuntu, WSL or Git Bash):

```bash
export STORAGE_BACKEND=s3 S3_ENDPOINT_URL=http://127.0.0.1:9000 S3_REGION=us-east-1
export S3_ACCESS_KEY=<dev access key> S3_SECRET_KEY=<dev secret key>   # never commit these
export S3_BUCKET=alc-evidence BACKUP_ROOT=/srv/alc-drill/backups
export PGHOST=127.0.0.1 PGPORT=55432 PGUSER=alc PGDATABASE=alc_growth
apps/api/scripts/backup/backup.sh
set_dir=$(ls -d "$BACKUP_ROOT"/2*Z | tail -n 1)
apps/api/scripts/backup/verify_backup.sh "$set_dir"
# restore into a NEW private bucket, never over alc-evidence
mc alias set drill http://127.0.0.1:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
mc mb drill/alc-evidence-restore-test && mc anonymous set none drill/alc-evidence-restore-test
S3_BUCKET=alc-evidence-restore-test apps/api/scripts/backup/restore_storage.sh --deep-verify "$set_dir"
mc diff drill/alc-evidence drill/alc-evidence-restore-test      # no output = identical
mc rb --force drill/alc-evidence-restore-test                   # remove ONLY the test bucket
```

## Troubleshooting

| Symptom | Cause / action |
| --- | --- |
| `refusing to start` (exit 75) | another backup/retention run holds the lock; wait. With the `mkdir` lock (no `flock`), remove `BACKUP_ROOT/.backup.lock.d` only after checking no run is active. |
| `BACKUP_ROOT must not ...` | the root is unsafe (system/home directory, repository, relative, `..`); use a dedicated directory. |
| `pg_dump: error: connection ... password authentication failed` | fix `PGPASSFILE` (mode `0600`, `host:port:db:user:password`). |
| `pg_restore: unsupported version` | the client tools are older than the dump; use tools of the same or newer major version. |
| `ETag differs` | the downloaded bytes differ from the object store's checksum — retry; if the store uses server-side encryption, set `BACKUP_SKIP_ETAG_CHECK=1` (size + SHA256 still apply). |
| `unsupported object key` | a key that cannot be a safe file path (e.g. a "folder" marker ending in `/`); inspect and remove stray objects from the bucket. |
| `database ... is not empty` | restore mode A only accepts a new/empty database; use a new name, or the confirmed mode B. |
| `could not drop ...` | sessions are still connected: stop the API and other clients. |
| `active evidence object(s) ... missing` (warning) | uploads/deletions happened during the run, or objects are missing in production; re-run inside a write freeze, investigate the listed keys. |
| Manual storage copy without the scripts | `mc mirror --preserve <alias>/alc-evidence /path/objects/` keeps keys; it produces no inventory, so `verify_backup.sh` cannot check such a copy. |

## RPO / RTO

| Target | Value | Note |
| --- | --- | --- |
| RPO (maximum data loss) | **≤ 24 hours** | RECOMMENDED OPERATIONAL TARGET — nightly backups; not a client contractual SLA |
| RTO (time to restore service) | **≤ 4 hours** | RECOMMENDED OPERATIONAL TARGET — clean server, restore, verification, smoke test; not a client contractual SLA |

Shorter targets need more frequent backups, PostgreSQL WAL archiving / point-in-time recovery
and MinIO bucket replication — out of scope for these scripts.

## What not to do

* Do not restore over the live database to "test" a backup — restore into a new database.
* Do not run `restore_postgres.sh --force` while the API is running.
* Do not run `alembic upgrade head` on a restored database before checking its revision.
* Do not use a set that has an `INCOMPLETE` marker or fails `verify_backup.sh`.
* Do not copy the evidence bucket with tools that rename, flatten or re-prefix keys.
* Do not delete backup sets by hand while a backup or retention run is active; never point
  `BACKUP_ROOT` at a shared or system directory.
* Do not keep backups only on the production server.
* Do not make the bucket or the backup directory public, and do not expose the MinIO console.
* Do not put passwords or keys in scripts, manifests, command lines, tickets or chat.
