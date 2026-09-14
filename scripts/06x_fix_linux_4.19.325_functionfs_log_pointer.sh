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

sig = "static inline struct f_fs_opts *ffs_do_functionfs_bind("
start = text.index(sig)
end = text.index("\nstatic int _ffs_func_bind", start)

canonical = """static inline struct f_fs_opts *ffs_do_functionfs_bind(struct usb_function *f,
						struct usb_configuration *c)
{
	struct ffs_function *func = ffs_func_from_usb(f);
	struct f_fs_opts *ffs_opts =
		container_of(f->fi, struct f_fs_opts, func_inst);
	struct ffs_data *ffs;
	int ret;

	ENTER();

	/*
	 * Legacy gadget triggers binding in functionfs_ready_callback,
	 * which already uses locking; taking the same lock here would
	 * cause a deadlock.
	 *
	 * Configfs-enabled gadgets however do need ffs_dev_lock.
	 */
	if (!ffs_opts->no_configfs)
		ffs_dev_lock();
	ret = ffs_opts->dev->desc_ready ? 0 : -ENODEV;
	ffs = ffs_opts->dev->ffs_data;
	if (!ffs_opts->no_configfs)
		ffs_dev_unlock();
	if (ret)
		return ERR_PTR(ret);

	func->ffs = ffs;
	func->conf = c;
	func->gadget = c->cdev->gadget;

	/*
	 * in drivers/usb/gadget/configfs.c:configfs_composite_bind()
	 * configurations are bound in sequence with list_for_each_entry,
	 * in each configuration its functions are bound in sequence
	 * with list_for_each_entry, so we assume no race condition
	 * with regard to ffs_opts->bound access
	 */
	if (!ffs_opts->refcnt) {
		ret = functionfs_bind(func->ffs, c->cdev);
		if (ret) {
			ffs_log("functionfs_bind returned %d", ret);
			return ERR_PTR(ret);
		}
	}
	ffs_opts->refcnt++;
	func->function.strings = func->ffs->stringtabs;

	return ffs_opts;
}
"""

text = text[:start] + canonical + text[end:]
path.write_text(text)

final = path.read_text()
start = final.index(sig)
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

for obsolete in (
    "struct ffs_data *ffs_data",
    "ffs_data = ffs_opts->dev->ffs_data;",
    "func->ffs = ffs_data;",
    "struct ffs_data *ffs = ffs_opts->dev->ffs_data;",
):
    if obsolete in block:
        raise SystemExit(f"obsolete FunctionFS pointer form remains: {obsolete!r}")

lock_pos = block.index("ffs_dev_lock();")
assign_pos = block.index("ffs = ffs_opts->dev->ffs_data;")
unlock_pos = block.index("ffs_dev_unlock();")
func_assign_pos = block.index("func->ffs = ffs;")
if not lock_pos < assign_pos < unlock_pos < func_assign_pos:
    raise SystemExit("FunctionFS snapshot/assignment ordering is invalid")
PY
git -C "$KERNEL_DIR" diff --check -- drivers/usb/gadget/function/f_fs.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'pointer_name=ffs-for-samsung-ipc-log-macro\n'
  printf 'assignment=inside-configfs-lock-window\n'
  printf 'result=linux-4.19.325-functionfs-log-pointer-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION FunctionFS logging pointer compatibility repaired"
