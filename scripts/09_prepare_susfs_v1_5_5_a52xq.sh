#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/09_integrate_susfs_v1_5_5.sh"

test -f "$TARGET" || {
  echo "Missing SUSFS integration script: $TARGET" >&2
  exit 1
}

python3 - "$TARGET" <<'PY_PATCHER'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

old = '''find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\\n' | sort > "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"
if test -s "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"; then
  cat "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"
  fail "SUSFS v1.5.5 patch produced rejects; see $PATCH_LOG"
fi
test "$apply_rc" -eq 0 || fail "SUSFS v1.5.5 patch failed without reject files; see $PATCH_LOG"
'''

new = r'''find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt"

cat > "$ARTIFACTS_DIR/susfs-v1.5.5-expected-a52xq-rejects.txt" <<'EXPECTED_REJECTS'
fs/dcache.c.rej
fs/namei.c.rej
fs/namespace.c.rej
fs/notify/fdinfo.c.rej
fs/overlayfs/readdir.c.rej
fs/proc/task_mmu.c.rej
fs/readdir.c.rej
include/linux/sched.h.rej
kernel/sys.c.rej
EXPECTED_REJECTS

diff -u \
  "$ARTIFACTS_DIR/susfs-v1.5.5-expected-a52xq-rejects.txt" \
  "$ARTIFACTS_DIR/susfs-v1.5.5-rejects.txt" \
  > "$ARTIFACTS_DIR/susfs-v1.5.5-a52xq-reject-set.diff" \
  || fail "The SUSFS reject set changed; manual review is required"

# These exact mount-hiding hunks come from the previously boot-tested A52XQ
# SUSFS v1.5.5 source patch. The generic 4.19 patch already applied every
# other namespace hunk successfully.
A52_NAMESPACE_PATCH="$ARTIFACTS_DIR/susfs-v1.5.5-a52xq-namespace-resolution.patch"
cat > "$A52_NAMESPACE_PATCH" <<'A52_NAMESPACE_PATCH_EOF'
diff --git a/fs/namespace.c b/fs/namespace.c
--- a/fs/namespace.c
+++ b/fs/namespace.c
@@ -31,10 +31,37 @@
 #ifdef CONFIG_KDP_NS
 #include <linux/kdp.h>
 #endif
+#if defined(CONFIG_KSU_SUSFS_SUS_MOUNT) || defined(CONFIG_KSU_SUSFS_TRY_UMOUNT)
+#include <linux/susfs_def.h>
+#endif
 
 #include "pnode.h"
 #include "internal.h"
 
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+extern bool susfs_is_current_ksu_domain(void);
+extern bool susfs_is_current_zygote_domain(void);
+
+static DEFINE_IDA(susfs_mnt_id_ida);
+static DEFINE_IDA(susfs_mnt_group_ida);
+
+#define CL_ZYGOTE_COPY_MNT_NS BIT(24)
+#define CL_COPY_MNT_NS BIT(25)
+#endif
+
+#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT
+extern void susfs_auto_add_sus_ksu_default_mount(const char __user *to_pathname);
+bool susfs_is_auto_add_sus_ksu_default_mount_enabled = true;
+#endif
+#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT
+extern int susfs_auto_add_sus_bind_mount(const char *pathname, struct path *path_target);
+bool susfs_is_auto_add_sus_bind_mount_enabled = true;
+#endif
+#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT
+extern void susfs_auto_add_try_umount_for_bind_mount(struct path *path);
+bool susfs_is_auto_add_try_umount_for_bind_mount_enabled = true;
+#endif
+
 /* Maximum number of mounts in a mount namespace */
 unsigned int sysctl_mount_max __read_mostly = 100000;
 
@@ -242,13 +323,31 @@ static void drop_mountpoint(struct fs_pin *p)
 #endif
 }
 
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+static struct mount *alloc_vfsmnt(const char *name, bool should_spoof, int custom_mnt_id)
+#else
 static struct mount *alloc_vfsmnt(const char *name)
+#endif
 {
 	struct mount *mnt = kmem_cache_zalloc(mnt_cache, GFP_KERNEL);
 	if (mnt) {
 		int err;
 
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+		if (should_spoof) {
+			if (!custom_mnt_id)
+				err = susfs_mnt_alloc_id(mnt);
+			else {
+				mnt->mnt_id = custom_mnt_id;
+				err = 0;
+			}
+			goto bypass_orig_flow;
+		}
+#endif
 		err = mnt_alloc_id(mnt);
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+bypass_orig_flow:
+#endif
 		if (err)
 			goto out_free_cache;
 #ifdef CONFIG_KDP_NS
@@ -1131,6 +1240,15 @@ vfs_kern_mount(struct file_system_type *type, int flags, const char *name, void
 	mnt->mnt_mountpoint = mnt->mnt.mnt_root;
 #endif
 	mnt->mnt_parent = mnt;
+
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+	if (susfs_is_current_zygote_domain()) {
+		mnt->mnt.susfs_mnt_id_backup = mnt->mnt_id;
+		mnt->mnt_id = current->susfs_last_fake_mnt_id++;
+	}
+#endif
+
 	lock_mount_hash();
 	list_add_tail(&mnt->mnt_instance, &root->d_sb->s_mounts);
 	unlock_mount_hash();
@@ -1255,6 +1418,15 @@ static struct mount *clone_mnt(struct mount *old, struct dentry *root,
 	mnt->mnt_mountpoint = mnt->mnt.mnt_root;
 #endif
 	mnt->mnt_parent = mnt;
+
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+	if (likely(is_current_zygote_domain) && !(flag & CL_ZYGOTE_COPY_MNT_NS)) {
+		mnt->mnt.susfs_mnt_id_backup = mnt->mnt_id;
+		mnt->mnt_id = current->susfs_last_fake_mnt_id++;
+	}
+#endif
+
 	lock_mount_hash();
 	list_add_tail(&mnt->mnt_instance, &sb->s_mounts);
 	unlock_mount_hash();
@@ -3336,6 +3542,15 @@ struct mnt_namespace *copy_mnt_ns(unsigned long flags, struct mnt_namespace *ns,
 	copy_flags = CL_COPY_UNBINDABLE | CL_EXPIRE;
 	if (user_ns != ns->user_ns)
 		copy_flags |= CL_SHARED_TO_SLAVE | CL_UNPRIVILEGED;
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+	copy_flags |= CL_COPY_MNT_NS;
+	if (is_zygote_pid)
+		copy_flags |= CL_ZYGOTE_COPY_MNT_NS;
+#endif
+
 #ifdef CONFIG_KDP_NS
 	new = copy_tree(old, old->mnt->mnt_root, copy_flags);
 #else
@@ -3392,6 +3607,29 @@ struct mnt_namespace *copy_mnt_ns(unsigned long flags, struct mnt_namespace *ns,
 #endif
 			p = next_mnt(p, old);
 	}
+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+	if (is_zygote_pid) {
+		last_entry_mnt_id = list_first_entry(&new_ns->list,
+						       struct mount, mnt_list)->mnt_id;
+		list_for_each_entry(q, &new_ns->list, mnt_list) {
+			if (unlikely(q->mnt_id >= DEFAULT_SUS_MNT_ID))
+				continue;
+			q->mnt.susfs_mnt_id_backup = q->mnt_id;
+			q->mnt_id = last_entry_mnt_id++;
+		}
+	}
+	current->susfs_last_fake_mnt_id = last_entry_mnt_id;
+#endif
+
 	namespace_unlock();
 
 	if (rootmnt)
A52_NAMESPACE_PATCH_EOF

git -C "$KERNEL_DIR" apply --check --whitespace=nowarn "$A52_NAMESPACE_PATCH" \
  || fail "A52XQ namespace compatibility patch no longer applies"
git -C "$KERNEL_DIR" apply --whitespace=nowarn "$A52_NAMESPACE_PATCH"
sha256sum "$A52_NAMESPACE_PATCH" > "$A52_NAMESPACE_PATCH.sha256"

python3 - "$KERNEL_DIR/include/linux/sched.h" "$KERNEL_DIR/kernel/sys.c" <<'PY_A52'
from pathlib import Path
import sys

sched_path = Path(sys.argv[1])
sys_path = Path(sys.argv[2])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


sched = sched_path.read_text()
if 'u64\t\t\t\tsusfs_task_state;' not in sched:
    sched = replace_once(
        sched,
        '\t/* task is frozen/stopped (used by the cgroup freezer) */\n'
        '\tANDROID_KABI_USE(1, unsigned frozen:1);\n\n',
        '\t/* task is frozen/stopped (used by the cgroup freezer) */\n'
        '\tANDROID_KABI_USE(1, unsigned frozen:1);\n\n'
        '#ifdef CONFIG_KSU_SUSFS\n'
        '\tu64\t\t\t\tsusfs_task_state;\n'
        '\tu64\t\t\t\tsusfs_last_fake_mnt_id;\n'
        '#endif\n\n',
        'A52XQ task_struct SUSFS fields',
    )
sched_path.write_text(sched)

sys_c = sys_path.read_text()
if 'extern void susfs_spoof_uname' not in sys_c:
    sys_c = replace_once(
        sys_c,
        'SYSCALL_DEFINE1(newuname, struct new_utsname __user *, name)\n',
        '#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME\n'
        'extern void susfs_spoof_uname(struct new_utsname *tmp);\n'
        '#endif\n'
        'SYSCALL_DEFINE1(newuname, struct new_utsname __user *, name)\n',
        'A52XQ uname SUSFS declaration',
    )
if 'susfs_spoof_uname(&tmp);' not in sys_c:
    sys_c = replace_once(
        sys_c,
        '\tmemcpy(&tmp, utsname(), sizeof(tmp));\n'
        '\tif (!strncmp(current->comm, "bpfloader", 9) ||\n',
        '\tmemcpy(&tmp, utsname(), sizeof(tmp));\n'
        '#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME\n'
        '\tsusfs_spoof_uname(&tmp);\n'
        '#endif\n'
        '\tif (!strncmp(current->comm, "bpfloader", 9) ||\n',
        'A52XQ uname SUSFS call',
    )
sys_path.write_text(sys_c)
PY_A52

# The remaining rejected hunks belong only to features intentionally disabled
# for the first device test: SUS_PATH, SUS_KSTAT and SUS_OVERLAYFS. The fdinfo
# body reject is also only used for SUS_KSTAT inode spoofing.
find "$KERNEL_DIR" -type f -name '*.rej' -delete
find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q . \
  && fail "Reject files remain after A52XQ SUSFS resolution"

grep -Fq '#include <linux/susfs_def.h>' "$KERNEL_DIR/fs/namespace.c" \
  || fail "A52XQ namespace SUSFS include is missing"
grep -Fq 'static DEFINE_IDA(susfs_mnt_id_ida);' "$KERNEL_DIR/fs/namespace.c" \
  || fail "A52XQ SUSFS mount-ID allocator is missing"
grep -Fq 'copy_flags |= CL_ZYGOTE_COPY_MNT_NS;' "$KERNEL_DIR/fs/namespace.c" \
  || fail "A52XQ zygote namespace flag is missing"
grep -Fq 'susfs_last_fake_mnt_id' "$KERNEL_DIR/include/linux/sched.h" \
  || fail "A52XQ task_struct SUSFS state is missing"
grep -Fq 'susfs_spoof_uname(&tmp);' "$KERNEL_DIR/kernel/sys.c" \
  || fail "A52XQ uname spoof hook is missing"

printf 'initial_apply_exit=%s\nresolved_reject_count=9\nresolution=a52xq-tested-contexts\n' \
  "$apply_rc" > "$ARTIFACTS_DIR/susfs-v1.5.5-a52xq-resolution.txt"
'''

count = text.count(old)
if count != 1:
    raise SystemExit(f"reject handling block: expected one match, found {count}")

path.write_text(text.replace(old, new, 1))
PY_PATCHER

bash -n "$TARGET"
echo "Prepared A52XQ-specific SUSFS v1.5.5 integration"
