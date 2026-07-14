#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/08_build_resukisu_safe_checkpoint.sh"
GENERATED="$SCRIPT_DIR/.generated-resukisu-susfs-minimal-checkpoint.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

python3 - "$SOURCE" "$GENERATED" <<'CHECKPOINTWRAPPERPY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
text = source.read_text()
anchor = 'bash "$GENERATED"\n'
replacement = '"$SCRIPT_DIR/08_patch_and_run_resukisu_susfs_minimal.sh" "$GENERATED"\n'
if text.count(anchor) != 1:
    raise SystemExit('safe checkpoint final execution anchor mismatch')
out.write_text(text.replace(anchor, replacement, 1))
out.chmod(0o755)
CHECKPOINTWRAPPERPY

exec "$GENERATED" "$@"
