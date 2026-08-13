#!/usr/bin/env python3
"""Phase 260: correlate KGSL node lifetime with the existing GPU/MMU corridor.

Phase259 already provides target-only /dev/kgsl-3d0 mknod, unlink, rename,
lookup and /dev mount-namespace observations. The inherited Phase248/249
corridor already records Adreno/GMU probe, ringbuffer/dispatcher, GMU domain
allocation, IOMMU group/attach results and the GPU ARM-SMMU parent probe.

Phase260 deliberately does not duplicate those fragile driver hooks. It adds the
missing VFS-to-file_operations handoff at the returned fd boundary and fail-
closed audits that the complete F259 + K248 + K249 suspicion spectrum is still
present in the generated source.

Instrumentation only. No return value, node lifetime, mount, namespace, device
registration, power vote, IOMMU operation or GPU command is changed.
"""
from __future__ import annotations

import re
from pathlib import Path

MARKER = "A52_PHASE260_KGSL_SUSPICION_SPECTRUM_V1"
REC_MARKER = "A52_PHASE260_RECORDER_V1"
OPEN_MARKER = "A52_PHASE260_VFS_TO_KGSL_HANDOFF_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def _function_from_start(text: str, start: int, label: str) -> tuple[int, int, str]:
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace not found")
    depth = 0
    state = "code"
    i = brace
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                state = "block"; i += 2; continue
            if c == "/" and n == "/":
                state = "line"; i += 2; continue
            if c == '"':
                state = "string"; i += 1; continue
            if c == "'":
                state = "char"; i += 1; continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1, text[start:i + 1]
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"; i += 2; continue
        elif state == "line":
            if c == "\n":
                state = "code"
        else:
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2; continue
            if c == quote:
                state = "code"
        i += 1
    raise RuntimeError(f"{label}: unterminated function")


def _find_function(text: str, signature: str, label: str) -> tuple[int, int, str]:
    start = text.find(signature)
    if start < 0:
        raise RuntimeError(f"{label}: function not found")
    return _function_from_start(text, start, label)


def patch_recorder(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if REC_MARKER in text:
        return
    if "A52_PHASE259_RECORDER_V1" not in text:
        raise RuntimeError(f"{path}: repaired Phase259 recorder marker missing")
    text = replace_once(
        text,
        'if (strncmp(fmt, "F259", 4) &&\n',
        f'''/* {REC_MARKER}\n * Retain the Phase260 VFS-to-KGSL handoff bridge.\n */\nif (strncmp(fmt, "F260", 4) &&\n    strncmp(fmt, "F259", 4) &&\n''',
        f"{path}: F260 admission",
    )
    text = replace_once(
        text,
        'return !strncmp(message, "F259 ", 5) ||\n',
        'return !strncmp(message, "F260 ", 5) ||\n       !strncmp(message, "F259 ", 5) ||\n',
        f"{path}: F260 retention",
    )
    path.write_text(text, encoding="utf-8")


OPEN_HELPER = r'''/* A52_PHASE260_VFS_TO_KGSL_HANDOFF_V1 */
static atomic_t a52_r260_handoff_count = ATOMIC_INIT(0);

static void a52_r260_trace_kgsl_fd_handoff(int fd)
{
	struct file *file = NULL;
	struct inode *inode = NULL;
	unsigned int n = atomic_inc_return(&a52_r260_handoff_count);

	/* Keep this target trace bounded even if userspace retries repeatedly. */
	if (n > 16)
		return;

	if (fd >= 0)
		file = fget(fd);
	if (file)
		inode = file_inode(file);

	a52_ackfr_record("F260 vh n=%u fd=%d ok=%d p=%d c=%.15s ns=%lx",
		n, fd, file ? 1 : 0, current->pid, current->comm,
		(unsigned long)current->nsproxy);
	if (file) {
		a52_ackfr_record("F260 vf mo=%o rdev=%lx ino=%lu fop=%px op=%ps",
			inode ? inode->i_mode : 0,
			inode ? (unsigned long)inode->i_rdev : 0UL,
			inode ? inode->i_ino : 0,
			file->f_op,
			(file->f_op && file->f_op->open) ? file->f_op->open : NULL);
		fput(file);
	}
}

'''


def patch_open(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if OPEN_MARKER in text:
        return
    if "A52_PHASE259_DEV_MOUNT_SNAPSHOT_V1" not in text:
        raise RuntimeError(f"{path}: Phase259 open snapshot marker missing")

    if "#include <linux/file.h>" not in text:
        include_hits = list(re.finditer(r"(?m)^#include <linux/[^>]+>\s*$", text))
        if not include_hits:
            raise RuntimeError(f"{path}: linux include anchor missing")
        pos = include_hits[-1].end()
        text = text[:pos] + "\n#include <linux/file.h>" + text[pos:]

    start, end, _fn = _find_function(
        text,
        "static void a52_r259_trace_kgsl_open_mount_snapshot(int open_rc)",
        f"{path}: Phase259 mount snapshot",
    )
    text = text[:end] + "\n\n" + OPEN_HELPER + text[end:]

    anchor = "\t\ta52_r259_trace_kgsl_open_mount_snapshot(fd);\n"
    text = replace_once(
        text,
        anchor,
        anchor + "\t\ta52_r260_trace_kgsl_fd_handoff(fd);\n",
        f"{path}: Phase260 handoff call",
    )
    path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    paths = {
        "recorder": root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c",
        "namei": root / "fs/namei.c",
        "open": root / "fs/open.c",
        "adreno": root / "drivers/gpu/msm/adreno.c",
        "gmu": root / "drivers/gpu/msm/kgsl_gmu.c",
        "iommu": root / "drivers/iommu/iommu.c",
        "smmu": root / "drivers/iommu/arm/arm-smmu/arm-smmu.c",
    }
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise RuntimeError("Phase260 spectrum source files missing: " + ", ".join(missing))

    data = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}

    required = {
        "recorder": (
            REC_MARKER,
            'strncmp(fmt, "F260", 4)',
            '!strncmp(message, "F260 ", 5)',
            'strncmp(fmt, "F259", 4)',
            'strncmp(fmt, "K249", 4)',
            'strncmp(fmt, "K248", 4)',
        ),
        "namei": (
            "A52_PHASE259_KERNEL_DENTRY_VFS_V1",
            "F259 mkb", "F259 mkx", "F259 ulb", "F259 ulx",
            "F259 rnb", "F259 rnx", "F259 v1", "F259 v2", "F259 v3",
        ),
        "open": (
            OPEN_MARKER, "F259 op", "F259 on", "F260 vh", "F260 vf",
            "a52_r260_trace_kgsl_fd_handoff(fd)",
        ),
        "adreno": (
            "A52_PHASE248_KGSL_GMU_IOMMU_CORRIDOR_V1",
            "K248 A plat rc=%d", "K248 A rb rc=%d", "K248 A dsp rc=%d",
        ),
        "gmu": (
            "K248 C dom n=%.12s ok=%d", "K248 C att rc=%d n=%.12s",
        ),
        "iommu": (
            "A52_PHASE249_GPU_SMMU_ENODEV_ROOT_V1",
            "K249 I grp ok=%d", "K249 I ret rc=%d s=nogrp", "K249 I ag rc=%d",
        ),
        "smmu": (
            "A52_PHASE249_GPU_SMMU_ENODEV_ROOT_V1",
            "K249 S clkon rc=%d", "K249 S reg rc=%d", "K249 S exit rc=0",
        ),
    }
    for name, tokens in required.items():
        for token in tokens:
            if token not in data[name]:
                raise RuntimeError(f"Phase260 {name} spectrum missing {token!r}")

    # Preserve the perturbation guard from Phase259.
    for forbidden in ("a52_r257_kgsl_user_node_event", "strncpy_from_user(path, name"):
        if forbidden in data["namei"]:
            raise RuntimeError(f"Phase260 reintroduced forbidden pathname probe: {forbidden}")


def apply(root: Path) -> None:
    patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
    patch_open(root / "fs/open.c")
    verify(root)
    print(
        f"{MARKER}: F259 node/mount lifetime + bounded F260 fd/fops handoff + "
        "inherited K248/K249 Adreno/GMU/IOMMU/SMMU spectrum verified",
        flush=True,
    )


def self_test() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        MARKER, REC_MARKER, OPEN_MARKER, "F260 vh", "F260 vf",
        "K248 A dsp rc=%d", "K249 I grp ok=%d", "K249 S clkon rc=%d",
        "n > 16", "fget(fd)", "fput(file)",
    ):
        if token not in source:
            raise AssertionError(f"Phase260 source missing {token}")
    print("Phase 260 suspicion-spectrum self-test: PASS", flush=True)


if __name__ == "__main__":
    self_test()
