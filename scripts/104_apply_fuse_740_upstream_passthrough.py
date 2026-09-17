#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 104_apply_fuse_740_upstream_passthrough.py <kernel-dir>")

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


# Phase 104: native upstream FUSE 7.40 passthrough on the old Samsung 4.19 base.
# Phase103 already proved the 7.40 extended INIT handshake and normal I/O.
#
# Compatibility note:
# Modern Android userspace negotiates max_stack_depth=1 because its backing file
# normally lives directly on the lower filesystem (stack depth 0).  This Samsung
# 4.19 device can still expose the MediaProvider backing fd through legacy
# sdcardfs (stack depth 1).  In that single, identified case, promote the
# effective FUSE depth to 2, but only when FILESYSTEM_MAX_STACK_DEPTH permits it.
# All other upstream stack-depth rejections remain intact.

uapi = read("include/uapi/linux/fuse.h")
inode = read("fs/fuse/inode.c")

required = [
    "#define FUSE_KERNEL_MINOR_VERSION 40",
    "#define FUSE_PASSTHROUGH_UPSTREAM\t(1ULL << 37)",
    "#define FUSE_INIT_EXT",
    "arg->flags2 = 0;",
    "arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)",
    "FUSE_NEGOTIATION_PROBE",
]
for needle in required:
    if needle not in (uapi + inode):
        raise SystemExit(f"Phase104 missing Phase103 prerequisite: {needle}")

# Upstream passthrough is not combined with writeback cache in this backport.
replace_once(
    "fs/fuse/inode.c",
    """\t\tFUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |
\t\tFUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT |
""",
    """\t\tFUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |
\t\tFUSE_NO_OPEN_SUPPORT |
""",
    "drop_writeback_cache_offer_for_upstream_passthrough",
)

# Advertise bit 37 through flags2 bit 5.
replace_once(
    "fs/fuse/inode.c",
    """\targ->flags2 = 0;
""",
    """\targ->flags2 = (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32);
""",
    "advertise_upstream_passthrough_bit37",
)

# Keep the proven legacy <7.36 path separate from the real 7.40 path.
old_init = """\t\t\tif (arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)) {
\t\t\t\tint max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;

\t\t\t\t/*
\t\t\t\t * Legacy 7.27 userspace has no max_stack_depth field.
\t\t\t\t * Keep the proven P2 limit unless a real >=7.40 daemon
\t\t\t\t * explicitly negotiates a valid modern depth.
\t\t\t\t */
\t\t\t\tif (arg->minor >= 40 && arg->max_stack_depth > 0 &&
\t\t\t\t    arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH)
\t\t\t\t\tmax_stack_depth = arg->max_stack_depth;

\t\t\t\tfc->passthrough = 1;
\t\t\t\tfc->max_stack_depth = max_stack_depth;
\t\t\t\tfc->sb->s_stack_depth = max_stack_depth;
\t\t\t}
"""

new_init = """\t\t\tif (arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)) {
\t\t\t\tint max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;

\t\t\t\tfc->passthrough = 1;
\t\t\t\tfc->max_stack_depth = max_stack_depth;
\t\t\t\tfc->sb->s_stack_depth = max_stack_depth;
\t\t\t} else if (arg->minor >= 40 &&
\t\t\t\t   (arg->flags2 & (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32)) &&
\t\t\t\t   arg->max_stack_depth > 0 &&
\t\t\t\t   arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH &&
\t\t\t\t   !(arg->flags & FUSE_WRITEBACK_CACHE)) {
\t\t\t\tfc->passthrough = 1;
\t\t\t\tfc->max_stack_depth = arg->max_stack_depth;
\t\t\t\tfc->sb->s_stack_depth = arg->max_stack_depth;
\t\t\t\tpr_info("FUSE_740_PASSTHROUGH_NEGOTIATED dev=%u:%u max_stack_depth=%u flags2=0x%08x\\n",
\t\t\t\t\tMAJOR(fc->dev), MINOR(fc->dev),
\t\t\t\t\targ->max_stack_depth, arg->flags2);
\t\t\t}
"""
replace_once("fs/fuse/inode.c", old_init, new_init,
             "negotiate_upstream_passthrough")

# Needed for the narrowly scoped filesystem-name compatibility check below.
replace_once(
    "fs/fuse/backing.c",
    """#include <linux/file.h>
#include <linux/slab.h>
""",
    """#include <linux/file.h>
#include <linux/slab.h>
#include <linux/string.h>
""",
    "backing_string_include",
)

# Android MediaProvider does not get CAP_SYS_ADMIN. Android common explicitly
# relaxes this check because /dev/fuse access is already restricted by Android.
replace_once(
    "fs/fuse/backing.c",
    """\tif (!fc->passthrough)
\t\treturn -EPERM;

\t/*
\t * Preserve the proven Android ioctl-126 behavior.  The new persistent
\t * API follows upstream and requires privilege.
\t */
\tif (!legacy_once && !capable(CAP_SYS_ADMIN))
\t\treturn -EPERM;
""",
    """\tif (!fc->passthrough)
\t\treturn -EPERM;

\t/*
\t * Android MediaProvider is intentionally not granted CAP_SYS_ADMIN.
\t * /dev/fuse ownership and SELinux policy already gate this interface.
\t */
""",
    "android_relax_backing_open_cap_sys_admin",
)

replace_once(
    "fs/fuse/backing.c",
    """\tif (!fc->passthrough || !capable(CAP_SYS_ADMIN))
\t\treturn -EPERM;
\tif (backing_id <= 0)
""",
    """\tif (!fc->passthrough)
\t\treturn -EPERM;
\tif (backing_id <= 0)
""",
    "android_relax_backing_close_cap_sys_admin",
)

# Preserve all normal validation, but adapt one old-Samsung storage-stack case.
old_backing_checks = """\tif (!fc->passthrough)
\t\treturn -EPERM;

\t/*
\t * Android MediaProvider is intentionally not granted CAP_SYS_ADMIN.
\t * /dev/fuse ownership and SELinux policy already gate this interface.
\t */

\tif (flags || padding)
\t\treturn -EINVAL;

\tfile = fget(fd);
\tif (!file)
\t\treturn -EBADF;

\tif (!file->f_op || !file->f_op->read_iter || !file->f_op->write_iter) {
\t\tret = -EBADF;
\t\tgoto out_fput;
\t}

\t/* Modern persistent registrations are limited to regular files. */
\tif (!legacy_once && !S_ISREG(file_inode(file)->i_mode)) {
\t\tret = -EINVAL;
\t\tgoto out_fput;
\t}

\tbacking_sb = file_inode(file)->i_sb;
\tif (fc->max_stack_depth <= 0 ||
\t    backing_sb->s_stack_depth >= fc->max_stack_depth) {
\t\tret = -ELOOP;
\t\tgoto out_fput;
\t}
"""

new_backing_checks = """\tif (!fc->passthrough) {
\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=no_passthrough fd=%d err=%d\\n",
\t\t\t\t    fd, -EPERM);
\t\treturn -EPERM;
\t}

\t/*
\t * Android MediaProvider is intentionally not granted CAP_SYS_ADMIN.
\t * /dev/fuse ownership and SELinux policy already gate this interface.
\t */

\tif (flags || padding) {
\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=map_flags fd=%d flags=0x%x padding=%llu err=%d\\n",
\t\t\t\t    fd, flags, (unsigned long long)padding, -EINVAL);
\t\treturn -EINVAL;
\t}

\tfile = fget(fd);
\tif (!file) {
\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=bad_fd fd=%d err=%d\\n",
\t\t\t\t    fd, -EBADF);
\t\treturn -EBADF;
\t}

\tif (!file->f_op || !file->f_op->read_iter || !file->f_op->write_iter) {
\t\tret = -EOPNOTSUPP;
\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=file_ops fd=%d mode=0%o err=%d\\n",
\t\t\t\t    fd, file_inode(file)->i_mode, ret);
\t\tgoto out_fput;
\t}

\t/* Modern persistent registrations are limited to regular files. */
\tif (!legacy_once && !S_ISREG(file_inode(file)->i_mode)) {
\t\tret = S_ISDIR(file_inode(file)->i_mode) ? -EISDIR : -EINVAL;
\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=file_type fd=%d mode=0%o err=%d\\n",
\t\t\t\t    fd, file_inode(file)->i_mode, ret);
\t\tgoto out_fput;
\t}

\tbacking_sb = file_inode(file)->i_sb;
\tif (fc->max_stack_depth <= 0) {
\t\tret = -ELOOP;
\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=zero_limit fd=%d fs=%s backing_depth=%d limit=%d err=%d\\n",
\t\t\t\t    fd,
\t\t\t\t    backing_sb->s_type ? backing_sb->s_type->name : "?",
\t\t\t\t    backing_sb->s_stack_depth, fc->max_stack_depth, ret);
\t\tgoto out_fput;
\t}

\tif (backing_sb->s_stack_depth >= fc->max_stack_depth) {
\t\tconst char *fsname = backing_sb->s_type ? backing_sb->s_type->name : NULL;
\t\tbool samsung_sdcardfs_compat =
\t\t\t!legacy_once && fsname && !strcmp(fsname, "sdcardfs") &&
\t\t\tfc->max_stack_depth == 1 &&
\t\t\tbacking_sb->s_stack_depth == 1 &&
\t\t\tfc->max_stack_depth < FILESYSTEM_MAX_STACK_DEPTH;

\t\tif (samsung_sdcardfs_compat) {
\t\t\tfc->max_stack_depth = 2;
\t\t\tfc->sb->s_stack_depth = 2;
\t\t\tpr_info_ratelimited("FUSE_740_LEGACY_STACK_COMPAT fd=%d fs=%s backing_depth=%d promoted_limit=%d\\n",
\t\t\t\t\t    fd, fsname, backing_sb->s_stack_depth,
\t\t\t\t\t    fc->max_stack_depth);
\t\t} else {
\t\t\tret = -ELOOP;
\t\t\tpr_info_ratelimited("FUSE_740_BACKING_REJECT reason=stack_depth fd=%d fs=%s backing_depth=%d limit=%d err=%d\\n",
\t\t\t\t\t    fd, fsname ? fsname : "?",
\t\t\t\t\t    backing_sb->s_stack_depth,
\t\t\t\t\t    fc->max_stack_depth, ret);
\t\t\tgoto out_fput;
\t\t}
\t}
"""
replace_once("fs/fuse/backing.c", old_backing_checks, new_backing_checks,
             "android16_samsung_legacy_stack_compat")

# Log successful persistent backing registrations.
replace_once(
    "fs/fuse/backing.c",
    """\tret = fuse_backing_id_alloc(fc, backing);
\tif (ret < 0) {
\t\tfuse_backing_put(backing);
\t\treturn ret;
\t}

\treturn ret;
""",
    """\tret = fuse_backing_id_alloc(fc, backing);
\tif (ret < 0) {
\t\tfuse_backing_put(backing);
\t\treturn ret;
\t}

\tif (!legacy_once)
\t\tpr_info_ratelimited("FUSE_740_BACKING_OPEN id=%d fs=%s backing_depth=%d limit=%d\\n",
\t\t\t\t    ret,
\t\t\t\t    backing_sb->s_type ? backing_sb->s_type->name : "?",
\t\t\t\t    backing_sb->s_stack_depth, fc->max_stack_depth);

\treturn ret;
""",
    "backing_open_runtime_probe",
)

# Log successful OPEN replies consuming persistent backing_id.
replace_once(
    "fs/fuse/passthrough.c",
    """\tff->passthrough.backing = backing;
\tff->passthrough.filp = backing->file;
\tff->passthrough.cred = backing->cred;
\treturn 0;
""",
    """\tff->passthrough.backing = backing;
\tff->passthrough.filp = backing->file;
\tff->passthrough.cred = backing->cred;
\tif (persistent)
\t\tpr_info_ratelimited("FUSE_740_PASSTHROUGH_SETUP backing_id=%d\\n",
\t\t\t\t    backing_id);
\treturn 0;
""",
    "passthrough_setup_runtime_probe",
)

checks = {
    "include/uapi/linux/fuse.h": [
        "#define FUSE_KERNEL_MINOR_VERSION 40",
        "#define FUSE_PASSTHROUGH_UPSTREAM\t(1ULL << 37)",
        "#define FOPEN_PASSTHROUGH",
        "backing_id;     /* modern FUSE passthrough */",
        "FUSE_DEV_IOC_BACKING_OPEN",
        "FUSE_DEV_IOC_BACKING_CLOSE",
    ],
    "fs/fuse/inode.c": [
        "arg->flags2 = (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32);",
        "FUSE_740_PASSTHROUGH_NEGOTIATED",
        "arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH",
        "!(arg->flags & FUSE_WRITEBACK_CACHE)",
    ],
    "fs/fuse/backing.c": [
        "FUSE_740_LEGACY_STACK_COMPAT",
        "FUSE_740_BACKING_REJECT",
        "FUSE_740_BACKING_OPEN",
        "strcmp(fsname, \"sdcardfs\")",
        "fc->max_stack_depth = 2;",
        "fc->sb->s_stack_depth = 2;",
    ],
    "fs/fuse/passthrough.c": [
        "FUSE_740_PASSTHROUGH_SETUP",
        "openarg->backing_id",
        "FOPEN_PASSTHROUGH",
    ],
}

for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: missing Phase104 postcondition: {needle}")

inode = read("fs/fuse/inode.c")
if "FUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT" in inode:
    raise SystemExit("Phase104 still advertises WRITEBACK_CACHE")
if "arg->flags2 = 0;" in inode:
    raise SystemExit("Phase104 flags2 offer remained zero")

report = root.parent.parent / "artifacts" / "phase104-fuse-740-upstream-passthrough.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=104-fuse-740-upstream-passthrough\n"
    "baseline=phase103-fuse-740-init-runtime-proven\n"
    "protocol=7.40\n"
    "upstream_passthrough_bit37=advertised\n"
    "legacy_bit31_passthrough=not-advertised\n"
    "writeback_cache_offer=disabled\n"
    "android_cap_sys_admin_relaxation=enabled\n"
    "samsung_sdcardfs_stack_compat=depth1-to-depth2-only\n"
    "runtime_tags=FUSE_740_PASSTHROUGH_NEGOTIATED,FUSE_740_LEGACY_STACK_COMPAT,FUSE_740_BACKING_OPEN,FUSE_740_PASSTHROUGH_SETUP\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
