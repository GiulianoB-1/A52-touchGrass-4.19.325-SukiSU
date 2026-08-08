#!/usr/bin/env python3
from __future__ import annotations
import hashlib, importlib.util, json, os, shutil, sys, zipfile
from pathlib import Path

BRANCH = "agent/a52-phase239-cx-vdd-parent-fix-v1"
TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_phase238_package():
    path = Path(__file__).with_name("238_package.py")
    spec = importlib.util.spec_from_file_location("phase238_package", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase239_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=239 focus=cx-vdd-parent-fix",
        b"A52GDSC CX_VDD_PARENT get rc=%d",
        b"A52GDSC CX_VDD_PARENT get rc=0",
        b"A52GDSC CX_VDD_PARENT enable-vote rc=%d",
        b"A52GDSC CX_VDD_PARENT disable-unvote rc=%d",
        b"A52GDSC CX_VDD_PARENT is-enabled parent=%d",
        b"A52GDSC CX_VDD_PARENT is-enabled=%u reg=0x%x",
        b"G238 RP plat cx-att=%d stage=%d rc=%d drv=%s",
        b"KGPPOST 230 cxw cand r=",
        b"A52GDSC GPU_CX_PROFILE_V1",
        b"A52GDSC GPU_GX_PROFILE_V2",
        b"A52ZAP 233 load",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase 239 Image marker: {marker.decode()}")
        print(f"Phase 239 Image marker present: {marker.decode()}")
    if b"BOOT rs=ready phase=238 focus=gpu-supplier-broad" in data:
        raise RuntimeError("stale Phase 238 boot identity remains in Phase 239 Image")


def finalize(base_out: Path, source_run_id: int, source_run_url: str) -> Path:
    p238 = load_phase238_package()
    original_verify = p238.verify_phase238_image
    p238.verify_phase238_image = lambda image: verify_phase239_image(image)
    try:
        out238 = p238.finalize(base_out, source_run_id, source_run_url)
    finally:
        p238.verify_phase238_image = original_verify

    out = Path("phase239-out")
    shutil.rmtree(out, ignore_errors=True)
    out238.rename(out)
    verify_phase239_image(out / "compile/Image")

    audit_dir = out / "audit/phase239"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "239_phase238_cx_vdd_parent_fix.py",
        "239_phase238_runtime_identity.py",
        "239_package.py",
        "227_phase226_retention_wrapper.py",
        "238_package.py",
    ):
        shutil.copy2(Path("scripts") / name, audit_dir / name)
    for src, dst in (
        ("scripts/239_design.md", "PHASE239-DESIGN.md"),
        ("scripts/239_trigger.txt", "PHASE239-HARDWARE-TEST.txt"),
    ):
        shutil.copy2(src, out / dst)

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 239,
        "base_phase": 238,
        "functional_base_phase": 239,
        "hardware_validated": False,
        "status": "phase239-cx-vdd-parent-fix-ci-audited-not-hardware-validated",
        "phase239_graphics_behavior_changed": True,
        "phase239_provider_behavior_changed": True,
        "phase239_driver_core_behavior_changed": False,
        "phase239_recorder_behavior_changed": True,
        "phase239_scope": "gpu_cx_gdsc vdd_parent-supply only",
        "phase239_cx_vdd_parent_acquire": True,
        "phase239_cx_low_svs_vote": 64,
        "phase239_cx_is_enabled_parent_guard": True,
        "phase239_cx_enable_vote_retained_until_disable": True,
        "phase239_cx_disable_unvote": True,
        "phase239_parent_supply_ordering_retained": True,
        "phase239_phase238_tracing_retained": True,
        "phase239_recorder_transport": "Phase 210 R48 RS48 CRC32C unchanged",
        "phase239_touchgrass_reference_commit": TOUCHGRASS_COMMIT,
        "phase239_question": (
            "Does restoring the exact TouchGrass vdd_parent contract let "
            "3d9106c.qcom,gdsc bind and release KGSL from -EPROBE_DEFER?"
        ),
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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
        "functional_base_phase": 239,
        "change": "restore exact gpu_cx_gdsc vdd_parent-supply parent-rail semantics",
        "rs_roots": 48,
        "late_replay_ms": 155000,
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 239 GPU CX vdd_parent fix candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Phase 239 restores the missing TouchGrass vdd_parent-supply contract for\n"
        "gpu_cx_gdsc@3d9106c while preserving parent-supply ordering, GPU GX, UFS,\n"
        "MDSS, the Phase 210 R48/RS48/CRC32C transport, and all Phase 238 tracing.\n"
        "The CX provider now acquires vdd_parent, applies the exact LOW_SVS vote,\n"
        "guards is_enabled register access with the parent rail, retains a successful\n"
        "enable vote until disable, and removes that vote on disable/error.\n\n"
        "CI-validated only until the phone confirms CX binding and KGSL progress.\n",
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
    p238 = load_phase238_package()
    p237 = p238.load_phase237_package()
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
