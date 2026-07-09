#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESOLVER="$SCRIPT_DIR/09_fix_susfs_v1_5_5_a52xq_resolver_v3.sh"

test -f "$RESOLVER" || {
  echo "Missing A52XQ SUSFS resolver: $RESOLVER" >&2
  exit 1
}

python3 - "$RESOLVER" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = "                           'static void cleanup_group_ids(',\n"
new = "                           'static void cleanup_mnt(',\n"
count = text.count(old)
if count != 1:
    raise SystemExit(f"clone_mnt boundary: expected one match, found {count}")
path.write_text(text.replace(old, new, 1))
PY

exec "$RESOLVER" "$@"
