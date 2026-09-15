#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 102_apply_fuse_negotiation_probe.py <kernel-dir>")

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

# Phase 102 is diagnostics-only. It must not change the advertised FUSE
# protocol, feature flags, negotiated values, or data-path behavior.
uapi = read("include/uapi/linux/fuse.h")
if "#define FUSE_KERNEL_MINOR_VERSION 27" not in uapi:
    raise SystemExit("negotiation probe requires the proven FUSE 7.27 baseline")

inode = read("fs/fuse/inode.c")
required = [
    "int max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;",
    "fc->max_stack_depth = max_stack_depth;",
    "fc->sb->s_stack_depth = max_stack_depth;",
]
for needle in required:
    if needle not in inode:
        raise SystemExit(f"missing corrected P3 state: {needle}")

old = (
    "static void process_init_reply(struct fuse_conn *fc, struct fuse_req *req)\n"
    "{\n"
    "\tstruct fuse_init_out *arg = &req->misc.init_out;\n\n"
)

new = (
    "static void process_init_reply(struct fuse_conn *fc, struct fuse_req *req)\n"
    "{\n"
    "\tstruct fuse_init_out *arg = &req->misc.init_out;\n"
    "\tu32 probe_payload_len = 0;\n\n"
    "\tif (req->out.h.len >= sizeof(struct fuse_out_header))\n"
    "\t\tprobe_payload_len = req->out.h.len - sizeof(struct fuse_out_header);\n\n"
    "\t/*\n"
    "\t * Phase 102 diagnostic only: report the raw userspace FUSE_INIT reply.\n"
    "\t * Do not clamp, rewrite, or otherwise affect negotiation here.\n"
    "\t * Per the FUSE protocol, arg->minor is the userspace-supported minor.\n"
    "\t */\n"
    "\tpr_info(\"FUSE_NEGOTIATION_PROBE dev=%u:%u kernel_offer=%u.%u \"\n"
    "\t\t\"userspace_reply=%u.%u payload_len=%u error=%d flags=0x%08x \"\n"
    "\t\t\"flags2=0x%08x max_readahead=%u max_write=%u max_background=%u \"\n"
    "\t\t\"congestion_threshold=%u time_gran=%u max_pages=%u \"\n"
    "\t\t\"map_alignment=%u max_stack_depth=%u request_timeout=%u\\n\",\n"
    "\t\tMAJOR(fc->dev), MINOR(fc->dev),\n"
    "\t\tFUSE_KERNEL_VERSION, FUSE_KERNEL_MINOR_VERSION,\n"
    "\t\targ->major, arg->minor, probe_payload_len, req->out.h.error,\n"
    "\t\targ->flags, arg->flags2, arg->max_readahead, arg->max_write,\n"
    "\t\targ->max_background, arg->congestion_threshold, arg->time_gran,\n"
    "\t\targ->max_pages, arg->map_alignment, arg->max_stack_depth,\n"
    "\t\targ->request_timeout);\n\n"
)

replace_once("fs/fuse/inode.c", old, new, "init_reply_probe")

post = read("fs/fuse/inode.c")
checks = [
    "FUSE_NEGOTIATION_PROBE dev=%u:%u",
    "kernel_offer=%u.%u",
    "userspace_reply=%u.%u",
    "flags2=0x%08x",
    "max_stack_depth=%u",
    "request_timeout=%u",
]
for needle in checks:
    if needle not in post:
        raise SystemExit(f"probe postcondition missing: {needle}")

# Guard against accidental protocol/behavior changes in this diagnostic phase.
if "#define FUSE_KERNEL_MINOR_VERSION 27" not in read("include/uapi/linux/fuse.h"):
    raise SystemExit("probe unexpectedly changed FUSE minor")

report = root.parent.parent / "artifacts" / "phase102-fuse-negotiation-probe.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=102-fuse-negotiation-probe\n"
    "baseline=corrected-fuse-p3-btp2c-runtime-proven\n"
    "protocol_minor=27-unchanged\n"
    "behavior_change=none\n"
    "probe_tag=FUSE_NEGOTIATION_PROBE\n"
    "probe_fields=kernel_offer,userspace_reply,payload_len,error,flags,flags2,max_readahead,max_write,max_background,congestion_threshold,time_gran,max_pages,map_alignment,max_stack_depth,request_timeout\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
