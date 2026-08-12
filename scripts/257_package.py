#!/usr/bin/env python3
"""Build/package Phase257 over the authoritative Phase256 candidate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase257-kgsl-publication-pipeline-v1"
HERE = Path(__file__).resolve().parent
PHASE256_PACKAGE = HERE / "256_package.py"
PHASE255_OVERLAY = HERE / "255_phase254_postboot_visibility_overlay.py"
CHAIN_MARKER = "A52_PHASE257_COMMITTED_CHILD_BUILD_CHAIN_V1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_committed_chain() -> None:
    """Require the Phase257 hook to exist in the committed child-visible chain."""
    text = PHASE255_OVERLAY.read_text(encoding="utf-8")
    required = (
        CHAIN_MARKER,
        'PHASE257 = Path(__file__).resolve().parent / "257_phase256_kgsl_publication_pipeline_overlay.py"',
        'stages.append((PHASE257, "Phase257 KGSL publication-pipeline recorder"))',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(
                f"Phase257 committed build chain missing {token!r}; refusing parent-only injection"
            )


def load_phase256():
    spec = importlib.util.spec_from_file_location("phase256_package_for_257", PHASE256_PACKAGE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase256 packager: {PHASE256_PACKAGE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_candidate(out: Path) -> None:
    image = (out / "compile/Image").read_bytes()
    markers = (
        b"GFXPOST 225",
        b"F256 da n=%.16s M=%u m=%u",
        b"F256 ue n=%.16s rc=%d",
        b"F257 add n=%u M=%u m=%u s=%d",
        b"F257 wr n=%u a=%.4s p=%d g=%d M=%u m=%u s=%d",
        b"F257 wrx n=%u rc=%d dn=%d mc=%d",
        b"F257 md r=%d dn=%.31s M=%u m=%u",
        b"F257 mk n=%u rc=%d p=%d g=%d mo=%o M=%u m=%u c=%.15s",
        b"F257 ul n=%u rc=%d p=%d g=%d c=%.15s",
        b"F257 s1 n=%u o=%d ac=%d ar=%d M=%d m=%d s=%d at=%llu",
        b"F257 s2 wc=%d wr=%d a=%d p=%d g=%d M=%d m=%d s=%d ft=%llu",
        b"F257 s3 mc=%d mr=%d dn=%d c=%.15s d=%.31s lt=%llu",
        b"F257 s4 kc=%d kr=%d p=%d g=%d mo=%o M=%u m=%u kt=%llu",
        b"F257 s5 uc=%d ur=%d p=%d g=%d kc=%.15s uc=%.15s ut=%llu",
    )
    for marker in markers:
        if marker not in image:
            raise RuntimeError(f"missing Phase257 compiled marker: {marker.decode()}")

    config = (out / "config/final.config").read_text(encoding="utf-8")
    for token in (
        "CONFIG_TMPFS_POSIX_ACL=y",
        "CONFIG_TMPFS_XATTR=y",
        "CONFIG_QCOM_KGSL=y",
        "CONFIG_QCOM_KGSL_IOMMU=y",
    ):
        if token not in config:
            raise RuntimeError(f"Phase257 final config missing inherited {token}")
    for forbidden in ("CONFIG_DEVTMPFS=y", "CONFIG_UEVENT_HELPER=y"):
        if forbidden in config:
            raise RuntimeError(f"Phase257 forbidden config enabled: {forbidden}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase257-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_candidate(out)

    audit_dir = out / "audit/phase257"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "255_phase254_postboot_visibility_overlay.py",
        "256_phase255_kgsl_devnode_framework_overlay.py",
        "256_package.py",
        "257_phase256_kgsl_publication_pipeline_overlay.py",
        "257_package.py",
        "257_design.md",
    ):
        shutil.copy2(HERE / name, audit_dir / name)
    shutil.copy2(HERE / "257_design.md", out / "PHASE257-DESIGN.md")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 257,
        "base_phase": 256,
        "functional_base_phase": 256,
        "hardware_validated": False,
        "status": "phase257-kgsl-publication-pipeline-ci-audited-not-hardware-validated",
        "phase257_diagnostic_only": True,
        "phase257_behavior_changed": False,
        "phase257_recorder_prefix": "F257",
        "phase257_committed_child_build_chain": True,
        "phase257_initial_kobj_add_retained": True,
        "phase257_sysfs_uevent_store_traced": True,
        "phase257_kobject_synth_uevent_traced": True,
        "phase257_generic_devname_traced": True,
        "phase257_mknod_syscall_traced": True,
        "phase257_unlink_syscall_traced": True,
        "phase257_late_open_reemit": True,
        "phase257_manual_mknod": False,
        "phase257_devtmpfs_enabled": False,
        "phase257_uevent_helper_enabled": False,
        "phase257_selinux_weakened": False,
        "phase257_dt_changed": False,
        "phase257_ramdisk_changed": False,
        "phase257_ueventd_rules_changed": False,
        "phase257_return_values_changed": False,
        "phase257_major_minor_changed": False,
        "phase257_goal": (
            "record the full kgsl-3d0 publication path from device_add and ueventd coldboot "
            "through userspace mknod result, possible unlink, and the later ENOENT open"
        ),
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 257,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 256,
        "change": "diagnostic-only full KGSL userspace publication-pipeline recorder",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 257 KGSL publication-pipeline recorder\n\n"
        "NOT HARDWARE VALIDATED. Flash package/boot.img to BOOT only after CI succeeds.\n\n"
        "Phase257 preserves Phase256 behavior and records initial KOBJ_ADD, sysfs uevent "
        "coldboot writes, kobject_synth_uevent return, generic DEVNAME generation, "
        "mknod/mknodat attempts and results, unlink/unlinkat removal attempts, and retained "
        "snapshots during /dev/kgsl-3d0 open failures. It does not create the node, change "
        "devtmpfs, weaken SELinux, alter DT/ramdisk/ueventd rules, force return values, or "
        "change the KGSL major/minor.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    verify_committed_chain()
    phase256 = load_phase256()
    rc = phase256.main()
    if rc:
        return rc
    inherited = Path("phase256-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase256 inherited package output missing")
    finalize(inherited)
    print("Phase 257 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 257 packaging failed: {exc}", file=sys.stderr)
        raise
