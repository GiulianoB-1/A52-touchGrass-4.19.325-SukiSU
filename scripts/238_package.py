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

BRANCH = "agent/a52-phase239-gpu-cx-vdd-parent-v1"
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


def verify_phase239_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=239 focus=gpu-cx-vdd-parent",
        b"A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=%d",
        b"A52GDSC CX_VDD_PARENT_VOTE_V1 name=%s rc=%d",
        b"A52GDSC CX_VDD_PARENT_UNVOTE_V1 name=%s rc=%d",
        b"A52GDSC CX_VDD_PARENT_STATE_V1 name=%s enabled=%d",
        b"vdd_parent-supply",
        b"vdd_parent",
        b"G238 P in dev=%s drv=%s node=%s",
        b"G238 D sup c=%s s=%s drv=%s st=%u fl=%u",
        b"G238 GD in dev=%s drv=%s rn=%s comp=%s cx=%u",
        b"G238 RP plat cx-att=%d stage=%d rc=%d drv=%s",
        b"G238 RP gd cx-probes=%d stage=%d rc=%d",
        b"3d9106c.qcom,gdsc",
        b"KGPPOST 230 cxw cand r=",
        b"KGPPOST 230 cxw match r=",
        b"KGPPOST 230 cxw probe r=",
        b"KGPPOST 230 cxw attach-in a=",
        b"KGPPOST 230 cxw attach-out rc=",
        b"A52GDSC GPU_CX_PROFILE_V1",
        b"A52GDSC GPU_GX_PROFILE_V2",
        b"A52ZAP 233 load",
        b"OFPOP enter",
        b"P3P enter n=%d dev=%s drv=%s",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase 239 Image marker: {marker.decode()}")
        print(f"Phase 239 Image marker present: {marker.decode()}")
    for marker in (
        b"BOOT rs=ready phase=238 focus=gpu-supplier-broad",
        b"BOOT rs=ready phase=237 focus=ofpop-probe",
    ):
        if marker in data:
            raise RuntimeError(f"forbidden stale runtime identity: {marker.decode()}")


def finalize(base_out: Path, source_run_id: int, source_run_url: str) -> Path:
    out = Path("phase239-out")
    shutil.rmtree(out, ignore_errors=True)
    base_out.rename(out)
    verify_phase239_image(out / "compile/Image")

    audit_dir = out / "audit/phase239"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "199_runtime_fix_binary_audit.py",
        "227_phase226_retention_wrapper.py",
        "230_phase229_driver_core_wrapper.py",
        "235_phase234_rscc_master_overlay.py",
        "236_phase235_display_init_overlay.py",
        "237_phase236_ofpop_probe_overlay.py",
        "238_phase237_platform_include_preflight.py",
        "238_phase237_broad_gpu_supplier_overlay.py",
        "238_phase237_gdsc_parent_diag_repair.py",
        "238_phase237_cx_journal_extension.py",
        "238_phase237_cx_driver_walk_extension.py",
        "238_phase237_controlflow_safety_overlay.py",
        "238_phase237_retention_replay_timing_repair.py",
        "238_phase237_c_indent_sanitize.py",
        "239_phase238_gpu_cx_vdd_parent_overlay.py",
        "239_phase238_identity_overlay.py",
        "237_package.py",
        "238_package.py",
    ):
        shutil.copy2(Path("scripts") / name, audit_dir / name)
    shutil.copy2("scripts/239_design.md", out / "PHASE239-DESIGN.md")
    shutil.copy2("scripts/239_trigger.txt", out / "PHASE239-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 239,
        "base_phase": 238,
        "functional_base_phase": 233,
        "hardware_validated": False,
        "status": "phase239-gpu-cx-vdd-parent-ci-audited-not-hardware-validated",
        "phase239_graphics_behavior_changed": True,
        "phase239_provider_behavior_changed": True,
        "phase239_driver_core_behavior_changed": False,
        "phase239_recorder_transport_changed": False,
        "phase239_gpu_cx_vdd_parent_restored": True,
        "phase239_parent_supply_ordering_retained": True,
        "phase239_low_svs_vote": "RPMH_REGULATOR_LEVEL_LOW_SVS",
        "phase239_vdd_parent_consumer": "vdd_parent",
        "phase239_target": "gpu_cx_gdsc@3d9106c",
        "phase239_touchgrass_reference_commit": TOUCHGRASS_COMMIT,
        "phase239_recorder_transport": "Phase 210 R48 RS48 CRC32C unchanged",
        "phase239_phase238_diagnostics_retained": True,
        "phase239_guardrails": [
            "no device link removed or bypassed",
            "no KGSL return code rewritten",
            "GX/UFS/MDSS profiles receive no vdd_parent handle and keep prior behavior",
            "vdd_parent acquisition preserves -EPROBE_DEFER",
            "parent-supply regulator-core ordering remains enabled",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    identity = {
        "phase": 239,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": TOUCHGRASS_COMMIT,
        "hardware_validated": False,
        "functional_base_phase": 233,
        "change": "restore Lagoon GPU CX vdd_parent acquisition and LOW_SVS vote semantics",
        "rs_roots": 48,
        "late_replay_ms": 155000,
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 239 GPU CX vdd_parent functional-fix candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Phase 239 restores the downstream Qualcomm vdd_parent-supply behavior\n"
        "missing from the Phase 233 gpu_cx_gdsc compatibility profile. It keeps\n"
        "parent-supply ordering, acquires the separate vdd_parent consumer, votes\n"
        "LOW_SVS while CX is enabled/accessed, unwinds failures, and retains all\n"
        "Phase 238 R48/RS48 diagnostics for hardware proof.\n\n"
        "CI-validated only until the next hardware capture confirms CX binding.\n",
        encoding="utf-8",
    )

    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )
    return out


def main() -> int:
    p237 = load_phase237_package()
    base = p237.load_phase233_package()
    run_id, run_url, artifact_id = base.dispatch_and_wait()

    work = Path("phase239-work")
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
    print(f"Phase 239 package prepared from source run {run_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 239 packaging failed: {exc}", file=sys.stderr)
        raise
