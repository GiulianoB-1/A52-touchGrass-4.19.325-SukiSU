#!/usr/bin/env python3
"""Correct the Phase 238 GDSC parent diagnostic without changing behavior.

Phase 233 preserves regulator ordering through TouchGrass's `parent-supply`.
The original Phase 238 recorder helper accidentally used `vdd_parent-supply`
for its parent-present state. Repair only the recorder-owned
a52_g238_gd_enter() helper, then retain one `vdd_parent-supply` phandle trace
as a negative/control observation.

Phase 239 legitimately adds a functional `vdd_parent-supply` consumer in the
legacy GDSC probe. That reference is outside the recorder helper and must remain
untouched. Refuse any unexpected recorder-helper shape.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TARGET = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE238_GDSC_PROVIDER_TRACE_V1"
PHASE239_MARKER = "A52_PHASE239_GPU_CX_VDD_PARENT_V1"
OLD = '"vdd_parent-supply"'
NEW = '"parent-supply"'
EXPECTED_DIAG_OLD_COUNT = 3
ENTER_SIG = "static int a52_g238_gd_enter(struct platform_device *pdev)"
ENTER_NEXT = "static void a52_g238_gd_stage(struct platform_device *pdev, int stage,"
PHANDLE_OLD = 'a52_g238_gd_phandle(pdev, "vdd_parent-supply");'
PHANDLE_NEW = 'a52_g238_gd_phandle(pdev, "parent-supply");'


def enter_bounds(text: str, label: str) -> tuple[int, int]:
    start = text.find(ENTER_SIG)
    if start < 0:
        raise RuntimeError(f"{label}: Phase 238 GDSC enter helper not found")
    end = text.find(ENTER_NEXT, start)
    if end < 0:
        raise RuntimeError(f"{label}: Phase 238 GDSC enter helper end not found")
    if text.find(ENTER_SIG, start + 1) >= 0:
        raise RuntimeError(f"{label}: duplicate Phase 238 GDSC enter helper")
    return start, end


def validate_repaired_block(block: str, label: str) -> None:
    if block.count(PHANDLE_NEW) != 1 or block.count(PHANDLE_OLD) != 1:
        raise RuntimeError(f"{label}: dual parent phandle trace verification failed")
    if block.count(OLD) != 1:
        raise RuntimeError(
            f"{label}: recorder helper must retain {OLD} only in control phandle trace"
        )
    if block.count(NEW) != EXPECTED_DIAG_OLD_COUNT:
        raise RuntimeError(
            f"{label}: recorder helper expected {EXPECTED_DIAG_OLD_COUNT} corrected "
            f"{NEW} references, found {block.count(NEW)}"
        )


def repair(text: str, label: str) -> str:
    if MARKER not in text:
        raise RuntimeError(f"{label}: missing Phase 238 GDSC trace marker")

    start, end = enter_bounds(text, label)
    block = text[start:end]

    # Idempotent path: the recorder helper is already corrected. Functional
    # Phase 239 vdd_parent-supply references elsewhere in the file are ignored.
    if (
        block.count(OLD) == 1
        and block.count(NEW) == EXPECTED_DIAG_OLD_COUNT
        and block.count(PHANDLE_NEW) == 1
        and block.count(PHANDLE_OLD) == 1
    ):
        validate_repaired_block(block, label)
        return text

    old_count = block.count(OLD)
    if old_count != EXPECTED_DIAG_OLD_COUNT:
        raise RuntimeError(
            f"{label}: expected {EXPECTED_DIAG_OLD_COUNT} recorder diagnostic "
            f"{OLD} references inside a52_g238_gd_enter(), found {old_count}"
        )
    if block.count(PHANDLE_OLD) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one old recorder parent phandle trace"
        )

    repaired_block = block.replace(OLD, NEW)
    if OLD in repaired_block:
        raise RuntimeError(
            f"{label}: stale recorder-owned {OLD} diagnostic reference remains"
        )
    if repaired_block.count(PHANDLE_NEW) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one corrected recorder parent phandle trace"
        )

    # Keep the old spelling only as the recorder's second explicit control
    # trace. Do not touch functional vdd_parent-supply users outside this helper.
    repaired_block = repaired_block.replace(
        PHANDLE_NEW,
        PHANDLE_NEW + "\n\t" + PHANDLE_OLD,
        1,
    )
    validate_repaired_block(repaired_block, label)

    repaired = text[:start] + repaired_block + text[end:]

    # Phase 239's functional consumer must survive this diagnostic-only repair.
    if PHASE239_MARKER in text:
        outside_before = text[:start].count(OLD) + text[end:].count(OLD)
        new_end = start + len(repaired_block)
        outside_after = repaired[:start].count(OLD) + repaired[new_end:].count(OLD)
        if outside_after != outside_before:
            raise RuntimeError(
                f"{label}: Phase 239 functional {OLD} references changed "
                f"outside recorder helper ({outside_before} -> {outside_after})"
            )
        if outside_after < 1:
            raise RuntimeError(
                f"{label}: Phase 239 marker present but functional {OLD} "
                "reference is missing outside recorder helper"
            )

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
            "expected exactly one generated Phase 238 GDSC root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def fixture_text(*, phase239: bool) -> str:
    functional = ""
    if phase239:
        functional = (
            "/* A52_PHASE239_GPU_CX_VDD_PARENT_V1 */\n"
            'functional_probe("vdd_parent-supply");\n'
        )
    return (
        "/* A52_PHASE238_GDSC_PROVIDER_TRACE_V1 */\n"
        + functional
        + "static int a52_g238_gd_enter(struct platform_device *pdev)\n"
        "{\n"
        'a("vdd_parent-supply");\n'
        'b("vdd_parent-supply");\n'
        'a52_g238_gd_phandle(pdev, "vdd_parent-supply");\n'
        "return 0;\n"
        "}\n\n"
        "static void a52_g238_gd_stage(struct platform_device *pdev, int stage,\n"
        "                                    const char *op, int line)\n"
        "{\n"
        "}\n"
    )


def self_test() -> None:
    # Original Phase 238 shape.
    fixture = fixture_text(phase239=False)
    repaired = repair(fixture, "fixture/phase238")
    start, end = enter_bounds(repaired, "fixture/phase238")
    validate_repaired_block(repaired[start:end], "fixture/phase238")
    if repair(repaired, "fixture/phase238/idempotent") != repaired:
        raise AssertionError("Phase 238 parent diagnostic repair is not idempotent")

    # Phase 239 regression: one real functional vdd_parent-supply reference is
    # present outside the recorder helper and must not be rewritten.
    fixture239 = fixture_text(phase239=True)
    repaired239 = repair(fixture239, "fixture/phase239")
    if 'functional_probe("vdd_parent-supply");' not in repaired239:
        raise AssertionError("Phase 239 functional vdd_parent-supply was modified")
    if 'functional_probe("parent-supply");' in repaired239:
        raise AssertionError("Phase 239 functional supply was rewritten incorrectly")
    start239, end239 = enter_bounds(repaired239, "fixture/phase239")
    validate_repaired_block(repaired239[start239:end239], "fixture/phase239")
    if repaired239.count(OLD) != 2:
        raise AssertionError(
            "Phase 239 should retain one functional and one control "
            "vdd_parent-supply reference"
        )
    if repair(repaired239, "fixture/phase239/idempotent") != repaired239:
        raise AssertionError("Phase 239-aware diagnostic repair is not idempotent")

    # Regression test for inherited-builder layout.
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        target = generated / TARGET
        target.parent.mkdir(parents=True)
        target.write_text(fixture239, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != generated.resolve():
            raise AssertionError(
                f"Phase 238 generated-root locator selected {found}, expected {generated}"
            )

    print(
        "Phase 238 GDSC parent diagnostic repair self-test: PASS "
        "(Phase 239 functional vdd_parent preserved)",
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
        "Phase 238 GDSC parent diagnostic repair: recorder parent-supply primary, "
        "vdd_parent-supply control retained; functional Phase 239 vdd_parent untouched "
        f"in {path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
