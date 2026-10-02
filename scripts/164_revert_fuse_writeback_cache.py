#!/usr/bin/env python3
from pathlib import Path
import sys

def fail(msg):
    raise SystemExit(msg)

if len(sys.argv) != 2:
    fail(f"usage: {sys.argv[0]} <kernel-tree>")

root = Path(sys.argv[1]).resolve()
inode = root / "fs/fuse/inode.c"
if not inode.is_file():
    fail(f"missing {inode}")

s = inode.read_text()

with_wb = (
    "FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |\n"
    "\t\tFUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT |"
)
without_wb = (
    "FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |\n"
    "\t\tFUSE_NO_OPEN_SUPPORT |"
)

if with_wb in s:
    if s.count(with_wb) != 1:
        fail(f"P164: expected one writeback advertisement anchor, found {s.count(with_wb)}")
    s = s.replace(with_wb, without_wb, 1)
elif without_wb not in s:
    fail("P164: FUSE init capability anchor not found")

# Keep Phase104 upstream passthrough path and its safety guard intact.
required = [
    "#define FUSE_KERNEL_MINOR_VERSION 40",
]
uapi = (root / "include/uapi/linux/fuse.h").read_text()
for needle in required:
    if needle not in uapi:
        fail(f"P164: missing prerequisite {needle}")

for needle in [
    "arg->flags2 = (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32);",
    "!(arg->flags & FUSE_WRITEBACK_CACHE)",
    "FUSE_740_PASSTHROUGH_NEGOTIATED",
]:
    if needle not in s:
        fail(f"P164: Phase104 passthrough prerequisite missing: {needle}")

inode.write_text(s)

# Final invariants
final = inode.read_text()
if with_wb in final:
    fail("P164: FUSE_WRITEBACK_CACHE is still advertised")
if without_wb not in final:
    fail("P164: expected non-writeback FUSE capability line missing")

print("[PASS] P164 FUSE writeback regression rollback")
print("writeback_cache_offer=disabled")
print("phase104_passthrough=preserved")
print("reason=P162 D6 regression suspected after boot-test: downloads broken")
