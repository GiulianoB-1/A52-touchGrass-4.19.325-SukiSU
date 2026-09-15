#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 101_apply_fuse_passthrough_modern_p3.py <kernel-dir>")

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
# Phase 101 / FUSE modern P3
#
# P1 and P2 are retained exactly.  P3 ports the modern passthrough stacking
# semantics that are safe on the Samsung 4.19 / Android legacy ABI:
#
#   - source-compatible modern backing_id name in fuse_open_out while keeping
#     the exact 16-byte wire layout and legacy passthrough_fh alias;
#   - modern fuse_init_out field names in the previously reserved 32-byte tail,
#     without changing the 64-byte reply size;
#   - per-connection max_stack_depth;
#   - legacy-safe default max_stack_depth = 1;
#   - backing registration enforces the negotiated/compatibility depth instead
#     of the global FILESYSTEM_MAX_STACK_DEPTH constant.
#
# We intentionally DO NOT bump FUSE_KERNEL_MINOR_VERSION from the Samsung 7.27
# baseline here.  Old Android daemons continue seeing the proven ABI.  If a
# future tree advertises >= 7.40, the same code can consume max_stack_depth.
# ---------------------------------------------------------------------------

# The upstream 7.40 open reply renamed the final 32-bit slot to backing_id.
# Use a union so legacy Android source and modern source both map to the same
# bytes. sizeof(struct fuse_open_out) remains 16.
replace_once(
    "include/uapi/linux/fuse.h",
    "struct fuse_open_out {\n"
    "\tuint64_t\tfh;\n"
    "\tuint32_t\topen_flags;\n"
    "\tuint32_t\tpassthrough_fh;\n"
    "};\n",
    "struct fuse_open_out {\n"
    "\tuint64_t\tfh;\n"
    "\tuint32_t\topen_flags;\n"
    "\tunion {\n"
    "\t\tuint32_t\tpassthrough_fh; /* Android legacy V2 ABI */\n"
    "\t\tint32_t\t\tbacking_id;     /* modern FUSE passthrough */\n"
    "\t};\n"
    "};\n",
    "uapi_open_backing_id_alias",
)

# Modern fuse_init_out reuses the same 64-byte structure.  Renaming the old
# padding/reserved tail is wire-size neutral and makes the 7.40 stack-depth
# reply layout available without changing the request side or negotiated minor.
replace_once(
    "include/uapi/linux/fuse.h",
    "\tuint16_t\tmax_pages;\n"
    "\tuint16_t\tpadding;\n"
    "\tuint32_t\tunused[8];\n"
    "};\n",
    "\tuint16_t\tmax_pages;\n"
    "\tuint16_t\tmap_alignment;\n"
    "\tuint32_t\tflags2;\n"
    "\tuint32_t\tmax_stack_depth;\n"
    "\tuint16_t\trequest_timeout;\n"
    "\tuint16_t\tunused[11];\n"
    "};\n",
    "uapi_init_out_modern_tail",
)

# Track the effective passthrough stack-depth limit per connection, matching
# current upstream FUSE.  The legacy Android path uses depth 1.
replace_once(
    "fs/fuse/fuse_i.h",
    "\t/** Android passthrough mode negotiated during FUSE_INIT. */\n"
    "\tunsigned passthrough:1;\n\n"
    "\t/** The number of requests waiting for completion */\n",
    "\t/** Android passthrough mode negotiated during FUSE_INIT. */\n"
    "\tunsigned passthrough:1;\n\n"
    "\t/** Maximum stack depth allowed for passthrough backing files. */\n"
    "\tint max_stack_depth;\n\n"
    "\t/** The number of requests waiting for completion */\n",
    "conn_max_stack_depth",
)

# Replace the old Android behavior that pessimistically marked the FUSE mount
# at FILESYSTEM_MAX_STACK_DEPTH.  Upstream semantics use the actual passthrough
# limit.  Since this tree still negotiates protocol 7.27, depth 1 is the safe
# compatibility value.  The >=7.40 branch is future-ready but cannot activate
# until the kernel minor is deliberately raised in a separate ABI phase.
replace_once(
    "fs/fuse/inode.c",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t\t\tif (arg->flags & FUSE_PASSTHROUGH) {\n"
    "\t\t\t\tfc->passthrough = 1;\n"
    "\t\t\t\t/* Passthrough already consumes the available stack layer. */\n"
    "\t\t\t\tfc->sb->s_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;\n"
    "\t\t\t}\n"
    "#endif\n",
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t\t\tif (arg->flags & FUSE_PASSTHROUGH) {\n"
    "\t\t\t\tint max_stack_depth = 1;\n\n"
    "\t\t\t\t/*\n"
    "\t\t\t\t * P3 keeps Samsung's protocol 7.27 ABI.  For a future\n"
    "\t\t\t\t * >=7.40 negotiation, accept the daemon-provided depth\n"
    "\t\t\t\t * only when it is valid; otherwise retain the proven\n"
    "\t\t\t\t * legacy single-layer passthrough model.\n"
    "\t\t\t\t */\n"
    "\t\t\t\tif (arg->minor >= 40 && arg->max_stack_depth > 0 &&\n"
    "\t\t\t\t    arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH)\n"
    "\t\t\t\t\tmax_stack_depth = arg->max_stack_depth;\n\n"
    "\t\t\t\tfc->passthrough = 1;\n"
    "\t\t\t\tfc->max_stack_depth = max_stack_depth;\n"
    "\t\t\t\tfc->sb->s_stack_depth = max_stack_depth;\n"
    "\t\t\t}\n"
    "#endif\n",
    "init_stack_depth_semantics",
)

# Persistent and legacy backing registration now share the connection-specific
# stack limit.  With the 7.27 compatibility path this means backing files must
# live on an unstacked filesystem (depth 0), exactly the modern depth=1 model.
replace_once(
    "fs/fuse/backing.c",
    "\tbacking_sb = file_inode(file)->i_sb;\n"
    "\tif (backing_sb->s_stack_depth >= FILESYSTEM_MAX_STACK_DEPTH) {\n"
    "\t\tret = -ELOOP;\n"
    "\t\tgoto out_fput;\n"
    "\t}\n",
    "\tbacking_sb = file_inode(file)->i_sb;\n"
    "\tif (fc->max_stack_depth <= 0 ||\n"
    "\t    backing_sb->s_stack_depth >= fc->max_stack_depth) {\n"
    "\t\tret = -ELOOP;\n"
    "\t\tgoto out_fput;\n"
    "\t}\n",
    "backing_stack_depth_enforcement",
)

# Consume the modern name.  The union keeps old ioctl-126 responses identical.
replace_once(
    "fs/fuse/passthrough.c",
    "\tint backing_id = (int)openarg->passthrough_fh;\n",
    "\tint backing_id = openarg->backing_id;\n",
    "passthrough_modern_backing_id",
)

replace_once(
    "fs/fuse/Kconfig",
    "\t  Supports Android's legacy registration ABI and a persistent modern\n"
    "\t  BACKING_OPEN/BACKING_CLOSE backing-file registry.\n",
    "\t  Supports Android's legacy registration ABI, a persistent modern\n"
    "\t  BACKING_OPEN/BACKING_CLOSE registry, and upstream-style backing\n"
    "\t  filesystem stack-depth enforcement.\n",
    "kconfig_p3_help",
)

# Structural guards.
checks = {
    "include/uapi/linux/fuse.h": [
        "uint32_t\tpassthrough_fh; /* Android legacy V2 ABI */",
        "int32_t\t\tbacking_id;     /* modern FUSE passthrough */",
        "uint32_t\tmax_stack_depth;",
        "uint32_t\tflags2;",
        "FUSE_DEV_IOC_BACKING_OPEN",
        "FUSE_DEV_IOC_BACKING_CLOSE",
        "FUSE_DEV_IOC_PASSTHROUGH_OPEN",
    ],
    "fs/fuse/fuse_i.h": [
        "int max_stack_depth;",
        "struct fuse_backing *backing;",
        "struct idr backing_files_map;",
    ],
    "fs/fuse/inode.c": [
        "int max_stack_depth = 1;",
        "arg->minor >= 40",
        "fc->max_stack_depth = max_stack_depth;",
        "fc->sb->s_stack_depth = max_stack_depth;",
    ],
    "fs/fuse/backing.c": [
        "fc->max_stack_depth <= 0",
        "backing_sb->s_stack_depth >= fc->max_stack_depth",
        "fuse_backing_open",
        "fuse_backing_close",
    ],
    "fs/fuse/passthrough.c": [
        "int backing_id = openarg->backing_id;",
        "fuse_passthrough_splice_read",
        "fuse_passthrough_splice_write",
        "fuse_backing_lookup",
    ],
}
for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: missing postcondition: {needle}")

# ABI-size logic guard: P3 must not modify the init request structure or bump
# protocol minor.  We want a deliberately wire-safe Samsung compatibility phase.
uapi = read("include/uapi/linux/fuse.h")
if "struct fuse_init_in {\n\tuint32_t\tmajor;\n\tuint32_t\tminor;\n\tuint32_t\tmax_readahead;\n\tuint32_t\tflags;\n};" not in uapi:
    raise SystemExit("P3 unexpectedly changed the legacy fuse_init_in wire layout")

if "#define FUSE_KERNEL_MINOR_VERSION 27" not in uapi:
    raise SystemExit("P3 requires and preserves Samsung FUSE protocol minor 27")

# Old global-depth behavior must be gone from the passthrough activation path.
inode = read("fs/fuse/inode.c")
if "fc->sb->s_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;" in inode:
    raise SystemExit("stale pessimistic passthrough stack-depth assignment remains")

backing = read("fs/fuse/backing.c")
if "backing_sb->s_stack_depth >= FILESYSTEM_MAX_STACK_DEPTH" in backing:
    raise SystemExit("backing registry still ignores per-connection stack depth")

report = root.parent.parent / "artifacts" / "phase101-fuse-modern-p3.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=101-fuse-modern-p3\n"
    "baseline=bt-p2c-success-dfd493919e6999e67520dce9a797e46ab66b0d5c\n"
    "fuse_p1=retained-runtime-proven\n"
    "fuse_p2=retained-runtime-proven\n"
    "legacy_android_ioctl_126=preserved\n"
    "persistent_backing_registry=preserved\n"
    "open_reply_size=16-bytes-preserved\n"
    "open_reply_alias=passthrough_fh-plus-backing_id\n"
    "init_reply_size=64-bytes-preserved-modern-tail-names\n"
    "protocol_minor=27-preserved\n"
    "compat_max_stack_depth=1\n"
    "future_7_40_max_stack_depth=parser-ready\n"
    "backing_stack_depth=connection-enforced\n"
    "unsafe_full_7_40_minor_bump=deferred\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
