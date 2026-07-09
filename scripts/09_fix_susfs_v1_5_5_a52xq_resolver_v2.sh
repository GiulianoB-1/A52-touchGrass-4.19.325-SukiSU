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

old = r'''git -C "$KERNEL_DIR" apply --check --whitespace=nowarn "$A52_NAMESPACE_PATCH" \
  || fail "A52XQ namespace compatibility patch no longer applies"
git -C "$KERNEL_DIR" apply --whitespace=nowarn "$A52_NAMESPACE_PATCH"
sha256sum "$A52_NAMESPACE_PATCH" > "$A52_NAMESPACE_PATCH.sha256"
'''

new = r'''python3 - "$KERNEL_DIR/fs/namespace.c" <<'PY_NAMESPACE'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact match, found {count}")
    return source.replace(old, new, 1)


def insert_in_function(source: str, function_marker: str, next_marker: str,
                       old: str, new: str, label: str) -> str:
    start = source.index(function_marker)
    end = source.index(next_marker, start)
    body = source[start:end]
    count = body.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one function-local match, found {count}")
    body = body.replace(old, new, 1)
    return source[:start] + body + source[end:]


# The generic 4.19 patch rejected only Samsung-context portions. Other SUSFS
# namespace hunks have already been applied, including ID allocation helpers,
# clone allocation policy, bind-mount handling and copy_mnt_ns declarations.
include_anchor = '''#ifdef CONFIG_KDP_NS
#include <linux/kdp.h>
#endif

#include "pnode.h"
#include "internal.h"

'''
include_replacement = '''#ifdef CONFIG_KDP_NS
#include <linux/kdp.h>
#endif
#if defined(CONFIG_KSU_SUSFS_SUS_MOUNT) || defined(CONFIG_KSU_SUSFS_TRY_UMOUNT)
#include <linux/susfs_def.h>
#endif

#include "pnode.h"
#include "internal.h"

#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
extern bool susfs_is_current_ksu_domain(void);
extern bool susfs_is_current_zygote_domain(void);

static DEFINE_IDA(susfs_mnt_id_ida);
static DEFINE_IDA(susfs_mnt_group_ida);

#define CL_ZYGOTE_COPY_MNT_NS BIT(24)
#define CL_COPY_MNT_NS BIT(25)
#endif

#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT
extern void susfs_auto_add_sus_ksu_default_mount(const char __user *to_pathname);
bool susfs_is_auto_add_sus_ksu_default_mount_enabled = true;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT
extern int susfs_auto_add_sus_bind_mount(const char *pathname, struct path *path_target);
bool susfs_is_auto_add_sus_bind_mount_enabled = true;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT
extern void susfs_auto_add_try_umount_for_bind_mount(struct path *path);
bool susfs_is_auto_add_try_umount_for_bind_mount_enabled = true;
#endif

'''
text = replace_once(text, include_anchor, include_replacement,
                    "namespace includes and SUSFS globals")

alloc_old = '''static struct mount *alloc_vfsmnt(const char *name)
{
	struct mount *mnt = kmem_cache_zalloc(mnt_cache, GFP_KERNEL);
	if (mnt) {
		int err;

		err = mnt_alloc_id(mnt);
		if (err)
			goto out_free_cache;
'''
alloc_new = '''#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
static struct mount *alloc_vfsmnt(const char *name, bool should_spoof, int custom_mnt_id)
#else
static struct mount *alloc_vfsmnt(const char *name)
#endif
{
	struct mount *mnt = kmem_cache_zalloc(mnt_cache, GFP_KERNEL);
	if (mnt) {
		int err;

#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
		if (should_spoof) {
			if (!custom_mnt_id)
				err = susfs_mnt_alloc_id(mnt);
			else {
				mnt->mnt_id = custom_mnt_id;
				err = 0;
			}
			goto bypass_orig_flow;
		}
#endif
		err = mnt_alloc_id(mnt);
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
bypass_orig_flow:
#endif
		if (err)
			goto out_free_cache;
'''
text = replace_once(text, alloc_old, alloc_new, "alloc_vfsmnt SUSFS signature")

vfs_mount_old = '''	mnt->mnt_parent = mnt;
	lock_mount_hash();
'''
vfs_mount_new = '''	mnt->mnt_parent = mnt;
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	if (susfs_is_current_zygote_domain()) {
		mnt->mnt.susfs_mnt_id_backup = mnt->mnt_id;
		mnt->mnt_id = current->susfs_last_fake_mnt_id++;
	}
#endif
	lock_mount_hash();
'''
text = insert_in_function(text,
                          'vfs_kern_mount(struct file_system_type *type',
                          'static struct mount *clone_mnt(',
                          vfs_mount_old, vfs_mount_new,
                          "vfs_kern_mount fake mount ID")

clone_old = '''	mnt->mnt_parent = mnt;
	lock_mount_hash();
'''
clone_new = '''	mnt->mnt_parent = mnt;
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	if (likely(is_current_zygote_domain) && !(flag & CL_ZYGOTE_COPY_MNT_NS)) {
		mnt->mnt.susfs_mnt_id_backup = mnt->mnt_id;
		mnt->mnt_id = current->susfs_last_fake_mnt_id++;
	}
#endif
	lock_mount_hash();
'''
text = insert_in_function(text,
                          'static struct mount *clone_mnt(',
                          'static void cleanup_group_ids(',
                          clone_old, clone_new,
                          "clone_mnt fake mount ID")

copy_flags_old = '''	copy_flags = CL_COPY_UNBINDABLE | CL_EXPIRE;
	if (user_ns != ns->user_ns)
		copy_flags |= CL_SHARED_TO_SLAVE | CL_UNPRIVILEGED;
'''
copy_flags_new = '''	copy_flags = CL_COPY_UNBINDABLE | CL_EXPIRE;
	if (user_ns != ns->user_ns)
		copy_flags |= CL_SHARED_TO_SLAVE | CL_UNPRIVILEGED;
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	copy_flags |= CL_COPY_MNT_NS;
	if (is_zygote_pid)
		copy_flags |= CL_ZYGOTE_COPY_MNT_NS;
#endif
'''
text = insert_in_function(text,
                          'struct mnt_namespace *copy_mnt_ns(',
                          'static int __init init_mount_tree(',
                          copy_flags_old, copy_flags_new,
                          "copy_mnt_ns clone flags")

copy_tail_old = '''	}
	namespace_unlock();

	if (rootmnt)
'''
copy_tail_new = '''	}
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	if (is_zygote_pid) {
		last_entry_mnt_id = list_first_entry(&new_ns->list,
						       struct mount, mnt_list)->mnt_id;
		list_for_each_entry(q, &new_ns->list, mnt_list) {
			if (unlikely(q->mnt_id >= DEFAULT_SUS_MNT_ID))
				continue;
			q->mnt.susfs_mnt_id_backup = q->mnt_id;
			q->mnt_id = last_entry_mnt_id++;
		}
	}
	current->susfs_last_fake_mnt_id = last_entry_mnt_id;
#endif
	namespace_unlock();

	if (rootmnt)
'''
text = insert_in_function(text,
                          'struct mnt_namespace *copy_mnt_ns(',
                          'static int __init init_mount_tree(',
                          copy_tail_old, copy_tail_new,
                          "copy_mnt_ns fake mount ID sequence")

# Fail closed on all previously rejected mount-hiding pieces.
required = (
    '#include <linux/susfs_def.h>',
    'static DEFINE_IDA(susfs_mnt_id_ida);',
    'static struct mount *alloc_vfsmnt(const char *name, bool should_spoof, int custom_mnt_id)',
    'copy_flags |= CL_ZYGOTE_COPY_MNT_NS;',
    'current->susfs_last_fake_mnt_id = last_entry_mnt_id;',
)
for marker in required:
    if text.count(marker) != 1:
        raise SystemExit(f"namespace validation failed for marker: {marker}")

path.write_text(text)
PY_NAMESPACE

# Keep the generated unified patch as an auditable description of the intended
# A52 changes, while applying them through exact function-scoped edits.
sha256sum "$A52_NAMESPACE_PATCH" > "$A52_NAMESPACE_PATCH.sha256"
'''

count = text.count(old)
if count != 1:
    raise SystemExit(f"namespace apply block: expected one match, found {count}")

path.write_text(text.replace(old, new, 1))
PY_PATCHER

bash -n "$TARGET"
echo "Made A52XQ SUSFS namespace resolution context-safe"
