#!/usr/bin/env python3
"""Move Phase 238 passive replays beyond the observed recorder retention hole.

The first Phase 238 hardware capture retained records through about 0.556 s and
then resumed at about 148.369 s. The original 145 s passive replay therefore
ran inside the missing window. This post-overlay repair changes only the two
Phase 238 delayed-work replay timers in generated C from 145 s to 155 s. It
does not force reprobes and does not change GPU/GDSC/KGSL behavior.
"""
from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

OLD_MS = "145000"
NEW_MS = "155000"


@dataclass(frozen=True)
class ReplayTarget:
    rel: Path
    work: str


TARGETS = (
    ReplayTarget(
        Path("drivers/base/platform.c"),
        "a52_g238_platform_replay_work",
    ),
    ReplayTarget(
        Path("drivers/regulator/a52-legacy-gdsc-regulator.c"),
        "a52_g238_gd_replay_work",
    ),
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
    return all((root / target.rel).is_file() for target in TARGETS)


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


def timer_pattern(work: str) -> re.Pattern[str]:
    return re.compile(
        rf"(schedule_delayed_work\(\s*&{re.escape(work)}\s*,\s*"
        rf"msecs_to_jiffies\(\s*)({OLD_MS}|{NEW_MS})(\s*\)\s*\)\s*;)",
        re.MULTILINE,
    )


def target_timer_ms(text: str, target: ReplayTarget, label: str) -> str:
    matches = list(timer_pattern(target.work).finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one Phase 238 timer for "
            f"{target.work}, found {len(matches)}"
        )
    return matches[0].group(2)


def patch_one(path: Path, target: ReplayTarget) -> None:
    if not path.is_file():
        raise RuntimeError(f"Phase 238 replay timing repair missing generated source: {path}")

    text = path.read_text(encoding="utf-8")
    pattern = timer_pattern(target.work)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            f"Phase 238 replay timing repair expected exactly one named timer "
            f"for {target.work} in {path}, found {len(matches)}"
        )

    match = matches[0]
    current = match.group(2)
    if current == NEW_MS:
        print(
            f"Phase 238 replay timing repair: {path} {target.work} already {NEW_MS} ms",
            flush=True,
        )
        return
    if current != OLD_MS:
        raise RuntimeError(
            f"Phase 238 replay timing repair unexpected timer for {target.work} "
            f"in {path}: {current} ms"
        )

    patched = text[:match.start(2)] + NEW_MS + text[match.end(2):]
    if target_timer_ms(patched, target, str(path)) != NEW_MS:
        raise RuntimeError(
            f"Phase 238 replay timing repair verification failed for "
            f"{target.work} in {path}"
        )

    path.write_text(patched, encoding="utf-8")
    print(
        f"Phase 238 replay timing repair: {path} {target.work} "
        f"{OLD_MS} -> {NEW_MS} ms",
        flush=True,
    )


def fake_source(target: ReplayTarget, timer_ms: str) -> str:
    return f"""before
schedule_delayed_work(&{target.work},
                      msecs_to_jiffies({timer_ms}));
schedule_delayed_work(&unrelated_work,
                      msecs_to_jiffies({OLD_MS}));
after
"""


def self_test() -> None:
    if OLD_MS == NEW_MS or len(TARGETS) != 2:
        raise AssertionError("Phase 238 replay timing repair constants are invalid")

    # Reproduce the inherited-builder layout that caused the real failure:
    # wrapper cwd = repository root, generated kernel source = gki/common.
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        for target in TARGETS:
            path = generated / target.rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fake_source(target, OLD_MS), encoding="utf-8")

        # dd.c is intentionally not a Phase 238 delayed-work target. The old
        # helper incorrectly required it to contain a 145 s replay timer.
        dd = generated / "drivers/base/dd.c"
        dd.parent.mkdir(parents=True, exist_ok=True)
        dd.write_text("/* no Phase 238 delayed-work replay lives here */\n", encoding="utf-8")

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

        for target in TARGETS:
            path = generated / target.rel
            patch_one(path, target)
            text = path.read_text(encoding="utf-8")
            if target_timer_ms(text, target, str(path)) != NEW_MS:
                raise AssertionError(f"replay timing self-test patch failed: {target.rel}")
            if text.count(f"msecs_to_jiffies({OLD_MS})") != 1:
                raise AssertionError(
                    f"unrelated 145 s timer was modified in self-test: {target.rel}"
                )

            # A second pass must be harmless.
            before = text
            patch_one(path, target)
            after = path.read_text(encoding="utf-8")
            if after != before:
                raise AssertionError(f"replay timing patch is not idempotent: {target.rel}")

    print("Phase 238 replay timing repair self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = locate_generated(sys.argv[1:])
    for target in TARGETS:
        patch_one(root / target.rel, target)
    print(f"Phase 238 retention-safe replay timing repair: PASS root={root}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 238 retention-safe replay timing repair failed: {exc}", file=sys.stderr)
        raise
