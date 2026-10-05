#!/usr/bin/env bash
# Verify an existing backup set without restoring anything:
#   manifest present, complete and well-formed; no INCOMPLETE marker; dump size + SHA256;
#   dump readable by pg_restore --list; inventory SHA256; every stored object present with the
#   recorded size and SHA256; no unexpected files.
# Usage: verify_backup.sh <BACKUP_ROOT>/<YYYYMMDDTHHMMSSZ>
# Exit status 0 only when every check passes.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[[ $# -eq 1 && "$1" != -* ]] || { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[[ -d "$1" ]] || die "backup set not found: $1"
require_cmd pg_restore "$BACKUP_PYTHON"
tool verify "$1"
