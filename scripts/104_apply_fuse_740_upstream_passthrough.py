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

# ---------------------------------------------------------------------------
# Phase 104 / native upstream FUSE 7.40 passthrough
#
# Phase103 proved the 7.40 extended INIT handshake and normal storage I/O.
# Phase104 now advertises the upstream 7.40 passthrough capability at bit 37,
# consumes userspace max_stack_depth, enables the persistent BACKING_OPEN /
# BACKING_CLOSE path from P2, and uses FOPEN_PASSTHROUGH + backing_id from P3.
#
# Upstream explicitly does not support the FUSE_PASSTHROUGH +
# FUSE_WRITEBACK_CACHE combination.  Therefore this phase stops advertising
# WRITEBACK_CACHE when advertising upstream passthrough.
# ---------------------------------------------------------------------------

uapi = read("include/uapi/linux/fuse.h")
inode = read("fs/fuse/inode.c")

required = [
    "#define FUSE_KERNEL_MINOR_VERSION 40",
    "#define FUSE_PASSTHROUGH_UPSTREAM	(1ULL << 37)",
    "#define FUSE_INIT_EXT",
    "arg->flags2 = 0;",
    "arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)",
    "FUSE_NEGOTIATION_PROBE",
]
for needle in required:
    if needle not in (uapi + inode):
        raise SystemExit(f"Phase104 missing Phase103 prerequisite: {needle}")

# Do not offer writeback cache together with upstream passthrough.
replace_once(
    "fs/fuse/inode.c",
    "		FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |
"
    "		FUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT |
",
    "		FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |
"
    "		FUSE_NO_OPEN_SUPPORT |
",
    "drop_writeback_cache_offer_for_upstream_passthrough",
)

# Advertise upstream protocol bit 37 through the extended flags2 word.
replace_once(
    "fs/fuse/inode.c",
    "	arg->flags2 = 0;
",
    "	arg->flags2 = (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32);
",
    "advertise_upstream_passthrough_bit37",
)

# Keep the proven legacy path for older userspace, and add a completely
# separate >=7.40 upstream path.  This avoids interpreting modern flags using
# the old Android bit-31 semantics.
old_block = (
    "			if (arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)) {
"
    "				int max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;

"
    "				/*
"
    "				 * Legacy 7.27 userspace has no max_stack_depth field.
"
    "				 * Keep the proven P2 limit unless a real >=7.40 daemon
"
    "				 * explicitly negotiates a valid modern depth.
"
    "				 */
"
    "				if (arg->minor >= 40 && arg->max_stack_depth > 0 &&
"
    "				    arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH)
"
    "					max_stack_depth = arg->max_stack_depth;

"
    "				fc->passthrough = 1;
"
    "				fc->max_stack_depth = max_stack_depth;
"
    "				fc->sb->s_stack_depth = max_stack_depth;
"
    "			}
"
)

new_block = (
    "			if (arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)) {
"
    "				int max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;

"
    "				fc->passthrough = 1;
"
    "				fc->max_stack_depth = max_stack_depth;
"
    "				fc->sb->s_stack_depth = max_stack_depth;
"
    "			} else if (arg->minor >= 40 &&
"
    "				   (arg->flags2 & (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32)) &&
"
    "				   arg->max_stack_depth > 0 &&
"
    "				   arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH &&
"
    "				   !(arg->flags & FUSE_WRITEBACK_CACHE)) {
"
    "				fc->passthrough = 1;
"
    "				fc->max_stack_depth = arg->max_stack_depth;
"
    "				fc->sb->s_stack_depth = arg->max_stack_depth;
"
    "				pr_info(\"FUSE_740_PASSTHROUGH_NEGOTIATED dev=%u:%u max_stack_depth=%u flags2=0x%08x\\n\",
"
    "					MAJOR(fc->dev), MINOR(fc->dev),
"
    "					arg->max_stack_depth, arg->flags2);
"
    "			}
"
)

replace_once(
    "fs/fuse/inode.c",
    old_block,
    new_block,
    "negotiate_upstream_passthrough",
)

# Add low-volume runtime breadcrumbs proving that userspace is actually using
# BACKING_OPEN and that an OPEN reply consumed the persistent backing_id path.
replace_once(
    "fs/fuse/backing.c",
    "	ret = fuse_backing_id_alloc(fc, backing);
"
    "	if (ret < 0) {
"
    "		fuse_backing_put(backing);
"
    "		return ret;
"
    "	}

"
    "	return ret;
",
    "	ret = fuse_backing_id_alloc(fc, backing);
"
    "	if (ret < 0) {
"
    "		fuse_backing_put(backing);
"
    "		return ret;
"
    "	}

"
    "	if (!legacy_once)
"
    "		pr_info_ratelimited(\"FUSE_740_BACKING_OPEN id=%d backing_depth=%d limit=%d\\n\",
"
    "				    ret, backing_sb->s_stack_depth, fc->max_stack_depth);

"
    "	return ret;
",
    "backing_open_runtime_probe",
)

replace_once(
    "fs/fuse/passthrough.c",
    "	ff->passthrough.backing = backing;
"
    "	ff->passthrough.filp = backing->file;
"
    "	ff->passthrough.cred = backing->cred;
"
    "	return 0;
",
    "	ff->passthrough.backing = backing;
"
    "	ff->passthrough.filp = backing->file;
"
    "	ff->passthrough.cred = backing->cred;
"
    "	if (persistent)
"
    "		pr_info_ratelimited(\"FUSE_740_PASSTHROUGH_SETUP backing_id=%d\\n\",
"
    "				    backing_id);
"
    "	return 0;
",
    "passthrough_setup_runtime_probe",
)

checks = {
    "include/uapi/linux/fuse.h": [
        "#define FUSE_KERNEL_MINOR_VERSION 40",
        "#define FUSE_PASSTHROUGH_UPSTREAM	(1ULL << 37)",
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
        "FUSE_740_BACKING_OPEN",
        "backing_sb->s_stack_depth >= fc->max_stack_depth",
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
    "init_ext=enabled\n"
    "upstream_passthrough_bit37=advertised\n"
    "legacy_bit31_passthrough=not-advertised\n"
    "writeback_cache_offer=disabled-for-upstream-passthrough-safety\n"
    "backing_open_close=p2-persistent-registry\n"
    "open_reply=fopen_passthrough-plus-backing_id\n"
    "stack_depth=p3-daemon-negotiated\n"
    "runtime_tags=FUSE_740_PASSTHROUGH_NEGOTIATED,FUSE_740_BACKING_OPEN,FUSE_740_PASSTHROUGH_SETUP\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
