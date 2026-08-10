#!/usr/bin/env python3
"""Build/package Phase247 over the Phase246 diagnostic state."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BRANCH = "agent/a52-phase247-camcc-dense-hws-v1"
HERE = Path(__file__).resolve().parent
PHASE246 = HERE / "246_package.py"


def load_phase246():
    spec = importlib.util.spec_from_file_location("phase246_package_for_247", PHASE246)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase246 packager: {PHASE246}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BRANCH = BRANCH
    return module


def verify_phase247_image(image: Path) -> None:
    data = image.read_bytes()
    required = (
        b"BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers",
        b"CXF243 M c=%c q=%d rc=%d",
        b"CXF243 G c=%c q=%d rc=%d ls=%d",
        b"CXF246 V q=%d l=%d",
        b"CXF246 S n=%d f=%ps",
        b"CXF246 X q=%d n=%d",
        b"cam_cc_pll2_out_early",
        b"A52GDSC driver-register enter",
        b"A52GDSC driver-register exit rc=%d",
        b"3d9106c",
        b"3d9100c",
    )
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"missing Phase247 inherited Image marker: {marker.decode()}")
    forbidden = (
        b"BOOT rs=ready phase=244 focus=gdsc-subsys-initcall",
        b"CXF244 V q=%d l=%d",
        b"CXF244 I q=%d s=E",
        b"CXF244 I q=%d s=B",
        b"CXF244 I q=%d s=X rc=%d",
    )
    for marker in forbidden:
        if marker in data:
            raise RuntimeError(f"forbidden Phase244 marker in Phase247 Image: {marker.decode()}")


def refresh_sums(base, out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{base.sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(phase246, inherited: Path) -> Path:
    out = Path("phase247-out")
    shutil.rmtree(out, ignore_errors=True)
    inherited.rename(out)
    verify_phase247_image(out / "compile/Image")

    audit_dir = out / "audit/phase247"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "246_package.py",
        "247_package.py",
    ):
        shutil.copy2(HERE / name, audit_dir / name)

    if (HERE / "247_design.md").is_file():
        shutil.copy2(HERE / "247_design.md", out / "PHASE247-DESIGN.md")
    if (HERE / "247_trigger.txt").is_file():
        shutil.copy2(HERE / "247_trigger.txt", out / "PHASE247-HARDWARE-TEST.txt")

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "phase": 247,
        "base_phase": 246,
        "functional_base_phase": 245,
        "runtime_recorder_identity": 243,
        "hardware_validated": False,
        "status": "phase247-camcc-dense-clk-hws-ci-audited-not-hardware-validated",
        "phase247_phase245_fwdevlink_permissive_retained": True,
        "phase247_phase246_subsys_recorder_retained": True,
        "phase247_phase243_cxgx_hooks_retained": True,
        "phase247_phase244_overlay_applied": False,
        "phase247_change_scope": "camcc-lagoon generated auxiliary clk_hws sparse-to-dense conversion only",
        "phase247_global_qcom_common_changed": False,
        "phase247_camcc_regulator_gets_restored": False,
        "phase247_camcc_vdd_classes_restored": False,
        "phase247_camcc_pll_configuration_changed": False,
        "phase247_dt_changed": False,
        "phase247_boot_cmdline_changed": False,
        "phase247_recorder_transport_changed": False,
        "phase247_hardware_question": (
            "Does cam_cc_lagoon_init return after replacing the TouchGrass sparse hwclks binding array "
            "with the dense GKI 5.10 clk_hws registration list, allowing CXF246 n=82+ to appear?"
        ),
        "phase247_phase246_hardware_evidence": {
            "capture": "A52_RAW_RAMOOPS_20260810_104624.zip",
            "decoder": "Phase210+ R48 RS48 transport fusion",
            "current_sequence_range": "1-339",
            "last_current_record": "CXF246 S n=81 f=cam_cc_lagoon_init at 616ms",
            "next_initcall_record": "absent",
            "a52gdsc_before_camcc": "absent",
            "cx_gdsc_before_camcc": "device-created-only; no current provider bind evidence",
            "late_high_sequence_kgsl_records": "stale retained block; not current-boot continuation",
        },
        "phase247_static_root_cause_candidate": {
            "touchgrass_hw_array": "sparse binding-indexed hwclks with populated ID 6",
            "touchgrass_common_behavior": "qcom_cc_really_probe skips NULL hwclks entries",
            "gki_phase54_port": "field rename hwclks->clk_hws retained sparse designated index",
            "gki_510_common_behavior": "iterates every clk_hws entry without NULL skip",
            "failure_mechanism": "NULL clk_hws[0] can reach devm_clk_hw_register -> clk_hw_register -> __clk_register -> hw->init",
            "phase247_fix": "one-element dense clk_hws list containing cam_cc_pll2_out_early.hw",
        },
        "phase247_guardrails": [
            "Phase245 FW_DEVLINK_FLAGS_PERMISSIVE remains unchanged",
            "Phase246 CXF246 subsys initcall recorder remains unchanged",
            "Phase243 CX/GX supplier/provider hooks remain unchanged",
            "Phase244 remains skipped",
            "no qcom common.c framework patch",
            "no CAMCC regulator/VDD/cal_l/PLL behavior change",
            "no DT or boot cmdline change",
        ],
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity = {
        "phase": 247,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "hardware_validated": False,
        "functional_base_phase": 245,
        "diagnostic_base_phase": 246,
        "runtime_recorder_identity": 243,
        "change": (
            "single-variable CAMCC compatibility correction: convert TouchGrass sparse hwclks ID-6 array "
            "to a dense one-entry GKI 5.10 clk_hws registration list"
        ),
    }
    (out / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    (out / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 247 CAMCC dense-clk_hws compatibility candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Phase247 retains Phase245 fw_devlink=PERMISSIVE and the Phase246 CXF246 subsys recorder.\n"
        "The only Phase247 functional change is in generated drivers/clk/qcom/camcc-lagoon.c:\n"
        "TouchGrass's sparse [CAM_CC_PLL2_OUT_EARLY] hwclks array is converted to a dense\n"
        "one-entry GKI clk_hws list. No CAMCC VDD/regulator/PLL behavior is changed.\n"
        "The decisive hardware result is whether CXF246 advances beyond n=81 cam_cc_lagoon_init.\n",
        encoding="utf-8",
    )

    refresh_sums(phase246, out)
    return out


def main() -> int:
    phase246 = load_phase246()
    rc = phase246.main()
    if rc:
        return rc
    inherited = Path("phase246-out")
    if not inherited.is_dir():
        raise RuntimeError("Phase246 inherited package output missing")
    finalize(phase246, inherited)
    print("Phase 247 package prepared", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 247 packaging failed: {exc}", file=sys.stderr)
        raise
