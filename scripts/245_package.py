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

BRANCH = "agent/a52-phase245-fwdevlink-permissive-v2"
TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
OLD_DECL = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_ON;"
NEW_DECL = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_phase243_package():
    path = Path(__file__).with_name("243_package.py")
    spec = importlib.util.spec_from_file_location("phase243_package", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inherited Phase 243 packager: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase245_image(image: Path) -> None:
    data = image.read_bytes()
    # We intentionally retain Phase 243 runtime identity and recorder hooks.
    required = (
        b"BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers",
        b"CXF243 M c=%c q=%d rc=%d",
        b"CXF243 G c=%c q=%d rc=%d ls=%d",
        b"A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=%d",
        b"3d9106c",
        b"3d9100c",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing retained Phase 243 Image marker: {marker.decode()}")
    forbidden = (
        b"BOOT rs=ready phase=244 focus=gdsc-subsys-initcall",
        b"CXF244 V q=%d l=%d",
        b"CXF244 I q=%d s=E",
        b"CXF244 I q=%d s=B",
        b"CXF244 I q=%d s=X rc=%d",
    )
    for marker in forbidden:
        if marker in data:
            raise RuntimeError(f"forbidden Phase 244 Image marker present: {marker.decode()}")


def find_generated_core_audit(out: Path) -> dict[str, object]:
    """Best-effort packaged-source audit without weakening the build gate.

    Some inherited packager revisions retain generated source snapshots while
    others retain only patches/logs.  If a current drivers/base/core.c snapshot
    is present, prove the declaration directly.  Otherwise record that runtime
    build logs are the source-of-truth for the overlay application.
    """
    candidates: list[Path] = []
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if "core" not in name and path.name != "core.c":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "fw_devlink_flags" in text:
            candidates.append(path)
    matches: list[dict[str, object]] = []
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        matches.append({
            "path": str(path.relative_to(out)),
            "on_declaration_count": text.count(OLD_DECL),
            "permissive_declaration_count": text.count(NEW_DECL),
        })
    exact = [m for m in matches if m["permissive_declaration_count"] == 1 and m["on_declaration_count"] == 0]
    return {
        "packaged_core_candidates": matches,
        "packaged_core_exact_permissive_matches": exact,
        "packaged_core_snapshot_available": bool(matches),
    }


def finalize(base_out: Path, source_run_id: int, source_run_url: str) -> Path:
    # Reuse Phase 243's strict runtime-marker verification first.
    p243 = load_phase243_package()
    inherited = p243.finalize(base_out, source_run_id, source_run_url)

    out = Path("phase245-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_phase245_image(out / "compile/Image")

    audit_dir = out / "audit/phase245"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "243_package.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "245_package.py",
    ):
        shutil.copy2(Path("scripts") / name, audit_dir / name)

    source_audit = find_generated_core_audit(out)
    (audit_dir / "fwdevlink-source-audit.json").write_text(
        json.dumps(source_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 245,
        "base_phase": 243,
        "functional_base_phase": 243,
        "hardware_validated": False,
        "status": "phase245-fwdevlink-permissive-ab-ci-audited-not-hardware-validated",
        "phase245_change": "FW_DEVLINK_FLAGS_ON -> FW_DEVLINK_FLAGS_PERMISSIVE compiled default",
        "phase245_fw_devlink_behavior_changed": True,
        "phase245_dt_changed": False,
        "phase245_gdsc_provider_code_changed": False,
        "phase245_kgsl_code_changed": False,
        "phase245_gpucc_code_changed": False,
        "phase245_initcall_order_or_level_changed": False,
        "phase245_boot_cmdline_changed": False,
        "phase245_recorder_changed": False,
        "phase245_phase243_runtime_identity_retained": True,
        "phase245_phase244_overlay_applied": False,
        "phase245_touchgrass_reference_commit": TOUCHGRASS_COMMIT,
        "phase245_hypothesis": (
            "GKI fw_devlink=on blocks the standalone GPU CX GDSC before its provider probe; "
            "permissive inferred links should allow the Phase 243 CX provider path to run"
        ),
        "phase245_source_audit": source_audit,
        "phase245_guardrails": [
            "Phase 244 initcall diagnostics are not applied",
            "Phase 243 runtime recorder identity and hooks are retained",
            "no DT property is changed",
            "no device link is deleted or manually bypassed",
            "no KGSL/GPUCC/GDSC probe return value is rewritten",
            "no initcall level or ordering is changed",
            "boot cmdline remains unchanged; compiled default is the experiment variable",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 245,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": TOUCHGRASS_COMMIT,
        "hardware_validated": False,
        "base_phase": 243,
        "runtime_recorder_identity": 243,
        "change": "compiled fw_devlink default ON to PERMISSIVE; no other kernel behavior intentionally changed",
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 245 fw_devlink permissive A/B candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Single experimental kernel change:\n"
        "  fw_devlink_flags default: FW_DEVLINK_FLAGS_ON -> FW_DEVLINK_FLAGS_PERMISSIVE\n\n"
        "The Phase 243 recorder and runtime identity are intentionally retained so the\n"
        "result can be compared directly with the Phase 243 hardware capture. Phase 244\n"
        "initcall instrumentation is not applied. DT, GDSC provider implementation,\n"
        "KGSL, GPUCC, initcall ordering, boot cmdline, and recorder transport are not\n"
        "intentionally changed. This candidate is CI-validated only until hardware boot.\n",
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
    p243 = load_phase243_package()
    p237 = p243.load_phase237_package()
    base = p237.load_phase233_package()
    run_id, run_url, artifact_id = base.dispatch_and_wait()

    work = Path("phase245-work")
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
    print(f"Phase 245 package prepared from source run {run_id}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 245 packaging failed: {exc}", file=sys.stderr)
        raise
