#!/usr/bin/env python3
"""Compile-only repair shim for the Phase259 target-only KGSL trace.

Keeps the Phase258/259 experiment semantics intact while repairing generated-C
ordering and removing an unnecessary timekeeping dependency.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "259_kgsl_node_lifetime_trace.py"
REPAIR_MARKER = "A52_PHASE259_COMPILE_REPAIR_V2"
PLACEHOLDER = "A52_PHASE259_KERNEL_DENTRY_VFS_REPAIR_PLACEHOLDER_V2"


def load_target():
    spec = importlib.util.spec_from_file_location("a52_phase259_repaired", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase259 target: {TARGET}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load_target()
_orig_patch_namei = m.patch_namei
_orig_verify = m.verify


def strip_time_dependency(state: str) -> str:
    """Remove only Phase259's diagnostic timestamps, independent of whitespace."""
    state = state.replace(
        'F259 v2 kns=%lx kt=%llu uc=%d ur=%d uns=%lx ut=%llu',
        'F259 v2 kns=%lx uc=%d ur=%d uns=%lx',
        1,
    )
    state = state.replace(
        'F259 v3 rc=%d rr=%d ro=%d rn=%d rns=%lx rt=%llu',
        'F259 v3 rc=%d rr=%d ro=%d rn=%d rns=%lx',
        1,
    )

    # The final timestamp is also the final argument, so first turn the
    # preceding namespace argument into the call terminator. This is written
    # whitespace-agnostically because STATE_BLOCK may contain literal tabs.
    state, ul_n = re.subn(
        r'(?m)^(\s*READ_ONCE\(a52_r259_ul_ns\)),\s*\n\s*'
        r'\(unsigned long long\)\(READ_ONCE\(a52_r259_ul_ns_time\) / 1000000ULL\)\);',
        r'\1);',
        state,
        count=1,
    )
    state, rn_n = re.subn(
        r'(?m)^(\s*READ_ONCE\(a52_r259_rn_ns\)),\s*\n\s*'
        r'\(unsigned long long\)\(READ_ONCE\(a52_r259_rn_ns_time\) / 1000000ULL\)\);',
        r'\1);',
        state,
        count=1,
    )
    if ul_n != 1 or rn_n != 1:
        raise RuntimeError(
            f"Phase259 final time-argument anchors drifted: ul={ul_n} rn={rn_n}"
        )

    # Every remaining line mentioning *_ns_time is either its declaration, a
    # WRITE_ONCE timestamp update, or the non-final mk timestamp argument.
    state = "".join(
        line for line in state.splitlines(keepends=True) if "_ns_time" not in line
    )

    if "ktime_get_ns" in state or "_ns_time" in state:
        raise RuntimeError("Phase259 time dependency survived repair")
    if "kt=%llu" in state or "ut=%llu" in state or "rt=%llu" in state:
        raise RuntimeError("Phase259 time format survived repair")
    return state


m.STATE_BLOCK = strip_time_dependency(m.STATE_BLOCK)


def repaired_patch_namei(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if m.NAMEI_MARKER in text:
        return

    positions = {
        "vfs_mknod": text.find("int vfs_mknod("),
        "vfs_unlink": text.find("int vfs_unlink("),
        "vfs_rename": text.find("int vfs_rename("),
    }
    missing = [name for name, pos in positions.items() if pos < 0]
    if missing:
        raise RuntimeError("Phase259 VFS anchors missing: " + ", ".join(missing))

    # Put definitions before every instrumented function, then let the original
    # Phase259 hooker add only its function-local calls. A placeholder prevents
    # its idempotence guard from returning early.
    state = m.STATE_BLOCK.replace(m.NAMEI_MARKER, PLACEHOLDER, 1)
    insert = min(positions.values())
    path.write_text(text[:insert] + state + text[insert:], encoding="utf-8")

    saved = m.STATE_BLOCK
    m.STATE_BLOCK = ""
    try:
        _orig_patch_namei(path)
    finally:
        m.STATE_BLOCK = saved

    text = path.read_text(encoding="utf-8")
    if text.count(PLACEHOLDER) != 1:
        raise RuntimeError("Phase259 helper placeholder count drifted")
    text = text.replace(PLACEHOLDER, m.NAMEI_MARKER, 1)
    text = text.replace("#include <linux/ktime.h>\n\n", "", 1)
    path.write_text(text, encoding="utf-8")


m.patch_namei = repaired_patch_namei


def repaired_verify(root: Path) -> None:
    _orig_verify(root)
    namei = (root / "fs/namei.c").read_text(encoding="utf-8")
    openc = (root / "fs/open.c").read_text(encoding="utf-8")
    for forbidden in ("->mnt_ns", "MAJOR(", "MINOR(", "ktime_get_ns", "_ns_time"):
        if forbidden in namei or forbidden in openc:
            raise RuntimeError(f"Phase259 compile repair failed: {forbidden}")
    first_use = min(
        namei.find("int vfs_mknod("),
        namei.find("int vfs_unlink("),
        namei.find("int vfs_rename("),
    )
    helper = namei.find("static bool a52_r259_trace_kgsl_dentry_match")
    if helper < 0 or first_use < 0 or helper >= first_use:
        raise RuntimeError("Phase259 helper definitions are not before first VFS use")
    if REPAIR_MARKER not in Path(__file__).read_text(encoding="utf-8"):
        raise RuntimeError("Phase259 compile repair marker missing")


m.verify = repaired_verify


if __name__ == "__main__":
    raise SystemExit(m.main())
