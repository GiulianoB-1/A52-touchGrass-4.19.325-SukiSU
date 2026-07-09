#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
PKG_OBSERVER_C="$SUKISU_DIR/kernel/manager/pkg_observer.c"
ALLOWLIST_C="$SUKISU_DIR/kernel/policy/allowlist.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-manager-policy.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-manager-policy.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before manager/policy compatibility patch"
test -f "$PKG_OBSERVER_C" || fail "SukiSU pkg_observer.c is missing"
test -f "$ALLOWLIST_C" || fail "SukiSU allowlist.c is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"

grep -Fq 'int (*handle_event)(struct fsnotify_group *group,' "$KERNEL_DIR/include/linux/fsnotify_backend.h" || fail "Linux 4.19 fsnotify handle_event API is missing"
! grep -Fq 'handle_inode_event' "$KERNEL_DIR/include/linux/fsnotify_backend.h" || fail "Kernel unexpectedly has the newer fsnotify callback"
grep -Fq 'int task_work_add(struct task_struct *task, struct callback_head *twork, bool);' "$KERNEL_DIR/include/linux/task_work.h" || fail "Linux 4.19 Boolean task_work_add API is missing"
! grep -RFn 'TWA_RESUME' "$KERNEL_DIR/include" >/dev/null 2>&1 || fail "Kernel unexpectedly defines TWA_RESUME; review patch"
grep -Fq 'static inline void put_task_struct(struct task_struct *t)' "$KERNEL_DIR/include/linux/sched/task.h" || fail "put_task_struct declaration is missing"
grep -Fq 'ssize_t strscpy(char *, const char *, size_t);' "$KERNEL_DIR/include/linux/string.h" || fail "strscpy is missing"
! grep -Fq 'strscpy_pad' "$KERNEL_DIR/include/linux/string.h" || fail "Kernel unexpectedly provides strscpy_pad; review patch"

info "Adapting SukiSU package observer and allowlist to Linux 4.19 APIs"
python3 - "$PKG_OBSERVER_C" "$ALLOWLIST_C" <<'PY'
from pathlib import Path
import sys

pkg_path = Path(sys.argv[1])
allow_path = Path(sys.argv[2])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


pkg = pkg_path.read_text()
old_callback = '''static int ksu_handle_inode_event(struct fsnotify_mark *mark, u32 mask, struct inode *inode, struct inode *dir,
                                  const struct qstr *file_name, u32 cookie)
{
    if (!file_name)
        return 0;
    if (mask & FS_ISDIR)
        return 0;
    if (file_name->len == 13 && !memcmp(file_name->name, "packages.list", 13)) {
        pr_info("packages.list detected: %d\\n", mask);
        track_throne(false);
    }
    return 0;
}

static const struct fsnotify_ops ksu_ops = {
    .handle_inode_event = ksu_handle_inode_event,
};
'''
new_callback = '''static int ksu_handle_event(struct fsnotify_group *group, struct inode *inode,
                            u32 mask, const void *data, int data_type,
                            const unsigned char *file_name, u32 cookie,
                            struct fsnotify_iter_info *iter_info)
{
    if (!file_name)
        return 0;
    if (mask & FS_ISDIR)
        return 0;
    if (!strcmp((const char *)file_name, "packages.list")) {
        pr_info("packages.list detected: %d\\n", mask);
        track_throne(false);
    }
    return 0;
}

static const struct fsnotify_ops ksu_ops = {
    .handle_event = ksu_handle_event,
};
'''
pkg = replace_once(pkg, old_callback, new_callback, "pkg_observer.c fsnotify callback")
pkg_path.write_text(pkg)

allow = allow_path.read_text()
allow = replace_once(
    allow,
    '#include <linux/task_work.h>\n',
    '#include <linux/task_work.h>\n#include <linux/sched/task.h>\n',
    "allowlist.c task lifetime include",
)
allow = replace_once(
    allow,
    '#include <linux/slab.h>\n',
    '#include <linux/slab.h>\n#include <linux/string.h>\n',
    "allowlist.c string include",
)
allow = replace_once(
    allow,
    '    if (task_work_add(tsk, cb, TWA_RESUME)) {\n',
    '    if (task_work_add(tsk, cb, true)) { /* Linux 4.19 notify-resume API. */\n',
    "allowlist.c task_work_add mode",
)
allow = replace_once(
    allow,
    '                strscpy_pad(domain, KSU_DEFAULT_SELINUX_DOMAIN, domain_len);\n',
    '                memset(domain, 0, domain_len);\n                strscpy(domain, KSU_DEFAULT_SELINUX_DOMAIN, domain_len);\n',
    "allowlist.c padded domain copy",
)
allow = replace_once(
    allow,
    '        fallthrough;\n',
    '        /* fall through */\n',
    "allowlist.c fallthrough annotation",
)
allow_path.write_text(allow)
PY

! grep -Fq 'handle_inode_event' "$PKG_OBSERVER_C" || fail "New fsnotify callback remains"
grep -Fq '.handle_event = ksu_handle_event' "$PKG_OBSERVER_C" || fail "Linux 4.19 fsnotify callback was not installed"
grep -Fq 'const unsigned char *file_name' "$PKG_OBSERVER_C" || fail "Linux 4.19 fsnotify filename signature is missing"
grep -Fq '#include <linux/sched/task.h>' "$ALLOWLIST_C" || fail "Task lifetime header was not added"
grep -Fq '#include <linux/string.h>' "$ALLOWLIST_C" || fail "String header was not added"
! grep -Fq 'TWA_RESUME' "$ALLOWLIST_C" || fail "Unsupported TWA_RESUME remains"
grep -Fq 'task_work_add(tsk, cb, true)' "$ALLOWLIST_C" || fail "Boolean task-work notification mode is missing"
! grep -Fq 'strscpy_pad' "$ALLOWLIST_C" || fail "Unsupported strscpy_pad remains"
grep -Fq 'memset(domain, 0, domain_len);' "$ALLOWLIST_C" || fail "Domain zero-padding is missing"
grep -Fq 'strscpy(domain, KSU_DEFAULT_SELINUX_DOMAIN, domain_len);' "$ALLOWLIST_C" || fail "Domain copy is missing"
! grep -Fq 'fallthrough;' "$ALLOWLIST_C" || fail "Unsupported fallthrough macro remains"
grep -Fq '/* fall through */' "$ALLOWLIST_C" || fail "Old-kernel fallthrough annotation is missing"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- \
  kernel/manager/pkg_observer.c \
  kernel/policy/allowlist.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 manager/policy patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'fsnotify_callback=handle_event-linux-4.19-signature\n'
  printf 'fsnotify_filename=nul-terminated-name\n'
  printf 'task_work_mode=boolean-notify-true\n'
  printf 'task_reference_release=put_task_struct-header-added\n'
  printf 'profile_domain_copy=zero-pad-then-strscpy\n'
  printf 'fallthrough_annotation=comment\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 manager/policy compatibility patch applied"
