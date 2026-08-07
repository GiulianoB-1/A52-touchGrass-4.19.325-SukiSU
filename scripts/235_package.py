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

BRANCH = "agent/a52-phase235-rscc-master-recorder-build-v1"
TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_phase233_package():
    path = Path(__file__).with_name("233_package.py")
    if not path.is_file():
        payload = Path(__file__).with_name("233_payload.py")
        if not payload.is_file():
            raise RuntimeError(f"missing Phase 233 payload: {payload}")
        import subprocess
        subprocess.run([sys.executable, str(payload)], check=True)
    spec = importlib.util.spec_from_file_location("phase233_package", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inherited packager: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REF = BRANCH
    return module


def verify_phase235_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=235 focus=rscc-master",
        b"RSCCFOCUS match path=device-attach",
        b"RSCCFOCUS match path=driver-attach",
        b"RSCC probe enter",
        b"RSCC component-add enter",
        b"RSCC bind enter",
        b"RSCC bind exit",
        b"DRMCOMP collect enter",
        b"DRMCOMP connectors prop=",
        b"DRMCOMP connector i=",
        b"DRMCOMP match-add i=",
        b"DRMCOMP master-add enter",
        b"DRMCOMP master-add exit",
        b"COMP master-add enter",
        b"COMP master-add result",
        b"COMP master stage=",
        b"COMP slot i=",
        b"COMP component-add enter",
        b"COMP component-add result",
        b"A52GDSC GPU_CX_PROFILE_V1",
        b"A52GDSC GPU_GX_PROFILE_V2",
        b"A52ZAP 233 load",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase 235 Image marker: {marker.decode()}")
        print(f"Phase 235 Image marker present: {marker.decode()}")

    forbidden = (
        b"BOOT rs=ready phase=234 focus=rscc",
        b"RSCCCORE match path=device-attach",
        b"RSCCCORE match path=driver-attach",
    )
    for marker in forbidden:
        if marker in data:
            raise RuntimeError(f"forbidden Phase 235 Image marker present: {marker.decode()}")


def finalize(base_out: Path, source_run_id: int, source_run_url: str) -> Path:
    out = Path("phase235-out")
    shutil.rmtree(out, ignore_errors=True)
    base_out.rename(out)
    verify_phase235_image(out / "compile/Image")

    audit_dir = out / "audit/phase235"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "230_phase229_driver_core_wrapper.py",
        "235_phase234_rscc_master_overlay.py",
        "235_package.py",
    ):
        shutil.copy2(Path("scripts") / name, audit_dir / name)
    shutil.copy2("scripts/235_design.md", out / "PHASE235-DESIGN.md")
    shutil.copy2("scripts/235_trigger.txt", out / "PHASE235-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 235,
        "base_phase": 234,
        "functional_base_phase": 233,
        "hardware_validated": False,
        "status": "phase235-rscc-component-master-recorder-ci-audited-not-hardware-validated",
        "phase235_display_behavior_changed": False,
        "phase235_graphics_provider_behavior_changed": False,
        "phase235_recorder_behavior_changed": True,
        "phase235_recorder_transport": "Phase 210 R48 RS48 CRC32C unchanged",
        "phase235_recorder_filter": [
            "RSCC*", "DRMCOMP*", "COMP *", "BOOT ctl=*", "BOOT rs=ready*"
        ],
        "phase235_failed_candidate_match_spam_suppressed": True,
        "phase235_real_sde_rsc_match_retained": True,
        "phase235_rscc_probe_stages_retained": True,
        "phase235_rscc_component_bind_retained": True,
        "phase235_drm_connectors_trace_retained": True,
        "phase235_component_master_trace_retained": True,
        "phase235_component_slot_trace_retained": True,
        "phase235_component_core_trace_scope": "SDE/DSI/MDP devices only",
        "phase235_question": "Does qcom,sde-rsc enter the DRM master match list and reach sde_rsc_bind?",
        "phase235_touchgrass_reference_commit": TOUCHGRASS_COMMIT,
        "phase235_collector_compatibility": "OrangeFox R48 collector v3.2 unchanged",
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    identity = {
        "phase": 235,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": TOUCHGRASS_COMMIT,
        "hardware_validated": False,
        "functional_base_phase": 233,
        "change": "RSCC DRM component-master recorder admission over Phase 234 focus",
        "rs_roots": 48,
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 235 RSCC component-master recorder candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND THE PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Phase 235 keeps the Phase 210 R48 RS48 + CRC32C transport exactly\n"
        "as inherited from Phase 234. It only widens persisted event admission\n"
        "from RSCC to the existing bounded DRM component traces.\n\n"
        "Persisted classes:\n"
        "  RSCC*    exact RSCC match/probe/component-add/bind\n"
        "  DRMCOMP* MDP connectors enumeration and DRM master registration\n"
        "  COMP *   component-core SDE/DSI/MDP master/slot/component state\n"
        "  BOOT ctl / BOOT rs=ready recorder control and identity\n\n"
        "The generic RSCCCORE candidate-driver flood remains forbidden. The\n"
        "capture should tell us whether sde_rscc is absent from the MDP\n"
        "connectors list, present but unmatched, blocked during master bring-up,\n"
        "or successfully reaches sde_rsc_bind().\n\n"
        "Use the existing OrangeFox R48 collector v3.2 after the test boot.\n"
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
    base = load_phase233_package()
    run_id, run_url, artifact_id = base.dispatch_and_wait()
    work = Path("phase235-work")
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
    print(f"Phase 235 package prepared from source run {run_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 235 packaging failed: {exc}", file=sys.stderr)
        raise
