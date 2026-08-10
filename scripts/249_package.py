#!/usr/bin/env python3
"""Build/package Phase249 over the hardware-proven Phase247 functional state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase249-kgsl-smmu-enodev-root-v1"
HERE = Path(__file__).resolve().parent
PHASE248 = HERE / "248_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase248():
    spec = importlib.util.spec_from_file_location("phase248_package_for_249", PHASE248)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase248 packager: {PHASE248}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase249_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers",
        b"CXF246 X q=%d n=%d",
        b"cam_cc_pll2_out_early",
        b"K248 A gmu in",
        b"K248 M iommu in",
        b"K248 C att rc=%d n=%.12s",
        b"K249 S ent",
        b"K249 S dt rc=%d",
        b"K249 S map in",
        b"K249 S map rc=%d",
        b"K249 S impl rc=%d",
        b"K249 S irqs n=%d g=%u c=%u",
        b"K249 S clkget rc=%d",
        b"K249 S clkon rc=%d",
        b"K249 S cfg rc=%d",
        b"K249 S irq in i=%d n=%d",
        b"K249 S irq rc=%d i=%d",
        b"K249 S sys rc=%d",
        b"K249 S reg rc=%d",
        b"K249 S bus rc=%d",
        b"K249 I ent",
        b"K249 I grp ok=%d",
        b"K249 I ret rc=%d s=nogrp",
        b"K249 I g id=%d cnt=%d a=%d",
        b"K249 I ag rc=%d",
        b"K249 I ret rc=%d",
        b"3d40000",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase249 Image marker: {marker.decode()}")
    for marker in (b"BOOT rs=ready phase=244 focus=gdsc-subsys-initcall", b"CXF244 V q=%d l=%d"):
        if marker in data:
            raise RuntimeError(f"forbidden Phase244 marker in Phase249 Image: {marker.decode()}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase249-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_phase249_image(out / "compile/Image")

    audit_dir = out / "audit/phase249"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
        "249_phase248_gpu_smmu_enodev_root_overlay.py",
        "248_package.py",
        "249_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    if (HERE / "249_design.md").is_file():
        shutil.copy2(HERE / "249_design.md", out / "PHASE249-DESIGN.md")
    if (HERE / "249_trigger.txt").is_file():
        shutil.copy2(HERE / "249_trigger.txt", out / "PHASE249-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 249,
        "base_phase": 248,
        "functional_base_phase": 247,
        "runtime_recorder_identity": 243,
        "hardware_validated": False,
        "status": "phase249-gpu-smmu-enodev-root-ci-audited-not-hardware-validated",
        "phase249_diagnostic_only": True,
        "phase249_phase248_corridor_retained": True,
        "phase249_phase247_camcc_fix_retained": True,
        "phase249_phase245_fwdevlink_permissive_retained": True,
        "phase249_phase244_overlay_applied": False,
        "phase249_gpu_smmu_behavior_changed": False,
        "phase249_iommu_group_behavior_changed": False,
        "phase249_return_values_changed": False,
        "phase249_probe_order_changed": False,
        "phase249_dt_changed": False,
        "phase249_regulator_or_power_behavior_changed": False,
        "phase249_boot_cmdline_changed": False,
        "phase249_recorder_transport_changed": False,
        "phase249_hardware_question": (
            "Which exact internal arm_smmu_device_probe operation returns -EBUSY for "
            "3d40000.arm,smmu-kgsl, and does gmu_user have an IOMMU group when "
            "iommu_attach_device is called?"
        ),
        "phase249_phase248_hardware_evidence": {
            "capture": "A52_RAW_RAMOOPS_20260810_144738.zip",
            "decoder": "Phase210+ R48 RS48 CRC32C transport fusion",
            "current_contiguous_sequence_range": "1-876",
            "raw_snapshots_identical_sha256": "8534732fca7ec2746bce388e3240291749ac5e20f1096eb6832d08b23d1fa049",
            "subsys": "completes; CXF246 X n=155",
            "gpu_gdsc": "GX and CX providers bind successfully",
            "gpu_smmu": "3d40000.arm,smmu-kgsl arm-smmu probe returns -EBUSY and remains unbound",
            "kgsl_second_probe": "enters GMU probe after all driver-core suppliers are ready",
            "gmu_user_domain": "iommu_domain_alloc succeeds",
            "gmu_user_attach": "iommu_attach_device returns -ENODEV; K248 C att rc=-19",
            "last_current_record": "K248 M iommu rc=-19 seq=876",
        },
        "phase249_static_context": {
            "touchgrass_gmu_user": "qcom,smmu-gmu-user-cb with iommus=<&kgsl_smmu 4>",
            "touchgrass_gmu_kernel": "qcom,smmu-gmu-kernel-cb with iommus=<&kgsl_smmu 5>",
            "stream_ids_changed": False,
            "interpretation": (
                "-ENODEV from iommu_attach_device can mean no iommu_group or a deeper attach_dev failure; "
                "Phase249 records the group state and attach-group return without changing behavior."
            ),
        },
        "phase249_guardrails": [
            "Phase247 CAMCC dense-clk_hws correction remains unchanged",
            "Phase248 KGSL/GMU/IOMMU corridor remains unchanged",
            "FW_DEVLINK_FLAGS_PERMISSIVE remains unchanged",
            "no SMMU resource, IRQ, clock, registration, or bus-init return is rewritten",
            "no iommu_group or attach behavior is rewritten",
            "no DT property, stream ID, or SMMU node status is changed",
            "K249 records are critical recorder events; physical R48/RS48 transport is unchanged",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 249,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 247,
        "runtime_recorder_identity": 243,
        "change": "diagnostic-only K249 GPU arm-smmu -EBUSY and gmu_user iommu-group root corridor",
    }
    (out / "BUILD-IDENTITY.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 249 GPU SMMU / GMU ENODEV root diagnostic\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Functional state remains Phase247. Phase249 adds diagnostic-only K249 records around the "
        "3d40000 arm-smmu probe and gmu_user iommu_attach_device group path.\n"
        "No SMMU/IOMMU behavior, DT, stream ID, return value, power vote, or cmdline is changed.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase248 = load_phase248()
    rc = phase248.main()
    if rc:
        return rc
    inherited = Path("phase248-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase248 inherited package output missing")
    finalize(inherited)
    print("Phase 249 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 249 packaging failed: {exc}", file=sys.stderr)
        raise
