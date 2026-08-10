#!/usr/bin/env python3
"""Build/package Phase250 over Phase249 hardware evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase250-gpu-smmu-power-contract-v1"
HERE = Path(__file__).resolve().parent
PHASE249 = HERE / "249_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase249():
    spec = importlib.util.spec_from_file_location("phase249_package_for_250", PHASE249)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase249 packager: {PHASE249}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase250_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1",
        b"K249 S clkon rc=%d",
        b"K249 I grp ok=%d",
        b"K250 S gdscget rc=%d n=%d",
        b"K250 S regon rc=%d n=%d",
        b"K250 S clkon rc=%d",
        b"qcom,regulator-names",
        b"3d40000",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase250 Image marker: {marker.decode()}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase250-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_phase250_image(out / "compile/Image")

    audit_dir = out / "audit/phase250"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "249_phase248_gpu_smmu_enodev_root_overlay.py",
        "250_phase249_gpu_smmu_power_contract_overlay.py",
        "249_package.py",
        "250_package.py",
        "250_gpu_component_audit.md",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    shutil.copy2(HERE / "250_gpu_component_audit.md", out / "PHASE250-GPU-COMPONENT-AUDIT.md")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 250,
        "base_phase": 249,
        "functional_base_phase": 247,
        "hardware_validated": False,
        "status": "phase250-gpu-smmu-power-contract-ci-audited-not-hardware-validated",
        "phase250_corrective": True,
        "phase250_reference_trace": "TOUCHGRASS_GPU_TRACE_20260810_210342.zip",
        "phase250_touchgrass_records": 8192,
        "phase250_touchgrass_all_recorded_rc_zero": True,
        "phase250_root_cause": (
            "ACK arm-smmu enables gcc_gpu_memnoc_gfx_clk without first acquiring the "
            "DT-declared qcom,regulator-names vdd/CX supply; Phase249 hardware returns -EBUSY."
        ),
        "phase250_power_order_target": [
            "optional bus vote (no-op for a52xq GPU SMMU)",
            "vdd/CX regulator enable",
            "clock prepare",
            "clock enable",
            "SMMU configuration and registration",
        ],
        "phase250_arm_smmu_regulator_contract_changed": True,
        "phase250_arm_smmu_runtime_pm_supply_symmetry_changed": True,
        "phase250_clock_topology_changed": False,
        "phase250_gdsc_provider_implementation_changed": False,
        "phase250_dt_changed": False,
        "phase250_stream_ids_changed": False,
        "phase250_iommu_group_behavior_forced": False,
        "phase250_iommu_attach_return_forced": False,
        "phase250_kgsl_gmu_semantics_changed": False,
        "phase250_hfi_semantics_changed": False,
        "phase250_adreno_a6xx_semantics_changed": False,
        "phase250_probe_order_changed": False,
        "phase250_boot_cmdline_changed": False,
        "phase250_expected_hardware_progression": (
            "K250 gdscget rc=0 n=1 -> K250 regon rc=0 n=1 -> K250 clkon rc=0 -> "
            "K249 cfg/reg/bus rc=0 -> gmu_user iommu_group present -> GMU/KGSL attaches rc=0"
        ),
        "phase250_guardrails": [
            "consume existing qcom,regulator-names and vdd-supply; do not rewrite DT",
            "preserve existing gcc_gpu_memnoc_gfx_clk definition and branch semantics",
            "preserve GPU CX/GX GDSC implementation and supplier topology",
            "preserve SIDs 0/2/4/5",
            "preserve real regulator and clock return codes",
            "do not fabricate iommu groups or force attach success",
            "leave KGSL/GMU/HFI/Adreno logic unchanged after audit found no proven bring-up mismatch",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 250,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 247,
        "change": "restore qcom ARM-SMMU vdd/CX regulator-before-clock contract",
    }
    (out / "BUILD-IDENTITY.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 250 GPU SMMU power-contract correction\n\n"
        "HARDWARE TEST: flash package/boot.img to BOOT only.\n\n"
        "Phase250 consumes the existing GPU SMMU qcom,regulator-names=vdd supply before "
        "enabling gcc_gpu_memnoc_gfx_clk. DT, SIDs, GDSC/clock definitions, IOMMU grouping, "
        "and KGSL/GMU/HFI/Adreno semantics are not rewritten.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase249 = load_phase249()
    rc = phase249.main()
    if rc:
        return rc
    inherited = Path("phase249-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase249 inherited package output missing")
    finalize(inherited)
    print("Phase 250 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 250 packaging failed: {exc}", file=sys.stderr)
        raise
