#!/usr/bin/env bash
set -Eeuo pipefail

FINAL_SCRIPT="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

test -n "$FINAL_SCRIPT" || { printf 'ERROR: missing generated build script path\n' >&2; exit 1; }
test -f "$FINAL_SCRIPT" || { printf 'ERROR: generated build script not found: %s\n' "$FINAL_SCRIPT" >&2; exit 1; }

python3 "$SCRIPT_DIR/08_patch_resukisu_susfs_minimal.py" "$FINAL_SCRIPT"
python3 "$SCRIPT_DIR/08_patch_resukisu_susfs_includes.py" "$FINAL_SCRIPT"
bash -n "$FINAL_SCRIPT"
exec bash "$FINAL_SCRIPT"
