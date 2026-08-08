#!/usr/bin/env python3
"""Move Phase 238 passive replays beyond the observed recorder retention hole.

The first Phase 238 hardware capture retained records through about 0.556 s and
then resumed at about 148.369 s. The original 145 s passive replay therefore
ran inside the missing window. This post-overlay repair changes only the three
Phase 238 delayed-work replay timers in generated C from 145 s to 155 s. It
does not force reprobes and does not change GPU/GDSC/KGSL behavior.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

OLD = "msecs_to_jiffies(145000)"
NEW = "msecs_to_jiffies(155000)"
TARGETS = (
    Path("drivers/base/dd.c"),
    Path("drivers/base/platform.c"),
    Path("drivers/regulator/a52-legacy-gdsc-regulator.c"),
)


def candidate_roots(arguments: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
    roots.extend((cwd, cwd / "workspace/gki-phase199-src", cwd / "gki/common"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def root_has_expected_sources(root: Path) -> bool:
    for rel in TARGETS:
        path = root / rel
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
        if text.count(OLD) != 1 or text.count(NEW) != 0:
            return False
    return True


def locate_generated(arguments: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches = [
        root
        for root in candidate_roots(arguments, base)
        if root_has_expected_sources(root)
    ]

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)

    if len(unique) != 1:
        rendered = ", ".join(str(path) for path in unique) or "none"
        raise RuntimeError(
            "expected exactly one generated Phase 238 replay source root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def patch_one(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Phase 238 replay timing repair missing generated source: {path}")
    text = path.read_text(encoding="utf-8")
    old_count = text.count(OLD)
    new_count = text.count(NEW)
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            f"Phase 238 replay timing repair expected exactly one old timer and no new timer in {path}: old={old_count} new={new_count}"
        )
    text = text.replace(OLD, NEW, 1)
    if text.count(OLD) != 0 or text.count(NEW) != 1:
        raise RuntimeError(f"Phase 238 replay timing repair verification failed: {path}")
    path.write_text(text, encoding="utf-8")
    print(f"Phase 238 replay timing repair: {path} 145000 -> 155000 ms", flush=True)


def self_test() -> None:
    if OLD == NEW or len(TARGETS) != 3:
        raise AssertionError("Phase 238 replay timing repair constants are invalid")

    # Reproduce the inherited-builder layout that caused the real failure:
    # wrapper cwd = repository root, generated kernel source = gki/common.
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        for rel in TARGETS:
            path = generated / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"before\n{OLD}\nafter\n", encoding="utf-8")

        found_from_repo = locate_generated([], cwd=repo)
        if found_from_repo.resolve() != generated.resolve():
            raise AssertionError(
                f"repository-root locator failed: {found_from_repo} != {generated}"
            )

        found_from_kernel = locate_generated([], cwd=generated)
        if found_from_kernel.resolve() != generated.resolve():
            raise AssertionError(
                f"kernel-root locator failed: {found_from_kernel} != {generated}"
            )

        for rel in TARGETS:
            patch_one(generated / rel)
            text = (generated / rel).read_text(encoding="utf-8")
            if OLD in text or text.count(NEW) != 1:
                raise AssertionError(f"replay timing self-test patch failed: {rel}")

    print("Phase 238 replay timing repair self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = locate_generated(sys.argv[1:])
    for rel in TARGETS:
        patch_one(root / rel)
    print(f"Phase 238 retention-safe replay timing repair: PASS root={root}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 238 retention-safe replay timing repair failed: {exc}", file=sys.stderr)
        raise
