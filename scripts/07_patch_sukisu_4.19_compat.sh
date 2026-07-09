#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
INIT_C="$SUKISU_DIR/kernel/core/init.c"
SUCOMPAT_C="$SUKISU_DIR/kernel/feature/sucompat.c"
PATCH_MEMORY_C="$SUKISU_DIR/kernel/hook/arm64/patch_memory.c"
SECCOMP_CACHE_C="$SUKISU_DIR/kernel/infra/seccomp_cache.c"
SECCOMP_CACHE_H="$SUKISU_DIR/kernel/infra/seccomp_cache.h"
SETUID_HOOK_C="$SUKISU_DIR/kernel/hook/setuid_hook.c"
APP_PROFILE_C="$SUKISU_DIR/kernel/policy/app_profile.c"
APP_PROFILE_H="$SUKISU_DIR/kernel/policy/app_profile.h"
SU_MOUNT_NS_C="$SUKISU_DIR/kernel/infra/su_mount_ns.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-compat.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-compat.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SukiSU compatibility patch"
for file in \
  "$INIT_C" "$SUCOMPAT_C" "$PATCH_MEMORY_C" \
  "$SECCOMP_CACHE_C" "$SECCOMP_CACHE_H" "$SETUID_HOOK_C" \
  "$APP_PROFILE_C" "$APP_PROFILE_H" "$SU_MOUNT_NS_C"; do
  test -f "$file" || fail "Required SukiSU source is missing: $file"
done

test ! -e "$KERNEL_DIR/include/linux/pgtable.h" || fail "Unexpected linux/pgtable.h exists; review compatibility patch"
test -f "$KERNEL_DIR/arch/arm64/include/asm/pgtable.h" || fail "ARM64 asm/pgtable.h is missing"
! grep -Fq '#define MODULE_IMPORT_NS' "$KERNEL_DIR/include/linux/module.h" || fail "MODULE_IMPORT_NS already exists; review compatibility patch"
! grep -RFn 'strncpy_from_user_nofault' "$KERNEL_DIR/include" "$KERNEL_DIR/mm" >/dev/null 2>&1 || fail "Kernel already provides strncpy_from_user_nofault; review compatibility patch"
! grep -RFn 'copy_to_kernel_nofault' "$KERNEL_DIR/include" "$KERNEL_DIR/mm" >/dev/null 2>&1 || fail "Kernel already provides copy_to_kernel_nofault; review compatibility patch"
grep -Fq 'probe_kernel_write(void *dst, const void *src, size_t size)' "$KERNEL_DIR/include/linux/uaccess.h" || fail "Legacy probe_kernel_write helper is missing"
grep -Fq 'void put_seccomp_filter(struct task_struct *tsk)' "$KERNEL_DIR/kernel/seccomp.c" || fail "Legacy put_seccomp_filter implementation is missing"
! grep -Fq 'struct action_cache cache;' "$KERNEL_DIR/kernel/seccomp.c" || fail "Kernel unexpectedly contains the newer seccomp cache layout"
! grep -Fq 'filter_count' "$KERNEL_DIR/include/linux/seccomp.h" || fail "Kernel unexpectedly contains the newer seccomp filter_count layout"
test ! -e "$KERNEL_DIR/include/uapi/linux/mount.h" || fail "Unexpected uapi/linux/mount.h exists; review compatibility patch"
test -f "$KERNEL_DIR/include/uapi/linux/fs.h" || fail "Legacy UAPI fs.h is missing"
grep -Fq 'int ksys_mount(char __user *dev_name' "$KERNEL_DIR/fs/namespace.c" || fail "Legacy ksys_mount implementation is missing"

info "Applying exact SukiSU compatibility fixes for Linux 4.19"
python3 - \
  "$INIT_C" "$SUCOMPAT_C" "$PATCH_MEMORY_C" \
  "$SECCOMP_CACHE_C" "$SECCOMP_CACHE_H" "$SETUID_HOOK_C" \
  "$APP_PROFILE_C" "$APP_PROFILE_H" "$SU_MOUNT_NS_C" <<'PY'
from pathlib import Path
import sys

(
    init_c,
    sucompat_c,
    patch_memory_c,
    seccomp_cache_c,
    seccomp_cache_h,
    setuid_hook_c,
    app_profile_c,
    app_profile_h,
    su_mount_ns_c,
) = map(Path, sys.argv[1:])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# MODULE_IMPORT_NS was introduced after Linux 4.19.
init_text = init_c.read_text()
old_import = '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 13, 0)
MODULE_IMPORT_NS("VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver");
#else
MODULE_IMPORT_NS(VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver);
#endif
'''
new_import = '''#ifdef MODULE_IMPORT_NS
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 13, 0)
MODULE_IMPORT_NS("VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver");
#else
MODULE_IMPORT_NS(VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver);
#endif
#endif
'''
init_c.write_text(replace_once(init_text, old_import, new_import, "core/init.c MODULE_IMPORT_NS"))

# Linux 4.19 has no linux/pgtable.h wrapper and no strncpy_from_user_nofault().
sucompat_text = sucompat_c.read_text()
sucompat_text = replace_once(
    sucompat_text,
    '#include <linux/pgtable.h>\n',
    '#include <asm/pgtable.h> /* Linux 4.19 has no <linux/pgtable.h> wrapper. */\n',
    "feature/sucompat.c pgtable include",
)
helper_marker = '''#define SU_PATH "/system/bin/su"
#define SH_PATH "/system/bin/sh"

'''
helper = '''#define SU_PATH "/system/bin/su"
#define SH_PATH "/system/bin/sh"

/*
 * Linux 4.19 predates strncpy_from_user_nofault(). Keep this compatibility
 * helper local to SukiSU and preserve the upstream non-pagefault semantics.
 */
static long ksu_strncpy_from_user_nofault(char *dst,
                                          const char __user *unsafe_addr,
                                          long count)
{
    mm_segment_t old_fs = get_fs();
    long ret;

    if (unlikely(count <= 0))
        return 0;

    set_fs(USER_DS);
    pagefault_disable();
    ret = strncpy_from_user(dst, unsafe_addr, count);
    pagefault_enable();
    set_fs(old_fs);

    if (ret >= count) {
        ret = count;
        dst[ret - 1] = '\\0';
    } else if (ret > 0) {
        ret++;
    }

    return ret;
}

'''
sucompat_text = replace_once(sucompat_text, helper_marker, helper, "feature/sucompat.c helper insertion")
call = 'strncpy_from_user_nofault(path, *filename_user, sizeof(path));'
if sucompat_text.count(call) != 2:
    raise SystemExit(f"feature/sucompat.c: expected two nofault copy calls, found {sucompat_text.count(call)}")
sucompat_text = sucompat_text.replace(call, 'ksu_strncpy_from_user_nofault(path, *filename_user, sizeof(path));')
sucompat_c.write_text(sucompat_text)

# copy_to_kernel_nofault() is the newer name for probe_kernel_write().
patch_text = patch_memory_c.read_text()
patch_text = replace_once(
    patch_text,
    'copy_to_kernel_nofault(map, src, len);',
    'probe_kernel_write(map, src, len); /* Linux 4.19 helper name. */',
    "hook/arm64/patch_memory.c kernel write helper",
)
patch_memory_c.write_text(patch_text)

# The seccomp action cache was added after this kernel. Compiling the newer
# private struct layout on 4.19 would corrupt struct seccomp_filter at runtime.
cache_text = seccomp_cache_c.read_text()
cache_marker = '#include "infra/seccomp_cache.h"\n\n'
cache_text = replace_once(
    cache_text,
    cache_marker,
    cache_marker + '#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 2)\n\n',
    "infra/seccomp_cache.c guard start",
)
cache_text = cache_text.rstrip() + '\n\n#endif /* Linux 5.10.2+ seccomp action cache */\n'
seccomp_cache_c.write_text(cache_text)

cache_header_text = seccomp_cache_h.read_text()
old_decls = '''extern void ksu_seccomp_clear_cache(struct seccomp_filter *filter, int nr);
extern void ksu_seccomp_allow_cache(struct seccomp_filter *filter, int nr);
'''
new_decls = '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 2)
extern void ksu_seccomp_clear_cache(struct seccomp_filter *filter, int nr);
extern void ksu_seccomp_allow_cache(struct seccomp_filter *filter, int nr);
#endif
'''
seccomp_cache_h.write_text(replace_once(cache_header_text, old_decls, new_decls, "infra/seccomp_cache.h declarations"))

# Expose one audited helper that disables and releases seccomp correctly on
# both old and new kernels.
profile_text = app_profile_c.read_text()
profile_text = replace_once(
    profile_text,
    'void seccomp_filter_release(struct task_struct *tsk);\n\nstatic void disable_seccomp(void)\n',
    '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0)
void seccomp_filter_release(struct task_struct *tsk);
#endif

void ksu_disable_seccomp_for_current(void)
''',
    "policy/app_profile.c helper declaration",
)
profile_text = replace_once(
    profile_text,
    '    atomic_set(&current->seccomp.filter_count, 0);\n',
    '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0)
    atomic_set(&current->seccomp.filter_count, 0);
#endif
''',
    "policy/app_profile.c filter_count guard",
)
profile_text = replace_once(
    profile_text,
    '    seccomp_filter_release(fake);\n',
    '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0)
    seccomp_filter_release(fake);
#else
    put_seccomp_filter(fake);
#endif
''',
    "policy/app_profile.c filter release",
)
profile_text = replace_once(
    profile_text,
    '    disable_seccomp();\n',
    '    ksu_disable_seccomp_for_current();\n',
    "policy/app_profile.c helper call",
)
app_profile_c.write_text(profile_text)

profile_header_text = app_profile_h.read_text()
profile_header_text = replace_once(
    profile_header_text,
    'int escape_with_root_profile(void);\n\n',
    'int escape_with_root_profile(void);\n\nvoid ksu_disable_seccomp_for_current(void);\n\n',
    "policy/app_profile.h helper declaration",
)
app_profile_h.write_text(profile_header_text)

# Below 5.10.2 there is no action-cache bitmap. Disable seccomp using the
# audited helper before taking the signal lock used for the tracepoint flag.
setuid_text = setuid_hook_c.read_text()
setuid_text = replace_once(
    setuid_text,
    '#include "policy/allowlist.h"\n',
    '#include "policy/allowlist.h"\n#include "policy/app_profile.h"\n',
    "hook/setuid_hook.c app profile include",
)
old_manager = '''    if (unlikely(is_uid_manager(new_uid))) {
        spin_lock_irq(&current->sighand->siglock);
        ksu_seccomp_allow_cache(current->seccomp.filter, __NR_reboot);
        ksu_set_task_tracepoint_flag(current);
        spin_unlock_irq(&current->sighand->siglock);

'''
new_manager = '''    if (unlikely(is_uid_manager(new_uid))) {
#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 10, 2)
        ksu_disable_seccomp_for_current();
#endif
        spin_lock_irq(&current->sighand->siglock);
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 2)
        ksu_seccomp_allow_cache(current->seccomp.filter, __NR_reboot);
#endif
        ksu_set_task_tracepoint_flag(current);
        spin_unlock_irq(&current->sighand->siglock);

'''
setuid_text = replace_once(setuid_text, old_manager, new_manager, "hook/setuid_hook.c manager seccomp path")
old_allowed = '''        if (current->seccomp.mode == SECCOMP_MODE_FILTER && current->seccomp.filter) {
            spin_lock_irq(&current->sighand->siglock);
            ksu_seccomp_allow_cache(current->seccomp.filter, __NR_reboot);
            spin_unlock_irq(&current->sighand->siglock);
        }
'''
new_allowed = '''        if (current->seccomp.mode == SECCOMP_MODE_FILTER && current->seccomp.filter) {
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 2)
            spin_lock_irq(&current->sighand->siglock);
            ksu_seccomp_allow_cache(current->seccomp.filter, __NR_reboot);
            spin_unlock_irq(&current->sighand->siglock);
#else
            ksu_disable_seccomp_for_current();
#endif
        }
'''
setuid_text = replace_once(setuid_text, old_allowed, new_allowed, "hook/setuid_hook.c allowed UID seccomp path")
setuid_hook_c.write_text(setuid_text)

# Linux 4.19 stores mount flags in uapi/linux/fs.h and predates path_mount().
# Use the old in-kernel mount syscall helper under KERNEL_DS.
mount_text = su_mount_ns_c.read_text()
mount_text = replace_once(
    mount_text,
    '#include <linux/version.h>\n#include <uapi/linux/mount.h>\n',
    '#include <linux/version.h>\n#include <linux/uaccess.h>\n#include <uapi/linux/fs.h>\n',
    "infra/su_mount_ns.c mount includes",
)
old_path_decl = '''extern int path_mount(const char *dev_name, struct path *path, const char *type_page, unsigned long flags,
                      void *data_page);

'''
mount_text = replace_once(mount_text, old_path_decl, '', "infra/su_mount_ns.c path_mount declaration")
old_private_mount = '''    struct path root_path;
    get_fs_root(current->fs, &root_path);
    int pm_ret = path_mount(NULL, &root_path, NULL, MS_PRIVATE | MS_REC, NULL);
    path_put(&root_path);
'''
new_private_mount = '''    mm_segment_t old_fs = get_fs();
    int pm_ret;

    set_fs(KERNEL_DS);
    pm_ret = (int)ksys_mount(NULL, (char __user *)"/", NULL,
                             MS_PRIVATE | MS_REC, NULL);
    set_fs(old_fs);
'''
mount_text = replace_once(mount_text, old_private_mount, new_private_mount, "infra/su_mount_ns.c private mount path")
su_mount_ns_c.write_text(mount_text)
PY

grep -Fq '#ifdef MODULE_IMPORT_NS' "$INIT_C" || fail "MODULE_IMPORT_NS guard was not added"
! grep -Fq '#include <linux/pgtable.h>' "$SUCOMPAT_C" || fail "Unsupported linux/pgtable.h include remains"
grep -Fq '#include <asm/pgtable.h>' "$SUCOMPAT_C" || fail "ARM64 pgtable fallback was not added"
grep -Fq 'static long ksu_strncpy_from_user_nofault' "$SUCOMPAT_C" || fail "Local nofault copy helper was not added"
test "$(grep -Fc 'ksu_strncpy_from_user_nofault(path, *filename_user, sizeof(path));' "$SUCOMPAT_C")" -eq 2 || fail "SukiSU nofault copy calls were not redirected"
! grep -Eq '^[[:space:]]*strncpy_from_user_nofault\(' "$SUCOMPAT_C" || fail "Unsupported kernel helper call remains"
! grep -Fq 'copy_to_kernel_nofault(map, src, len)' "$PATCH_MEMORY_C" || fail "Unsupported copy_to_kernel_nofault call remains"
grep -Fq 'probe_kernel_write(map, src, len)' "$PATCH_MEMORY_C" || fail "Legacy probe_kernel_write call was not installed"
grep -Fq '#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 2)' "$SECCOMP_CACHE_C" || fail "Seccomp cache implementation is not version guarded"
grep -Fq 'void ksu_disable_seccomp_for_current(void)' "$APP_PROFILE_C" || fail "Audited seccomp-disable helper is missing"
grep -Fq 'put_seccomp_filter(fake);' "$APP_PROFILE_C" || fail "Legacy seccomp filter release path is missing"
grep -Fq 'ksu_disable_seccomp_for_current();' "$SETUID_HOOK_C" || fail "Old-kernel setuid seccomp path is missing"
! grep -Fq '#include <uapi/linux/mount.h>' "$SU_MOUNT_NS_C" || fail "Unsupported mount header remains"
grep -Fq '#include <uapi/linux/fs.h>' "$SU_MOUNT_NS_C" || fail "Legacy mount flag header is missing"
! grep -Fq 'path_mount(' "$SU_MOUNT_NS_C" || fail "Unsupported path_mount call remains"
grep -Fq 'ksys_mount(NULL, (char __user *)"/"' "$SU_MOUNT_NS_C" || fail "Legacy private-mount call is missing"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- \
  kernel/core/init.c \
  kernel/feature/sucompat.c \
  kernel/hook/arm64/patch_memory.c \
  kernel/infra/seccomp_cache.c \
  kernel/infra/seccomp_cache.h \
  kernel/hook/setuid_hook.c \
  kernel/policy/app_profile.c \
  kernel/policy/app_profile.h \
  kernel/infra/su_mount_ns.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 compatibility patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'module_import_ns=guarded-when-macro-exists\n'
  printf 'pgtable_include=asm/pgtable.h\n'
  printf 'user_string_nofault=local-upstream-semantics-backport\n'
  printf 'user_string_nofault_calls=2\n'
  printf 'kernel_write_nofault=probe_kernel_write-legacy-name\n'
  printf 'kernel_write_nofault_calls=1\n'
  printf 'seccomp_action_cache=disabled-below-5.10.2\n'
  printf 'seccomp_old_kernel_behavior=disable-and-release-filter\n'
  printf 'seccomp_filter_release=put_seccomp_filter-on-4.19\n'
  printf 'mount_flags_header=uapi/linux/fs.h\n'
  printf 'private_mount_api=ksys_mount-with-KERNEL_DS\n'
  printf 'compat_scope=linux-4.19-only\n'
  printf 'compat_patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 compatibility patch applied"
