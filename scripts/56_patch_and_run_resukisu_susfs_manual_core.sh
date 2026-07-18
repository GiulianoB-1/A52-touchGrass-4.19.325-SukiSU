#!/usr/bin/env bash
set -Eeuo pipefail

FINAL_SCRIPT="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

test -n "$FINAL_SCRIPT" || { echo 'ERROR: missing generated build script path' >&2; exit 1; }
test -f "$FINAL_SCRIPT" || { echo "ERROR: generated build script not found: $FINAL_SCRIPT" >&2; exit 1; }

python3 "$SCRIPT_DIR/56_patch_resukisu_susfs_manual_core.py" "$FINAL_SCRIPT"
bash -n "$FINAL_SCRIPT"
exec bash "$FINAL_SCRIPT"
