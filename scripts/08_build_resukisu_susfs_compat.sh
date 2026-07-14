#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/08_build_resukisu_safe_checkpoint.sh"
GENERATED="$SCRIPT_DIR/.generated-resukisu-susfs-minimal.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

python3 - "$SOURCE" "$GENERATED" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
text = source.read_text()

# Install the pinned Linux 4.19 SUSFS source, but adapt ReSukiSU to the actual
# 1.4.2 API instead of supplying fake newer entry points.
anchor = 'cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"\n'
minimal = r'''cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"
# SUSFS 1.4.2 only includes core_hook.h for its disabled legacy SUS_SU mode.
sed -i '/#include "\.\.\/drivers\/kernelsu\/core_hook\.h"/d' "$KERNEL_DIR/fs/susfs.c"

# ReSukiSU uses this magic to route SUSFS userspace commands through reboot(2).
# The 1.4.2 header predates the shared definition, so add only the magic value.
python3 - "$KERNEL_DIR/include/linux/susfs.h" <<'SUSFSHEADERPY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
if '#define SUSFS_MAGIC 0xFAFAFAFA\n' not in text:
    guard = '#define KSU_SUSFS_H\n'
    if text.count(guard) != 1:
        raise SystemExit('include/linux/susfs.h guard anchor mismatch')
    text = text.replace(guard, guard + '\n#define SUSFS_MAGIC 0xFAFAFAFA\n', 1)
    path.write_text(text)
SUSFSHEADERPY

# Adapt the ReSukiSU dispatcher to the real SUSFS 1.4.2 command API. Do not
# provide no-op functions for unsupported newer commands.
python3 - "$RESUKISU_DIR/kernel/supercall/dispatch.c" <<'DISPATCHPY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
text = text.replace('#include <linux/susfs_def.h>\n', '#include <linux/susfs.h>\n', 1)
text = text.replace(
    '#ifdef CONFIG_KSU_SUSFS\n                susfs_start_sdcard_monitor_fn();\n#endif\n',
    '',
    1,
)
start_marker = '#ifdef CONFIG_KSU_SUSFS\nint ksu_handle_susfs_cmd(unsigned int cmd, void __user **arg)\n'
end_marker = '#endif\n\n#ifdef CONFIG_KSU_TOOLKIT_SUPPORT\n'
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit('ReSukiSU SUSFS dispatcher block not found')
replacement = r'''#ifdef CONFIG_KSU_SUSFS
int ksu_handle_susfs_cmd(unsigned int cmd, void __user **arg)
{
    void __user *uarg = *arg;

    switch (cmd) {
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
    case CMD_SUSFS_ADD_SUS_PATH:
        return susfs_add_sus_path((struct st_susfs_sus_path __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
    case CMD_SUSFS_ADD_SUS_KSTAT:
    case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY:
        return susfs_add_sus_kstat((struct st_susfs_sus_kstat __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
    case CMD_SUSFS_SET_UNAME:
        return susfs_set_uname((struct st_susfs_uname __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
    case CMD_SUSFS_ENABLE_LOG: {
        bool enabled;
        if (copy_from_user(&enabled, uarg, sizeof(enabled)))
            return -EFAULT;
        susfs_set_log(enabled);
        return 0;
    }
#endif
    default:
        return -EOPNOTSUPP;
    }
}
#endif

#ifdef CONFIG_KSU_TOOLKIT_SUPPORT
'''
text = text[:start] + replacement + text[end + len(end_marker):]
path.write_text(text)
DISPATCHPY
'''
if text.count(anchor) != 1:
    raise SystemExit('SUSFS header-copy anchor mismatch')
text = text.replace(anchor, minimal, 1)

# The safe template installs manual-mode fstat return hooks. In SUSFS mode,
# ReSukiSU provides ksu_handle_vfs_fstat(), which must adjust the kernel kstat
# before it is copied to userspace. Replace the manual hooks after their block.
fstat_anchor = 'stat.write_text(text)\nPY\n\ninfo "Adapting ReSukiSU to Linux 4.19 and disabling unmount defaults"\n'
fstat_fix = r'''stat.write_text(text)
PY

info "Switching fstat integration to the native ReSukiSU SUSFS path"
python3 - "$KERNEL_DIR/fs/stat.c" <<'SUSFSFSTATPY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old_decl = ('#ifdef CONFIG_KSU\n'
            'extern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\n'
            'extern void ksu_handle_newfstat_ret(unsigned int *fd, struct stat __user **statbuf_ptr);\n'
            '#if defined(__ARCH_WANT_STAT64) || defined(__ARCH_WANT_COMPAT_STAT64)\n'
            'extern void ksu_handle_fstat64_ret(unsigned long *fd, struct stat64 __user **statbuf_ptr);\n'
            '#endif\n#endif\n')
new_decl = ('#ifdef CONFIG_KSU\n'
            'extern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\n'
            'extern void ksu_handle_vfs_fstat(int fd, loff_t *kstat_size_ptr);\n'
            '#endif\n')
if text.count(old_decl) != 1:
    raise SystemExit('manual fstat declarations not found')
text = text.replace(old_decl, new_decl, 1)

old_newfstat = ('SYSCALL_DEFINE2(newfstat, unsigned int, fd, struct stat __user *, statbuf)\n'
                '{\n'
                '\tstruct kstat stat;\n'
                '\tint error = vfs_fstat(fd, &stat);\n\n'
                '\tif (!error)\n'
                '\t\terror = cp_new_stat(&stat, statbuf);\n'
                '#ifdef CONFIG_KSU\n'
                '\tif (!error)\n'
                '\t\tksu_handle_newfstat_ret(&fd, &statbuf);\n'
                '#endif\n\n'
                '\treturn error;\n'
                '}\n')
new_newfstat = ('SYSCALL_DEFINE2(newfstat, unsigned int, fd, struct stat __user *, statbuf)\n'
                '{\n'
                '\tstruct kstat stat;\n'
                '\tint error = vfs_fstat(fd, &stat);\n'
                '#ifdef CONFIG_KSU\n'
                '\tif (!error)\n'
                '\t\tksu_handle_vfs_fstat(fd, &stat.size);\n'
                '#endif\n\n'
                '\tif (!error)\n'
                '\t\terror = cp_new_stat(&stat, statbuf);\n\n'
                '\treturn error;\n'
                '}\n')
if text.count(old_newfstat) != 1:
    raise SystemExit('manual newfstat hook not found')
text = text.replace(old_newfstat, new_newfstat, 1)

old_fstat64 = ('SYSCALL_DEFINE2(fstat64, unsigned long, fd, struct stat64 __user *, statbuf)\n'
               '{\n'
               '\tstruct kstat stat;\n'
               '\tint error = vfs_fstat(fd, &stat);\n\n'
               '\tif (!error)\n'
               '\t\terror = cp_new_stat64(&stat, statbuf);\n'
               '#ifdef CONFIG_KSU\n'
               '\tif (!error)\n'
               '\t\tksu_handle_fstat64_ret(&fd, &statbuf);\n'
               '#endif\n\n'
               '\treturn error;\n'
               '}\n')
new_fstat64 = ('SYSCALL_DEFINE2(fstat64, unsigned long, fd, struct stat64 __user *, statbuf)\n'
               '{\n'
               '\tstruct kstat stat;\n'
               '\tint error = vfs_fstat(fd, &stat);\n'
               '#ifdef CONFIG_KSU\n'
               '\tif (!error)\n'
               '\t\tksu_handle_vfs_fstat((int)fd, &stat.size);\n'
               '#endif\n\n'
               '\tif (!error)\n'
               '\t\terror = cp_new_stat64(&stat, statbuf);\n\n'
               '\treturn error;\n'
               '}\n')
if text.count(old_fstat64) != 1:
    raise SystemExit('manual fstat64 hook not found')
text = text.replace(old_fstat64, new_fstat64, 1)
path.write_text(text)
SUSFSFSTATPY

grep -Fq 'ksu_handle_vfs_fstat(fd, &stat.size);' "$KERNEL_DIR/fs/stat.c" || \
  fail "Native SUSFS fstat hook is missing"
! grep -Fq 'ksu_handle_newfstat_ret' "$KERNEL_DIR/fs/stat.c" || \
  fail "Manual newfstat hook remains in SUSFS build"

info "Adapting ReSukiSU to Linux 4.19 and disabling unmount defaults"
'''
if text.count(fstat_anchor) != 1:
    raise SystemExit('fstat post-processing anchor mismatch')
text = text.replace(fstat_anchor, fstat_fix, 1)

# Enable the SUSFS hook method, but disable every optional hiding/spoofing
# feature for the first on-device root/module validation build.
old_config = ('  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -e KSU_MANUAL_HOOK \\\n'
              '  -d KSU_SUSFS -e KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
              '  -e KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -e KSU_MANUAL_HOOK_AUTO_INPUT_HOOK')
new_config = ('  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -d KSU_MANUAL_HOOK \\\n'
              '  -e KSU_SUSFS -d KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
              '  -d KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -d KSU_MANUAL_HOOK_AUTO_INPUT_HOOK \\\n'
              '  -d KSU_SUSFS_SUS_PATH -d KSU_SUSFS_SUS_MOUNT \\\n'
              '  -d KSU_SUSFS_SUS_KSTAT -d KSU_SUSFS_SPOOF_UNAME \\\n'
              '  -d KSU_SUSFS_ENABLE_LOG -d KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \\\n'
              '  -d KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG -d KSU_SUSFS_OPEN_REDIRECT \\\n'
              '  -d KSU_SUSFS_SUS_MAP')
if text.count(old_config) != 1:
    raise SystemExit('ReSukiSU config anchor mismatch')
text = text.replace(old_config, new_config, 1)

text = text.replace('resukisu-v4.1.0-safe', 'resukisu-v4.1.0-susfs-v1.4.2-minimal-test')
text = text.replace("require_line \"$FINAL_CONFIG\" 'CONFIG_KSU_MANUAL_HOOK=y'",
                    "require_line \"$FINAL_CONFIG\" '# CONFIG_KSU_MANUAL_HOOK is not set'")
text = text.replace("require_line \"$FINAL_CONFIG\" '# CONFIG_KSU_SUSFS is not set'",
                    "require_line \"$FINAL_CONFIG\" 'CONFIG_KSU_SUSFS=y'")
text = text.replace("printf 'resukisu_version=%s\\n' \"$RESUKISU_VERSION_FULL\"",
                    "printf 'resukisu_version=%s\\n' \"$RESUKISU_VERSION_FULL\"\n  printf 'susfs_version=%s\\n' \"$SUSFS_VERSION\"\n  printf 'susfs_profile=minimal-root-module-test\\n'")

out.write_text(text)
out.chmod(0o755)
PY

exec "$GENERATED" "$@"
