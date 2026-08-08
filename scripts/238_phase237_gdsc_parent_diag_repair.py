#!/usr/bin/env python3
"""Correct the Phase 238 GDSC diagnostic property to TouchGrass parent-supply.

This is diagnostic-only. Phase 233 already preserves the regulator parent through
`parent-supply`; the original Phase 238 helper accidentally inspected
`vdd_parent-supply`. After the broad recorder overlay is generated, replace only
the three diagnostic string references. Refuse any unexpected source shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE238_GDSC_PROVIDER_TRACE_V1"
OLD = '"vdd_parent-supply"'
NEW = '"parent-supply"'
EXPECTED_OLD_COUNT = 3


def repair(text: str, label: str) -> str:
    if MARKER not in text:
        raise RuntimeError(f"{label}: missing Phase 238 GDSC trace marker")
    old_count = text.count(OLD)
    if old_count != EXPECTED_OLD_COUNT:
        raise RuntimeError(
            f"{label}: expected {EXPECTED_OLD_COUNT} diagnostic {OLD} references, "
            f"found {old_count}"
        )
    repaired = text.replace(OLD, NEW)
    if OLD in repaired:
        raise RuntimeError(f"{label}: stale {OLD} diagnostic reference remains")
    return repaired


def self_test() -> None:
    fixture = (
        "/* A52_PHASE238_GDSC_PROVIDER_TRACE_V1 */\n"
        'a("vdd_parent-supply");\n'
        'b("vdd_parent-supply");\n'
        'c("vdd_parent-supply");\n'
        'functional("parent-supply");\n'
    )
    repaired = repair(fixture, "fixture")
    if repaired.count(NEW) != 4 or OLD in repaired:
        raise AssertionError("Phase 238 parent diagnostic repair self-test failed")
    print("Phase 238 GDSC parent-supply diagnostic repair self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    path = Path.cwd() / TARGET
    if not path.is_file():
        raise SystemExit(f"Phase 238 parent diagnostic repair missing generated source: {path}")
    original = path.read_text(encoding="utf-8")
    repaired = repair(original, str(path))
    path.write_text(repaired, encoding="utf-8")
    print(
        "Phase 238 GDSC parent diagnostic repair: vdd_parent-supply -> parent-supply",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
