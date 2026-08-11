#!/usr/bin/env python3
"""Build/package Phase252 over Phase251 hardware evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase252-legacy-msm-bus-rpmh-v1"
HERE = Path(__file__).resolve().parent
PHASE251 = HERE / "251_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase251():
    spec = importlib.util.spec_from_file_location("phase251_package_for_252", PHASE251)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase251 packager: {PHASE251}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_candidate(out: Path) -> None:
    image = (out / "compile/Image").read_bytes()
    for marker in (
        b"A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1",
        b"K250 S regon rc=%d n=%d",
        b"K251 G hfiirq rc=%d",
        b"K251 G gmuirq rc=%d",
        b"K251 B gpu tbl=%d",
        b"K251 B gpu pcl=%u",
        b"K251 B cnoc tbl=%d",
        b"K251 G gpubw rc=%d",
        b"K251 G cnoc rc=%d",
    ):
        if marker not in image:
            raise RuntimeError(f"missing retained Phase252 Image marker: {marker.decode()}")

    config = (out / "config/final.config").read_text(encoding="utf-8")
    for token in (
        "CONFIG_OF=y",
        "CONFIG_QCOM_COMMAND_DB=y",
        "CONFIG_QCOM_RPMH=y",
        "CONFIG_QCOM_BUS_SCALING=y",
        "CONFIG_QCOM_BUS_CONFIG_RPMH=y",
    ):
        if token not in config:
            raise RuntimeError(f"Phase252 final config missing {token}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase252-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_candidate(out)

    audit_dir = out / "audit/phase252"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "218_payload.py",
        "227_phase226_retention_wrapper.py",
        "250_phase249_gpu_smmu_power_contract_overlay.py",
        "251_phase250_gmu_post_mmio_tail_diag_overlay.py",
        "252_config_retention_gate.py",
        "252_phase251_legacy_msm_bus_rpmh_overlay.py",
        "252_msm_bus_510_compat.py",
        "252_msm_bus_510_format_guard.py",
        "251_package.py",
        "252_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 252,
        "base_phase": 251,
        "functional_base_phase": 250,
        "hardware_validated": False,
        "status": "phase252-legacy-msm-bus-rpmh-gki510-compat-ci-audited-not-hardware-validated",
        "phase252_corrective": True,
        "phase252_trigger_capture": "A52_RAW_RAMOOPS_20260811_090721(1).zip",
        "phase252_trigger": "K251 B gpu tbl=0 -> K251 G gpubw rc=-19 after HFI and GMU IRQ acquisition succeeds",
        "phase252_root_cause": (
            "Phase251 has CONFIG_OF=y but no CONFIG_QCOM_BUS_SCALING; the downstream "
            "include/linux/msm-bus.h therefore compiles msm_bus_pdata_from_node() and "
            "msm_bus_cl_get_pdata() as NULL-returning stubs. TouchGrass enables "
            "CONFIG_QCOM_BUS_SCALING=y and CONFIG_QCOM_BUS_CONFIG_RPMH=y."
        ),
        "phase252_touchgrass_commit": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "phase252_bus_contract": "legacy Qualcomm MSM-bus client API with RPMh/BCM provider",
        "phase252_qcom_bus_scaling_enabled": True,
        "phase252_qcom_bus_config_rpmh_enabled": True,
        "phase252_imports_pinned_touchgrass_msm_bus": True,
        "phase252_gki510_api_compatibility_applied": True,
        "phase252_gki510_format_guard_applied": True,
        "phase252_gki510_adaptations": [
            "bus_find_device match callbacks use const void *",
            "RPMh invalidate follows the GKI 5.10 void API",
            "command-db aux data follows pointer+length and ERR_PTR semantics",
            "BCM TCS command macros come from the GKI 5.10 tcs header",
            "debug timestamps use timespec64/ktime_to_timespec64 with signed formats",
            "ALC previous latency vote values are initialized from the previous usecase",
        ],
        "phase252_generic_interconnect_removed": False,
        "phase252_phase251_diagnostics_retained": True,
        "phase252_phase250_smmu_power_fix_retained": True,
        "phase252_dt_changed": False,
        "phase252_stream_ids_changed": False,
        "phase252_iommu_semantics_changed": False,
        "phase252_irq_semantics_changed": False,
        "phase252_return_values_forced": False,
        "phase252_probe_order_changed": False,
        "phase252_boot_cmdline_changed": False,
        "phase252_expected_hardware_progression": (
            "K251 B gpu tbl=1 -> K251 B gpu pcl!=0 -> K251 G gpubw rc=0, then "
            "continue into CNOC/RPMh diagnostics or expose the next real failure"
        ),
        "phase252_guardrails": [
            "do not bypass the NULL bus table check",
            "do not leave msm_bus vote APIs as no-op stubs",
            "do not remove generic SM6350 interconnect providers used by other clients",
            "use only the pinned hardware-proven TouchGrass legacy msm_bus sources",
            "adapt only APIs required by the pinned GKI 5.10 compile contract",
            "retain Phase251 K251 diagnostics for hardware validation",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 252,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 250,
        "change": "restore pinned TouchGrass legacy MSM-bus RPMh bandwidth contract with GKI 5.10 API compatibility",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 252 legacy MSM-bus RPMh correction\n\n"
        "HARDWARE TEST: flash package/boot.img to BOOT only after CI succeeds.\n\n"
        "Phase252 restores the pinned TouchGrass legacy MSM-bus RPMh implementation, "
        "enables QCOM_BUS_SCALING/QCOM_BUS_CONFIG_RPMH, and applies a fail-closed GKI "
        "5.10 API compatibility pass. Phase251 K251 diagnostics remain active to verify "
        "GPU and CNOC bandwidth clients on hardware.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase251 = load_phase251()
    rc = phase251.main()
    if rc:
        return rc
    inherited = Path("phase251-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase251 inherited package output missing")
    finalize(inherited)
    print("Phase 252 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 252 packaging failed: {exc}", file=sys.stderr)
        raise
