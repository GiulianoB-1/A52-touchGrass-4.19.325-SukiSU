#!/usr/bin/env python3
"""Run the Phase 226 wrapper, then retain ODSPOST records after capacity."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MARKER = "A52_PHASE226_ODSIGN_GATE_TRACE"
ALLOW = '!strncmp(message, "ODSPOST ", 8)'
ANCHOR_RE = re.compile(
    r'(?m)^(?P<indent>[ \t]*)!strncmp\(message, "BOOTPOST ", 9\) \|\|$'
)
RELATIVE = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def patch_text(text: str, *, label: str) -> str:
    if MARKER not in text:
        raise RuntimeError(f"{label}: missing Phase 226 recorder marker")
    count = text.count(ALLOW)
    if count == 1:
        return text
    if count != 0:
        raise RuntimeError(f"{label}: unexpected ODSPOST allowlist count {count}")
    matches = list(ANCHOR_RE.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            f"{label}: expected one BOOTPOST allowlist anchor, found {len(matches)}"
        )
    match = matches[0]
    insertion = match.group(0) + "\n" + match.group("indent") + ALLOW + " ||"
    patched = text[:match.start()] + insertion + text[match.end():]
    if patched.count(ALLOW) != 1:
        raise RuntimeError(f"{label}: ODSPOST allowlist insertion failed")
    return patched


def candidate_sources(arguments: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        root = Path(value)
        candidates.extend((root / RELATIVE, root / "a52_ack_secure_flight_recorder.c"))
    candidates.extend(
        (
            Path("workspace/gki-phase199-src") / RELATIVE,
            Path("gki/common") / RELATIVE,
        )
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def patch_generated_source(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for candidate in candidate_sources(arguments):
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        if MARKER in text:
            matches.append(candidate)
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(
            f"expected one generated Phase 226 recorder source, found {len(matches)}: {rendered}"
        )
    target = matches[0]
    original = target.read_text(encoding="utf-8")
    patched = patch_text(original, label=str(target))
    target.write_text(patched, encoding="utf-8")
    verified = target.read_text(encoding="utf-8")
    if verified.count(ALLOW) != 1:
        raise RuntimeError(f"{target}: final ODSPOST retention audit failed")
    return target


def self_test() -> None:
    fixture = (
        "/* A52_PHASE226_ODSIGN_GATE_TRACE */\n"
        "return !strncmp(message, \"GFXPOST \", 8) ||\n"
        "       !strncmp(message, \"BOOTPOST \", 9) ||\n"
        "       !strncmp(message, \"KMSPOST \", 8);\n"
    )
    patched = patch_text(fixture, label="self-test")
    if patched.count(ALLOW) != 1:
        raise AssertionError("Phase 227 retention self-test failed")
    if patch_text(patched, label="self-test-idempotent") != patched:
        raise AssertionError("Phase 227 retention patch is not idempotent")
    print("Phase 227 ODSPOST post-capacity retention self-test: PASS")


def main() -> int:
    base = Path(__file__).with_name("218_phase217_wrapper_phase226.py")

    if "--self-test" in sys.argv[1:]:
        if base.is_file():
            base_args = list(sys.argv[1:])
            if "--root" not in base_args:
                base_args[0:0] = ["--root", "."]
            completed = subprocess.run(
                [sys.executable, str(base), *base_args], check=False
            )
            if completed.returncode:
                return completed.returncode
        self_test()
        return 0

    if not base.is_file():
        raise SystemExit(f"missing Phase 226 base wrapper: {base}")

    completed = subprocess.run([sys.executable, str(base), *sys.argv[1:]], check=False)
    if completed.returncode:
        return completed.returncode

    target = patch_generated_source(sys.argv[1:])
    print(f"Phase 227 retained ODSPOST after recorder capacity in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
