#!/usr/bin/env python3
"""Compile-only repair shim for Phase259 plus the Phase260/261 overlays."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "259_kgsl_node_lifetime_trace.py"
PHASE260 = HERE / "260_kgsl_suspicion_spectrum.py"
PHASE261 = HERE / "261_kgsl_open_rootcause.py"
REPAIR_MARKER = "A52_PHASE259_COMPILE_REPAIR_V2"
PLACEHOLDER = "A52_PHASE259_KERNEL_DENTRY_VFS_REPAIR_PLACEHOLDER_V2"
PHASE261_OBSERVE_ONLY = "A52_PHASE261_QCOM_WDT_OBSERVE_ONLY_V1"
PHASE262_MARKER = "A52_PHASE262_FW_LOADER_FALLBACK_AB_V1"


def load_target():
    spec = importlib.util.spec_from_file_location("a52_phase259_repaired", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase259 target: {TARGET}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_phase260():
    spec = importlib.util.spec_from_file_location("a52_phase260_spectrum", PHASE260)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase260 overlay: {PHASE260}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_phase261():
    spec = importlib.util.spec_from_file_location("a52_phase261_rootcause", PHASE261)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase261 overlay: {PHASE261}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load_target()
p260 = load_phase260()
p261 = load_phase261()
_orig_patch_namei = m.patch_namei
_orig_verify = m.verify


def strip_time_dependency(state: str) -> str:
    state = state.replace(
        'F259 v2 kns=%lx kt=%llu uc=%d ur=%d uns=%lx ut=%llu',
        'F259 v2 kns=%lx uc=%d ur=%d uns=%lx', 1)
    state = state.replace(
        'F259 v3 rc=%d rr=%d ro=%d rn=%d rns=%lx rt=%llu',
        'F259 v3 rc=%d rr=%d ro=%d rn=%d rns=%lx', 1)
    state, ul_n = re.subn(
        r'(?m)^(\s*READ_ONCE\(a52_r259_ul_ns\)),\s*\n\s*'
        r'\(unsigned long long\)\(READ_ONCE\(a52_r259_ul_ns_time\) / 1000000ULL\)\);',
        r'\1);', state, count=1)
    state, rn_n = re.subn(
        r'(?m)^(\s*READ_ONCE\(a52_r259_rn_ns\)),\s*\n\s*'
        r'\(unsigned long long\)\(READ_ONCE\(a52_r259_rn_ns_time\) / 1000000ULL\)\);',
        r'\1);', state, count=1)
    if ul_n != 1 or rn_n != 1:
        raise RuntimeError(f"Phase259 final time anchors drifted: ul={ul_n} rn={rn_n}")
    state = "".join(line for line in state.splitlines(keepends=True) if "_ns_time" not in line)
    if "ktime_get_ns" in state or "_ns_time" in state:
        raise RuntimeError("Phase259 time dependency survived repair")
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
    first_use = min(namei.find("int vfs_mknod("), namei.find("int vfs_unlink("), namei.find("int vfs_rename("))
    helper = namei.find("static bool a52_r259_trace_kgsl_dentry_match")
    if helper < 0 or first_use < 0 or helper >= first_use:
        raise RuntimeError("Phase259 helper definitions are not before first VFS use")
    if REPAIR_MARKER not in Path(__file__).read_text(encoding="utf-8"):
        raise RuntimeError("Phase259 compile repair marker missing")


m.verify = repaired_verify


def phase261_observe_only(source: str) -> str:
    if PHASE261_OBSERVE_ONLY in source:
        return source
    return f"/* {PHASE261_OBSERVE_ONLY}: no functional watchdog changes */\n" + source


def phase262_enable_fw_fallback(config: str) -> str:
    required = (
        "CONFIG_FW_LOADER=y",
        "CONFIG_FW_LOADER_USER_HELPER=y",
        "CONFIG_SCSI=y",
        "CONFIG_CHR_DEV_SG=y",
        "CONFIG_QCOM_KGSL=y",
        "CONFIG_QCOM_KGSL_IOMMU=y",
    )
    for line in required:
        if line not in config.splitlines():
            raise RuntimeError(f"Phase262 prerequisite missing: {line}")
    enabled = "CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y"
    disabled = "# CONFIG_FW_LOADER_USER_HELPER_FALLBACK is not set"
    if enabled in config.splitlines():
        return config
    if config.splitlines().count(disabled) != 1:
        raise RuntimeError("Phase262 fallback config anchor drifted")
    out = config.replace(disabled, enabled, 1)
    before = [line for line in config.splitlines() if line != disabled]
    after = [line for line in out.splitlines() if line != enabled]
    if before != after:
        raise RuntimeError("Phase262 changed more than the fallback config bit")
    return out


def phase262_apply(root: Path) -> None:
    cfg = root.parent.parent / "workspace/gki-phase199-out/.config"
    if not cfg.is_file():
        raise RuntimeError(f"Phase262 config missing: {cfg}")
    before = cfg.read_text(encoding="utf-8")
    after = phase262_enable_fw_fallback(before)
    cfg.write_text(after, encoding="utf-8")
    if "CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y" not in after.splitlines():
        raise RuntimeError("Phase262 fallback bit did not apply")
    print(f"{PHASE262_MARKER}: CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        m.self_test()
        p260.self_test()
        p261.self_test()
        sample = "\n".join((
            "CONFIG_FW_LOADER=y",
            "CONFIG_FW_LOADER_USER_HELPER=y",
            "# CONFIG_FW_LOADER_USER_HELPER_FALLBACK is not set",
            "CONFIG_SCSI=y",
            "CONFIG_CHR_DEV_SG=y",
            "CONFIG_QCOM_KGSL=y",
            "CONFIG_QCOM_KGSL_IOMMU=y",
            "CONFIG_A52_PHASE262_SENTINEL=y",
            "",
        ))
        changed = phase262_enable_fw_fallback(sample)
        assert "CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y" in changed
        assert "CONFIG_A52_PHASE262_SENTINEL=y" in changed
        assert changed.count("CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y") == 1
        print("Phase 262 firmware-loader fallback A/B self-test: PASS", flush=True)
        return 0
    root = m.p257.locate(sys.argv[1:])
    rc = m.main()
    if rc:
        return rc
    p260.apply(root)
    p261.WDT = PHASE261_OBSERVE_ONLY
    p261.watchdog = phase261_observe_only
    p261.apply(root)
    phase262_apply(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
