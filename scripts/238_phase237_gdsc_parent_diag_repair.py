#!/usr/bin/env python3
"""Correct the Phase 238 GDSC parent diagnostic without changing behavior.

Phase 233 preserves regulator ordering through TouchGrass's `parent-supply`.
The original Phase 238 helper accidentally used `vdd_parent-supply` for its
parent-present state. Replace those three generated diagnostic references with
`parent-supply`, then retain one additional `vdd_parent-supply` phandle trace as
a negative/control observation. Refuse any unexpected source shape.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TARGET = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE238_GDSC_PROVIDER_TRACE_V1"
OLD = '"vdd_parent-supply"'
NEW = '"parent-supply"'
EXPECTED_OLD_COUNT = 3
PHANDLE_OLD = 'a52_g238_gd_phandle(pdev, "vdd_parent-supply");'
PHANDLE_NEW = 'a52_g238_gd_phandle(pdev, "parent-supply");'


def repair(text: str, label: str) -> str:
    if MARKER not in text:
        raise RuntimeError(f"{label}: missing Phase 238 GDSC trace marker")
    old_count = text.count(OLD)
    if old_count != EXPECTED_OLD_COUNT:
        raise RuntimeError(
            f"{label}: expected {EXPECTED_OLD_COUNT} diagnostic {OLD} references, "
            f"found {old_count}"
        )
    if text.count(PHANDLE_OLD) != 1:
        raise RuntimeError(f"{label}: expected exactly one old parent phandle trace")

    repaired = text.replace(OLD, NEW)
    if OLD in repaired:
        raise RuntimeError(f"{label}: stale {OLD} diagnostic reference remains")
    if repaired.count(PHANDLE_NEW) != 1:
        raise RuntimeError(f"{label}: expected exactly one corrected parent phandle trace")

    # Keep the old spelling only as a second explicit control trace. The
    # `parent=` state and the primary phandle trace now use `parent-supply`.
    repaired = repaired.replace(
        PHANDLE_NEW,
        PHANDLE_NEW + "\n\\t" + PHANDLE_OLD,
        1,
    )
    if repaired.count(PHANDLE_NEW) != 1 or repaired.count(PHANDLE_OLD) != 1:
        raise RuntimeError(f"{label}: dual parent phandle trace verification failed")
    if repaired.count(OLD) != 1:
        raise RuntimeError(f"{label}: old spelling must remain only in control phandle trace")
    return repaired


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches: list[Path] = []
    for root in candidate_roots(args, base):
        path = root / TARGET
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if MARKER not in text:
            continue
        matches.append(root)

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(root) for root in unique) or "none"
        raise RuntimeError(
            f"expected exactly one generated Phase 238 GDSC root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def self_test() -> None:
    fixture = (
        "/* A52_PHASE238_GDSC_PROVIDER_TRACE_V1 */\n"
        'a("vdd_parent-supply");\n'
        'b("vdd_parent-supply");\n'
        'a52_g238_gd_phandle(pdev, "vdd_parent-supply");\n'
        'functional("parent-supply");\n'
    )
    repaired = repair(fixture, "fixture")
    if repaired.count(PHANDLE_NEW) != 1 or repaired.count(PHANDLE_OLD) != 1:
        raise AssertionError("Phase 238 parent diagnostic repair self-test failed")
    if repaired.count(OLD) != 1 or repaired.count(NEW) != 4:
        raise AssertionError("Phase 238 parent property count self-test failed")

    # Regression test for the inherited-builder layout: the workflow runs from
    # the repository root while the generated kernel source lives in gki/common.
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        target = generated / TARGET
        target.parent.mkdir(parents=True)
        target.write_text(fixture, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != generated.resolve():
            raise AssertionError(
                f"Phase 238 generated-root locator selected {found}, expected {generated}"
            )

    print("Phase 238 GDSC parent-supply diagnostic repair self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = locate_generated(sys.argv[1:])
    path = root / TARGET
    original = path.read_text(encoding="utf-8")
    repaired = repair(original, str(path))
    path.write_text(repaired, encoding="utf-8")
    print(
        "Phase 238 GDSC parent diagnostic repair: parent-supply primary, "
        f"vdd_parent-supply control trace retained in {path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
