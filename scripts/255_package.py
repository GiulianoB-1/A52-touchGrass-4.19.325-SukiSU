#!/usr/bin/env python3
"""Build/package Phase255 over the hardware-tested Phase254 functional state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase255-postboot-visibility-v1"
HERE = Path(__file__).resolve().parent
PHASE254_PACKAGE = HERE / "254_package.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase254():
    spec = importlib.util.spec_from_file_location(
        "phase254_package_for_255", PHASE254_PACKAGE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase254 packager: {PHASE254_PACKAGE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_candidate(out: Path) -> None:
    image = (out / "compile/Image").read_bytes()
    for marker in (
        b"K254 D disabled dev=%s cb=0",
        b"K254 D ok t=%u d=%d cb=%u asid=%u used=%u",
        b"K255VIS",
        b"TRIPOST 228",
        b"BOOTPOST ",
        b"USRPOST 224",
        b"ODSPOST 226",
        b"GFXPOST 225",
    ):
        if marker not in image:
            raise RuntimeError(
                f"missing Phase255 compiled Image marker: {marker.decode()}"
            )

    config = (out / "config/final.config").read_text(encoding="utf-8")
    for token in (
        "CONFIG_ARM_SMMU=y",
        "CONFIG_QCOM_BUS_SCALING=y",
        "CONFIG_QCOM_BUS_CONFIG_RPMH=y",
    ):
        if token not in config:
            raise RuntimeError(f"Phase255 final config missing {token}")


def refresh_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(
            f"{sha256(path)}  ./{path.relative_to(out)}\n"
            for path in files
        ),
        encoding="utf-8",
    )


def finalize(inherited: Path) -> Path:
    out = Path("phase255-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_candidate(out)

    audit_dir = out / "audit/phase255"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "252_msm_bus_510_format_guard.py",
        "254_phase253_kgsl_disabled_default_domain_contract.py",
        "255_phase254_postboot_visibility_overlay.py",
        "254_package.py",
        "255_package.py",
        "255_design.md",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    shutil.copy2(HERE / "255_design.md", out / "PHASE255-DESIGN.md")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 255,
        "base_phase": 254,
        "functional_base_phase": 254,
        "hardware_validated": False,
        "status": "phase255-postboot-visibility-ci-audited-not-hardware-validated",
        "phase255_diagnostic_only": True,
        "phase255_behavior_changed": False,
        "phase255_phase254_functional_state_retained": True,
        "phase255_recorder_transport_changed": False,
        "phase255_rs48_changed": False,
        "phase255_crc32c_changed": False,
        "phase255_dt_changed": False,
        "phase255_probe_order_changed": False,
        "phase255_return_values_changed": False,
        "phase255_gpu_semantics_changed": False,
        "phase255_iommu_semantics_changed": False,
        "phase255_security_decisions_changed": False,
        "phase255_restored_prefixes": [
            "BOOTPOST",
            "USRPOST",
            "ODSPOST",
            "GFXPOST",
            "TRIPOST",
        ],
        "phase255_key_checkpoint": "TRIPOST 228",
        "phase255_checkpoint_tracks": [
            "vold",
            "odsign-odrefresh",
            "surfaceflinger",
            "kgsl",
        ],
        "phase255_goal": (
            "identify the real first post-BOOT_READY Android/userspace milestone "
            "that Phase254 reached, without introducing another functional fix"
        ),
        "phase255_guardrails": [
            "retain the complete Phase254 qcom,iommu-dma=disabled correction",
            "do not alter KGSL, GMU, SMMU, clocks, regulators, DT or firmware",
            "do not alter service ordering, scheduling, timeouts or return values",
            "restore only previously compiled metadata-only recorder prefixes",
            "retain RS(255,207), CRC32C and the existing persistent memory layout",
        ],
    })
    audit_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    identity = {
        "phase": 255,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 254,
        "change": "restore post-BOOT_READY persistent recorder visibility only",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 255 post-BOOT_READY visibility candidate\n\n"
        "HARDWARE TEST: flash package/boot.img to BOOT only after CI succeeds.\n\n"
        "Phase255 keeps the hardware-tested Phase254 GPU/SMMU/GMU behavior and "
        "changes only persistent-recorder admission/retention. It restores the "
        "already-existing BOOTPOST, USRPOST, ODSPOST, GFXPOST and TRIPOST "
        "metadata so the next ramoops can show how far Android actually proceeds "
        "after BOOT_READY. No functional boot fix is added in this phase.\n",
        encoding="utf-8",
    )
    refresh_sums(out)
    return out


def main() -> int:
    phase254 = load_phase254()
    rc = phase254.main()
    if rc:
        return rc
    inherited = Path("phase254-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase254 inherited package output missing")
    finalize(inherited)
    print("Phase 255 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 255 packaging failed: {exc}", file=sys.stderr)
        raise
