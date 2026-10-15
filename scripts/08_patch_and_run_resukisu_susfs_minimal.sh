#!/usr/bin/env bash
set -Eeuo pipefail

FINAL_SCRIPT="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

test -n "$FINAL_SCRIPT" || { printf 'ERROR: missing generated build script path\n' >&2; exit 1; }
test -f "$FINAL_SCRIPT" || { printf 'ERROR: generated build script not found: %s\n' "$FINAL_SCRIPT" >&2; exit 1; }

python3 "$SCRIPT_DIR/08_patch_resukisu_susfs_minimal.py" "$FINAL_SCRIPT"
python3 "$SCRIPT_DIR/08_patch_resukisu_susfs_includes.py" "$FINAL_SCRIPT"
python3 - "$FINAL_SCRIPT" <<'REMOVEUNSUPPORTEDUMOUNTPY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
anchor = 'info "Connecting ReSukiSU to the kernel build"\n'
block = r'''info "Removing unsupported SUSFS unmount helper reference"
python3 - "$RESUKISU_DIR/kernel/feature/kernel_umount.c" <<'KERNELUMOUNTPY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
lines = text.splitlines(keepends=True)
removed = [line for line in lines if 'susfs_extra_works' in line]
if not removed:
    raise SystemExit('unsupported susfs_extra_works reference not found')
path.write_text(''.join(line for line in lines if 'susfs_extra_works' not in line))
KERNELUMOUNTPY

'''
if text.count(anchor) != 1:
    raise SystemExit('ReSukiSU build connection anchor mismatch')
path.write_text(text.replace(anchor, block + anchor, 1))
REMOVEUNSUPPORTEDUMOUNTPY
bash -n "$FINAL_SCRIPT"
exec bash "$FINAL_SCRIPT" "$@"
