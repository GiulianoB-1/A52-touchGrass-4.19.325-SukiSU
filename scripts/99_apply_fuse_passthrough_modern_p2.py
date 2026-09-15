#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 99_apply_fuse_passthrough_modern_p2.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
changes = []

def read(rel):
    p = root / rel
    if not p.is_file():
        raise SystemExit(f"missing {p}")
    return p.read_text()

def write(rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)

def replace_once(rel, old, new, label):
    s = read(rel)
    if new in s:
        changes.append(label + "_already")
        return
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor in {rel}, found {count}")
    write(rel, s.replace(old, new, 1))
    changes.append(label)

def replace_tail(rel, marker, new_tail, label):
    s = read(rel)
    if new_tail in s:
        changes.append(label + "_already")
        return
    pos = s.find(marker)
    if pos < 0:
        raise SystemExit(f"{label}: marker not found in {rel}: {marker}")
    write(rel, s[:pos] + new_tail)
    changes.append(label)

# ---------------------------------------------------------------------------
# Phase 99 / FUSE modern P2
#
# P1 remains intact: get_current_cred(), read/write/splice/mmap passthrough,
# legacy Android ioctl 126, protocol 7.27 wire layout.
#
# P2 imports the persistent backing-file lifetime model from modern upstream:
#   - refcounted struct fuse_backing
#   - persistent BACKING_OPEN/BACKING_CLOSE ioctls
#   - connection-wide backing ID registry
#   - per-open references to persistent backing objects
#
# Compatibility model:
#   Legacy Android:
#     ioctl 126 -> legacy one-shot backing object -> passthrough_fh
#   Modern-capable userspace on this hybrid 7.27 ABI:
#     BACKING_OPEN -> persistent backing ID
#     OPEN reply sets FOPEN_PASSTHROUGH and carries the ID in the same final
#     32-bit fuse_open_out slot (passthrough_fh on the legacy source ABI)
#     BACKING_CLOSE drops the daemon's persistent registry reference.
#
# Protocol 7.40 max_stack_depth negotiation is intentionally deferred to P3.
# ---------------------------------------------------------------------------

# Modern persistent backing registration UAPI. Keep Android ioctl 126 exactly.
replace_once(
    "include/uapi/linux/fuse.h",
    "#define FUSE_DEV_IOC_CLONE\t_IOR(FUSE_DEV_IOC_MAGIC, 0, uint32_t)\n"
    "/* 127 is reserved for Android's deprecated V1 passthrough interface. */\n",
    "struct fuse_backing_map {\n"
    "\tint32_t\t\tfd;\n"
    "\tuint32_t\tflags;\n"
    "\tuint64_t\tpadding;\n"
    "};\n\n"
    "#define FUSE_DEV_IOC_CLONE\t_IOR(FUSE_DEV_IOC_MAGIC, 0, uint32_t)\n"
    "#define FUSE_DEV_IOC_BACKING_OPEN \\\n"
    "\t_IOW(FUSE_DEV_IOC_MAGIC, 1, struct fuse_backing_map)\n"
    "#define FUSE_DEV_IOC_BACKING_CLOSE \\\n"
    "\t_IOW(FUSE_DEV_IOC_MAGIC, 2, uint32_t)\n"
    "/* 127 is reserved for Android's deprecated V1 passthrough interface. */\n",
    "uapi_backing_ioctls",
)

# Refcounted backing object owns the file+credential references.  The filp and
# cred fields in fuse_passthrough are borrowed aliases retained so the proven
# P1 fast paths do not need an invasive rewrite.
replace_once(
    "fs/fuse/fuse_i.h",
    "struct fuse_passthrough {\n"
    "\tstruct file *filp;\n"
    "\tconst struct cred *cred;\n"
    "};\n",
    "struct fuse_backing {\n"
    "\tstruct file *file;\n"
    "\tconst struct cred *cred;\n"
    "\trefcount_t count;\n"
    "\tbool legacy_once;\n"
    "};\n\n"
    "struct fuse_passthrough {\n"
    "\tstruct fuse_backing *backing;\n"
    "\t/* Borrowed aliases owned by backing, retained for the P1 fast paths. */\n"
    "\tstruct file *filp;\n"
    "\tconst struct cred *cred;\n"
    "};\n",
    "backing_struct",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "\t/** Pending lower-file registrations keyed by userspace handle. */\n"
    "\tstruct idr passthrough_req;\n"
    "\tspinlock_t passthrough_req_lock;\n",
    "\t/** Persistent/legacy lower-file registrations keyed by backing ID. */\n"
    "\tstruct idr backing_files_map;\n"
    "\tspinlock_t backing_files_lock;\n",
    "conn_backing_registry",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "int fuse_passthrough_open(struct fuse_dev *fud, u32 lower_fd);\n"
    "int fuse_passthrough_setup(struct fuse_conn *fc, struct fuse_file *ff,\n",
    "int fuse_passthrough_open(struct fuse_dev *fud, u32 lower_fd);\n"
    "int fuse_backing_open(struct fuse_conn *fc, struct fuse_backing_map *map);\n"
    "int fuse_backing_close(struct fuse_conn *fc, int backing_id);\n"
    "struct fuse_backing *fuse_backing_lookup(struct fuse_conn *fc, int backing_id);\n"
    "struct fuse_backing *fuse_backing_take_legacy(struct fuse_conn *fc,\n"
    "\t\t\t\t\t       int backing_id);\n"
    "void fuse_backing_put(struct fuse_backing *backing);\n"
    "void fuse_backing_files_init(struct fuse_conn *fc);\n"
    "void fuse_backing_files_free(struct fuse_conn *fc);\n"
    "int fuse_passthrough_setup(struct fuse_conn *fc, struct fuse_file *ff,\n",
    "backing_prototypes",
)

# Build the new backing registry as a separate object, matching modern FUSE
# structure while using only VFS/IDR primitives available on Linux 4.19.
backing_c = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * FUSE persistent backing-file registry for the A52 Linux 4.19 backport.
 *
 * Lifetime model adapted from modern upstream fs/fuse/backing.c while keeping
 * Android's legacy ioctl-126 ABI as a compatibility shim.
 */
#include "fuse_i.h"

#include <linux/capability.h>
#include <linux/file.h>
#include <linux/slab.h>

static struct fuse_backing *fuse_backing_get(struct fuse_backing *backing)
{
	if (backing && refcount_inc_not_zero(&backing->count))
		return backing;
	return NULL;
}

static void fuse_backing_free(struct fuse_backing *backing)
{
	if (!backing)
		return;

	if (backing->file)
		fput(backing->file);
	if (backing->cred)
		put_cred(backing->cred);
	kfree(backing);
}

void fuse_backing_put(struct fuse_backing *backing)
{
	if (backing && refcount_dec_and_test(&backing->count))
		fuse_backing_free(backing);
}

void fuse_backing_files_init(struct fuse_conn *fc)
{
	idr_init(&fc->backing_files_map);
	spin_lock_init(&fc->backing_files_lock);
}

static int fuse_backing_id_alloc(struct fuse_conn *fc,
				 struct fuse_backing *backing)
{
	int id;

	idr_preload(GFP_KERNEL);
	spin_lock(&fc->backing_files_lock);
	id = idr_alloc(&fc->backing_files_map, backing, 1, 0, GFP_ATOMIC);
	spin_unlock(&fc->backing_files_lock);
	idr_preload_end();

	return id;
}

static struct fuse_backing *fuse_backing_id_remove(struct fuse_conn *fc,
						    int backing_id,
						    bool legacy_only)
{
	struct fuse_backing *backing;

	spin_lock(&fc->backing_files_lock);
	backing = idr_find(&fc->backing_files_map, backing_id);
	if (backing && (!legacy_only || backing->legacy_once))
		backing = idr_remove(&fc->backing_files_map, backing_id);
	else
		backing = NULL;
	spin_unlock(&fc->backing_files_lock);

	return backing;
}

static int fuse_backing_id_free(int id, void *p, void *data)
{
	struct fuse_backing *backing = p;

	fuse_backing_put(backing);
	return 0;
}

void fuse_backing_files_free(struct fuse_conn *fc)
{
	idr_for_each(&fc->backing_files_map, fuse_backing_id_free, NULL);
	idr_destroy(&fc->backing_files_map);
}

static int fuse_backing_open_common(struct fuse_conn *fc, int fd,
				    u32 flags, u64 padding, bool legacy_once)
{
	struct fuse_backing *backing;
	struct file *file;
	struct super_block *backing_sb;
	int ret;

	if (!fc->passthrough)
		return -EPERM;

	/*
	 * Preserve the proven Android ioctl-126 behavior.  The new persistent
	 * API follows upstream and requires privilege.
	 */
	if (!legacy_once && !capable(CAP_SYS_ADMIN))
		return -EPERM;

	if (flags || padding)
		return -EINVAL;

	file = fget(fd);
	if (!file)
		return -EBADF;

	if (!file->f_op || !file->f_op->read_iter || !file->f_op->write_iter) {
		ret = -EBADF;
		goto out_fput;
	}

	/* Modern persistent registrations are limited to regular files. */
	if (!legacy_once && !S_ISREG(file_inode(file)->i_mode)) {
		ret = -EINVAL;
		goto out_fput;
	}

	backing_sb = file_inode(file)->i_sb;
	if (backing_sb->s_stack_depth >= FILESYSTEM_MAX_STACK_DEPTH) {
		ret = -ELOOP;
		goto out_fput;
	}

	backing = kzalloc(sizeof(*backing), GFP_KERNEL);
	if (!backing) {
		ret = -ENOMEM;
		goto out_fput;
	}

	backing->file = file;
	backing->cred = get_current_cred();
	backing->legacy_once = legacy_once;
	refcount_set(&backing->count, 1);

	ret = fuse_backing_id_alloc(fc, backing);
	if (ret < 0) {
		fuse_backing_put(backing);
		return ret;
	}

	return ret;

out_fput:
	fput(file);
	return ret;
}

/* Android V2 ioctl-126 compatibility shim: registration is consumed by OPEN. */
int fuse_passthrough_open(struct fuse_dev *fud, u32 lower_fd)
{
	if (!fud)
		return -EINVAL;
	return fuse_backing_open_common(fud->fc, lower_fd, 0, 0, true);
}

/* Modern persistent API: registration survives OPEN until BACKING_CLOSE. */
int fuse_backing_open(struct fuse_conn *fc, struct fuse_backing_map *map)
{
	if (!map)
		return -EINVAL;
	return fuse_backing_open_common(fc, map->fd, map->flags, map->padding,
					false);
}

int fuse_backing_close(struct fuse_conn *fc, int backing_id)
{
	struct fuse_backing *backing;

	if (!fc->passthrough || !capable(CAP_SYS_ADMIN))
		return -EPERM;
	if (backing_id <= 0)
		return -EINVAL;

	/* BACKING_CLOSE is only for persistent registrations. */
	spin_lock(&fc->backing_files_lock);
	backing = idr_find(&fc->backing_files_map, backing_id);
	if (!backing || backing->legacy_once) {
		spin_unlock(&fc->backing_files_lock);
		return -ENOENT;
	}
	backing = idr_remove(&fc->backing_files_map, backing_id);
	spin_unlock(&fc->backing_files_lock);

	fuse_backing_put(backing);
	return 0;
}

struct fuse_backing *fuse_backing_lookup(struct fuse_conn *fc, int backing_id)
{
	struct fuse_backing *backing;

	if (backing_id <= 0)
		return NULL;

	spin_lock(&fc->backing_files_lock);
	backing = idr_find(&fc->backing_files_map, backing_id);
	if (!backing || backing->legacy_once)
		backing = NULL;
	else
		backing = fuse_backing_get(backing);
	spin_unlock(&fc->backing_files_lock);

	return backing;
}

/*
 * Legacy ioctl-126 IDs are one-shot.  Removing the ID transfers the registry's
 * single reference directly to the opened fuse_file.
 */
struct fuse_backing *fuse_backing_take_legacy(struct fuse_conn *fc,
					      int backing_id)
{
	if (backing_id <= 0)
		return NULL;
	return fuse_backing_id_remove(fc, backing_id, true);
}
'''
write("fs/fuse/backing.c", backing_c)
changes.append("backing_c")

replace_once(
    "fs/fuse/Makefile",
    "fuse-$(CONFIG_FUSE_PASSTHROUGH) += passthrough.o\n",
    "fuse-$(CONFIG_FUSE_PASSTHROUGH) += passthrough.o backing.o\n",
    "makefile_backing",
)

replace_once(
    "fs/fuse/Kconfig",
    "\t  filesystem file so read, write, splice and mmap I/O can bypass userspace.\n"
    "\t  This is the Android passthrough ABI used by MediaProvider.\n",
    "\t  filesystem file so read, write, splice and mmap I/O can bypass userspace.\n"
    "\t  Supports Android's legacy registration ABI and a persistent modern\n"
    "\t  BACKING_OPEN/BACKING_CLOSE backing-file registry.\n",
    "kconfig_backing_help",
)

# Add modern ioctls to /dev/fuse while retaining ioctl 126.
replace_once(
    "fs/fuse/dev.c",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tcase FUSE_DEV_IOC_PASSTHROUGH_OPEN:\n",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tcase FUSE_DEV_IOC_BACKING_OPEN: {\n"
    "\t\tstruct fuse_backing_map map;\n\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (!copy_from_user(&map, (void __user *)arg, sizeof(map))) {\n"
    "\t\t\tfud = fuse_get_dev(file);\n"
    "\t\t\terr = fud ? fuse_backing_open(fud->fc, &map) : -EINVAL;\n"
    "\t\t}\n"
    "\t\tbreak;\n"
    "\t}\n"
    "\tcase FUSE_DEV_IOC_BACKING_CLOSE: {\n"
    "\t\tu32 backing_id;\n\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (!get_user(backing_id, (__u32 __user *)arg)) {\n"
    "\t\t\tfud = fuse_get_dev(file);\n"
    "\t\t\terr = fud ? fuse_backing_close(fud->fc, backing_id) : -EINVAL;\n"
    "\t\t}\n"
    "\t\tbreak;\n"
    "\t}\n"
    "\tcase FUSE_DEV_IOC_PASSTHROUGH_OPEN:\n",
    "dev_backing_ioctls",
)

# Replace the old one-shot implementation tail.  All P1 I/O fast paths above
# this marker remain untouched and continue using the borrowed filp/cred aliases.
new_tail = r'''int fuse_passthrough_setup(struct fuse_conn *fc, struct fuse_file *ff,
			   struct fuse_open_out *openarg)
{
	struct fuse_backing *backing;
	int backing_id = (int)openarg->passthrough_fh;
	bool persistent = !!(openarg->open_flags & FOPEN_PASSTHROUGH);

	if (!fc->passthrough)
		return -EPERM;
	if (backing_id <= 0)
		return -EINVAL;

	if (persistent)
		backing = fuse_backing_lookup(fc, backing_id);
	else
		backing = fuse_backing_take_legacy(fc, backing_id);

	if (!backing)
		return -EINVAL;

	ff->passthrough.backing = backing;
	ff->passthrough.filp = backing->file;
	ff->passthrough.cred = backing->cred;
	return 0;
}

void fuse_passthrough_release(struct fuse_passthrough *passthrough)
{
	if (!passthrough)
		return;

	if (passthrough->backing)
		fuse_backing_put(passthrough->backing);

	passthrough->backing = NULL;
	passthrough->filp = NULL;
	passthrough->cred = NULL;
}
'''
replace_tail(
    "fs/fuse/passthrough.c",
    "int fuse_passthrough_open(struct fuse_dev *fud, u32 lower_fd)\n",
    new_tail,
    "passthrough_backing_lifetime",
)

# Connection lifecycle now owns the unified backing registry.
replace_once(
    "fs/fuse/inode.c",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tidr_init(&fc->passthrough_req);\n"
    "\tspin_lock_init(&fc->passthrough_req_lock);\n"
    "#endif\n",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tfuse_backing_files_init(fc);\n"
    "#endif\n",
    "conn_init_backing_registry",
)

old_free = (
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "static int free_fuse_passthrough(int id, void *p, void *data)\n"
    "{\n"
    "\tstruct fuse_passthrough *passthrough = p;\n\n"
    "\tfuse_passthrough_release(passthrough);\n"
    "\tkfree(passthrough);\n"
    "\treturn 0;\n"
    "}\n"
    "#endif\n\n"
)
replace_once(
    "fs/fuse/inode.c",
    old_free,
    "",
    "remove_old_free_callback",
)

replace_once(
    "fs/fuse/inode.c",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tidr_for_each(&fc->passthrough_req, free_fuse_passthrough, NULL);\n"
    "\tidr_destroy(&fc->passthrough_req);\n"
    "#endif\n",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tfuse_backing_files_free(fc);\n"
    "#endif\n",
    "conn_free_backing_registry",
)

# Structural verification: legacy P1 + modern P2 must coexist.
checks = {
    "include/uapi/linux/fuse.h": [
        "#define FUSE_PASSTHROUGH\t(1 << 31)",
        "#define FOPEN_PASSTHROUGH\t(1 << 7)",
        "uint32_t\tpassthrough_fh;",
        "struct fuse_backing_map",
        "FUSE_DEV_IOC_BACKING_OPEN",
        "FUSE_DEV_IOC_BACKING_CLOSE",
        "FUSE_DEV_IOC_PASSTHROUGH_OPEN",
    ],
    "fs/fuse/fuse_i.h": [
        "struct fuse_backing {",
        "refcount_t count;",
        "bool legacy_once;",
        "struct idr backing_files_map;",
        "struct fuse_backing *backing;",
    ],
    "fs/fuse/backing.c": [
        "fuse_backing_open",
        "fuse_backing_close",
        "fuse_backing_lookup",
        "fuse_backing_take_legacy",
        "fuse_passthrough_open",
        "get_current_cred()",
    ],
    "fs/fuse/passthrough.c": [
        "fuse_passthrough_splice_read",
        "fuse_passthrough_splice_write",
        "FOPEN_PASSTHROUGH",
        "fuse_backing_lookup",
        "fuse_backing_take_legacy",
        "fuse_backing_put",
    ],
    "fs/fuse/dev.c": [
        "FUSE_DEV_IOC_BACKING_OPEN",
        "FUSE_DEV_IOC_BACKING_CLOSE",
        "FUSE_DEV_IOC_PASSTHROUGH_OPEN",
    ],
    "fs/fuse/inode.c": [
        "fuse_backing_files_init(fc);",
        "fuse_backing_files_free(fc);",
        "fuse_features_group",
    ],
    "fs/fuse/Makefile": [
        "fuse-$(CONFIG_FUSE_PASSTHROUGH) += passthrough.o backing.o",
    ],
}
for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: missing postcondition: {needle}")

# Ensure the old pending-object registry is completely gone.
for rel in ("fs/fuse/fuse_i.h", "fs/fuse/inode.c", "fs/fuse/passthrough.c"):
    text = read(rel)
    if "passthrough_req" in text or "passthrough_req_lock" in text:
        raise SystemExit(f"{rel}: stale legacy registry remains")

report = root.parent.parent / "artifacts" / "phase99-fuse-modern-p2.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=99-fuse-modern-p2\n"
    "baseline=bt-p2b-success-f8e6826f66381094f1c943f06f14c53041b0ac0a\n"
    "fuse_p1=retained-boot-proven\n"
    "legacy_android_ioctl_126=preserved-one-shot-shim\n"
    "persistent_backing_registry=enabled\n"
    "backing_open_ioctl=enabled-number-1\n"
    "backing_close_ioctl=enabled-number-2\n"
    "per_open_backing_refs=refcounted\n"
    "modern_open_flag=FOPEN_PASSTHROUGH\n"
    "wire_open_slot=legacy-passthrough_fh-compatible-with-backing-id-offset\n"
    "protocol=7.27-preserved\n"
    "max_stack_depth_negotiation=deferred-p3\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
