#!/usr/bin/env python3
"""Phase 240 compatibility entrypoint for the post-sanitize generated DD tree.

The Phase 240 base overlay was authored after the Phase 238 indentation sanitizer
in the cumulative order, but its two whole-function driver_attach templates kept
literal ``\\t`` escape pairs.  The generated drivers/base/dd.c contains real tab
characters by that point.  Normalize only those two template constants before
running the base overlay; no generated-kernel behavior, match result, probe
return, supplier state, device link, or recorder semantics are changed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "240_phase239_cx_frozen_latch_overlay.py"
# The generated-tree locator remains owned by BASE and searches gki/common.


def load_base():
    if not BASE.is_file():
        raise RuntimeError(f"missing Phase 240 base overlay: {BASE}")
    spec = importlib.util.spec_from_file_location("phase240_cx_frozen_latch_base", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase 240 base overlay: {BASE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_driver_walk_templates(module) -> None:
    for name in ("DRIVER_WALK_OLD", "DRIVER_WALK_NEW"):
        value = getattr(module, name, None)
        if not isinstance(value, str):
            raise RuntimeError(f"Phase 240 base overlay missing string template {name}")
        if "\\t" not in value:
            raise RuntimeError(
                f"Phase 240 {name} no longer has the expected literal-tab form; "
                "remove this compatibility entrypoint and update the base overlay"
            )
        normalized = value.replace("\\t", "\t")
        if "\\t" in normalized:
            raise RuntimeError(f"Phase 240 {name} still contains literal tab escapes")
        setattr(module, name, normalized)


def self_test(module) -> int:
    old_before = module.DRIVER_WALK_OLD
    new_before = module.DRIVER_WALK_NEW
    normalize_driver_walk_templates(module)
    if module.DRIVER_WALK_OLD == old_before or module.DRIVER_WALK_NEW == new_before:
        raise AssertionError("Phase 240 driver-walk templates were not normalized")
    if "\tret = bus_for_each_dev(drv->bus, NULL, drv, __driver_attach);" not in module.DRIVER_WALK_OLD:
        raise AssertionError("Phase 240 normalized old driver_attach template is malformed")
    if 'CXF240 drvwalk-in r=%.24s bus=%.16s' not in module.DRIVER_WALK_NEW:
        raise AssertionError("Phase 240 normalized new driver_attach template lost entry marker")
    if 'CXF240 drvwalk-out r=%.24s rc=%d' not in module.DRIVER_WALK_NEW:
        raise AssertionError("Phase 240 normalized new driver_attach template lost exit marker")

    # The base self-test now constructs dd_fixture from the normalized templates,
    # exactly matching the real-tab representation produced by the Phase 238
    # indentation sanitizer in the cumulative build.
    module.self_test()
    print(
        "Phase 240 post-sanitize driver-walk compatibility self-test: PASS",
        flush=True,
    )
    return 0


def main() -> int:
    module = load_base()
    if "--self-test" in sys.argv[1:]:
        return self_test(module)
    normalize_driver_walk_templates(module)
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
