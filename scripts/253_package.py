#!/usr/bin/env python3
"""Build/package Phase253 over the hardware-validated Phase252 progression."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase253-kgsl-platform-enodev-corridor-v1"
HERE = Path(__file__).resolve().parent
PHASE252 = HERE / "252_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase252():
    spec = importlib.util.spec_from_file_location("phase252_package_for_253", PHASE252)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase252 packager: {PHASE252}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_candidate(out: Path) -> None:
    image = (out / "compile/Image").read_bytes()
    for marker in (
        b"K248 A plat in",
        b"K248 A plat rc=%d",
        b"K251 G gpubw rc=%d",
        b"K251 G cnoc rc=%d",
        b"K253 D init dyn=%d cb=%u asid=%u proc=%u",
        b"set DOMAIN_ATTR_PROCID failed: %d",
        b"get DOMAIN_ATTR_CONTEXT_BANK failed: %d",
        b"get DOMAIN_ATTR_TTBR0 failed: %d",
        b"get DOMAIN_ATTR_CONTEXTIDR failed: %d",
    ):
        if marker not in image:
            raise RuntimeError(f"missing Phase253 compiled Image marker: {marker.decode()}")

    config = (out / "config/final.config").read_text(encoding="utf-8")
    for token in (
        "CONFIG_OF=y",
        "CONFIG_ARM_SMMU=y",
        "CONFIG_QCOM_BUS_SCALING=y",
        "CONFIG_QCOM_BUS_CONFIG_RPMH=y",
    ):
        if token not in config:
            raise RuntimeError(f"Phase253 final config missing {token}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase253-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_candidate(out)

    audit_dir = out / "audit/phase253"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "252_phase251_legacy_msm_bus_rpmh_overlay.py",
        "252_msm_bus_510_compat.py",
        "252_msm_bus_510_format_guard.py",
        "253_phase252_kgsl_smmu_domain_contract_overlay.py",
        "252_package.py",
        "253_package.py",
        "253_surfaceflinger_component_audit.md",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    shutil.copy2(
        HERE / "253_surfaceflinger_component_audit.md",
        out / "PHASE253-SURFACEFLINGER-COMPONENT-AUDIT.md",
    )

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 253,
        "base_phase": 252,
        "functional_base_phase": 252,
        "hardware_validated": False,
        "status": "phase253-kgsl-smmu-domain-contract-ci-audited-not-hardware-validated",
        "phase253_corrective": True,
        "phase253_trigger_capture": "A52_RAW_RAMOOPS_20260811_162821.zip",
        "phase253_trigger": "K248 A plat in -> kgsl_device_platform_probe rc=-19 after GMU/RPMh success",
        "phase253_root_cause": (
            "KGSL default pagetable creation runs in per-process mode and first sets "
            "DOMAIN_ATTR_PROCID. The Phase252 ACK arm-smmu port exposes the enum but "
            "returns -ENODEV because Qualcomm downstream domain semantics were absent."
        ),
        "phase253_touchgrass_commit": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "phase253_domain_attributes": [
            "DOMAIN_ATTR_PROCID",
            "DOMAIN_ATTR_DYNAMIC",
            "DOMAIN_ATTR_CONTEXT_BANK",
            "DOMAIN_ATTR_TTBR0",
            "DOMAIN_ATTR_CONTEXTIDR",
        ],
        "phase253_dynamic_kgsl_domains": True,
        "phase253_unique_dynamic_asids": True,
        "phase253_dynamic_stream_rewrite": False,
        "phase253_fabricated_iommu_groups": False,
        "phase253_forced_attach_success": False,
        "phase253_stream_ids_changed": False,
        "phase253_dt_changed": False,
        "phase253_phase252_msm_bus_retained": True,
        "phase253_phase250_smmu_power_fix_retained": True,
        "phase253_surfaceflinger_static_audit_completed": True,
        "phase253_surfaceflinger_other_deterministic_blockers_found": False,
        "phase253_expected_hardware_progression": (
            "K248 A plat rc=0 -> KGSLI init/default PT -> memstore/ringbuffer/dispatcher -> "
            "kgsl-3d0 node -> SurfaceFlinger EGL/gralloc -> first DRM atomic commit"
        ),
        "phase253_guardrails": [
            "do not return success for unsupported domain attributes without semantics",
            "do not fabricate IOMMU groups",
            "do not bypass iommu_attach_device failures",
            "dynamic KGSL pagetable domains must not rewrite the live gfx stream mapping",
            "retain existing ACK display/Apps-SMMU behavior for domains not using KGSL PROCID/DYNAMIC",
            "retain Phase252 legacy MSM-bus/RPMh and Phase250 SMMU power fixes",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 253,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 252,
        "change": "restore KGSL Qualcomm ARM-SMMU PROCID/dynamic per-process domain contract",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 253 KGSL ARM-SMMU domain-contract correction\n\n"
        "HARDWARE TEST: flash package/boot.img to BOOT only after CI succeeds.\n\n"
        "Phase253 fixes the Phase252 kgsl_device_platform_probe -ENODEV by restoring "
        "the downstream KGSL PROCID/CONTEXT_BANK/TTBR0/CONTEXTIDR contract and the "
        "dynamic per-process pagetable semantics needed by SurfaceFlinger. Existing "
        "K248/K251/GFX/display diagnostics remain available for the next boundary.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase252 = load_phase252()
    rc = phase252.main()
    if rc:
        return rc
    inherited = Path("phase252-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase252 inherited package output missing")
    finalize(inherited)
    print("Phase 253 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 253 packaging failed: {exc}", file=sys.stderr)
        raise
