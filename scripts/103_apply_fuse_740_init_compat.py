#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 103_apply_fuse_740_init_compat.py <kernel-dir>")

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
# Phase 103 / FUSE 7.40 INIT compatibility
#
# Goal:
#   - advertise protocol 7.40 to the already-proven Android 16 userspace;
#   - use the 7.36+ extended 64-byte FUSE_INIT request with FUSE_INIT_EXT;
#   - retain the Phase102 negotiation probe;
#   - retain all P1/P2/P3 code, but DO NOT negotiate modern passthrough yet.
#
# Why passthrough is intentionally unnegotiated here:
#   bit 31 is Android's legacy passthrough capability only for protocol <7.36.
#   In the modern ABI bit 31 is reserved and upstream passthrough moves into
#   the extended flags range.  Phase103 isolates the protocol transition from
#   the passthrough transition.
# ---------------------------------------------------------------------------

uapi = read("include/uapi/linux/fuse.h")
if "#define FUSE_KERNEL_MINOR_VERSION 27" not in uapi:
    raise SystemExit("Phase103 expects corrected P3/Phase102 FUSE 7.27 input")
if "FUSE_NEGOTIATION_PROBE" not in read("fs/fuse/inode.c"):
    raise SystemExit("Phase103 requires the Phase102 negotiation probe")

replace_once(
    "include/uapi/linux/fuse.h",
    "#define FUSE_KERNEL_MINOR_VERSION 27\n",
    "#define FUSE_KERNEL_MINOR_VERSION 40\n",
    "protocol_minor_40",
)

replace_once(
    "include/uapi/linux/fuse.h",
    "/* Android FUSE passthrough uses bit 31 for protocol versions < 7.36. */\n"
    "#define FUSE_PASSTHROUGH\t(1 << 31)\n",
    "/* Android legacy passthrough capability, valid only for protocol < 7.36. */\n"
    "#define FUSE_PASSTHROUGH\t(1 << 31)\n"
    "/* FUSE 7.36 extended INIT request/reply capability. */\n"
    "#define FUSE_INIT_EXT\t\t(1 << 30)\n"
    "/* Bit 31 is reserved in the modern >=7.36 INIT capability namespace. */\n"
    "#define FUSE_INIT_RESERVED\t(1 << 31)\n"
    "/* FUSE 7.40 upstream passthrough capability, carried in flags2 bit 5. */\n"
    "#define FUSE_PASSTHROUGH_UPSTREAM\t(1ULL << 37)\n",
    "uapi_extended_init_flags",
)

replace_once(
    "include/uapi/linux/fuse.h",
    "struct fuse_init_in {\n"
    "\tuint32_t\tmajor;\n"
    "\tuint32_t\tminor;\n"
    "\tuint32_t\tmax_readahead;\n"
    "\tuint32_t\tflags;\n"
    "};\n",
    "struct fuse_init_in {\n"
    "\tuint32_t\tmajor;\n"
    "\tuint32_t\tminor;\n"
    "\tuint32_t\tmax_readahead;\n"
    "\tuint32_t\tflags;\n"
    "\tuint32_t\tflags2;\n"
    "\tuint32_t\tunused[11];\n"
    "};\n",
    "uapi_extended_init_in",
)

# Stop advertising Android's legacy bit-31 passthrough when claiming >=7.36.
# The P1/P2/P3 implementation remains compiled and available for the next phase.
replace_once(
    "fs/fuse/inode.c",
    "\t\tFUSE_PARALLEL_DIROPS | FUSE_HANDLE_KILLPRIV | FUSE_POSIX_ACL |\n"
    "\t\tFUSE_ABORT_ERROR | FUSE_MAX_PAGES\n"
    "#ifdef CONFIG_FUSE_PASSTHROUGH\n"
    "\t\t| FUSE_PASSTHROUGH\n"
    "#endif\n"
    "\t\t;\n",
    "\t\tFUSE_PARALLEL_DIROPS | FUSE_HANDLE_KILLPRIV | FUSE_POSIX_ACL |\n"
    "\t\tFUSE_ABORT_ERROR | FUSE_MAX_PAGES | FUSE_INIT_EXT;\n"
    "\targ->flags2 = 0;\n",
    "send_init_740_compat_flags",
)

# Prevent a modern >=7.36 userspace reply from ever being mistaken for the old
# Android bit-31 passthrough capability.  Legacy fallback remains functional if
# an older daemon is ever used.
replace_once(
    "fs/fuse/inode.c",
    "\t\t\tif (arg->flags & FUSE_PASSTHROUGH) {\n"
    "\t\t\t\tint max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;\n",
    "\t\t\tif (arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)) {\n"
    "\t\t\t\tint max_stack_depth = FILESYSTEM_MAX_STACK_DEPTH;\n",
    "legacy_passthrough_minor_guard",
)

# Structural and safety guards.
uapi = read("include/uapi/linux/fuse.h")
inode = read("fs/fuse/inode.c")

checks = {
    "include/uapi/linux/fuse.h": [
        "#define FUSE_KERNEL_MINOR_VERSION 40",
        "#define FUSE_INIT_EXT\t\t(1 << 30)",
        "#define FUSE_INIT_RESERVED\t(1 << 31)",
        "#define FUSE_PASSTHROUGH_UPSTREAM\t(1ULL << 37)",
        "uint32_t\tflags2;",
        "uint32_t\tunused[11];",
        "uint32_t\tmax_stack_depth;",
        "backing_id;     /* modern FUSE passthrough */",
    ],
    "fs/fuse/inode.c": [
        "FUSE_ABORT_ERROR | FUSE_MAX_PAGES | FUSE_INIT_EXT;",
        "arg->flags2 = 0;",
        "arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)",
        "FUSE_NEGOTIATION_PROBE dev=%u:%u",
    ],
}
for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: missing Phase103 postcondition: {needle}")

if "| FUSE_PASSTHROUGH\n#ifdef" in inode:
    raise SystemExit("legacy passthrough advertisement unexpectedly remains")
if "\t\t| FUSE_PASSTHROUGH\n" in inode:
    raise SystemExit("legacy passthrough is still advertised in FUSE_INIT")
if "arg->flags2 = FUSE_PASSTHROUGH" in inode:
    raise SystemExit("modern passthrough must remain disabled in Phase103")

report = root.parent.parent / "artifacts" / "phase103-fuse-740-init-compat.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=103-fuse-740-init-compat\n"
    "baseline=bt-p2c-plus-fuse-p1-p2-corrected-p3-runtime-proven\n"
    "phase102_probe=retained-boot-proven\n"
    "kernel_offer=7.40\n"
    "init_request_size=64\n"
    "init_ext=advertised-bit30\n"
    "flags2_request=zero\n"
    "legacy_bit31_passthrough=not-advertised-for-7.40\n"
    "modern_bit37_passthrough=defined-but-not-advertised\n"
    "p1_p2_p3_code=retained\n"
    "expected_first_test=protocol-handshake-and-normal-storage-only\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
