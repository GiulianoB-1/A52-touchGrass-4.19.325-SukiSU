#!/usr/bin/env python3
"""Correct the Phase 238 GDSC parent diagnostic without changing behavior.

Phase 233 preserves regulator ordering through TouchGrass's `parent-supply`.
The original Phase 238 helper accidentally used `vdd_parent-supply` for its
parent-present diagnostics. Phase 239 also adds a *functional*
`vdd_parent-supply` lookup inside the real GDSC probe, so the repair must never
do a file-global replacement. Rewrite only the Phase 238 helper block, retain
one helper control phandle trace, and leave the functional probe untouched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TARGET = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE238_GDSC_PROVIDER_TRACE_V1"
OLD = '"vdd_parent-supply"'
NEW = '"parent-supply"'
EXPECTED_HELPER_OLD_COUNT = 3
PHANDLE_OLD = 'a52_g238_gd_phandle(pdev, "vdd_parent-supply");'
PHANDLE_NEW = 'a52_g238_gd_phandle(pdev, "parent-supply");'
PROBE_ANCHOR = "static int a52_legacy_gdsc_probe("


def helper_split(text: str, label: str) -> tuple[str, str]:
    positions: list[int] = []
    start = 0
    while True:
        idx = text.find(PROBE_ANCHOR, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + 1
    if len(positions) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one GDSC probe anchor, found {len(positions)}"
        )
    idx = positions[0]
    return text[:idx], text[idx:]


def repair(text: str, label: str) -> str:
    if MARKER not in text:
        raise RuntimeError(f"{label}: missing Phase 238 GDSC trace marker")

    helper, functional = helper_split(text, label)
    old_count = helper.count(OLD)
    if old_count != EXPECTED_HELPER_OLD_COUNT:
        raise RuntimeError(
            f"{label}: expected {EXPECTED_HELPER_OLD_COUNT} diagnostic {OLD} "
            f"references in helper block, found {old_count}"
        )
    if helper.count(PHANDLE_OLD) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one old parent phandle trace in helper block"
        )

    repaired_helper = helper.replace(OLD, NEW)
    if OLD in repaired_helper:
        raise RuntimeError(f"{label}: stale helper diagnostic {OLD} remains")
    if repaired_helper.count(PHANDLE_NEW) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one corrected parent phandle trace"
        )

    # Keep the old spelling only as a second explicit diagnostic/control trace.
    repaired_helper = repaired_helper.replace(
        PHANDLE_NEW,
        PHANDLE_NEW + "\n\\t" + PHANDLE_OLD,
        1,
    )
    if repaired_helper.count(PHANDLE_NEW) != 1 or repaired_helper.count(PHANDLE_OLD) != 1:
        raise RuntimeError(f"{label}: dual parent phandle trace verification failed")
    if repaired_helper.count(OLD) != 1:
        raise RuntimeError(
            f"{label}: helper old spelling must remain only in control phandle trace"
        )

    repaired = repaired_helper + functional
    if functional not in repaired:
        raise RuntimeError(f"{label}: functional probe was unexpectedly modified")
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
    helper = (
        "/* A52_PHASE238_GDSC_PROVIDER_TRACE_V1 */\n"
        'a("vdd_parent-supply");\n'
        'b("vdd_parent-supply");\n'
        'a52_g238_gd_phandle(pdev, "vdd_parent-supply");\n'
        'diagnostic("parent-supply");\n'
    )
    probe = (
        "static int a52_legacy_gdsc_probe(struct platform_device *pdev)\n"
        "{\n"
        '    functional("vdd_parent-supply");\n'
        '    ordering("parent-supply");\n'
        "    return 0;\n"
        "}\n"
    )
    fixture = helper + probe
    repaired = repair(fixture, "fixture")
    repaired_helper, repaired_probe = helper_split(repaired, "fixture/repaired")
    if repaired_helper.count(PHANDLE_NEW) != 1 or repaired_helper.count(PHANDLE_OLD) != 1:
        raise AssertionError("Phase 238 parent diagnostic repair self-test failed")
    if repaired_helper.count(OLD) != 1:
        raise AssertionError("Phase 238 helper old-property scope self-test failed")
    if repaired_probe != probe:
        raise AssertionError("Phase 239 functional vdd_parent probe was modified")
    if repaired_probe.count(OLD) != 1:
        raise AssertionError("Phase 239 functional vdd_parent property was not preserved")

    # Also prove backward compatibility with the original Phase 238 source,
    # where no functional vdd_parent lookup follows the helper block.
    legacy_probe = (
        "static int a52_legacy_gdsc_probe(struct platform_device *pdev)\n"
        "{\n    return 0;\n}\n"
    )
    legacy = repair(helper + legacy_probe, "fixture/phase238")
    legacy_helper, legacy_functional = helper_split(legacy, "fixture/phase238/repaired")
    if legacy_helper.count(OLD) != 1 or OLD in legacy_functional:
        raise AssertionError("Phase 238 backward compatibility self-test failed")

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

    print(
        "Phase 239-safe GDSC parent diagnostic repair self-test: PASS",
        flush=True,
    )


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
        "Phase 238 GDSC parent diagnostic repair: helper uses parent-supply, "
        f"functional vdd_parent-supply preserved in {path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
