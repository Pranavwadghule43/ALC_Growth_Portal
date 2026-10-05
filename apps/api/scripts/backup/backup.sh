#!/usr/bin/env bash
# Create one complete, verified backup set:
#   <BACKUP_ROOT>/<YYYYMMDDTHHMMSSZ>/database/<db>_db_<id>.dump   PostgreSQL custom-format dump
#                                   storage/objects/<exact keys>  evidence objects
#                                   storage/inventory.json        size + SHA256 of every object
#                                   manifest.json                 written last, status=complete
# The set carries an INCOMPLETE marker until every step (including verification) has
# succeeded; a failed run exits non-zero and leaves the marker in place.
#
# Required environment:
#   BACKUP_ROOT                           dedicated backup directory (validated)
#   PGHOST PGPORT PGUSER PGDATABASE       database to back up; password via ~/.pgpass,
#                                         PGPASSFILE or PGPASSWORD (never printed)
#   STORAGE_BACKEND                       s3 or local, as for the API, plus
#     s3:    S3_BUCKET S3_ACCESS_KEY S3_SECRET_KEY [S3_ENDPOINT_URL S3_REGION]
#     local: LOCAL_STORAGE_PATH
# Optional: BACKUP_PYTHON (Python with boto3, default python3), APP_RELEASE (recorded when the
# deployment has no git checkout).
#
# Stop application writes first for a consistent pair: see docs/BACKUP_AND_RECOVERY.md.
set -euo pipefail
umask 077
# shellcheck source-path=SCRIPTDIR source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }
if [[ $# -gt 0 ]]; then
    [[ "$1" == "-h" || "$1" == "--help" ]] && { usage; exit 0; }
    die "unexpected argument: $1 (see --help)"
fi

require_env BACKUP_ROOT STORAGE_BACKEND
require_pg_env
[[ "$STORAGE_BACKEND" == s3 || "$STORAGE_BACKEND" == local ]] || die "STORAGE_BACKEND must be s3 or local"
require_cmd pg_dump pg_restore psql "$BACKUP_PYTHON"

# shellcheck disable=SC2153  # BACKUP_ROOT is provided by the environment
root="$(tool check-root --create "$BACKUP_ROOT")"
acquire_lock "$root"

set_dir=""
step="start"
mark_failed() {
    local status="$1"
    if [[ "$status" -ne 0 && -n "$set_dir" && -d "$set_dir" ]]; then
        printf 'failed_step=%s\nfailed_at=%s\n' "$step" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            >>"$set_dir/INCOMPLETE"
        log "backup FAILED at step '$step' (exit $status); $set_dir is marked INCOMPLETE and is not a valid backup"
    fi
}
add_cleanup mark_failed

app_commit() {
    if [[ -n "${APP_RELEASE:-}" ]]; then
        printf '%s' "$APP_RELEASE"
    elif command -v git >/dev/null 2>&1; then
        git -C "$BACKUP_SCRIPT_DIR" rev-parse --short=12 HEAD 2>/dev/null || true
    fi
}

log "backup started (root=$root, $(pg_target), storage=$STORAGE_BACKEND)"
step="create set"
paths="$(tool begin-set "$root" --database "$PGDATABASE")"
set_dir="$(sed -n 1p <<<"$paths")"
dump="$(sed -n 2p <<<"$paths")"
[[ -n "$set_dir" && -n "$dump" ]] || die "could not create the backup set"

step="database dump"
log "database dump started ($(basename "$dump"))"
# pg_dump takes its own consistent snapshot; the application keeps running.
pg_dump --no-password --format=custom --file="$dump.part"
step="database dump validation"
pg_restore --list "$dump.part" >/dev/null
mv -- "$dump.part" "$dump"
log "database dump completed and readable by pg_restore"

step="storage backup"
tool storage-backup "$set_dir" --backend "$STORAGE_BACKEND"

step="verification"
server_version="$(psql --no-password -XAt -c 'SHOW server_version')"
tool finalize "$set_dir" --dump "$dump" --database-name "$PGDATABASE" \
    --server-version "$server_version" --pg-dump-version "$(pg_dump --version)" \
    --app-commit "$(app_commit)"

step="done"
log "backup finished: $set_dir"
