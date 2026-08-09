#!/usr/bin/env python3
"""Finalize Phase 241 audit metadata after the inherited packager completes.

The inherited Phase 241 packager predates the CXF241 classification/retention
and C90 compile-shape repairs and still copies the Phase 240 hardware trigger.
Keep its build/package path stable, then add both Phase 241 repair scripts to
the artifact audit bundle, replace the stale trigger with the Phase 241 plan,
pin the guarantees in final-audit.json, and regenerate SHA256SUMS so every
delivered file is covered.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

OUT = Path("phase241-out")
RETENTION_REPAIR = Path("scripts/241_phase240_cxf241_postcapacity_repair.py")
COMPILE_REPAIR = Path("scripts/241_phase240_compile_shape_repair.py")
TRIGGER = Path("scripts/241_trigger.txt")
RETENTION_AUDIT_REL = Path("audit/phase241/241_phase240_cxf241_postcapacity_repair.py")
COMPILE_AUDIT_REL = Path("audit/phase241/241_phase240_compile_shape_repair.py")
HARDWARE_PLAN_REL = Path("PHASE241-HARDWARE-TEST.txt")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regenerate_sums(out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def finalize(out: Path, retention_repair: Path, compile_repair: Path, trigger: Path) -> None:
    if not out.is_dir():
        raise RuntimeError(f"Phase 241 output directory missing: {out}")
    for label, path in (
        ("classification/retention repair", retention_repair),
        ("compile-shape repair", compile_repair),
        ("hardware trigger", trigger),
    ):
        if not path.is_file():
            raise RuntimeError(f"Phase 241 {label} missing: {path}")

    required = (
        out / "package/boot.img",
        out / "compile/Image",
        out / "final-audit.json",
        out / "BUILD-IDENTITY.json",
    )
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Phase 241 required output missing/empty: {path}")

    retention_copy = out / RETENTION_AUDIT_REL
    compile_copy = out / COMPILE_AUDIT_REL
    retention_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(retention_repair, retention_copy)
    shutil.copy2(compile_repair, compile_copy)
    shutil.copy2(trigger, out / HARDWARE_PLAN_REL)

    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("phase") != 241 or audit.get("base_phase") != 240:
        raise RuntimeError(
            "Phase 241 final-audit identity mismatch: "
            f"phase={audit.get('phase')} base={audit.get('base_phase')}"
        )
    if audit.get("hardware_validated") is not False:
        raise RuntimeError("Phase 241 artifact must remain hardware_validated=false")

    audit.update({
        "phase241_cxf241_source_classification_repaired": True,
        "phase241_cxf241_postcapacity_critical": True,
        "phase241_phase240_replay_retention_hole_closed": True,
        "phase241_replay_recursion_guard_retained": True,
        "phase241_c90_declaration_order_repaired": True,
        "phase241_phase240_replay_helper_maybe_unused": True,
        "phase241_compile_shape_repair_behavioral_scope": "compile-shape only; runtime decisions unchanged",
        "phase241_hardware_plan_current": True,
        "phase241_retention_repair_behavioral_scope": "diagnostic persistence/classification only",
    })
    guardrails = list(audit.get("phase241_guardrails", []))
    for item in (
        "CXF241 late replay is admitted by the post-capacity critical classifier",
        "CXF241 create/dreg source records remain classifiable before replay",
        "a52_r241_replaying remains the replay recursion guard",
        "Phase 241 create/dreg logging follows pre-existing C declarations",
        "superseded Phase 240 replay helper is retained __maybe_unused without restoring its heartbeat call",
        "delivered hardware plan requires Phase 241 runtime identity before interpretation",
    ):
        if item not in guardrails:
            guardrails.append(item)
    audit["phase241_guardrails"] = guardrails
    audit_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    regenerate_sums(out)

    delivered_retention = retention_copy.read_text(encoding="utf-8")
    for token in (
        "A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1",
        "A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1",
        'return !strncmp(message, "CXF241 ", 7) ||',
    ):
        if token not in delivered_retention:
            raise RuntimeError(f"delivered Phase 241 retention audit missing {token}")

    delivered_compile = compile_copy.read_text(encoding="utf-8")
    for token in (
        "A52_PHASE241_R240_REPLAY_MAYBE_UNUSED_V1",
        "A52_PHASE241_OF_DECLARATION_ORDER_V1",
        "A52_PHASE241_DRIVER_DECLARATION_ORDER_V1",
        "__maybe_unused a52_r240_cxf_replay",
    ):
        if token not in delivered_compile:
            raise RuntimeError(f"delivered Phase 241 compile-shape audit missing {token}")

    hardware_plan = (out / HARDWARE_PLAN_REL).read_text(encoding="utf-8")
    for token in (
        "PHASE 241 HARDWARE VALIDATION",
        "BOOT rs=ready phase=241 focus=cx-broad-corridor-latch",
        "CXF241 replay-begin",
        "CXF241 pop i=",
        "CXF241 drv i=",
        "CXF241 prb i=",
        "CXF241 sup i=",
    ):
        if token not in hardware_plan:
            raise RuntimeError(f"delivered Phase 241 hardware plan missing {token}")
    if "PHASE 240 HARDWARE VALIDATION" in hardware_plan:
        raise RuntimeError("stale Phase 240 hardware plan survived Phase 241 finalization")

    print(
        "Phase 241 package final audit: retention + compile-shape repairs bundled; current hardware plan installed; metadata pinned; SHA256SUMS regenerated",
        flush=True,
    )


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        out = root / "phase241-out"
        retention = root / "retention.py"
        compile_repair = root / "compile.py"
        trigger = root / "241_trigger.txt"
        (out / "package").mkdir(parents=True)
        (out / "compile").mkdir(parents=True)
        (out / "package/boot.img").write_bytes(b"boot")
        (out / "compile/Image").write_bytes(b"image")
        (out / "BUILD-IDENTITY.json").write_text("{}\n", encoding="utf-8")
        (out / "final-audit.json").write_text(
            json.dumps({
                "phase": 241,
                "base_phase": 240,
                "hardware_validated": False,
                "phase241_guardrails": [],
            }) + "\n",
            encoding="utf-8",
        )
        (out / HARDWARE_PLAN_REL).write_text(
            "PHASE 240 HARDWARE VALIDATION\n",
            encoding="utf-8",
        )
        retention.write_text(
            "A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1\n"
            "A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1\n"
            "return !strncmp(message, \"CXF241 \", 7) ||\n",
            encoding="utf-8",
        )
        compile_repair.write_text(
            "A52_PHASE241_R240_REPLAY_MAYBE_UNUSED_V1\n"
            "A52_PHASE241_OF_DECLARATION_ORDER_V1\n"
            "A52_PHASE241_DRIVER_DECLARATION_ORDER_V1\n"
            "__maybe_unused a52_r240_cxf_replay\n",
            encoding="utf-8",
        )
        trigger.write_text(
            "PHASE 241 HARDWARE VALIDATION\n"
            "BOOT rs=ready phase=241 focus=cx-broad-corridor-latch\n"
            "CXF241 replay-begin\n"
            "CXF241 pop i=\nCXF241 drv i=\nCXF241 prb i=\nCXF241 sup i=\n",
            encoding="utf-8",
        )
        finalize(out, retention, compile_repair, trigger)
        audit = json.loads((out / "final-audit.json").read_text(encoding="utf-8"))
        if audit.get("phase241_cxf241_postcapacity_critical") is not True:
            raise AssertionError("post-capacity audit flag missing")
        if audit.get("phase241_c90_declaration_order_repaired") is not True:
            raise AssertionError("compile-shape audit flag missing")
        if audit.get("phase241_hardware_plan_current") is not True:
            raise AssertionError("hardware-plan audit flag missing")
        if not (out / RETENTION_AUDIT_REL).is_file():
            raise AssertionError("retention repair was not copied into audit bundle")
        if not (out / COMPILE_AUDIT_REL).is_file():
            raise AssertionError("compile-shape repair was not copied into audit bundle")
        if "PHASE 241 HARDWARE VALIDATION" not in (out / HARDWARE_PLAN_REL).read_text(encoding="utf-8"):
            raise AssertionError("Phase 241 hardware plan was not installed")
        if not (out / "SHA256SUMS").is_file():
            raise AssertionError("SHA256SUMS was not regenerated")
    print("Phase 241 package final audit self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    finalize(OUT, RETENTION_REPAIR, COMPILE_REPAIR, TRIGGER)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 241 package final audit failed: {exc}", file=sys.stderr)
        raise
