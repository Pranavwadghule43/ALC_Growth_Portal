#!/usr/bin/env bash
# Restore a PostgreSQL backup into the database named by PGDATABASE, then verify it.
#
# Usage:
#   restore_postgres.sh [--create] <backup set directory | dump file>
#       Restore into a NEW or EMPTY database (--create creates it when it does not exist).
#   restore_postgres.sh --force <backup set directory | dump file>     with RESTORE_CONFIRM=<PGDATABASE>
#       DESTRUCTIVE: drop the existing, non-empty database and replace it with the backup.
#       Stop the application first; refused while other sessions are connected.
#   restore_postgres.sh --check
#       Only verify PGDATABASE: alembic_version and row counts of the application tables
#       (use it on the source and the restored database to compare them).
#
# A backup set directory is verified first (manifest, SHA256, pg_restore --list); a bare dump
# file is only checked with pg_restore --list.
#
# Environment: PGHOST PGPORT PGUSER PGDATABASE (password via ~/.pgpass, PGPASSFILE or
# PGPASSWORD; never printed). Optional: PGMAINTENANCE_DB (default postgres) for create/drop,
# RESTORE_DB_OWNER (owner of a created database), RESTORE_ROLE (role that owns the restored
# objects when connecting as an administrator), RESTORE_VERIFY_TABLES (tables that must exist;
# default: the core application tables).
#
# The postgres, template0 and template1 databases are never restore targets.
set -euo pipefail
umask 077
# shellcheck source-path=SCRIPTDIR source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
LOG_TAG=restore

REQUIRED_TABLES="${RESTORE_VERIFY_TABLES:-rcus dcus sbus alcs users activities activity_evidence partners}"
OPTIONAL_TABLES="activity_reviews activity_revisions growth_challenges challenge_progress tasks notifications audit_logs refresh_tokens"

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

force=0 create=0 check_only=0 source_path=""
for arg in "$@"; do
    case "$arg" in
        --force) force=1 ;;
        --create) create=1 ;;
        --check) check_only=1 ;;
        -h | --help) usage; exit 0 ;;
        -*) die "unknown option: $arg" ;;
        *) [[ -z "$source_path" ]] || die "only one backup may be given"; source_path="$arg" ;;
    esac
done

require_pg_env
require_cmd psql pg_restore createdb dropdb

psql_target() { psql --no-password -X -v ON_ERROR_STOP=1 "$@"; }
psql_maintenance() { psql_target --dbname="${PGMAINTENANCE_DB:-postgres}" "$@"; }

valid_identifier() { [[ "$1" =~ ^[a-z_][a-z0-9_]*$ ]]; }

# Verify the database named by PGDATABASE; $1 = expected Alembic revision (may be empty).
verify_database() {
    local expected="$1" revisions table rows missing=()
    revisions="$(psql_target -At -c 'SELECT version_num FROM alembic_version ORDER BY 1')" \
        || die "alembic_version is missing or unreadable in $PGDATABASE"
    [[ -n "$revisions" ]] || die "alembic_version is empty in $PGDATABASE"
    revisions="${revisions//$'\n'/,}"
    log "alembic revision: $revisions"
    if [[ -n "$expected" && "$revisions" != "$expected" ]]; then
        die "restored alembic revision $revisions does not match the backup ($expected)"
    fi
    for table in $REQUIRED_TABLES; do
        valid_identifier "$table" || die "invalid table name in RESTORE_VERIFY_TABLES: $table"
        [[ "$(psql_target -At -c "SELECT to_regclass('public.$table') IS NOT NULL")" == t ]] \
            || missing+=("$table")
    done
    [[ ${#missing[@]} -eq 0 ]] || die "required table(s) missing: ${missing[*]}"
    printf '%-22s %s\n' table rows
    for table in $REQUIRED_TABLES $OPTIONAL_TABLES; do
        valid_identifier "$table" || continue
        if [[ "$(psql_target -At -c "SELECT to_regclass('public.$table') IS NOT NULL")" == t ]]; then
            rows="$(psql_target -At -c "SELECT count(*) FROM public.$table")"
            printf '%-22s %s\n' "$table" "$rows"
        else
            printf '%-22s %s\n' "$table" "(absent)"
        fi
    done
    log "database $PGDATABASE verified (connection, alembic_version, required tables)"
}

if [[ $check_only -eq 1 ]]; then
    [[ -z "$source_path" && $force -eq 0 && $create -eq 0 ]] || die "--check takes no other arguments"
    log "checking $(pg_target)"
    verify_database ""
    exit 0
fi

[[ -n "$source_path" ]] || { usage; exit 2; }
is_protected_database "$PGDATABASE" && die "refusing to restore into the system database '$PGDATABASE'"

# ---- the backup to restore -------------------------------------------------------------------
expected_revision=""
if [[ -d "$source_path" ]]; then
    require_cmd "$BACKUP_PYTHON"
    description="$(tool describe "$source_path")" || die "backup set failed verification"
    dump="$(sed -n 's/^dump=//p' <<<"$description")"
    expected_revision="$(sed -n 's/^alembic_revision=//p' <<<"$description")"
elif [[ -f "$source_path" ]]; then
    dump="$source_path"
    log "WARNING: restoring a bare dump file: no manifest, so no SHA256 check is possible"
else
    die "backup not found: $source_path"
fi
pg_restore --list "$dump" >/dev/null || die "pg_restore cannot read $dump"

# ---- the target ------------------------------------------------------------------------------
log "restore target: $(pg_target)"
log "backup dump:    $(basename "$dump")${expected_revision:+ (alembic revision $expected_revision)}"

exists="$(psql_maintenance -At -v name="$PGDATABASE" <<'SQL'
SELECT count(*) FROM pg_database WHERE datname = :'name';
SQL
)"
if [[ "$exists" == 0 ]]; then
    [[ $create -eq 1 ]] || die "database $PGDATABASE does not exist (create it, or pass --create)"
    mode="new database"
else
    tables="$(psql_target -At <<'SQL'
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%';
SQL
)"
    if [[ "$tables" == 0 ]]; then
        mode="empty database"
    else
        if [[ $force -ne 1 || "${RESTORE_CONFIRM:-}" != "$PGDATABASE" ]]; then
            die "database $PGDATABASE is not empty ($tables relations). Restoring would DESTROY it. To replace it: stop the application, then re-run with --force and RESTORE_CONFIRM=$PGDATABASE"
        fi
        mode="replace existing database"
    fi
fi
log "restore mode: $mode"

if [[ "$mode" == "replace existing database" ]]; then
    log "DESTRUCTIVE: dropping database $PGDATABASE (confirmed with --force and RESTORE_CONFIRM)"
    dropdb --no-password --maintenance-db="${PGMAINTENANCE_DB:-postgres}" -- "$PGDATABASE" \
        || die "could not drop $PGDATABASE (stop the application and close other sessions first)"
fi
if [[ "$mode" != "empty database" ]]; then
    owner=()
    [[ -n "${RESTORE_DB_OWNER:-}" ]] && owner=(--owner="$RESTORE_DB_OWNER")
    createdb --no-password --maintenance-db="${PGMAINTENANCE_DB:-postgres}" "${owner[@]}" -- "$PGDATABASE"
    log "created database $PGDATABASE"
fi

role=()
[[ -n "${RESTORE_ROLE:-}" ]] && role=(--role="$RESTORE_ROLE")
log "pg_restore started"
pg_restore --no-password --no-owner --no-privileges --exit-on-error --single-transaction \
    "${role[@]}" --dbname="$PGDATABASE" "$dump"
log "pg_restore completed"

verify_database "$expected_revision"
log "restore finished. Next: compare the alembic revision with the application release before"
log "running any migration (see docs/BACKUP_AND_RECOVERY.md, 'Alembic after a restore')."
