#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

BRANCH = "agent/a52-phase238-broad-gpu-supplier-recorder-v1"
TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_phase237_package():
    path = Path(__file__).with_name("237_package.py")
    spec = importlib.util.spec_from_file_location("phase237_package", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inherited Phase 237 packager: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase238_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=238 focus=gpu-supplier-broad",
        b"A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1",
        b"A52_PHASE238_PLATFORM_GPU_TRACE_V1",
        b"A52_PHASE238_DRIVER_CORE_GPU_TRACE_V1",
        b"A52_PHASE238_GDSC_PROVIDER_TRACE_V1",
        b"G238 P in dev=%s drv=%s node=%s",
        b"G238 P out dev=%s rc=%d l=%d drv=%s",
        b"G238 D in dev=%s drv=%s cur=%s",
        b"G238 D sup c=%s s=%s drv=%s st=%u fl=%u",
        b"G238 D sup-out dev=%s rc=%d",
        b"G238 D out dev=%s rc=%d l=%d cur=%s",
        b"G238 GD in dev=%s drv=%s rn=%s comp=%s cx=%u",
        b"G238 GD res dev=%s ok=1 s=%llx e=%llx",
        b"G238 GD prop dev=%s wait-rc=%d wait=%u",
        b"G238 GD prop dev=%s timeout-rc=%d timeout=%u",
        b"G238 GD prop dev=%s nostatus=%u parent=%u hwctl=%u hwctrl=%u",
        b"G238 GD ph dev=%s p=%s node=%s pdrv=%s",
        b"G238 GD st dev=%s s=%d op=%s l=%d",
        b"G238 GD out dev=%s rc=%d l=%d drv=%s",
        b"G238 RP plat cx-att=%d stage=%d rc=%d drv=%s",
        b"G238 RP gd cx-probes=%d stage=%d rc=%d",
        b"G238 RP gd res=%lx-%lx waitrc=%d wait=%u torc=%d to=%u ns=%u par=%u",
        b"3d9106c.qcom,gdsc",
        b"3d9100c.qcom,gdsc",
        b"3d90000.qcom,gpucc",
        b"3d00000.qcom,kgsl-3d0",
        b"vdd_parent-supply",
        b"hw-ctl-addr",
        b"hw-ctrl-addr",
        b"KGPPOST",
        b"OFPOP enter",
        b"P3P enter n=%d dev=%s drv=%s",
        b"DISPINIT register enter modeset=%u",
        b"RSCC probe enter",
        b"A52GDSC GPU_CX_PROFILE_V1",
        b"A52GDSC GPU_GX_PROFILE_V2",
        b"A52ZAP 233 load",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase 238 Image marker: {marker.decode()}")
        print(f"Phase 238 Image marker present: {marker.decode()}")

    forbidden = (
        b"BOOT rs=ready phase=237 focus=ofpop-probe",
        b"BOOT rs=ready phase=236 focus=display-init",
    )
    for marker in forbidden:
        if marker in data:
            raise RuntimeError(f"forbidden stale runtime identity: {marker.decode()}")


def finalize(base_out: Path, source_run_id: int, source_run_url: str) -> Path:
    out = Path("phase238-out")
    shutil.rmtree(out, ignore_errors=True)
    base_out.rename(out)
    verify_phase238_image(out / "compile/Image")

    audit_dir = out / "audit/phase238"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "199_runtime_fix_binary_audit.py",
        "230_phase229_driver_core_wrapper.py",
        "235_phase234_rscc_master_overlay.py",
        "236_phase235_display_init_overlay.py",
        "237_phase236_ofpop_probe_overlay.py",
        "238_phase237_platform_include_preflight.py",
        "238_phase237_broad_gpu_supplier_overlay.py",
        "238_phase237_controlflow_safety_overlay.py",
        "237_package.py",
        "238_package.py",
    ):
        shutil.copy2(Path("scripts") / name, audit_dir / name)
    shutil.copy2("scripts/238_design.md", out / "PHASE238-DESIGN.md")
    shutil.copy2("scripts/238_trigger.txt", out / "PHASE238-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 238,
        "base_phase": 237,
        "functional_base_phase": 233,
        "hardware_validated": False,
        "status": "phase238-broad-gpu-supplier-recorder-ci-audited-not-hardware-validated",
        "phase238_graphics_behavior_changed": False,
        "phase238_provider_behavior_changed": False,
        "phase238_driver_core_behavior_changed": False,
        "phase238_recorder_behavior_changed": True,
        "phase238_platform_include_preflight": True,
        "phase238_controlflow_safety_pass": True,
        "phase238_recorder_transport": "Phase 210 R48 RS48 CRC32C unchanged",
        "phase238_focus": [
            "gpu_cx_gdsc@3d9106c",
            "gpu_gx_gdsc@3d9100c",
            "gpucc@3d90000",
            "kgsl-3d0@3d00000",
            "qfprom@780000",
            "CX-level RPMh suppliers and device-links",
        ],
        "phase238_recorder_filter_additions": ["G238*", "KGPPOST*"],
        "phase238_platform_event_limit": 768,
        "phase238_driver_core_event_limit": 768,
        "phase238_gdsc_property_limit": 40,
        "phase238_late_replay_ms": 145000,
        "phase238_records": [
            "focused platform probe entry/stage/return",
            "really_probe supplier list and supplier-check result",
            "GDSC regulator-name, compatible, MMIO resource",
            "all CX/GX DT property names up to bounded limit",
            "CX wait/timeout/no-status/parent/hw-control properties",
            "vdd_parent/hw-control phandle target and bound platform driver",
            "suspicious regulator/MMIO/syscon/property call checkpoints",
            "every custom GDSC probe return code",
            "late binding/supplier-chain replay",
            "existing Phase 230 KGPPOST direct/replay evidence",
            "platform include preflight for cumulative Phase 237 sources",
            "control-flow safety rewrite after broad instrumentation",
        ],
        "phase238_question": (
            "Why does 3d9106c.qcom,gdsc remain unbound: supplier gating before "
            "platform probe, driver mismatch, or an exact failure inside the "
            "Phase 233 gpu_cx_gdsc compatibility provider?"
        ),
        "phase238_touchgrass_reference_commit": TOUCHGRASS_COMMIT,
        "phase238_collector_compatibility": "OrangeFox R48 collector v3.2 unchanged",
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    identity = {
        "phase": 238,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": TOUCHGRASS_COMMIT,
        "hardware_validated": False,
        "functional_base_phase": 233,
        "change": "broad focused GPU supplier-chain recorder over Phase 237",
        "rs_roots": 48,
        "late_replay_ms": 145000,
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 238 broad GPU supplier-chain recorder candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Phase 238 preserves the Phase 210 R48 RS48 + CRC32C transport and all\n"
        "earlier graphics/OF/KGSL instrumentation. It adds broad but GPU-focused\n"
        "records around driver-core supplier gating, platform probe, the Phase 233\n"
        "legacy GDSC provider, DT/phandle/MMIO/regulator checkpoints, and a late\n"
        "145-second replay so early CX evidence survives recorder retention.\n\n"
        "Use the existing OrangeFox R48 collector v3.2 after one test boot.\n"
        "CI-validated only until hardware capture confirms behavior.\n"
    )

    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files)
    )
    return out


def main() -> int:
    p237 = load_phase237_package()
    base = p237.load_phase233_package()
    run_id, run_url, artifact_id = base.dispatch_and_wait()

    work = Path("phase238-work")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    zip_path = work / "inherited.zip"
    base.download_artifact(artifact_id, zip_path)
    extract = work / "inherited"
    extract.mkdir()
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract)
    root = base.locate_root(extract)
    base_out = base.package(root, run_id, run_url)
    finalize(base_out, run_id, run_url)
    print(f"Phase 238 package prepared from source run {run_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 238 packaging failed: {exc}", file=sys.stderr)
        raise
