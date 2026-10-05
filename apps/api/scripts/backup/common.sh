# shellcheck shell=bash
# Shared helpers for the ALC Growth Portal backup / restore scripts. Sourced, never executed.
#
# Logging never prints secrets: PostgreSQL passwords come from ~/.pgpass / PGPASSFILE (or
# PGPASSWORD) and S3 keys from the environment; neither is ever echoed. The scripts never use
# `set -x`, `eval` or `rm -rf`; backup sets are only deleted by backup_tool.py (retention),
# which re-validates every path against BACKUP_ROOT.

BACKUP_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_TOOL="$BACKUP_SCRIPT_DIR/backup_tool.py"
BACKUP_PYTHON="${BACKUP_PYTHON:-python3}"
LOG_TAG="${LOG_TAG:-backup}"
# PostgreSQL databases that are never a restore target.
PROTECTED_DATABASES=(postgres template0 template1)

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$LOG_TAG" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

require_cmd() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
    done
}

require_env() {
    local name
    for name in "$@"; do
        [[ -n "${!name:-}" ]] || die "environment variable $name must be set"
    done
}

tool() { "$BACKUP_PYTHON" "$BACKUP_TOOL" "$@"; }

# Explicit libpq target; a connection URL in these variables is refused (it may hold a password).
require_pg_env() {
    require_env PGHOST PGPORT PGUSER PGDATABASE
    local name
    for name in PGHOST PGPORT PGUSER PGDATABASE; do
        [[ "${!name}" != *"://"* && "${!name}" != *"="* ]] \
            || die "$name must be a plain value, not a connection string or URL"
    done
    [[ "$PGPORT" =~ ^[0-9]+$ ]] || die "PGPORT must be a number"
}

# Non-secret description of the PostgreSQL target.
pg_target() {
    printf 'host=%s port=%s database=%s user=%s' "$PGHOST" "$PGPORT" "$PGDATABASE" "$PGUSER"
}

is_protected_database() {
    local name
    for name in "${PROTECTED_DATABASES[@]}"; do
        [[ "$1" == "$name" ]] && return 0
    done
    return 1
}

# ---- cleanup on exit (several handlers can be registered) ------------------------------------
CLEANUP_HANDLERS=()
add_cleanup() { CLEANUP_HANDLERS+=("$1"); }
run_cleanup() {
    local status=$? handler
    for handler in "${CLEANUP_HANDLERS[@]}"; do
        "$handler" "$status" || true
    done
    return "$status"
}
trap run_cleanup EXIT

# ---- single-run lock ------------------------------------------------------------------------
# One backup or retention run at a time per BACKUP_ROOT. flock is used where available (Linux:
# released automatically if the process dies); otherwise an atomic mkdir lock, which an
# operator must remove by hand after a crash. A second run refuses with exit status 75.
BACKUP_LOCK_DIR=""
release_mkdir_lock() {
    if [[ -n "$BACKUP_LOCK_DIR" && -d "$BACKUP_LOCK_DIR" ]]; then
        rm -f -- "$BACKUP_LOCK_DIR/pid"
        rmdir -- "$BACKUP_LOCK_DIR"
    fi
}

acquire_lock() {
    local root="$1" method="${BACKUP_LOCK_METHOD:-auto}"
    if [[ "$method" != mkdir ]] && command -v flock >/dev/null 2>&1; then
        exec 9>>"$root/.backup.lock"
        if ! flock -n 9; then
            log "another backup or retention run is in progress for $root; refusing to start"
            exit 75
        fi
    else
        if ! mkdir -- "$root/.backup.lock.d" 2>/dev/null; then
            log "lock $root/.backup.lock.d exists: another run is in progress (if none is, remove that directory)"
            exit 75
        fi
        BACKUP_LOCK_DIR="$root/.backup.lock.d"
        printf '%s\n' "$$" >"$BACKUP_LOCK_DIR/pid"
        add_cleanup release_mkdir_lock
    fi
}
