#!/usr/bin/env python3
"""Build/package Phase246 over the Phase245 permissive functional state."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase246-subsys-initcall-corridor-v1"
HERE = Path(__file__).resolve().parent
PHASE245 = HERE / "245_package.py"


def load_phase245():
    spec = importlib.util.spec_from_file_location("phase245_package_for_246", PHASE245)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase245 packager: {PHASE245}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    module.find_generated_core_audit = live_source_audit
    return module


def live_source_audit(_out: Path) -> dict[str, object]:
    return {
        "packaged_core_candidates": [],
        "packaged_core_exact_permissive_matches": [],
        "packaged_core_snapshot_available": False,
        "reason": (
            "inherited package retains historical core.c stage snapshots; live source is "
            "validated fail-closed by the Phase245 exact ON->PERMISSIVE overlay and the "
            "Phase246 generated-tree locator requires the PERMISSIVE declaration"
        ),
        "phase245_required_declaration": (
            "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"
        ),
    }


def verify_phase246_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers",
        b"CXF243 M c=%c q=%d rc=%d",
        b"CXF243 G c=%c q=%d rc=%d ls=%d",
        b"CXF246 V q=%d l=%d",
        b"CXF246 S n=%d f=%ps",
        b"CXF246 X q=%d n=%d",
        b"A52GDSC driver-register enter",
        b"A52GDSC driver-register exit rc=%d",
        b"A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=%d",
        b"3d9106c",
        b"3d9100c",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase246 Image marker: {marker.decode()}")
    forbidden = (
        b"BOOT rs=ready phase=244 focus=gdsc-subsys-initcall",
        b"CXF244 V q=%d l=%d",
        b"CXF244 I q=%d s=E",
        b"CXF244 I q=%d s=B",
        b"CXF244 I q=%d s=X rc=%d",
    )
    for marker in forbidden:
        if marker in data:
            raise RuntimeError(f"forbidden Phase244 marker in Phase246 Image: {marker.decode()}")


def refresh_sums(base, out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{base.sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(base, inherited: Path) -> Path:
    out = Path("phase246-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_phase246_image(out / "compile/Image")

    audit_dir = out / "audit/phase246"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "243_phase242_cxgx_live_supplier_overlay.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "245_package.py",
        "245_package_r2.py",
        "246_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    if (HERE / "246_design.md").is_file():
        shutil.copy2(HERE / "246_design.md", out / "PHASE246-DESIGN.md")
    if (HERE / "246_trigger.txt").is_file():
        shutil.copy2(HERE / "246_trigger.txt", out / "PHASE246-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 246,
        "base_phase": 245,
        "functional_base_phase": 245,
        "runtime_recorder_identity": 243,
        "hardware_validated": False,
        "status": "phase246-subsys-initcall-corridor-ci-audited-not-hardware-validated",
        "phase246_phase245_fwdevlink_permissive_retained": True,
        "phase246_phase243_cxgx_hooks_retained": True,
        "phase246_phase244_overlay_applied": False,
        "phase246_initcall_order_or_return_changed": False,
        "phase246_provider_behavior_changed": False,
        "phase246_dt_changed": False,
        "phase246_boot_cmdline_changed": False,
        "phase246_recorder_transport_changed": False,
        "phase246_new_runtime_records": [
            "CXF246 V q=<0..2> l=4 at subsys level entry",
            "CXF246 S n=<index> f=<symbol> immediately before each level-4 initcall",
            "CXF246 X q=<0..2> n=<count> only if all level-4 initcalls return",
        ],
        "phase246_hardware_question": (
            "Which exact level-4 initcall is the last one entered after Phase245 OFPOP exit, "
            "and does execution reach a52_legacy_gdsc_init/platform_driver_register?"
        ),
        "phase246_phase245_hardware_evidence": {
            "capture": "A52_RAW_RAMOOPS_20260810_094102.zip",
            "decoder": "Phase210+ R48 RS48 transport fusion",
            "valid_contiguous_sequences": 254,
            "sequence_range": "1-254",
            "last_record": "OFPOP exit rc=0 at 576ms",
            "cx_f243_records": 0,
            "a52gdsc_runtime_records": 0,
        },
        "phase246_guardrails": [
            "FW_DEVLINK_FLAGS_PERMISSIVE from Phase245 remains the functional state",
            "Phase244 source overlay is not applied",
            "no initcall is skipped, reordered, retried, or return-code rewritten",
            "only level 4 is traced",
            "one logical pre-call record per subsys initcall; R48/RS48 physical transport unchanged",
            "existing Phase243 CX/GX match, supplier-gate, and provider-entry hooks remain live",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 246,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 245,
        "runtime_recorder_identity": 243,
        "change": (
            "diagnostic-only subsys level-4 initcall pre-call symbol corridor; "
            "Phase245 fw_devlink permissive retained"
        ),
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 246 subsys-initcall corridor candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Functional state is unchanged from Phase245: fw_devlink defaults to PERMISSIVE.\n"
        "Phase246 adds diagnostic-only CXF246 records at initcall level 4. The critical\n"
        "record is `CXF246 S n=<index> f=<symbol>` immediately before each subsys\n"
        "initcall. If boot freezes inside one initcall, the final S record identifies\n"
        "the exact function entered. Existing A52GDSC and CXF243 hooks remain live.\n"
        "Phase244 is not applied. R48/RS48/CRC32C transport is unchanged.\n",
        encoding="utf-8",
    )

    refresh_sums(base, out)
    return out


def main() -> int:
    base = load_phase245()
    rc = base.main()
    if rc:
        return rc
    inherited = Path("phase245-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase245 inherited package output missing")
    finalize(base, inherited)
    print("Phase 246 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 246 packaging failed: {exc}", file=sys.stderr)
        raise
