#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
SUSFS_PATCH_URL="https://raw.githubusercontent.com/sabamdarif/nashc-build-process/0842ae1b4f41ac474d90f6243852abc0e6ad97b3/patchs/4.19/add_only_susfs_1.5.5.patch"
SUSFS_PATCH_BLOB="36f5c8ca0dabdfe66255b91b1e32d9456b1a29ca"
SUSFS_PATCH="$ARTIFACTS_DIR/susfs-v1.5.5-linux-4.19.patch"
PATCH_LOG="$LOG_DIR/apply-susfs-v1.5.5.log"
REPORT="$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt"

KCONFIG="$SUKISU_DIR/kernel/Kconfig"
INIT_C="$SUKISU_DIR/kernel/core/init.c"
SELINUX_C="$SUKISU_DIR/kernel/selinux/selinux.c"
SELINUX_H="$SUKISU_DIR/kernel/selinux/selinux.h"
SUPERCALL_C="$SUKISU_DIR/kernel/supercall/supercall.c"
FS_MAKEFILE="$KERNEL_DIR/fs/Makefile"
SYS_C="$KERNEL_DIR/kernel/sys.c"
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
COMPAT_C="$KERNEL_DIR/fs/susfs_sukisu_compat.c"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SUSFS integration"
test -d "$SUKISU_DIR/.git" || fail "Pinned SukiSU source is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"
for file in "$KCONFIG" "$INIT_C" "$SELINUX_C" "$SELINUX_H" "$SUPERCALL_C" "$FS_MAKEFILE" "$SYS_C" "$DEFCONFIG"; do
  test -f "$file" || fail "Required integration target is missing: $file"
done

info "Fetching pinned SUSFS v1.5.5 Linux 4.19 patch"
curl -fL --retry 3 --connect-timeout 30 "$SUSFS_PATCH_URL" -o "$SUSFS_PATCH"
test "$(git hash-object "$SUSFS_PATCH")" = "$SUSFS_PATCH_BLOB" || fail "Unexpected SUSFS patch blob"
sha256sum "$SUSFS_PATCH" > "$SUSFS_PATCH.sha256"

info "Applying the pinned SUSFS v1.5.5 VFS patch"
set +e
git -C "$KERNEL_DIR" apply --reject --whitespace=nowarn "$SUSFS_PATCH" >"$PATCH_LOG" 2>&1
apply_rc=$?
set -e
find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"
if test -s "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"; then
  cat "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"
  fail "SUSFS v1.5.5 patch produced rejects; see $PATCH_LOG"
fi
test "$apply_rc" -eq 0 || fail "SUSFS v1.5.5 patch failed without reject files; see $PATCH_LOG"

info "Adding SukiSU and legacy userspace compatibility glue"
python3 - "$KCONFIG" "$INIT_C" "$SELINUX_C" "$SELINUX_H" "$SUPERCALL_C" "$FS_MAKEFILE" "$SYS_C" "$DEFCONFIG" <<'PY'
from pathlib import Path
import sys

kconfig_path, init_path, selinux_c_path, selinux_h_path, supercall_path, fs_makefile_path, sys_c_path, defconfig_path = map(Path, sys.argv[1:])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)

susfs_menu = r'''
menu "SukiSU - SUSFS v1.5.5"

config KSU_SUSFS
    bool "SUSFS v1.5.5 support"
    depends on KSU && THREAD_INFO_IN_TASK
    default y

config KSU_SUSFS_HAS_MAGIC_MOUNT
    bool "Magic Mount compatibility"
    depends on KSU_SUSFS
    default y

config KSU_SUSFS_SUS_PATH
    bool "Hide suspicious paths"
    depends on KSU_SUSFS
    default n

config KSU_SUSFS_SUS_MOUNT
    bool "Hide suspicious mounts"
    depends on KSU_SUSFS
    default y

config KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT
    bool "Automatically hide SukiSU mounts"
    depends on KSU_SUSFS_SUS_MOUNT
    default y

config KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT
    bool "Automatically hide bind mounts"
    depends on KSU_SUSFS_SUS_MOUNT
    default n

config KSU_SUSFS_SUS_KSTAT
    bool "Spoof suspicious kstat"
    depends on KSU_SUSFS
    default n

config KSU_SUSFS_SUS_OVERLAYFS
    bool "Spoof overlayfs kstat"
    depends on KSU_SUSFS
    default n

config KSU_SUSFS_TRY_UMOUNT
    bool "SUSFS try-umount list"
    depends on KSU_SUSFS
    default n

config KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT
    bool "Automatically add bind mounts to try-umount"
    depends on KSU_SUSFS_TRY_UMOUNT
    default n

config KSU_SUSFS_SPOOF_UNAME
    bool "Spoof uname"
    depends on KSU_SUSFS
    default y

config KSU_SUSFS_ENABLE_LOG
    bool "Enable SUSFS kernel logging"
    depends on KSU_SUSFS
    default n

config KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS
    bool "Hide SukiSU and SUSFS symbols"
    depends on KSU_SUSFS
    default y

config KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG
    bool "Spoof cmdline or bootconfig"
    depends on KSU_SUSFS
    default y

config KSU_SUSFS_OPEN_REDIRECT
    bool "Enable open redirect"
    depends on KSU_SUSFS
    default n

config KSU_SUSFS_SUS_SU
    bool "Enable deprecated SUS_SU"
    depends on KSU_SUSFS && KPROBES && HAVE_KPROBES && KPROBE_EVENTS
    default n

endmenu
'''

kconfig = kconfig_path.read_text()
if 'config KSU_SUSFS\n' in kconfig:
    raise SystemExit('Kconfig already contains KSU_SUSFS')
end = kconfig.rfind('endmenu')
if end < 0:
    raise SystemExit('Kconfig final endmenu not found')
kconfig = kconfig[:end] + susfs_menu + '\n' + kconfig[end:]
kconfig_path.write_text(kconfig)

init = init_path.read_text()
init = replace_once(init, '#include <linux/workqueue.h>\n', '#include <linux/workqueue.h>\n#ifdef CONFIG_KSU_SUSFS\n#include <linux/susfs.h>\n#endif\n', 'core/init.c SUSFS include')
init = replace_once(init, '    ksu_feature_init();\n', '    ksu_feature_init();\n#ifdef CONFIG_KSU_SUSFS\n    susfs_init();\n#endif\n', 'core/init.c SUSFS init')
init_path.write_text(init)

selinux_c = selinux_c_path.read_text()
selinux_c += r'''

#ifdef CONFIG_KSU_SUSFS
bool susfs_is_current_ksu_domain(void)
{
    return is_ksu_domain();
}

bool susfs_is_current_zygote_domain(void)
{
    return is_zygote(current_cred());
}
#endif
'''
selinux_c_path.write_text(selinux_c)

selinux_h = selinux_h_path.read_text()
selinux_h = replace_once(selinux_h, 'bool is_init(const struct cred *cred);\n', 'bool is_init(const struct cred *cred);\n\n#ifdef CONFIG_KSU_SUSFS\nbool susfs_is_current_ksu_domain(void);\nbool susfs_is_current_zygote_domain(void);\n#endif\n', 'selinux.h SUSFS declarations')
selinux_h_path.write_text(selinux_h)

supercall = supercall_path.read_text()
supercall = replace_once(supercall, '#include <linux/version.h>\n', '#include <linux/version.h>\n#ifdef CONFIG_KSU_SUSFS\n#include <linux/susfs.h>\n#define KSU_SUSFS_MAGIC 0xFAFAFAFA\nextern int susfs_sukisu_handle_reboot(unsigned int cmd, void __user *arg);\n#endif\n', 'supercall.c SUSFS include')
supercall = replace_once(supercall,
'''    int magic1 = (int)PT_REGS_PARM1(real_regs);
    int magic2 = (int)PT_REGS_PARM2(real_regs);

    if (magic1 == KSU_INSTALL_MAGIC1 && magic2 == KSU_INSTALL_MAGIC2) {
        struct ksu_install_fd_tw *tw;
        unsigned long arg4 = (unsigned long)PT_REGS_SYSCALL_PARM4(real_regs);
''',
'''    int magic1 = (int)PT_REGS_PARM1(real_regs);
    int magic2 = (int)PT_REGS_PARM2(real_regs);
    unsigned int cmd = (unsigned int)PT_REGS_PARM3(real_regs);
    unsigned long arg4 = (unsigned long)PT_REGS_SYSCALL_PARM4(real_regs);

#ifdef CONFIG_KSU_SUSFS
    if (magic1 == KSU_INSTALL_MAGIC1 && magic2 == KSU_SUSFS_MAGIC && current_uid().val == 0) {
        susfs_sukisu_handle_reboot(cmd, (void __user *)arg4);
        return 0;
    }
#endif

    if (magic1 == KSU_INSTALL_MAGIC1 && magic2 == KSU_INSTALL_MAGIC2) {
        struct ksu_install_fd_tw *tw;
''', 'supercall.c reboot ABI bridge')
supercall_path.write_text(supercall)

fs_makefile = fs_makefile_path.read_text()
if 'susfs_sukisu_compat.o' not in fs_makefile:
    fs_makefile = replace_once(fs_makefile, 'obj-$(CONFIG_KSU_SUSFS) += susfs.o\n', 'obj-$(CONFIG_KSU_SUSFS) += susfs.o\nobj-$(CONFIG_KSU_SUSFS) += susfs_sukisu_compat.o\n', 'fs/Makefile compatibility object')
fs_makefile_path.write_text(fs_makefile)

sys_c = sys_c_path.read_text()
if 'susfs_handle_prctl' not in sys_c:
    marker = 'SYSCALL_DEFINE5(prctl, int, option, unsigned long, arg2, unsigned long, arg3,\n\t\tunsigned long, arg4, unsigned long, arg5)\n{\n'
    replacement = '#ifdef CONFIG_KSU_SUSFS\nextern bool susfs_handle_prctl(int option, unsigned long arg2, unsigned long arg3,\n                               unsigned long arg4, unsigned long arg5);\n#endif\n\n' + marker + '#ifdef CONFIG_KSU_SUSFS\n\tif (susfs_handle_prctl(option, arg2, arg3, arg4, arg5))\n\t\treturn 0;\n#endif\n'
    sys_c = replace_once(sys_c, marker, replacement, 'kernel/sys.c legacy prctl bridge')
sys_c_path.write_text(sys_c)

config = defconfig_path.read_text().rstrip() + '\n'
config_lines = r'''
CONFIG_KSU_SUSFS=y
CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y
# CONFIG_KSU_SUSFS_SUS_PATH is not set
CONFIG_KSU_SUSFS_SUS_MOUNT=y
CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT=y
# CONFIG_KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT is not set
# CONFIG_KSU_SUSFS_SUS_KSTAT is not set
# CONFIG_KSU_SUSFS_SUS_OVERLAYFS is not set
# CONFIG_KSU_SUSFS_TRY_UMOUNT is not set
# CONFIG_KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT is not set
CONFIG_KSU_SUSFS_SPOOF_UNAME=y
# CONFIG_KSU_SUSFS_ENABLE_LOG is not set
CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y
CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y
# CONFIG_KSU_SUSFS_OPEN_REDIRECT is not set
# CONFIG_KSU_SUSFS_SUS_SU is not set
'''.lstrip()
for symbol in ('CONFIG_KSU_SUSFS=', 'CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=', 'CONFIG_KSU_SUSFS_SUS_MOUNT='):
    if symbol in config:
        raise SystemExit(f'defconfig already contains {symbol}')
defconfig_path.write_text(config + config_lines)
PY

cat > "$COMPAT_C" <<'EOF'
#include <linux/cred.h>
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/susfs.h>
#include <linux/susfs_def.h>

#define KERNEL_SU_OPTION 0xDEADBEEF
#define SUSFS_ENABLED_FEATURES_SIZE 8192

struct sukisu_susfs_version {
    char value[16];
    int err;
};

struct sukisu_susfs_features {
    char value[SUSFS_ENABLED_FEATURES_SIZE];
    int err;
};

struct sukisu_susfs_variant {
    char value[16];
    int err;
};

struct sukisu_susfs_uname {
    char release[65];
    char version[65];
    int err;
};

struct sukisu_susfs_toggle {
    u32 enabled;
    int err;
};

static int susfs_copy_result(void __user *arg, const void *value, size_t size)
{
    return copy_to_user(arg, value, size) ? -EFAULT : 0;
}

static int susfs_write_err(void __user *arg, size_t offset, int err)
{
    return copy_to_user((char __user *)arg + offset, &err, sizeof(err)) ? -EFAULT : err;
}

static int susfs_show_version_compat(void __user *arg)
{
    struct sukisu_susfs_version out = { .err = 0 };
    strlcpy(out.value, SUSFS_VERSION, sizeof(out.value));
    return susfs_copy_result(arg, &out, sizeof(out));
}

static int susfs_show_variant_compat(void __user *arg)
{
    struct sukisu_susfs_variant out = { .err = 0 };
    strlcpy(out.value, SUSFS_VARIANT, sizeof(out.value));
    return susfs_copy_result(arg, &out, sizeof(out));
}

static int susfs_show_features_compat(void __user *arg)
{
    struct sukisu_susfs_features *out;
    int ret;

    out = kzalloc(sizeof(*out), GFP_KERNEL);
    if (!out)
        return -ENOMEM;

    scnprintf(out->value, sizeof(out->value),
              "SUS_MOUNT\nMAGIC_MOUNT\nSPOOF_UNAME\nHIDE_KSU_SUSFS_SYMBOLS\nSPOOF_CMDLINE_OR_BOOTCONFIG\n");
    out->err = 0;
    ret = susfs_copy_result(arg, out, sizeof(*out));
    kfree(out);
    return ret;
}

int susfs_sukisu_handle_reboot(unsigned int cmd, void __user *arg)
{
    int err = -EOPNOTSUPP;

    if (!arg)
        return -EINVAL;

    switch (cmd) {
    case CMD_SUSFS_SHOW_VERSION:
        return susfs_show_version_compat(arg);
    case CMD_SUSFS_SHOW_VARIANT:
        return susfs_show_variant_compat(arg);
    case CMD_SUSFS_SHOW_ENABLED_FEATURES:
        return susfs_show_features_compat(arg);
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
    case CMD_SUSFS_SET_UNAME:
        err = susfs_set_uname((struct st_susfs_uname __user *)arg);
        return susfs_write_err(arg, offsetof(struct sukisu_susfs_uname, err), err);
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
    case CMD_SUSFS_ENABLE_LOG: {
        struct sukisu_susfs_toggle toggle;
        if (copy_from_user(&toggle, arg, sizeof(toggle)))
            return -EFAULT;
        susfs_set_log(toggle.enabled != 0);
        return susfs_write_err(arg, offsetof(struct sukisu_susfs_toggle, err), 0);
    }
#endif
    case 0x55561: /* CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS */
        return susfs_write_err(arg, offsetof(struct sukisu_susfs_toggle, err), 0);
#ifdef CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG
    case CMD_SUSFS_SET_CMDLINE_OR_BOOTCONFIG:
        err = susfs_set_cmdline_or_bootconfig((char __user *)arg);
        return susfs_write_err(arg, SUSFS_FAKE_CMDLINE_OR_BOOTCONFIG_SIZE, err);
#endif
    default:
        return -EOPNOTSUPP;
    }
}

static void susfs_reply_error(unsigned long arg5, int error)
{
    if (arg5)
        copy_to_user((void __user *)arg5, &error, sizeof(error));
}

static int susfs_copy_legacy_string(unsigned long arg3, const char *value)
{
    size_t len = strlen(value) + 1;
    return copy_to_user((void __user *)arg3, value, len) ? -EFAULT : 0;
}

bool susfs_handle_prctl(int option, unsigned long arg2, unsigned long arg3,
                        unsigned long arg4, unsigned long arg5)
{
    int error = 0;
    u64 features = 0;
    int mode = SUS_SU_DISABLED;
    int ready = 0;

    (void)arg4;
    if ((unsigned int)option != KERNEL_SU_OPTION)
        return false;
    if (current_uid().val != 0)
        return true;

    switch (arg2) {
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
    case CMD_SUSFS_ADD_SUS_PATH:
        error = susfs_add_sus_path((struct st_susfs_sus_path __user *)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
    case CMD_SUSFS_ADD_SUS_MOUNT:
        error = susfs_add_sus_mount((struct st_susfs_sus_mount __user *)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
    case CMD_SUSFS_ADD_SUS_KSTAT:
    case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY:
        error = susfs_add_sus_kstat((struct st_susfs_sus_kstat __user *)arg3);
        break;
    case CMD_SUSFS_UPDATE_SUS_KSTAT:
        error = susfs_update_sus_kstat((struct st_susfs_sus_kstat __user *)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_TRY_UMOUNT
    case CMD_SUSFS_ADD_TRY_UMOUNT:
        error = susfs_add_try_umount((struct st_susfs_try_umount __user *)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
    case CMD_SUSFS_SET_UNAME:
        error = susfs_set_uname((struct st_susfs_uname __user *)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
    case CMD_SUSFS_ENABLE_LOG:
        if (arg3 > 1)
            error = -EINVAL;
        else
            susfs_set_log((bool)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG
    case CMD_SUSFS_SET_CMDLINE_OR_BOOTCONFIG:
        error = susfs_set_cmdline_or_bootconfig((char __user *)arg3);
        break;
#endif
#ifdef CONFIG_KSU_SUSFS_OPEN_REDIRECT
    case CMD_SUSFS_ADD_OPEN_REDIRECT:
        error = susfs_add_open_redirect((struct st_susfs_open_redirect __user *)arg3);
        break;
#endif
    case CMD_SUSFS_SHOW_VERSION:
        error = susfs_copy_legacy_string(arg3, SUSFS_VERSION);
        break;
    case CMD_SUSFS_SHOW_VARIANT:
        error = susfs_copy_legacy_string(arg3, SUSFS_VARIANT);
        break;
    case CMD_SUSFS_SHOW_ENABLED_FEATURES:
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
        features |= 1ULL << 1;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT
        features |= 1ULL << 2;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
        features |= 1ULL << 8;
#endif
#ifdef CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS
        features |= 1ULL << 10;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG
        features |= 1ULL << 11;
#endif
#ifdef CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT
        features |= 1ULL << 14;
#endif
        error = copy_to_user((void __user *)arg3, &features, sizeof(features)) ? -EFAULT : 0;
        break;
    case CMD_SUSFS_SHOW_SUS_SU_WORKING_MODE:
        error = copy_to_user((void __user *)arg3, &mode, sizeof(mode)) ? -EFAULT : 0;
        break;
    case CMD_SUSFS_IS_SUS_SU_READY:
        error = copy_to_user((void __user *)arg3, &ready, sizeof(ready)) ? -EFAULT : 0;
        break;
    case CMD_SUSFS_SUS_SU:
        error = -EOPNOTSUPP;
        break;
    default:
        error = -EINVAL;
        break;
    }

    susfs_reply_error(arg5, error);
    return true;
}
EOF

# Fail closed on the intended minimal, previously boot-tested feature set.
grep -Fq '#define SUSFS_VERSION "v1.5.5"' "$KERNEL_DIR/include/linux/susfs.h" || fail "Unexpected SUSFS version"
grep -Fq 'obj-$(CONFIG_KSU_SUSFS) += susfs_sukisu_compat.o' "$FS_MAKEFILE" || fail "SukiSU compatibility object is not built"
grep -Fq 'susfs_init();' "$INIT_C" || fail "SUSFS initialization is missing"
grep -Fq 'susfs_sukisu_handle_reboot' "$SUPERCALL_C" || fail "SukiSU reboot ABI bridge is missing"
grep -Fq 'susfs_handle_prctl' "$SYS_C" || fail "Legacy prctl ABI bridge is missing"
grep -Fq 'CONFIG_KSU_SUSFS=y' "$DEFCONFIG" || fail "SUSFS is not enabled in a52xq_defconfig"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_PATH is not set' "$DEFCONFIG" || fail "SUS_PATH must remain disabled for first boot"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_KSTAT is not set' "$DEFCONFIG" || fail "SUS_KSTAT must remain disabled for first boot"
grep -Fq '# CONFIG_KSU_SUSFS_TRY_UMOUNT is not set' "$DEFCONFIG" || fail "TRY_UMOUNT must remain disabled for first boot"
grep -Fq '# CONFIG_KSU_SUSFS_OPEN_REDIRECT is not set' "$DEFCONFIG" || fail "OPEN_REDIRECT must remain disabled for first boot"

git -C "$KERNEL_DIR" diff --check
git -C "$SUKISU_DIR" diff --check

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'susfs_version=v1.5.5\n'
  printf 'susfs_variant=NON-GKI\n'
  printf 'susfs_patch_blob=%s\n' "$SUSFS_PATCH_BLOB"
  printf 'patch_apply_exit=%s\n' "$apply_rc"
  printf 'manager_abi=reboot-supercall-compat\n'
  printf 'legacy_abi=prctl-compat\n'
  printf 'enabled_features=sus_mount,magic_mount,spoof_uname,hide_symbols,spoof_cmdline\n'
  printf 'disabled_for_first_boot=sus_path,sus_kstat,try_umount,open_redirect,sus_su,logging\n'
} | tee "$REPORT"

info "SUSFS v1.5.5 integrated with dual SukiSU and legacy userspace ABI"
