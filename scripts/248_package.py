#!/usr/bin/env python3
"""Build/package Phase248 over the hardware-proven Phase247 functional state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase248-kgsl-gmu-iommu-corridor-v1"
HERE = Path(__file__).resolve().parent
PHASE247 = HERE / "247_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase247():
    spec = importlib.util.spec_from_file_location("phase247_package_for_248", PHASE247)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase247 packager: {PHASE247}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase248_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers",
        b"CXF243 G c=%c q=%d rc=%d ls=%d",
        b"CXF246 S n=%d f=%ps",
        b"CXF246 X q=%d n=%d",
        b"cam_cc_pll2_out_early",
        b"K248 A ef rc=%d",
        b"K248 A gmu in",
        b"K248 A gmu rc=%d",
        b"K248 A plat in",
        b"K248 G ops in t=%d",
        b"K248 G ops rc=%d",
        b"K248 M iommu in",
        b"K248 M iommu rc=%d",
        b"K248 I pop in",
        b"K248 I cb in i=%d",
        b"K248 C dom n=%.12s ok=%d",
        b"K248 C att in n=%.12s",
        b"K248 C att rc=%d n=%.12s",
        b"3d9106c",
        b"3d9100c",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase248 Image marker: {marker.decode()}")
    for marker in (
        b"BOOT rs=ready phase=244 focus=gdsc-subsys-initcall",
        b"CXF244 V q=%d l=%d",
    ):
        if marker in data:
            raise RuntimeError(f"forbidden Phase244 marker in Phase248 Image: {marker.decode()}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase248-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_phase248_image(out / "compile/Image")

    audit_dir = out / "audit/phase248"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
        "247_package.py",
        "248_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    if (HERE / "248_design.md").is_file():
        shutil.copy2(HERE / "248_design.md", out / "PHASE248-DESIGN.md")
    if (HERE / "248_trigger.txt").is_file():
        shutil.copy2(HERE / "248_trigger.txt", out / "PHASE248-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 248,
        "base_phase": 247,
        "functional_base_phase": 247,
        "runtime_recorder_identity": 243,
        "hardware_validated": False,
        "status": "phase248-kgsl-gmu-iommu-corridor-ci-audited-not-hardware-validated",
        "phase248_diagnostic_only": True,
        "phase248_phase247_camcc_fix_retained": True,
        "phase248_phase245_fwdevlink_permissive_retained": True,
        "phase248_phase246_subsys_recorder_retained": True,
        "phase248_phase243_cxgx_hooks_retained": True,
        "phase248_phase244_overlay_applied": False,
        "phase248_return_values_changed": False,
        "phase248_probe_order_changed": False,
        "phase248_iommu_behavior_changed": False,
        "phase248_regulator_or_power_behavior_changed": False,
        "phase248_dt_changed": False,
        "phase248_boot_cmdline_changed": False,
        "phase248_recorder_transport_changed": False,
        "phase248_hardware_question": (
            "On the second KGSL probe after qfprom becomes ready, which exact Adreno/GMU/IOMMU "
            "operation is the final one entered, and does gmu_iommu context-bank attachment return?"
        ),
        "phase248_phase247_hardware_evidence": {
            "capture": "A52_RAW_RAMOOPS_20260810_121801.zip",
            "decoder": "Phase210+ R48 RS48 transport fusion",
            "current_contiguous_sequence_range": "1-855",
            "camcc": "fixed; subsys level completes with CXF246 X n=155",
            "gpu_cx_gdsc": "bound in current boot; supplier gate rc=0, provider entered, registration rc=0",
            "kgsl_first_probe": "adreno_probe returns -EPROBE_DEFER before qfprom binds",
            "qfprom": "binds successfully before deferred KGSL retry",
            "kgsl_second_probe": "all driver-core suppliers ready; adreno_probe entered; no return before current stream ends",
            "generic_gpu_smmu": "3d40000.arm,smmu-kgsl arm-smmu probe callback returns -EBUSY and remains unbound",
        },
        "phase248_static_context": {
            "first_defer_likely_dependency": "nvmem speed_bin via qfprom",
            "touchgrass_gmu_context_ids": "gfx user=0 secure=2, gmu user=4 kernel=5",
            "phase247_dtb_context_ids": "matches TouchGrass exactly",
            "next_corridor": "adreno_probe -> gmu_core_probe -> gmu_probe -> gmu_iommu_init -> iommu_attach_device -> kgsl_device_platform_probe",
        },
        "phase248_guardrails": [
            "Phase247 CAMCC dense-clk_hws correction remains unchanged",
            "GPU CX/GX GDSC provider implementation remains unchanged",
            "FW_DEVLINK_FLAGS_PERMISSIVE remains unchanged",
            "no iommu_domain_alloc/attach return value or behavior is rewritten",
            "no SMMU node is disabled or DT property changed",
            "no KGSL/GMU dependency is bypassed",
            "K248 records are critical recorder events; physical R48/RS48 transport is unchanged",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 248,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 247,
        "runtime_recorder_identity": 243,
        "change": "diagnostic-only K248 Adreno/GMU/IOMMU probe corridor over hardware-proven Phase247",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 248 KGSL/GMU/IOMMU corridor candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Functional state is Phase247: CAMCC dense clk_hws fix + fw_devlink permissive.\n"
        "Phase248 adds diagnostic-only K248 records inside the second KGSL/GMU/IOMMU probe path.\n"
        "No IOMMU attach behavior, DT, regulator, power, return value, or boot cmdline is changed.\n",
        encoding="utf-8",
    )

    refresh_sums(out)
    return out


def main() -> int:
    phase247 = load_phase247()
    rc = phase247.main()
    if rc:
        return rc
    inherited = Path("phase247-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase247 inherited package output missing")
    finalize(inherited)
    print("Phase 248 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 248 packaging failed: {exc}", file=sys.stderr)
        raise
