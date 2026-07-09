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

replacements = (
    (
        "                           'static void cleanup_group_ids(',\n",
        "                           'static void cleanup_mnt(',\n",
        1,
        "clone_mnt boundary",
    ),
    (
        "                           'static int __init init_mount_tree(',\n",
        "                           'static void __init init_mount_tree(',\n",
        2,
        "copy_mnt_ns boundary",
    ),
)

for old, new, expected, label in replacements:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} matches, found {count}")
    text = text.replace(old, new)

path.write_text(text)
PY

exec "$RESOLVER" "$@"
