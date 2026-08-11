#!/usr/bin/env python3
"""Build/package Phase254 over the hardware-tested Phase253 boundary."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase254-kgsl-default-domain-contract-v1"
HERE = Path(__file__).resolve().parent
PHASE253_PACKAGE = HERE / "253_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase253():
    spec = importlib.util.spec_from_file_location("phase253_package_for_254", PHASE253_PACKAGE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase253 packager: {PHASE253_PACKAGE}")
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
        b"K254 D disabled dev=%s cb=0",
        b"K254 C pre t=%u d=%d used=%u/%u s2=%u dev=%s",
        b"K254 C alloc t=%u d=%d rc=%d used=%u/%u",
        b"K254 A asid t=%u d=%d cb=%u asid=%u rc=%d",
        b"K254 D ok t=%u d=%d cb=%u asid=%u used=%u",
    ):
        if marker not in image:
            raise RuntimeError(f"missing Phase254 compiled Image marker: {marker.decode()}")

    config = (out / "config/final.config").read_text(encoding="utf-8")
    for token in (
        "CONFIG_OF=y",
        "CONFIG_ARM_SMMU=y",
        "CONFIG_QCOM_BUS_SCALING=y",
        "CONFIG_QCOM_BUS_CONFIG_RPMH=y",
    ):
        if token not in config:
            raise RuntimeError(f"Phase254 final config missing {token}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase254-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_candidate(out)

    audit_dir = out / "audit/phase254"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "252_msm_bus_510_format_guard.py",
        "253_phase252_kgsl_smmu_domain_contract_overlay.py",
        "254_phase253_kgsl_disabled_default_domain_contract.py",
        "253_package.py",
        "254_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 254,
        "base_phase": 253,
        "functional_base_phase": 252,
        "hardware_validated": False,
        "status": "phase254-kgsl-disabled-default-domain-contract-ci-audited-not-hardware-validated",
        "phase254_corrective": True,
        "phase254_trigger_capture": "A52_RAW_RAMOOPS_20260811_200415.zip",
        "phase254_trigger": (
            "GMU IOMMU/bandwidth/RPMh succeeds; first kgsl_device_platform_probe "
            "then returns -ENOSPC before a KGSL K253 domain-init success marker"
        ),
        "phase254_root_cause": (
            "TouchGrass interprets qcom,iommu-dma=disabled on auto-created DMA "
            "domains as dynamic software-only domains using logical CB0, so they "
            "do not consume/program a real context bank or context IRQ. The ACK "
            "5.10 port omitted that default-domain policy, allowing the KGSL/GMU "
            "context devices' default DMA domains to consume finite context banks "
            "before their explicit unmanaged domains are attached."
        ),
        "phase254_touchgrass_commit": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "phase254_dt_property": "qcom,iommu-dma=disabled",
        "phase254_live_dt_nodes": [
            "gfx3d_user", "gfx3d_secure", "gmu_user", "gmu_kernel"
        ],
        "phase254_default_dma_dynamic": True,
        "phase254_default_dma_logical_cb": 0,
        "phase254_default_dma_rewrites_streams": False,
        "phase254_default_dma_owns_context_bitmap": False,
        "phase254_default_dma_requests_context_irq": False,
        "phase254_dynamic_asid_retained": True,
        "phase254_phase253_domain_contract_retained": True,
        "phase254_phase252_msm_bus_retained": True,
        "phase254_fabricated_iommu_groups": False,
        "phase254_forced_attach_success": False,
        "phase254_stream_ids_changed": False,
        "phase254_dt_changed": False,
        "phase254_context_bank_diagnostics": True,
        "phase254_expected_hardware_progression": (
            "K254 D disabled for GMU/KGSL default DMA contexts with unchanged CB "
            "bitmap usage; explicit GMU domains still allocate real CBs; the first "
            "explicit KGSL pagetable gets a real CB and K248 A plat should progress "
            "past the previous -ENOSPC boundary toward KGSL registration."
        ),
        "phase254_guardrails": [
            "apply qcom,iommu-dma=disabled semantics only to IOMMU_DOMAIN_DMA",
            "do not mark explicit KGSL/GMU unmanaged domains dynamic from DT",
            "do not reserve or free a real context-map bit for disabled default domains",
            "do not rewrite S2CR/stream mappings for disabled default domains",
            "do not request a context IRQ for disabled default domains",
            "retain Phase253 PROCID/CONTEXT_BANK/TTBR0/CONTEXTIDR semantics",
            "retain Phase252 MSM-bus/RPMh behavior and all prior hardware-proven fixes",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 254,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 252,
        "change": "restore TouchGrass qcom,iommu-dma=disabled software-only default DMA domains",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 254 KGSL default-domain correction\n\n"
        "HARDWARE TEST: flash package/boot.img to BOOT only after CI succeeds.\n\n"
        "Phase254 restores the TouchGrass qcom,iommu-dma=disabled policy for "
        "auto-created DMA domains. Those domains become software-only dynamic "
        "domains using logical CB0 and therefore do not consume/program a real "
        "context bank before GMU/KGSL explicit domains are created. K254 markers "
        "record default policy, CB bitmap usage, ASID allocation, and successful "
        "domain completion while all Phase253/252 diagnostics remain active.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase253 = load_phase253()
    rc = phase253.main()
    if rc:
        return rc
    inherited = Path("phase253-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase253 inherited package output missing")
    finalize(inherited)
    print("Phase 254 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 254 packaging failed: {exc}", file=sys.stderr)
        raise
