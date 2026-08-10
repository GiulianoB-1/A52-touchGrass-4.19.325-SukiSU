#!/usr/bin/env python3
"""Build/package Phase251 over the Phase250 corrective state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase251-gmu-post-mmio-tail-diag-v1"
HERE = Path(__file__).resolve().parent
PHASE250 = HERE / "250_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase250():
    spec = importlib.util.spec_from_file_location("phase250_package_for_251", PHASE250)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase250 packager: {PHASE250}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1",
        b"K250 S regon rc=%d n=%d",
        b"K250 S clkon rc=%d",
        b"A52_PHASE251_GMU_POST_MMIO_TAIL_DIAG_V1",
        b"K251 G hfiirq rc=%d",
        b"K251 G gmuirq rc=%d",
        b"K251 G irqoff",
        b"K251 B gpu tbl=%d",
        b"K251 B gpu pcl=%u",
        b"K251 B cnoc tbl=%d",
        b"K251 B cnoc ccl=%u",
        b"K251 G gpubw rc=%d",
        b"K251 G cnoc rc=%d",
        b"K251 G rpmh rc=%d",
        b"K251 R bus rc=%d",
        b"K251 R gfx rc=%d",
        b"K251 R cx rc=%d",
        b"K251 R mx rc=%d",
        b"K251 R gpuvote rc=%d",
        b"K251 R gmuvote rc=%d",
        b"K251 G enabled",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase251 Image marker: {marker.decode()}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text("".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files), encoding="utf-8")


def finalize(inherited: Path) -> Path:
    out = Path("phase251-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_image(out / "compile/Image")

    audit_dir = out / "audit/phase251"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
        "249_phase248_gpu_smmu_enodev_root_overlay.py",
        "250_phase249_gpu_smmu_power_contract_overlay.py",
        "251_phase250_gmu_post_mmio_tail_diag_overlay.py",
        "250_package.py",
        "251_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 251,
        "base_phase": 250,
        "functional_base_phase": 250,
        "hardware_validated": False,
        "status": "phase251-gmu-post-mmio-tail-diagnostic-ci-audited-not-hardware-validated",
        "phase251_diagnostic_only": True,
        "phase251_phase250_correction_retained": True,
        "phase251_trigger": "Phase250 hardware: SMMU and both GMU IOMMU attaches succeed; K248 G ops returns -ENODEV after K248 M mmio rc=0",
        "phase251_hardware_question": "Which post-MMIO gmu_probe stage first returns -ENODEV: HFI/GMU IRQ, GPU BW table/client, CNOC BW table/client, or RPMh vote construction?",
        "phase251_gmu_semantics_changed": False,
        "phase251_bus_vote_semantics_changed": False,
        "phase251_irq_semantics_changed": False,
        "phase251_rpmh_semantics_changed": False,
        "phase251_dt_changed": False,
        "phase251_stream_ids_changed": False,
        "phase251_iommu_semantics_changed": False,
        "phase251_smmu_power_contract_changed": False,
        "phase251_probe_order_changed": False,
        "phase251_return_values_changed": False,
        "phase251_boot_cmdline_changed": False,
        "phase251_expected_markers": [
            "K251 G hfiirq rc=%d", "K251 G gmuirq rc=%d",
            "K251 B gpu tbl=%d", "K251 B gpu pcl=%u", "K251 G gpubw rc=%d",
            "K251 B cnoc tbl=%d", "K251 B cnoc ccl=%u", "K251 G cnoc rc=%d",
            "K251 R bus rc=%d", "K251 R gfx rc=%d", "K251 R cx rc=%d",
            "K251 R mx rc=%d", "K251 R gpuvote rc=%d", "K251 R gmuvote rc=%d",
            "K251 G rpmh rc=%d", "K251 G enabled"
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 251,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 250,
        "change": "diagnostic-only post-MMIO GMU probe tail markers",
    }
    (out / "BUILD-IDENTITY.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 251 GMU post-MMIO diagnostic\n\n"
        "HARDWARE TEST: flash package/boot.img to BOOT only.\n\n"
        "Phase251 retains the Phase250 GPU-SMMU power correction unchanged and adds only K251 diagnostics around the remaining gmu_probe tail. No DT, SID, IOMMU, IRQ, BW, RPMh, regulator, clock, return-value, or probe-order behavior is rewritten.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase250 = load_phase250()
    rc = phase250.main()
    if rc:
        return rc
    inherited = Path("phase250-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase250 inherited package output missing")
    finalize(inherited)
    print("Phase 251 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 251 packaging failed: {exc}", file=sys.stderr)
        raise
