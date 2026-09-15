#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 98_apply_fuse_passthrough_modern_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
changes = []

def read(rel):
    p = root / rel
    if not p.is_file():
        raise SystemExit(f"missing {p}")
    return p.read_text()

def write(rel, data):
    p = root / rel
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

def insert_before_once(rel, marker, snippet, label):
    s = read(rel)
    if snippet in s:
        changes.append(label + "_already")
        return
    count = s.count(marker)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker in {rel}, found {count}")
    write(rel, s.replace(marker, snippet + marker, 1))
    changes.append(label)

# ---------------------------------------------------------------------------
# Phase 98 / FUSE modern P1
#
# Keep Android's proven protocol-7.27 / ioctl-126 ABI intact while importing
# upstream passthrough correctness and fast-path ideas that fit Linux 4.19.
#
# Sources/intent:
#   c51248524a0f - fuse: use current creds for backing files
#   5ca73468612d - fuse: implement splice read/write passthrough
#   996b37da1e0f - backing-file: use fops->splice_write
#   20121d3f58f - fuse: update inode size after extending passthrough write
#
# Persistent BACKING_OPEN/BACKING_CLOSE and protocol-7.40 max_stack_depth are
# deliberately deferred to P2 because they change backing-object lifetime.
# ---------------------------------------------------------------------------

# Advertise the modern open flag value without changing the legacy wire size.
replace_once(
    "include/uapi/linux/fuse.h",
    "#define FUSE_PASSTHROUGH\t(1 << 31)\n",
    "#define FUSE_PASSTHROUGH\t(1 << 31)\n"
    "/* Upstream FUSE 7.40 open flag. Safe to expose on the 7.27-compatible ABI. */\n"
    "#define FOPEN_PASSTHROUGH\t(1 << 7)\n",
    "uapi_fopen_passthrough",
)

# Upstream 2026 uses a refcounted snapshot of current credentials rather than
# prepare_creds(), which allocates a mutable copy that is never modified.
replace_once(
    "fs/fuse/fuse_i.h",
    "struct fuse_passthrough {\n"
    "\tstruct file *filp;\n"
    "\tstruct cred *cred;\n"
    "};\n",
    "struct fuse_passthrough {\n"
    "\tstruct file *filp;\n"
    "\tconst struct cred *cred;\n"
    "};\n",
    "const_passthrough_cred",
)

replace_once(
    "fs/fuse/passthrough.c",
    "#include <linux/uio.h>\n",
    "#include <linux/uio.h>\n"
    "#include <linux/splice.h>\n",
    "splice_header",
)

replace_once(
    "fs/fuse/passthrough.c",
    "\tpassthrough->filp = passthrough_filp;\n"
    "\tpassthrough->cred = prepare_creds();\n"
    "\tif (!passthrough->cred) {\n"
    "\t\tres = -ENOMEM;\n"
    "\t\tkfree(passthrough);\n"
    "\t\tgoto err_free_file;\n"
    "\t}\n",
    "\tpassthrough->filp = passthrough_filp;\n"
    "\t/* A52 FUSE P1: upstream c51248524a0f, immutable credential snapshot. */\n"
    "\tpassthrough->cred = get_current_cred();\n",
    "get_current_cred",
)

# Public internal prototypes for the new fast paths.
replace_once(
    "fs/fuse/fuse_i.h",
    "ssize_t fuse_passthrough_write_iter(struct kiocb *iocb, struct iov_iter *iter);\n"
    "int fuse_passthrough_mmap(struct file *file, struct vm_area_struct *vma);\n",
    "ssize_t fuse_passthrough_write_iter(struct kiocb *iocb, struct iov_iter *iter);\n"
    "ssize_t fuse_passthrough_splice_read(struct file *in, loff_t *ppos,\n"
    "\t\t\t\t       struct pipe_inode_info *pipe,\n"
    "\t\t\t\t       size_t len, unsigned int flags);\n"
    "ssize_t fuse_passthrough_splice_write(struct pipe_inode_info *pipe,\n"
    "\t\t\t\t\tstruct file *out, loff_t *ppos,\n"
    "\t\t\t\t\tsize_t len, unsigned int flags);\n"
    "int fuse_passthrough_mmap(struct file *file, struct vm_area_struct *vma);\n",
    "splice_prototypes",
)

splice_impl = r'''
/*
 * A52 FUSE P1: splice passthrough.
 *
 * Upstream FUSE gained dedicated splice passthrough in Linux 6.9.  The
 * modern generic backing-file helpers do not exist on 4.19, so keep the
 * same semantics locally: run lower operations under the daemon credential,
 * prefer the lower filesystem's splice callback, and synchronize FUSE attrs.
 */
ssize_t fuse_passthrough_splice_read(struct file *in, loff_t *ppos,
                                     struct pipe_inode_info *pipe,
                                     size_t len, unsigned int flags)
{
    ssize_t ret;
    const struct cred *old_cred;
    struct fuse_file *ff = in->private_data;
    struct file *lower = ff->passthrough.filp;

    if (!len)
        return 0;

    old_cred = override_creds(ff->passthrough.cred);
    ret = rw_verify_area(READ, lower, ppos, len);
    if (!ret) {
        if (lower->f_op->splice_read)
            ret = lower->f_op->splice_read(lower, ppos, pipe, len, flags);
        else
            ret = generic_file_splice_read(lower, ppos, pipe, len, flags);
    }
    revert_creds(old_cred);

    fuse_file_accessed(in, lower);
    return ret;
}

ssize_t fuse_passthrough_splice_write(struct pipe_inode_info *pipe,
                                      struct file *out, loff_t *ppos,
                                      size_t len, unsigned int flags)
{
    ssize_t ret;
    const struct cred *old_cred;
    struct fuse_file *ff = out->private_data;
    struct inode *fuse_inode = file_inode(out);
    struct file *lower = ff->passthrough.filp;

    if (!len)
        return 0;

    inode_lock(fuse_inode);
    fuse_copyattr(out, lower);

    old_cred = override_creds(ff->passthrough.cred);
    ret = rw_verify_area(WRITE, lower, ppos, len);
    if (!ret) {
        file_start_write(lower);

        /*
         * Keep the 2024 upstream fix: filesystems may provide a specialized
         * splice_write implementation, so prefer it instead of forcing the
         * generic write_iter bridge.
         */
        if (lower->f_op->splice_write)
            ret = lower->f_op->splice_write(pipe, lower, ppos, len, flags);
        else
            ret = iter_file_splice_write(pipe, lower, ppos, len, flags);

        file_end_write(lower);
    }
    revert_creds(old_cred);

    if (ret > 0)
        fuse_copyattr(out, lower);

    inode_unlock(fuse_inode);
    return ret;
}

'''
insert_before_once(
    "fs/fuse/passthrough.c",
    "int fuse_passthrough_mmap(struct file *file, struct vm_area_struct *vma)\n",
    splice_impl,
    "splice_impl",
)

# Route normal FUSE splice operations to the lower file when passthrough is
# active. Direct-I/O fops remain unchanged, preserving the existing override.
file_wrappers = r'''
#ifdef CONFIG_FUSE_PASSTHROUGH
static ssize_t fuse_file_splice_read_pt(struct file *in, loff_t *ppos,
                                        struct pipe_inode_info *pipe,
                                        size_t len, unsigned int flags)
{
    struct fuse_file *ff = in->private_data;

    if (ff && ff->passthrough.filp)
        return fuse_passthrough_splice_read(in, ppos, pipe, len, flags);

    return generic_file_splice_read(in, ppos, pipe, len, flags);
}

static ssize_t fuse_file_splice_write_pt(struct pipe_inode_info *pipe,
                                         struct file *out, loff_t *ppos,
                                         size_t len, unsigned int flags)
{
    struct fuse_file *ff = out->private_data;

    if (ff && ff->passthrough.filp)
        return fuse_passthrough_splice_write(pipe, out, ppos, len, flags);

    return iter_file_splice_write(pipe, out, ppos, len, flags);
}
#endif

'''
insert_before_once(
    "fs/fuse/file.c",
    "static const struct file_operations fuse_file_operations = {\n",
    file_wrappers,
    "file_splice_wrappers",
)

replace_once(
    "fs/fuse/file.c",
    "\t.splice_read\t= generic_file_splice_read,\n",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t.splice_read\t= fuse_file_splice_read_pt,\n"
    "\t.splice_write\t= fuse_file_splice_write_pt,\n"
    "#else\n"
    "\t.splice_read\t= generic_file_splice_read,\n"
    "#endif\n",
    "file_operations_splice_route",
)

# Update the help text to describe the actual modernized operation set.
replace_once(
    "fs/fuse/Kconfig",
    "\t  filesystem file so read, write and mmap I/O can bypass userspace.\n",
    "\t  filesystem file so read, write, splice and mmap I/O can bypass userspace.\n",
    "kconfig_splice_help",
)

# Structural verification.
checks = {
    "include/uapi/linux/fuse.h": [
        "#define FUSE_PASSTHROUGH\t(1 << 31)",
        "#define FOPEN_PASSTHROUGH\t(1 << 7)",
        "FUSE_DEV_IOC_PASSTHROUGH_OPEN",
    ],
    "fs/fuse/fuse_i.h": [
        "const struct cred *cred;",
        "fuse_passthrough_splice_read",
        "fuse_passthrough_splice_write",
    ],
    "fs/fuse/passthrough.c": [
        "get_current_cred()",
        "fuse_passthrough_splice_read",
        "fuse_passthrough_splice_write",
        "lower->f_op->splice_write",
        "fuse_copyattr(out, lower);",
    ],
    "fs/fuse/file.c": [
        "fuse_file_splice_read_pt",
        "fuse_file_splice_write_pt",
        ".splice_write\t= fuse_file_splice_write_pt,",
    ],
}
for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: missing postcondition: {needle}")

# Guard the legacy Android ABI. P1 must not accidentally remove the proven
# ioctl-126 path or change the 7.27 open reply layout.
uapi = read("include/uapi/linux/fuse.h")
if "FUSE_DEV_IOC_PASSTHROUGH_OPEN" not in uapi or "passthrough_fh" not in uapi:
    raise SystemExit("legacy Android passthrough ABI was not preserved")

report = root.parent.parent / "artifacts" / "phase98-fuse-modern-p1.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=98-fuse-modern-p1\n"
    "base=bt-p2a-proven\n"
    "legacy_android_v2_ioctl_126=preserved\n"
    "fuse_protocol=7.27-preserved\n"
    "fopen_passthrough_flag=added-for-forward-compat\n"
    "credentials=get_current_cred-c51248524a0f\n"
    "splice_read=lower-fast-path\n"
    "splice_write=lower-fops-preferred-996b37da1e0f\n"
    "write_attr_sync=preserved-including-size\n"
    "backing_open_close=deferred-p2\n"
    "max_stack_depth_7_40=deferred-p2\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
