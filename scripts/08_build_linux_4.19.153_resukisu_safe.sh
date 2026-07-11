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
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass commit"
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

once(
    '#ifdef CONFIG_KSU\nextern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\n#endif\n',
    '#ifdef CONFIG_KSU\nextern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\nextern void ksu_handle_newfstat_ret(unsigned int *fd, struct stat __user **statbuf_ptr);\n#if defined(__ARCH_WANT_STAT64) || defined(__ARCH_WANT_COMPAT_STAT64)\nextern void ksu_handle_fstat64_ret(unsigned long *fd, struct stat64 __user **statbuf_ptr);\n#endif\n#endif\n',
    'stat declarations')
once(
    '\tif (!error)\n\t\terror = cp_new_stat(&stat, statbuf);\n\n\treturn error;\n}\n',
    '\tif (!error)\n\t\terror = cp_new_stat(&stat, statbuf);\n#ifdef CONFIG_KSU\n\tif (!error)\n\t\tksu_handle_newfstat_ret(&fd, &statbuf);\n#endif\n\n\treturn error;\n}\n',
    'newfstat return hook')
once(
    '\tif (!error)\n\t\terror = cp_new_stat64(&stat, statbuf);\n\n\treturn error;\n}\n',
    '\tif (!error)\n\t\terror = cp_new_stat64(&stat, statbuf);\n#ifdef CONFIG_KSU\n\tif (!error)\n\t\tksu_handle_fstat64_ret(&fd, &statbuf);\n#endif\n\n\treturn error;\n}\n',
    'fstat64 return hook')
stat.write_text(text)
PY

info "Adapting ReSukiSU to Linux 4.19 and disabling unmount defaults"
python3 - "$RESUKISU_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])

def replace_once(path, old, new, label):
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    path.write_text(text.replace(old, new, 1))

replace_once(
    root / 'kernel/supercall/dispatch.c',
    '#include <linux/thread_info.h>\n',
    '#include <linux/thread_info.h>\n#include <linux/pid.h>\n#include <linux/sched/signal.h>\n#include <linux/sched/task.h>\n',
    'scheduler includes')
replace_once(
    root / 'kernel/policy/allowlist.c',
    'default_non_root_profile.umount_modules = true;',
    'default_non_root_profile.umount_modules = false;',
    'default module unmount')
replace_once(
    root / 'kernel/feature/kernel_umount.c',
    'static bool ksu_kernel_umount_enabled = true;',
    'static bool ksu_kernel_umount_enabled = false;',
    'kernel unmount default')
PY

info "Connecting ReSukiSU to the kernel build"
ln -s ../KernelSU/kernel "$KERNEL_DIR/drivers/kernelsu"
python3 - "$KERNEL_DIR/drivers/Makefile" "$KERNEL_DIR/drivers/Kconfig" <<'PY'
from pathlib import Path
import sys
makefile = Path(sys.argv[1])
kconfig = Path(sys.argv[2])
make_line = 'obj-$(CONFIG_KSU) += kernelsu/'
source_line = 'source "drivers/kernelsu/Kconfig"'
make_text = '\n'.join(line for line in makefile.read_text().splitlines() if not ('CONFIG_KSU' in line and 'kernelsu' in line)).rstrip()
makefile.write_text(make_text + '\n\n' + make_line + '\n')
kconfig_text = '\n'.join(line for line in kconfig.read_text().splitlines() if line.strip() != source_line)
pos = kconfig_text.rfind('\nendmenu')
if pos < 0:
    raise SystemExit('drivers/Kconfig final endmenu not found')
kconfig.write_text(kconfig_text[:pos] + '\n' + source_line + kconfig_text[pos:] + '\n')
PY

"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \
  -e MODULES -e EXT4_FS -d KPROBES -e KSU -d KSU_DEBUG \
  -d KSU_TOOLKIT_SUPPORT -d KSU_DISABLE_MANAGER -d KSU_DISABLE_POLICY \
  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -e KSU_MANUAL_HOOK \
  -d KSU_SUSFS -e KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \
  -e KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -e KSU_MANUAL_HOOK_AUTO_INPUT_HOOK
python3 - "$DEFCONFIG" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
line = 'CONFIG_KSU_FULL_NAME_FORMAT="%TAG_NAME%-%COMMIT_SHA%@%REPO_NAME%"'
lines = [x for x in path.read_text().splitlines() if not x.startswith('CONFIG_KSU_FULL_NAME_FORMAT=')]
lines.append(line)
path.write_text('\n'.join(lines) + '\n')
PY

require_absent "$KERNEL_DIR/fs/read_write.c" "ksu_vfs_read_hook"
require_line "$KERNEL_DIR/drivers/Makefile" 'obj-$(CONFIG_KSU) += kernelsu/'
require_line "$KERNEL_DIR/drivers/Kconfig" 'source "drivers/kernelsu/Kconfig"'
require_line "$RESUKISU_DIR/kernel/policy/allowlist.c" '    default_non_root_profile.umount_modules = false;'
require_line "$RESUKISU_DIR/kernel/feature/kernel_umount.c" 'static bool ksu_kernel_umount_enabled = false;'
require_absent "$RESUKISU_DIR/kernel/policy/allowlist.c" 'default_non_root_profile.umount_modules = true;'
require_absent "$RESUKISU_DIR/kernel/feature/kernel_umount.c" 'static bool ksu_kernel_umount_enabled = true;'

git -C "$KERNEL_DIR" diff --check
git -C "$RESUKISU_DIR" diff --check
git -C "$KERNEL_DIR" diff --binary -- arch/arm64/configs/a52xq_defconfig drivers/Makefile drivers/Kconfig drivers/input/input.c fs/read_write.c fs/stat.c kernel/reboot.c > "$HOST_PATCH"
git -C "$RESUKISU_DIR" diff --binary -- kernel/supercall/dispatch.c kernel/policy/allowlist.c kernel/feature/kernel_umount.c > "$RESUKISU_PATCH"
sha256sum "$HOST_PATCH" "$RESUKISU_PATCH" > "$ARTIFACTS_DIR/linux-4.19.153-resukisu-patches.sha256"

info "Building Linux 4.19.153 + safe ReSukiSU"
build_kernel "$LABEL"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-$LABEL"
FINAL_CONFIG="$ARTIFACTS_DIR/config-$LABEL"
strings "$FINAL_IMAGE" > "$ARTIFACTS_DIR/Image-$LABEL.strings.txt"
grep -Fq 'Linux version 4.19.153-touchGrassKernel+' "$ARTIFACTS_DIR/Image-$LABEL.strings.txt" || fail "Unexpected kernel version string"
grep -Fxq "$RESUKISU_VERSION_FULL" "$ARTIFACTS_DIR/Image-$LABEL.strings.txt" || fail "Expected ReSukiSU version string is missing"
require_line "$FINAL_CONFIG" 'CONFIG_KSU=y'
require_line "$FINAL_CONFIG" 'CONFIG_KSU_MANUAL_HOOK=y'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS is not set'

{
  printf 'status=checkpoint-passed\n'
  printf 'flashable=no\n'
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'touchgrass_commit=%s\n' "$TOUCHGRASS_COMMIT"
  printf 'resukisu_commit=%s\n' "$RESUKISU_COMMIT"
  printf 'resukisu_version=%s\n' "$RESUKISU_VERSION_FULL"
  printf 'module_unmount_default=off\n'
  printf 'kernel_unmount_default=off\n'
  printf 'image_sha256=%s\n' "$(sha256sum "$FINAL_IMAGE" | awk '{print $1}')"
  printf 'image_bytes=%s\n' "$(stat -c %s "$FINAL_IMAGE")"
} | tee "$REPORT"

info "Linux 4.19.153 + safe ReSukiSU checkpoint passed"