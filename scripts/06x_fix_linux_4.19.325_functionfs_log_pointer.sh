#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/functionfs-log-pointer-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before FunctionFS pointer repair"

python3 - "$KERNEL_DIR/drivers/usb/gadget/function/f_fs.c" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
start = text.index("static inline struct f_fs_opts *ffs_do_functionfs_bind(")
end = text.index("\nstatic int _ffs_func_bind", start)
block = text[start:end]

replacements = (
    ("\tstruct ffs_data *ffs_data;\n", "\tstruct ffs_data *ffs;\n", "declaration"),
    ("\tffs_data = ffs_opts->dev->ffs_data;\n", "\tffs = ffs_opts->dev->ffs_data;\n", "locked assignment"),
    ("\tfunc->ffs = ffs_data;\n", "\tfunc->ffs = ffs;\n", "function assignment"),
)

for old, new, label in replacements:
    old_count = block.count(old)
    new_count = block.count(new)
    if old_count == 1:
        block = block.replace(old, new, 1)
    elif old_count == 0 and new_count == 1:
        pass
    else:
        raise SystemExit(
            f"FunctionFS {label} anchor mismatch: old={old_count}, new={new_count}"
        )

text = text[:start] + block + text[end:]
path.write_text(text)

final = path.read_text()
start = final.index("static inline struct f_fs_opts *ffs_do_functionfs_bind(")
end = final.index("\nstatic int _ffs_func_bind", start)
block = final[start:end]

required = (
    "\tstruct ffs_data *ffs;\n",
    "\tffs = ffs_opts->dev->ffs_data;\n",
    "\tfunc->ffs = ffs;\n",
    'ffs_log("functionfs_bind returned %d", ret);',
)
for item in required:
    if block.count(item) != 1:
        raise SystemExit(f"FunctionFS postcondition failed for: {item!r}")

obsolete_identifiers = (
    "\tstruct ffs_data *ffs_data;\n",
    "\tffs_data = ffs_opts->dev->ffs_data;\n",
    "\tfunc->ffs = ffs_data;\n",
)
for item in obsolete_identifiers:
    if item in block:
        raise SystemExit(f"obsolete FunctionFS variable form remains: {item!r}")

lock_pos = block.index("ffs_dev_lock();")
assign_pos = block.index("ffs = ffs_opts->dev->ffs_data;")
unlock_pos = block.index("ffs_dev_unlock();")
if not lock_pos < assign_pos < unlock_pos:
    raise SystemExit("FunctionFS pointer assignment is not protected by the configfs lock")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/usb/gadget/function/f_fs.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'pointer_name=ffs-for-samsung-ipc-log-macro\n'
  printf 'assignment=inside-configfs-lock-window\n'
  printf 'result=linux-4.19.325-functionfs-log-pointer-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION FunctionFS logging pointer compatibility repaired"
