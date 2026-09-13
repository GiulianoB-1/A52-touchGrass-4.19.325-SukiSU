#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 87_apply_fuse_passthrough_android.py <kernel-dir>")

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

# ---------------------------------------------------------------------------
# UAPI: preserve FUSE 7.27 wire size while repurposing fuse_open_out.padding
# as the Android passthrough handle and add Android's stable V2 ioctl number.
# ---------------------------------------------------------------------------
replace_once(
    "include/uapi/linux/fuse.h",
    "#define FUSE_MAX_PAGES\t\t(1 << 22)\n",
    "#define FUSE_MAX_PAGES\t\t(1 << 22)\n"
    "/* Android FUSE passthrough uses bit 31 for protocol versions < 7.36. */\n"
    "#define FUSE_PASSTHROUGH\t(1 << 31)\n",
    "uapi_passthrough_flag",
)

replace_once(
    "include/uapi/linux/fuse.h",
    "struct fuse_open_out {\n"
    "\tuint64_t\tfh;\n"
    "\tuint32_t\topen_flags;\n"
    "\tuint32_t\tpadding;\n"
    "};\n",
    "struct fuse_open_out {\n"
    "\tuint64_t\tfh;\n"
    "\tuint32_t\topen_flags;\n"
    "\tuint32_t\tpassthrough_fh;\n"
    "};\n",
    "uapi_open_out_passthrough_fh",
)

replace_once(
    "include/uapi/linux/fuse.h",
    "/* Device ioctls: */\n"
    "#define FUSE_DEV_IOC_CLONE\t_IOR(229, 0, uint32_t)\n",
    "/* Device ioctls: */\n"
    "#define FUSE_DEV_IOC_MAGIC\t229\n"
    "#define FUSE_DEV_IOC_CLONE\t_IOR(FUSE_DEV_IOC_MAGIC, 0, uint32_t)\n"
    "/* 127 is reserved for Android's deprecated V1 passthrough interface. */\n"
    "/* 126 is Android's stable V2 passthrough interface. */\n"
    "#define FUSE_DEV_IOC_PASSTHROUGH_OPEN \\\n"
    "\t_IOW(FUSE_DEV_IOC_MAGIC, 126, uint32_t)\n",
    "uapi_passthrough_ioctl",
)

# ---------------------------------------------------------------------------
# Kconfig / Makefile / defconfig.
# ---------------------------------------------------------------------------
replace_once(
    "fs/fuse/Kconfig",
    "config CUSE\n",
    "config FUSE_PASSTHROUGH\n"
    "\tbool \"Android FUSE passthrough support\"\n"
    "\tdepends on FUSE_FS\n"
    "\tdefault y\n"
    "\thelp\n"
    "\t  Allow a FUSE daemon to associate an opened FUSE file with a lower\n"
    "\t  filesystem file so read, write and mmap I/O can bypass userspace.\n"
    "\t  This is the Android passthrough ABI used by MediaProvider.\n"
    "\n"
    "config CUSE\n",
    "kconfig_passthrough",
)

replace_once(
    "fs/fuse/Makefile",
    "fuse-objs := dev.o dir.o file.o inode.o control.o xattr.o acl.o\n",
    "fuse-objs := dev.o dir.o file.o inode.o control.o xattr.o acl.o\n"
    "fuse-$(CONFIG_FUSE_PASSTHROUGH) += passthrough.o\n",
    "makefile_passthrough",
)

replace_once(
    "arch/arm64/configs/a52xq_defconfig",
    "CONFIG_FUSE_FS=y\n",
    "CONFIG_FUSE_FS=y\nCONFIG_FUSE_PASSTHROUGH=y\n",
    "defconfig_passthrough",
)

# ---------------------------------------------------------------------------
# Internal FUSE structures.
# ---------------------------------------------------------------------------
replace_once(
    "fs/fuse/fuse_i.h",
    "#include <linux/freezer.h>\n",
    "#include <linux/freezer.h>\n#include <linux/idr.h>\n#include <linux/cred.h>\n",
    "fuse_i_includes",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "struct fuse_conn;\n\n/** FUSE specific file data */\nstruct fuse_file {\n",
    "struct fuse_conn;\n\n"
    "/** Lower filesystem file and daemon credentials used by passthrough I/O. */\n"
    "struct fuse_passthrough {\n"
    "\tstruct file *filp;\n"
    "\tstruct cred *cred;\n"
    "};\n\n"
    "/** FUSE specific file data */\nstruct fuse_file {\n",
    "fuse_i_passthrough_struct",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "\t/** RB node to be linked on fuse_conn->polled_files */\n"
    "\tstruct rb_node polled_node;\n",
    "\t/** Container for Android FUSE passthrough state. */\n"
    "\tstruct fuse_passthrough passthrough;\n\n"
    "\t/** RB node to be linked on fuse_conn->polled_files */\n"
    "\tstruct rb_node polled_node;\n",
    "fuse_file_passthrough_member",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "\t/** Allow other than the mounter user to access the filesystem ? */\n"
    "\tunsigned allow_other:1;\n\n"
    "\t/** The number of requests waiting for completion */\n",
    "\t/** Allow other than the mounter user to access the filesystem ? */\n"
    "\tunsigned allow_other:1;\n\n"
    "\t/** Android passthrough mode negotiated during FUSE_INIT. */\n"
    "\tunsigned passthrough:1;\n\n"
    "\t/** The number of requests waiting for completion */\n",
    "fuse_conn_passthrough_bit",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "\t/** List of device instances belonging to this connection */\n"
    "\tstruct list_head devices;\n"
    "};\n",
    "\t/** List of device instances belonging to this connection */\n"
    "\tstruct list_head devices;\n\n"
    "\t/** Pending lower-file registrations keyed by userspace handle. */\n"
    "\tstruct idr passthrough_req;\n"
    "\tspinlock_t passthrough_req_lock;\n"
    "};\n",
    "fuse_conn_passthrough_idr",
)

replace_once(
    "fs/fuse/fuse_i.h",
    "bool fuse_invalid_attr(struct fuse_attr *attr);\n",
    "bool fuse_invalid_attr(struct fuse_attr *attr);\n\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "int fuse_passthrough_open(struct fuse_dev *fud, u32 lower_fd);\n"
    "int fuse_passthrough_setup(struct fuse_conn *fc, struct fuse_file *ff,\n"
    "\t\t\t   struct fuse_open_out *openarg);\n"
    "void fuse_passthrough_release(struct fuse_passthrough *passthrough);\n"
    "ssize_t fuse_passthrough_read_iter(struct kiocb *iocb, struct iov_iter *iter);\n"
    "ssize_t fuse_passthrough_write_iter(struct kiocb *iocb, struct iov_iter *iter);\n"
    "int fuse_passthrough_mmap(struct file *file, struct vm_area_struct *vma);\n"
    "#endif\n",
    "fuse_i_passthrough_prototypes",
)

# ---------------------------------------------------------------------------
# /dev/fuse ioctl registration.
# ---------------------------------------------------------------------------
old_ioctl = """static long fuse_dev_ioctl(struct file *file, unsigned int cmd,
			   unsigned long arg)
{
	int err = -ENOTTY;

	if (cmd == FUSE_DEV_IOC_CLONE) {
		int oldfd;

		err = -EFAULT;
		if (!get_user(oldfd, (__u32 __user *) arg)) {
			struct file *old = fget(oldfd);

			err = -EINVAL;
			if (old) {
				struct fuse_dev *fud = NULL;

				/*
				 * Check against file->f_op because CUSE
				 * uses the same ioctl handler.
				 */
				if (old->f_op == file->f_op &&
				    old->f_cred->user_ns == file->f_cred->user_ns)
					fud = fuse_get_dev(old);

				if (fud) {
					mutex_lock(&fuse_mutex);
					err = fuse_device_clone(fud->fc, file);
					mutex_unlock(&fuse_mutex);
				}
				fput(old);
			}
		}
	}
	return err;
}
"""
new_ioctl = """static long fuse_dev_ioctl(struct file *file, unsigned int cmd,
			   unsigned long arg)
{
	int err;
	int oldfd;
	struct fuse_dev *fud = NULL;

	switch (cmd) {
	case FUSE_DEV_IOC_CLONE:
		err = -EFAULT;
		if (!get_user(oldfd, (__u32 __user *)arg)) {
			struct file *old = fget(oldfd);

			err = -EINVAL;
			if (old) {
				/*
				 * Check against file->f_op because CUSE
				 * uses the same ioctl handler.
				 */
				if (old->f_op == file->f_op &&
				    old->f_cred->user_ns == file->f_cred->user_ns)
					fud = fuse_get_dev(old);

				if (fud) {
					mutex_lock(&fuse_mutex);
					err = fuse_device_clone(fud->fc, file);
					mutex_unlock(&fuse_mutex);
				}
				fput(old);
			}
		}
		break;
#ifdef CONFIG_FUSE_PASSTHROUGH
	case FUSE_DEV_IOC_PASSTHROUGH_OPEN:
		err = -EFAULT;
		if (!get_user(oldfd, (__u32 __user *)arg)) {
			err = -EINVAL;
			fud = fuse_get_dev(file);
			if (fud)
				err = fuse_passthrough_open(fud, oldfd);
		}
		break;
#endif
	default:
		err = -ENOTTY;
		break;
	}

	return err;
}
"""
replace_once("fs/fuse/dev.c", old_ioctl, new_ioctl, "dev_passthrough_ioctl")

# ---------------------------------------------------------------------------
# Open/create association and fast-path routing.
# ---------------------------------------------------------------------------
replace_once(
    "fs/fuse/file.c",
    "void fuse_file_free(struct fuse_file *ff)\n"
    "{\n"
    "\tfuse_request_free(ff->reserved_req);\n"
    "\tkfree(ff);\n"
    "}\n",
    "void fuse_file_free(struct fuse_file *ff)\n"
    "{\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tfuse_passthrough_release(&ff->passthrough);\n"
    "#endif\n"
    "\tfuse_request_free(ff->reserved_req);\n"
    "\tkfree(ff);\n"
    "}\n",
    "file_free_passthrough_release",
)

replace_once(
    "fs/fuse/file.c",
    "\t\tstruct fuse_open_out outarg;\n"
    "\t\tint err;\n\n"
    "\t\terr = fuse_send_open(fc, nodeid, file, opcode, &outarg);\n",
    "\t\tstruct fuse_open_out outarg;\n"
    "\t\tint err;\n\n"
    "\t\tmemset(&outarg, 0, sizeof(outarg));\n"
    "\t\terr = fuse_send_open(fc, nodeid, file, opcode, &outarg);\n",
    "open_zero_outarg",
)

replace_once(
    "fs/fuse/file.c",
    "\t\tif (!err) {\n"
    "\t\t\tff->fh = outarg.fh;\n"
    "\t\t\tff->open_flags = outarg.open_flags;\n\n"
    "\t\t} else if (err != -ENOSYS || isdir) {\n",
    "\t\tif (!err) {\n"
    "\t\t\tff->fh = outarg.fh;\n"
    "\t\t\tff->open_flags = outarg.open_flags;\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t\t\tif (!isdir)\n"
    "\t\t\t\tfuse_passthrough_setup(fc, ff, &outarg);\n"
    "#endif\n"
    "\n"
    "\t\t} else if (err != -ENOSYS || isdir) {\n",
    "open_passthrough_setup",
)

replace_once(
    "fs/fuse/file.c",
    "\tint opcode = isdir ? FUSE_RELEASEDIR : FUSE_RELEASE;\n\n"
    "\tfuse_prepare_release(ff, file->f_flags, opcode);\n",
    "\tint opcode = isdir ? FUSE_RELEASEDIR : FUSE_RELEASE;\n\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tfuse_passthrough_release(&ff->passthrough);\n"
    "#endif\n"
    "\tfuse_prepare_release(ff, file->f_flags, opcode);\n",
    "release_common_passthrough_release",
)

replace_once(
    "fs/fuse/file.c",
    "void fuse_sync_release(struct fuse_file *ff, int flags)\n"
    "{\n"
    "\tWARN_ON(refcount_read(&ff->count) > 1);\n"
    "\tfuse_prepare_release(ff, flags, FUSE_RELEASE);\n",
    "void fuse_sync_release(struct fuse_file *ff, int flags)\n"
    "{\n"
    "\tWARN_ON(refcount_read(&ff->count) > 1);\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tfuse_passthrough_release(&ff->passthrough);\n"
    "#endif\n"
    "\tfuse_prepare_release(ff, flags, FUSE_RELEASE);\n",
    "sync_release_passthrough_release",
)

# Route normal cached file operations into the lower file when passthrough was
# associated. Direct-I/O fops remain separate and therefore override this.
replace_once(
    "fs/fuse/file.c",
    "static ssize_t fuse_file_read_iter(struct kiocb *iocb, struct iov_iter *to)\n"
    "{\n"
    "\tstruct file *file = iocb->ki_filp;\n"
    "\tstruct fuse_conn *fc = get_fuse_conn(file_inode(file));\n"
    "\tssize_t ret;\n\n",
    "static ssize_t fuse_file_read_iter(struct kiocb *iocb, struct iov_iter *to)\n"
    "{\n"
    "\tstruct file *file = iocb->ki_filp;\n"
    "\tstruct fuse_conn *fc = get_fuse_conn(file_inode(file));\n"
    "\tssize_t ret;\n\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tstruct fuse_file *ff = file->private_data;\n"
    "\tif (ff->passthrough.filp)\n"
    "\t\treturn fuse_passthrough_read_iter(iocb, to);\n"
    "#endif\n\n",
    "read_iter_passthrough_route",
)

replace_once(
    "fs/fuse/file.c",
    "static ssize_t fuse_file_write_iter(struct kiocb *iocb, struct iov_iter *from)\n"
    "{\n"
    "\tstruct file *file = iocb->ki_filp;\n"
    "\tstruct inode *inode = file_inode(file);\n",
    "static ssize_t fuse_file_write_iter(struct kiocb *iocb, struct iov_iter *from)\n"
    "{\n"
    "\tstruct file *file = iocb->ki_filp;\n"
    "\tstruct inode *inode = file_inode(file);\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tstruct fuse_file *ff = file->private_data;\n\n"
    "\tif (ff->passthrough.filp)\n"
    "\t\treturn fuse_passthrough_write_iter(iocb, from);\n"
    "#endif\n",
    "write_iter_passthrough_route",
)

replace_once(
    "fs/fuse/file.c",
    "static int fuse_file_mmap(struct file *file, struct vm_area_struct *vma)\n"
    "{\n"
    "\tstruct fuse_conn *fc = get_fuse_conn(file_inode(file));\n",
    "static int fuse_file_mmap(struct file *file, struct vm_area_struct *vma)\n"
    "{\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tstruct fuse_file *ff = file->private_data;\n\n"
    "\tif (ff->passthrough.filp)\n"
    "\t\treturn fuse_passthrough_mmap(file, vma);\n"
    "#endif\n"
    "\tstruct fuse_conn *fc = get_fuse_conn(file_inode(file));\n",
    "mmap_passthrough_route",
)

replace_once(
    "fs/fuse/dir.c",
    "\tmemset(&inarg, 0, sizeof(inarg));\n"
    "\tmemset(&outentry, 0, sizeof(outentry));\n",
    "\tmemset(&inarg, 0, sizeof(inarg));\n"
    "\tmemset(&outentry, 0, sizeof(outentry));\n"
    "\tmemset(&outopen, 0, sizeof(outopen));\n",
    "create_zero_outopen",
)

replace_once(
    "fs/fuse/dir.c",
    "\tff->fh = outopen.fh;\n"
    "\tff->nodeid = outentry.nodeid;\n"
    "\tff->open_flags = outopen.open_flags;\n"
    "\tinode = fuse_iget(dir->i_sb, outentry.nodeid, outentry.generation,\n",
    "\tff->fh = outopen.fh;\n"
    "\tff->nodeid = outentry.nodeid;\n"
    "\tff->open_flags = outopen.open_flags;\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tfuse_passthrough_setup(fc, ff, &outopen);\n"
    "#endif\n"
    "\tinode = fuse_iget(dir->i_sb, outentry.nodeid, outentry.generation,\n",
    "create_passthrough_setup",
)

# ---------------------------------------------------------------------------
# Connection negotiation/lifecycle.
# ---------------------------------------------------------------------------
replace_once(
    "fs/fuse/inode.c",
    "\tINIT_LIST_HEAD(&fc->devices);\n"
    "\tatomic_set(&fc->num_waiting, 0);\n",
    "\tINIT_LIST_HEAD(&fc->devices);\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tidr_init(&fc->passthrough_req);\n"
    "\tspin_lock_init(&fc->passthrough_req_lock);\n"
    "#endif\n"
    "\tatomic_set(&fc->num_waiting, 0);\n",
    "conn_init_passthrough_idr",
)

replace_once(
    "fs/fuse/inode.c",
    "\t\t\tif (arg->flags & FUSE_MAX_PAGES) {\n"
    "\t\t\t\tfc->max_pages =\n"
    "\t\t\t\t\tmin_t(unsigned int, FUSE_MAX_MAX_PAGES,\n"
    "\t\t\t\t\tmax_t(unsigned int, arg->max_pages, 1));\n"
    "\t\t\t}\n",
    "\t\t\tif (arg->flags & FUSE_MAX_PAGES) {\n"
    "\t\t\t\tfc->max_pages =\n"
    "\t\t\t\t\tmin_t(unsigned int, FUSE_MAX_MAX_PAGES,\n"
    "\t\t\t\t\tmax_t(unsigned int, arg->max_pages, 1));\n"
    "\t\t\t}\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t\t\tif (arg->flags & FUSE_PASSTHROUGH) {\n"
    "\t\t\t\tfc->passthrough = 1;\n"
    "\t\t\t\t/* Passthrough already consumes the available stack layer. */\n"
    "\t\t\t\tfc->sb->s_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;\n"
    "\t\t\t}\n"
    "#endif\n",
    "init_reply_passthrough",
)

replace_once(
    "fs/fuse/inode.c",
    "\t\tFUSE_PARALLEL_DIROPS | FUSE_HANDLE_KILLPRIV | FUSE_POSIX_ACL |\n"
    "\t\tFUSE_ABORT_ERROR | FUSE_MAX_PAGES;\n",
    "\t\tFUSE_PARALLEL_DIROPS | FUSE_HANDLE_KILLPRIV | FUSE_POSIX_ACL |\n"
    "\t\tFUSE_ABORT_ERROR | FUSE_MAX_PAGES\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t\t| FUSE_PASSTHROUGH\n"
    "#endif\n"
    "\t\t;\n",
    "send_init_passthrough_flag",
)

replace_once(
    "fs/fuse/inode.c",
    "static void fuse_free_conn(struct fuse_conn *fc)\n"
    "{\n"
    "\tWARN_ON(!list_empty(&fc->devices));\n"
    "\tkfree_rcu(fc, rcu);\n"
    "}\n",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "static int free_fuse_passthrough(int id, void *p, void *data)\n"
    "{\n"
    "\tstruct fuse_passthrough *passthrough = p;\n\n"
    "\tfuse_passthrough_release(passthrough);\n"
    "\tkfree(passthrough);\n"
    "\treturn 0;\n"
    "}\n"
    "#endif\n\n"
    "static void fuse_free_conn(struct fuse_conn *fc)\n"
    "{\n"
    "\tWARN_ON(!list_empty(&fc->devices));\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tidr_for_each(&fc->passthrough_req, free_fuse_passthrough, NULL);\n"
    "\tidr_destroy(&fc->passthrough_req);\n"
    "#endif\n"
    "\tkfree_rcu(fc, rcu);\n"
    "}\n",
    "conn_free_passthrough_idr",
)

# Android userspace feature probe: /sys/fs/fuse/features/fuse_passthrough
replace_once(
    "fs/fuse/inode.c",
    "static struct kobject *fuse_kobj;\n\n"
    "static int fuse_sysfs_init(void)\n",
    "static struct kobject *fuse_kobj;\n\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "static ssize_t fuse_passthrough_show(struct kobject *kobj,\n"
    "\t\t\t\t     struct kobj_attribute *attr, char *buf)\n"
    "{\n"
    "\treturn scnprintf(buf, PAGE_SIZE, \"supported\\n\");\n"
    "}\n\n"
    "static struct kobj_attribute fuse_passthrough_attr =\n"
    "\t__ATTR(fuse_passthrough, 0444, fuse_passthrough_show, NULL);\n\n"
    "static struct attribute *fuse_features_attrs[] = {\n"
    "\t&fuse_passthrough_attr.attr,\n"
    "\tNULL,\n"
    "};\n\n"
    "static const struct attribute_group fuse_features_group = {\n"
    "\t.name = \"features\",\n"
    "\t.attrs = fuse_features_attrs,\n"
    "};\n"
    "#endif\n\n"
    "static int fuse_sysfs_init(void)\n",
    "sysfs_feature_definition",
)

replace_once(
    "fs/fuse/inode.c",
    "\terr = sysfs_create_mount_point(fuse_kobj, \"connections\");\n"
    "\tif (err)\n"
    "\t\tgoto out_fuse_unregister;\n\n"
    "\treturn 0;\n",
    "\terr = sysfs_create_mount_point(fuse_kobj, \"connections\");\n"
    "\tif (err)\n"
    "\t\tgoto out_fuse_unregister;\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\terr = sysfs_create_group(fuse_kobj, &fuse_features_group);\n"
    "\tif (err)\n"
    "\t\tgoto out_connections;\n"
    "#endif\n\n"
    "\treturn 0;\n\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    " out_connections:\n"
    "\tsysfs_remove_mount_point(fuse_kobj, \"connections\");\n"
    "#endif\n",
    "sysfs_feature_create",
)

replace_once(
    "fs/fuse/inode.c",
    "static void fuse_sysfs_cleanup(void)\n"
    "{\n"
    "\tsysfs_remove_mount_point(fuse_kobj, \"connections\");\n"
    "\tkobject_put(fuse_kobj);\n"
    "}\n",
    "static void fuse_sysfs_cleanup(void)\n"
    "{\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\tsysfs_remove_group(fuse_kobj, &fuse_features_group);\n"
    "#endif\n"
    "\tsysfs_remove_mount_point(fuse_kobj, \"connections\");\n"
    "\tkobject_put(fuse_kobj);\n"
    "}\n",
    "sysfs_feature_cleanup",
)

# ---------------------------------------------------------------------------
# Android 12 passthrough implementation, adapted to the 4.19 VFS API.
# ---------------------------------------------------------------------------
passthrough_c = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Android FUSE passthrough backport for the Samsung 4.19 FUSE core.
 *
 * Based on Android common android12-5.4 passthrough.c.  The FUSE protocol
 * remains 7.27; the existing 32-bit fuse_open_out padding slot carries the
 * passthrough handle exactly as Android's frozen-kernel ABI expects.
 */
#include "fuse_i.h"

#include <linux/cred.h>
#include <linux/file.h>
#include <linux/fuse.h>
#include <linux/idr.h>
#include <linux/slab.h>
#include <linux/uio.h>

#define PASSTHROUGH_IOCB_MASK 	(IOCB_APPEND | IOCB_DSYNC | IOCB_HIPRI | IOCB_NOWAIT | IOCB_SYNC)

struct fuse_aio_req {
	struct kiocb iocb;
	struct kiocb *iocb_fuse;
};

static rwf_t fuse_passthrough_iocb_to_rw_flags(int ifl)
{
	rwf_t flags = 0;

	if (ifl & IOCB_HIPRI)
		flags |= RWF_HIPRI;
	if (ifl & IOCB_DSYNC)
		flags |= RWF_DSYNC;
	if (ifl & IOCB_SYNC)
		flags |= RWF_SYNC;
	if (ifl & IOCB_NOWAIT)
		flags |= RWF_NOWAIT;
	if (ifl & IOCB_APPEND)
		flags |= RWF_APPEND;

	return flags;
}

static inline void kiocb_clone(struct kiocb *kiocb,
			       struct kiocb *kiocb_src, struct file *filp)
{
	*kiocb = (struct kiocb) {
		.ki_filp = filp,
		.ki_flags = kiocb_src->ki_flags,
		.ki_hint = kiocb_src->ki_hint,
		.ki_ioprio = kiocb_src->ki_ioprio,
		.ki_pos = kiocb_src->ki_pos,
	};
}

static void fuse_file_accessed(struct file *dst_file, struct file *src_file)
{
	struct inode *dst_inode;
	struct inode *src_inode;

	if (dst_file->f_flags & O_NOATIME)
		return;

	dst_inode = file_inode(dst_file);
	src_inode = file_inode(src_file);

	if (!timespec64_equal(&dst_inode->i_mtime, &src_inode->i_mtime) ||
	    !timespec64_equal(&dst_inode->i_ctime, &src_inode->i_ctime)) {
		dst_inode->i_mtime = src_inode->i_mtime;
		dst_inode->i_ctime = src_inode->i_ctime;
	}

	touch_atime(&dst_file->f_path);
}

static void fuse_copyattr(struct file *dst_file, struct file *src_file)
{
	struct inode *dst = file_inode(dst_file);
	struct inode *src = file_inode(src_file);

	dst->i_atime = src->i_atime;
	dst->i_mtime = src->i_mtime;
	dst->i_ctime = src->i_ctime;
	i_size_write(dst, i_size_read(src));
}

static void fuse_aio_cleanup_handler(struct fuse_aio_req *aio_req)
{
	struct kiocb *iocb = &aio_req->iocb;
	struct kiocb *iocb_fuse = aio_req->iocb_fuse;

	if (iocb->ki_flags & IOCB_WRITE) {
		__sb_writers_acquired(file_inode(iocb->ki_filp)->i_sb,
				      SB_FREEZE_WRITE);
		file_end_write(iocb->ki_filp);
		fuse_copyattr(iocb_fuse->ki_filp, iocb->ki_filp);
	}

	iocb_fuse->ki_pos = iocb->ki_pos;
	kfree(aio_req);
}

static void fuse_aio_rw_complete(struct kiocb *iocb, long res, long res2)
{
	struct fuse_aio_req *aio_req =
		container_of(iocb, struct fuse_aio_req, iocb);
	struct kiocb *iocb_fuse = aio_req->iocb_fuse;

	fuse_aio_cleanup_handler(aio_req);
	iocb_fuse->ki_complete(iocb_fuse, res, res2);
}

ssize_t fuse_passthrough_read_iter(struct kiocb *iocb_fuse,
				   struct iov_iter *iter)
{
	ssize_t ret;
	const struct cred *old_cred;
	struct file *fuse_filp = iocb_fuse->ki_filp;
	struct fuse_file *ff = fuse_filp->private_data;
	struct file *passthrough_filp = ff->passthrough.filp;

	if (!iov_iter_count(iter))
		return 0;

	old_cred = override_creds(ff->passthrough.cred);
	if (is_sync_kiocb(iocb_fuse)) {
		ret = vfs_iter_read(passthrough_filp, iter, &iocb_fuse->ki_pos,
				    fuse_passthrough_iocb_to_rw_flags(
					    iocb_fuse->ki_flags &
					    PASSTHROUGH_IOCB_MASK));
	} else {
		struct fuse_aio_req *aio_req;

		aio_req = kmalloc(sizeof(*aio_req), GFP_KERNEL);
		if (!aio_req) {
			ret = -ENOMEM;
			goto out;
		}

		aio_req->iocb_fuse = iocb_fuse;
		kiocb_clone(&aio_req->iocb, iocb_fuse, passthrough_filp);
		aio_req->iocb.ki_complete = fuse_aio_rw_complete;
		ret = call_read_iter(passthrough_filp, &aio_req->iocb, iter);
		if (ret != -EIOCBQUEUED)
			fuse_aio_cleanup_handler(aio_req);
	}
out:
	revert_creds(old_cred);
	fuse_file_accessed(fuse_filp, passthrough_filp);
	return ret;
}

ssize_t fuse_passthrough_write_iter(struct kiocb *iocb_fuse,
				    struct iov_iter *iter)
{
	ssize_t ret;
	const struct cred *old_cred;
	struct file *fuse_filp = iocb_fuse->ki_filp;
	struct fuse_file *ff = fuse_filp->private_data;
	struct inode *fuse_inode = file_inode(fuse_filp);
	struct file *passthrough_filp = ff->passthrough.filp;
	struct inode *passthrough_inode = file_inode(passthrough_filp);

	if (!iov_iter_count(iter))
		return 0;

	inode_lock(fuse_inode);
	fuse_copyattr(fuse_filp, passthrough_filp);

	old_cred = override_creds(ff->passthrough.cred);
	if (is_sync_kiocb(iocb_fuse)) {
		file_start_write(passthrough_filp);
		ret = vfs_iter_write(passthrough_filp, iter, &iocb_fuse->ki_pos,
				     fuse_passthrough_iocb_to_rw_flags(
					     iocb_fuse->ki_flags &
					     PASSTHROUGH_IOCB_MASK));
		file_end_write(passthrough_filp);
		if (ret > 0)
			fuse_copyattr(fuse_filp, passthrough_filp);
	} else {
		struct fuse_aio_req *aio_req;

		aio_req = kmalloc(sizeof(*aio_req), GFP_KERNEL);
		if (!aio_req) {
			ret = -ENOMEM;
			goto out;
		}

		file_start_write(passthrough_filp);
		__sb_writers_release(passthrough_inode->i_sb, SB_FREEZE_WRITE);

		aio_req->iocb_fuse = iocb_fuse;
		kiocb_clone(&aio_req->iocb, iocb_fuse, passthrough_filp);
		aio_req->iocb.ki_complete = fuse_aio_rw_complete;
		ret = call_write_iter(passthrough_filp, &aio_req->iocb, iter);
		if (ret != -EIOCBQUEUED)
			fuse_aio_cleanup_handler(aio_req);
	}
out:
	revert_creds(old_cred);
	inode_unlock(fuse_inode);
	return ret;
}

int fuse_passthrough_mmap(struct file *file, struct vm_area_struct *vma)
{
	int ret;
	const struct cred *old_cred;
	struct fuse_file *ff = file->private_data;
	struct file *passthrough_filp = ff->passthrough.filp;

	if (!passthrough_filp->f_op->mmap)
		return -ENODEV;

	if (WARN_ON(file != vma->vm_file))
		return -EIO;

	vma->vm_file = get_file(passthrough_filp);
	old_cred = override_creds(ff->passthrough.cred);
	ret = call_mmap(vma->vm_file, vma);
	revert_creds(old_cred);

	if (ret)
		fput(passthrough_filp);
	else
		fput(file);

	fuse_file_accessed(file, passthrough_filp);
	return ret;
}

int fuse_passthrough_open(struct fuse_dev *fud, u32 lower_fd)
{
	int res;
	struct file *passthrough_filp;
	struct fuse_conn *fc = fud->fc;
	struct inode *passthrough_inode;
	struct super_block *passthrough_sb;
	struct fuse_passthrough *passthrough;

	if (!fc->passthrough)
		return -EPERM;

	passthrough_filp = fget(lower_fd);
	if (!passthrough_filp) {
		pr_err("FUSE: invalid file descriptor for passthrough.\n");
		return -EBADF;
	}

	if (!passthrough_filp->f_op->read_iter ||
	    !passthrough_filp->f_op->write_iter) {
		pr_err("FUSE: passthrough file misses file operations.\n");
		res = -EBADF;
		goto err_free_file;
	}

	passthrough_inode = file_inode(passthrough_filp);
	passthrough_sb = passthrough_inode->i_sb;
	if (passthrough_sb->s_stack_depth >= FILESYSTEM_MAX_STACK_DEPTH) {
		pr_err("FUSE: fs stacking depth exceeded for passthrough\n");
		res = -EINVAL;
		goto err_free_file;
	}

	passthrough = kzalloc(sizeof(*passthrough), GFP_KERNEL);
	if (!passthrough) {
		res = -ENOMEM;
		goto err_free_file;
	}

	passthrough->filp = passthrough_filp;
	passthrough->cred = prepare_creds();
	if (!passthrough->cred) {
		res = -ENOMEM;
		kfree(passthrough);
		goto err_free_file;
	}

	idr_preload(GFP_KERNEL);
	spin_lock(&fc->passthrough_req_lock);
	res = idr_alloc(&fc->passthrough_req, passthrough, 1, 0, GFP_ATOMIC);
	spin_unlock(&fc->passthrough_req_lock);
	idr_preload_end();

	if (res > 0)
		return res;

	fuse_passthrough_release(passthrough);
	kfree(passthrough);
	return res;

err_free_file:
	fput(passthrough_filp);
	return res;
}

int fuse_passthrough_setup(struct fuse_conn *fc, struct fuse_file *ff,
			   struct fuse_open_out *openarg)
{
	struct fuse_passthrough *passthrough;
	int passthrough_fh = openarg->passthrough_fh;

	if (!fc->passthrough)
		return -EPERM;

	if (passthrough_fh <= 0)
		return -EINVAL;

	spin_lock(&fc->passthrough_req_lock);
	passthrough = idr_remove(&fc->passthrough_req, passthrough_fh);
	spin_unlock(&fc->passthrough_req_lock);

	if (!passthrough)
		return -EINVAL;

	ff->passthrough = *passthrough;
	kfree(passthrough);
	return 0;
}

void fuse_passthrough_release(struct fuse_passthrough *passthrough)
{
	if (passthrough->filp) {
		fput(passthrough->filp);
		passthrough->filp = NULL;
	}
	if (passthrough->cred) {
		put_cred(passthrough->cred);
		passthrough->cred = NULL;
	}
}
'''
write("fs/fuse/passthrough.c", passthrough_c)
changes.append("passthrough_c")

# ---------------------------------------------------------------------------
# Structural verification.
# ---------------------------------------------------------------------------
checks = {
    "include/uapi/linux/fuse.h": [
        "#define FUSE_PASSTHROUGH\t(1 << 31)",
        "uint32_t\tpassthrough_fh;",
        "FUSE_DEV_IOC_PASSTHROUGH_OPEN",
        "126, uint32_t",
    ],
    "fs/fuse/fuse_i.h": [
        "struct fuse_passthrough",
        "struct idr passthrough_req;",
        "unsigned passthrough:1;",
    ],
    "fs/fuse/dev.c": ["FUSE_DEV_IOC_PASSTHROUGH_OPEN", "fuse_passthrough_open"],
    "fs/fuse/file.c": [
        "fuse_passthrough_read_iter",
        "fuse_passthrough_write_iter",
        "fuse_passthrough_mmap",
        "fuse_passthrough_setup",
    ],
    "fs/fuse/dir.c": ["fuse_passthrough_setup(fc, ff, &outopen);"],
    "fs/fuse/inode.c": [
        "idr_init(&fc->passthrough_req);",
        "FUSE_PASSTHROUGH",
        "fuse_features_group",
        "fuse_passthrough_release(passthrough);",
    ],
    "fs/fuse/passthrough.c": [
        "fuse_passthrough_open",
        "fuse_passthrough_read_iter",
        "fuse_passthrough_write_iter",
        "fuse_passthrough_mmap",
        "prepare_creds()",
    ],
    "arch/arm64/configs/a52xq_defconfig": ["CONFIG_FUSE_PASSTHROUGH=y"],
}
for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: missing postcondition: {needle}")

report = root.parent.parent / "artifacts" / "phase87-fuse-passthrough.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=87-fuse-passthrough-android12-backport\n"
    "base=phase86\n"
    "fuse_protocol=7.27-preserved\n"
    "passthrough_flag=bit31-pre-7.36-android-abi\n"
    "passthrough_ioctl=v2-number-126\n"
    "sysfs_feature=/sys/fs/fuse/features/fuse_passthrough\n"
    "direct_io_override=preserved-by-separate-direct-fops\n"
    "lower_credentials=captured-at-ioctl\n"
    "async_io=ported\n"
    "mmap=ported\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
