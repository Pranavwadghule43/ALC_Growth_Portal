#!/usr/bin/env bash
# Apply the retention policy inside BACKUP_ROOT (operational recommendation, not a contract):
#   keep the newest valid set of each of the last BACKUP_KEEP_DAILY days (default 7),
#   BACKUP_KEEP_WEEKLY ISO weeks (4) and BACKUP_KEEP_MONTHLY months (3); always keep the newest
#   valid set; delete INCOMPLETE sets only when a newer valid set exists; ignore anything that
#   is not a backup set directory. Nothing outside BACKUP_ROOT is ever touched.
# Usage: retention.sh [--apply]     (without --apply it only prints the plan)
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

apply=()
case "${1:-}" in
    "") ;;
    --apply) apply=(--apply) ;;
    *) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
[[ $# -le 1 ]] || die "unexpected arguments"
require_env BACKUP_ROOT
require_cmd "$BACKUP_PYTHON"
# shellcheck disable=SC2153  # BACKUP_ROOT is provided by the environment
root="$(tool check-root "$BACKUP_ROOT")"
acquire_lock "$root"
log "retention ${apply[*]:-(dry run)} in $root"
tool prune "$root" "${apply[@]}" \
    --daily "${BACKUP_KEEP_DAILY:-7}" --weekly "${BACKUP_KEEP_WEEKLY:-4}" \
    --monthly "${BACKUP_KEEP_MONTHLY:-3}"
