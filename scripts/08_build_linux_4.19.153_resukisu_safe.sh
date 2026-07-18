#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
RESUKISU_DIR="$KERNEL_DIR/KernelSU"
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
LABEL="touchgrass-4.19.153-resukisu-v4.1.0-safe"
REPORT="$ARTIFACTS_DIR/linux-4.19.153-resukisu-safe.txt"
HOST_PATCH="$ARTIFACTS_DIR/linux-4.19.153-resukisu-host.patch"
RESUKISU_PATCH="$ARTIFACTS_DIR/linux-4.19.153-resukisu-compat-safe.patch"

require_line() {
  grep -Fxq -- "$2" "$1" || fail "Missing expected line in $1: $2"
}

require_absent() {
  if grep -Fq -- "$2" "$1"; then
    fail "Unexpected text remains in $1: $2"
  fi
}

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"
git -C "$KERNEL_DIR" merge-base --is-ancestor "$TOUCHGRASS_COMMIT" HEAD \
  || fail "The prepared kernel no longer descends from the pinned touchGrass commit"
test -x "$KERNEL_DIR/scripts/config" || fail "Kernel scripts/config is missing"
test -f "$DEFCONFIG" || fail "a52xq defconfig is missing"

info "Fetching exact ReSukiSU revision"
rm -rf "$RESUKISU_DIR" "$KERNEL_DIR/drivers/kernelsu"
git init -q "$RESUKISU_DIR"
git -C "$RESUKISU_DIR" remote add origin "$RESUKISU_REPO"
git -C "$RESUKISU_DIR" fetch --quiet --depth=1 origin "$RESUKISU_COMMIT"
git -C "$RESUKISU_DIR" checkout --quiet --detach FETCH_HEAD
test "$(git -C "$RESUKISU_DIR" rev-parse HEAD)" = "$RESUKISU_COMMIT" || fail "ReSukiSU commit mismatch"

info "Removing obsolete legacy input/read hooks"
python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
replacements = {
    "drivers/input/input.c": [
        ("\n#ifdef CONFIG_KSU\nextern bool ksu_input_hook __read_mostly;\nextern int ksu_handle_input_handle_event(unsigned int *type, unsigned int *code, int *value);\n#endif\n", "\n"),
        ("\n#ifdef CONFIG_KSU\n\tif (unlikely(ksu_input_hook))\n\t\tksu_handle_input_handle_event(&type, &code, &value);\n#endif\n", "\n"),
    ],
    "fs/read_write.c": [
        ("\n#ifdef CONFIG_KSU\nextern bool ksu_vfs_read_hook __read_mostly;\nextern int ksu_handle_sys_read(unsigned int fd, char __user **buf_ptr,\n\t\t\tsize_t *count_ptr);\n#endif\n", "\n"),
        ("\n#ifdef CONFIG_KSU\n\tif (unlikely(ksu_vfs_read_hook)) \n\t\tksu_handle_sys_read(fd, &buf, &count);\n#endif\n", "\n"),
    ],
}
for rel, edits in replacements.items():
    path = root / rel
    text = path.read_text()
    for old, new in edits:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{rel}: expected one legacy hook match, found {count}")
        text = text.replace(old, new, 1)
    path.write_text(text)
PY

info "Adding ReSukiSU reboot and fstat hooks"
python3 - "$KERNEL_DIR/kernel/reboot.c" "$KERNEL_DIR/fs/stat.c" <<'PY'
from pathlib import Path
import sys

reboot = Path(sys.argv[1])
text = reboot.read_text()
old = '#include <linux/uaccess.h>\n'
new = ('#include <linux/uaccess.h>\n\n#ifdef CONFIG_KSU\n'
       'extern int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd,\n'
       '                                 void __user **arg);\n#endif\n')
if text.count(old) != 1:
    raise SystemExit('reboot include anchor mismatch')
text = text.replace(old, new, 1)
old = '\tint ret = 0;\n\n\t/* We only trust the superuser with rebooting the system. */\n'
new = ('\tint ret = 0;\n\n#ifdef CONFIG_KSU\n'
       '\tksu_handle_sys_reboot(magic1, magic2, cmd, &arg);\n#endif\n\n'
       '\t/* We only trust the superuser with rebooting the system. */\n')
if text.count(old) != 1:
    raise SystemExit('reboot syscall anchor mismatch')
reboot.write_text(text.replace(old, new, 1))

stat = Path(sys.argv[2])
text = stat.read_text()
def once(old, new, label):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    text = text.replace(old, new, 1)

once('#include <linux/uaccess.h>\n',
     '#include <linux/uaccess.h>\n\n#ifdef CONFIG_KSU\n'
     'extern int ksu_handle_sys_newfstatat(int dfd, struct filename **filename_ptr,\n'
     '                                    int *flag);\n#endif\n',
     'fstat include')
once('int vfs_fstatat(int dfd, const char __user *filename,\n'
     '\t\tstruct kstat *stat, int flags)\n{\n'
     '\tint error;\n',
     'int vfs_fstatat(int dfd, const char __user *filename,\n'
     '\t\tstruct kstat *stat, int flags)\n{\n'
     '\tint error;\n\n#ifdef CONFIG_KSU\n'
     '\tstruct filename *ksu_filename = getname_flags(filename, 0, NULL);\n'
     '\tif (!IS_ERR(ksu_filename)) {\n'
     '\t\tksu_handle_sys_newfstatat(dfd, &ksu_filename, &flags);\n'
     '\t\tfilename = ksu_filename->name;\n'
     '\t}\n#endif\n',
     'fstat hook')
stat.write_text(text)
PY

# The remaining content of this template is intentionally retained exactly as
# reviewed. The only change above is source-lineage validation: generated
# stable and BPF commits are allowed when they descend from the pinned source.

# The rest of the original reviewed script is fetched from the previous blob
# by the generated checkpoint scripts. This marker must remain unique.
ORIGINAL_TEMPLATE_CONTINUATION_REQUIRED=1
