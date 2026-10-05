#!/usr/bin/env bash
# Restore the evidence objects of a verified backup set, under their exact keys.
#   * objects missing from the target are uploaded;
#   * objects already identical are skipped;
#   * objects that exist with different content stop the restore (nothing is written) unless
#     --force is given AND RESTORE_CONFIRM equals the target bucket name (or local path);
#   * objects in the target that are not in the backup are never modified or deleted.
# Usage: restore_storage.sh [--force] [--deep-verify] <backup set directory>
#   --deep-verify  re-download every restored object and compare its SHA256
# Target: STORAGE_BACKEND plus S3_BUCKET / S3_ACCESS_KEY / S3_SECRET_KEY [/ S3_ENDPOINT_URL],
# or LOCAL_STORAGE_PATH. Restore into a new, private bucket first when practising.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }
options=()
set_dir=""
for arg in "$@"; do
    case "$arg" in
        --force | --deep-verify) options+=("$arg") ;;
        -h | --help) usage; exit 0 ;;
        -*) die "unknown option: $arg" ;;
        *) [[ -z "$set_dir" ]] || die "only one backup set may be given"; set_dir="$arg" ;;
    esac
done
[[ -n "$set_dir" ]] || { usage; exit 2; }
[[ -d "$set_dir" ]] || die "backup set not found: $set_dir"
require_env STORAGE_BACKEND
require_cmd pg_restore "$BACKUP_PYTHON"

log "verifying backup set $set_dir before restoring"
tool verify "$set_dir"
tool storage-restore "$set_dir" --backend "$STORAGE_BACKEND" "${options[@]}"
