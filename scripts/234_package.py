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

BRANCH = "agent/a52-phase234-rscc-focused-recorder-v1"
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


def verify_focused_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=234 focus=rscc",
        b"RSCCFOCUS match path=device-attach",
        b"RSCCFOCUS match path=driver-attach",
        b"RSCC probe enter",
        b"RSCC probe stage=power-init",
        b"RSCC probe stage=rpmh-link",
        b"RSCC component-add enter",
        b"RSCC bind enter",
        b"A52GDSC GPU_CX_PROFILE_V1",
        b"A52GDSC GPU_GX_PROFILE_V2",
        b"A52ZAP 233 load",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase 234 Image marker: {marker.decode()}")
        print(f"Phase 234 Image marker present: {marker.decode()}")
    forbidden = (
        b"RSCCCORE match path=device-attach",
        b"RSCCCORE match path=driver-attach",
    )
    for marker in forbidden:
        if marker in data:
            raise RuntimeError(f"broad candidate-match marker remained: {marker.decode()}")


def finalize(base_out: Path, source_run_id: int, source_run_url: str) -> Path:
    out = Path("phase234-out")
    shutil.rmtree(out, ignore_errors=True)
    base_out.rename(out)
    verify_focused_image(out / "compile/Image")

    audit_dir = out / "audit/phase234"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in ("227_phase226_retention_wrapper.py", "234_package.py"):
        shutil.copy2(Path("scripts") / name, audit_dir / name)
    shutil.copy2("scripts/234_design.md", out / "PHASE234-DESIGN.md")
    shutil.copy2("scripts/234_trigger.txt", out / "PHASE234-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 234,
        "base_phase": 233,
        "functional_base_phase": 233,
        "hardware_validated": False,
        "status": "phase234-rscc-focused-recorder-ci-audited-not-hardware-validated",
        "phase234_display_behavior_changed": False,
        "phase234_graphics_provider_behavior_changed": False,
        "phase234_recorder_behavior_changed": True,
        "phase234_recorder_transport": "Phase 210 R48 RS48 CRC32C unchanged",
        "phase234_recorder_filter": ["RSCC*", "BOOT ctl=*", "BOOT rs=ready*"],
        "phase234_failed_candidate_match_spam_suppressed": True,
        "phase234_real_sde_rsc_match_retained": True,
        "phase234_rscc_probe_stages_retained": True,
        "phase234_rscc_component_bind_retained": True,
        "phase234_touchgrass_static_comparison": "no RSC driver or DT integration mismatch found",
        "phase234_touchgrass_compatible": "qcom,sde-rsc",
        "phase234_touchgrass_driver": "sde_rsc",
        "phase234_touchgrass_dt_node": "qcom,sde_rscc",
        "phase234_phase233_graphics_parity_retained": True,
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    identity = {
        "phase": 234,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": TOUCHGRASS_COMMIT,
        "hardware_validated": False,
        "functional_base_phase": 233,
        "change": "RSCC-focused RS48 recorder after TouchGrass static parity comparison",
        "rs_roots": 48,
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 234 RSCC-focused recorder candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND THE PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Static comparison against exact TouchGrass commit\n"
        f"{TOUCHGRASS_COMMIT} found the same qcom,sde-rsc compatible,\n"
        "sde_rsc driver, probe resources, clock names and built objects.\n"
        "No direct parity fix was found.\n\n"
        "Phase 234 retains the Phase 210 R48 RS48 + CRC32C wire format,\n"
        "but persists only RSCC and recorder-control events. Failed\n"
        "unrelated platform-driver candidates are suppressed; the actual\n"
        "sde_rsc match, supplier gate, probe stages, component_add, bind\n"
        "and deferred-probe result remain recorded.\n\n"
        "The Phase 233 graphics/provider payload and stock boot layout are\n"
        "unchanged. CI-validated, not hardware-validated.\n"
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
    work = Path("phase234-work")
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
    print(f"Phase 234 package prepared from source run {run_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 234 packaging failed: {exc}", file=sys.stderr)
        raise
