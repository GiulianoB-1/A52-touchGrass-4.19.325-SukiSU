#!/usr/bin/env python3
"""Phase 245 packaging revision 2.

The inherited artifact retains historical drivers/base/core.c snapshots from
older diagnostic phases.  They must not be presented as the live Phase 245
post-overlay source.  The live-source gate is instead the fail-closed Phase 245
overlay itself: the inherited builder cannot proceed unless it finds exactly one
FW_DEVLINK_FLAGS_ON declaration and replaces it with PERMISSIVE.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "245_package.py"


def load_base():
    spec = importlib.util.spec_from_file_location("phase245_package", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase 245 packager: {BASE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_source_audit(_out: Path) -> dict[str, object]:
    return {
        "packaged_core_candidates": [],
        "packaged_core_exact_permissive_matches": [],
        "packaged_core_snapshot_available": False,
        "reason": (
            "inherited package contains only historical core.c stage snapshots; "
            "live Phase245 source is validated fail-closed by "
            "245_phase243_fwdevlink_permissive_overlay.py in the builder"
        ),
        "overlay_required_old_declaration": "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_ON;",
        "overlay_required_new_declaration": "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;",
    }


def refresh_sums(base, out: Path) -> None:
    sums = out / "SHA256SUMS"
    sums.unlink(missing_ok=True)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    sums.write_text(
        "".join(f"{base.sha256(path)}  ./{path.relative_to(out)}\n" for path in files),
        encoding="utf-8",
    )


def main() -> int:
    base = load_base()
    base.find_generated_core_audit = live_source_audit
    rc = base.main()
    if rc:
        return rc
    out = Path("phase245-out")
    audit_dir = out / "audit/phase245"
    audit_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), audit_dir / Path(__file__).name)

    # Make the provenance explicit in final-audit as well.
    audit_path = out / "final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["phase245_packaging_revision"] = 2
    audit["phase245_live_source_gate"] = (
        "fail-closed exact declaration replacement in inherited builder; "
        "historical packaged core snapshots excluded"
    )
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    refresh_sums(base, out)
    print("Phase 245 packaging revision 2 audit correction applied", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 245 packaging r2 failed: {exc}", file=sys.stderr)
        raise
