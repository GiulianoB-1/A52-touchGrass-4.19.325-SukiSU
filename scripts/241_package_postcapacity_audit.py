#!/usr/bin/env python3
"""Finalize Phase 241 audit metadata after the inherited packager completes.

The inherited Phase 241 packager predates the CXF241 classification/retention
repair and still copies the Phase 240 hardware trigger. Keep its build/package
path stable, then add the repair script to the artifact audit bundle, replace
the stale trigger with the Phase 241 plan, pin the new guarantees in
final-audit.json, and regenerate SHA256SUMS so every delivered file is covered.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

OUT = Path("phase241-out")
REPAIR = Path("scripts/241_phase240_cxf241_postcapacity_repair.py")
TRIGGER = Path("scripts/241_trigger.txt")
AUDIT_REL = Path("audit/phase241/241_phase240_cxf241_postcapacity_repair.py")
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


def finalize(out: Path, repair: Path, trigger: Path) -> None:
    if not out.is_dir():
        raise RuntimeError(f"Phase 241 output directory missing: {out}")
    if not repair.is_file():
        raise RuntimeError(f"Phase 241 repair script missing: {repair}")
    if not trigger.is_file():
        raise RuntimeError(f"Phase 241 hardware trigger missing: {trigger}")

    required = (
        out / "package/boot.img",
        out / "compile/Image",
        out / "final-audit.json",
        out / "BUILD-IDENTITY.json",
    )
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Phase 241 required output missing/empty: {path}")

    audit_copy = out / AUDIT_REL
    audit_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repair, audit_copy)
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
        "phase241_hardware_plan_current": True,
        "phase241_retention_repair_behavioral_scope": "diagnostic persistence/classification only",
    })
    guardrails = list(audit.get("phase241_guardrails", []))
    for item in (
        "CXF241 late replay is admitted by the post-capacity critical classifier",
        "CXF241 create/dreg source records remain classifiable before replay",
        "a52_r241_replaying remains the replay recursion guard",
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

    delivered = (out / AUDIT_REL).read_text(encoding="utf-8")
    for token in (
        "A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1",
        "A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1",
        'return !strncmp(message, "CXF241 ", 7) ||',
    ):
        if token not in delivered:
            raise RuntimeError(f"delivered Phase 241 repair audit missing {token}")

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
        "Phase 241 package final audit: repair bundled; current hardware plan installed; metadata pinned; SHA256SUMS regenerated",
        flush=True,
    )


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        out = root / "phase241-out"
        repair = root / "repair.py"
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
        repair.write_text(
            "A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1\n"
            "A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1\n"
            "return !strncmp(message, \"CXF241 \", 7) ||\n",
            encoding="utf-8",
        )
        trigger.write_text(
            "PHASE 241 HARDWARE VALIDATION\n"
            "BOOT rs=ready phase=241 focus=cx-broad-corridor-latch\n"
            "CXF241 replay-begin\n"
            "CXF241 pop i=\nCXF241 drv i=\nCXF241 prb i=\nCXF241 sup i=\n",
            encoding="utf-8",
        )
        finalize(out, repair, trigger)
        audit = json.loads((out / "final-audit.json").read_text(encoding="utf-8"))
        if audit.get("phase241_cxf241_postcapacity_critical") is not True:
            raise AssertionError("post-capacity audit flag missing")
        if audit.get("phase241_hardware_plan_current") is not True:
            raise AssertionError("hardware-plan audit flag missing")
        if not (out / AUDIT_REL).is_file():
            raise AssertionError("repair script was not copied into audit bundle")
        if "PHASE 241 HARDWARE VALIDATION" not in (out / HARDWARE_PLAN_REL).read_text(encoding="utf-8"):
            raise AssertionError("Phase 241 hardware plan was not installed")
        if not (out / "SHA256SUMS").is_file():
            raise AssertionError("SHA256SUMS was not regenerated")
    print("Phase 241 package final audit self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    finalize(OUT, REPAIR, TRIGGER)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 241 package final audit failed: {exc}", file=sys.stderr)
        raise
