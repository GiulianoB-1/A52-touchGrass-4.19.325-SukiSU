#!/usr/bin/env python3
"""Build/package Phase256 over the Phase255 visibility baseline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase256-kgsl-devnode-framework-v1"
HERE = Path(__file__).resolve().parent
PHASE255_PACKAGE = HERE / "255_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase255():
    spec = importlib.util.spec_from_file_location("phase255_package_for_256", PHASE255_PACKAGE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase255 packager: {PHASE255_PACKAGE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_candidate(out: Path) -> None:
    image = (out / "compile/Image").read_bytes()
    for marker in (
        b"K254 D disabled dev=%s cb=0",
        b"TRIPOST 228",
        b"GFXPOST 225",
        b"F256 da n=%.16s M=%u m=%u",
        b"F256 ue n=%.16s rc=%d",
        b"F256 rn p=%d e=%d n=%.16s",
        b"F256 ex p=%d n=%.16s c=%ld",
    ):
        if marker not in image:
            raise RuntimeError(f"missing Phase256 compiled marker: {marker.decode()}")

    config = (out / "config/final.config").read_text(encoding="utf-8")
    required = (
        "CONFIG_TMPFS_POSIX_ACL=y",
        "CONFIG_TMPFS_XATTR=y",
        "CONFIG_QCOM_KGSL=y",
        "CONFIG_QCOM_KGSL_IOMMU=y",
        "CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ=y",
        "CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON=y",
        'CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR="msm-adreno-tz"',
    )
    for token in required:
        if token not in config:
            raise RuntimeError(f"Phase256 final config missing {token}")
    for forbidden in ("CONFIG_DEVTMPFS=y", "CONFIG_UEVENT_HELPER=y"):
        if forbidden in config:
            raise RuntimeError(f"Phase256 forbidden config enabled: {forbidden}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase256-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_candidate(out)

    audit_dir = out / "audit/phase256"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "255_phase254_postboot_visibility_overlay.py",
        "256_phase255_kgsl_devnode_framework_overlay.py",
        "255_package.py",
        "256_package.py",
        "256_design.md",
    ):
        shutil.copy2(HERE / name, audit_dir / name)
    shutil.copy2(HERE / "256_design.md", out / "PHASE256-DESIGN.md")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 256,
        "base_phase": 255,
        "functional_base_phase": 254,
        "hardware_validated": False,
        "status": "phase256-kgsl-devnode-framework-ci-audited-not-hardware-validated",
        "phase256_behavior_changed": True,
        "phase256_current_blocker_fix": ["CONFIG_TMPFS_XATTR=y", "CONFIG_TMPFS_POSIX_ACL=y"],
        "phase256_future_risk_fix": [
            "CONFIG_QCOM_KGSL=y",
            "CONFIG_QCOM_KGSL_IOMMU=y",
            "CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ=y",
            "CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON=y",
            'CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR="msm-adreno-tz"',
        ],
        "phase256_framework_milestones": [
            "zygote", "zygote64", "system_server", "SystemUI",
            "com.sec.android.app.launcher", "bootanimation", "surfaceflinger",
        ],
        "phase256_recorder_prefix": "F256",
        "phase256_manual_mknod": False,
        "phase256_devtmpfs_enabled": False,
        "phase256_uevent_helper_enabled": False,
        "phase256_selinux_weakened": False,
        "phase256_dt_changed": False,
        "phase256_ramdisk_changed": False,
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 256,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 254,
        "change": "restore KGSL devnode prerequisites, devfreq contract and framework visibility",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 256 KGSL devnode + framework milestone candidate\n\n"
        "NOT HARDWARE VALIDATED. Phase256 aligns /dev tmpfs metadata support with the "
        "TouchGrass golden reference, restores the KGSL/Adreno devfreq Kconfig contract, "
        "and retains F256 milestones through zygote/system_server/SystemUI/launcher. "
        "It does not manually create kgsl-3d0 or weaken SELinux.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase255 = load_phase255()
    rc = phase255.main()
    if rc:
        return rc
    inherited = Path("phase255-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase255 inherited package output missing")
    finalize(inherited)
    print("Phase 256 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 256 packaging failed: {exc}", file=sys.stderr)
        raise
