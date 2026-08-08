#!/usr/bin/env python3
"""Phase 240 runtime identity over the Phase 239 CX vdd_parent candidate."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OLD = "BOOT rs=ready phase=239 focus=gpu-cx-vdd-parent roots=%u copies=3 crc=crc32c"
NEW = "BOOT rs=ready phase=240 focus=cx-supplier-gate-latch roots=%u copies=3 crc=crc32c"
MARKER = "A52_PHASE240_CX_SUPPLIER_GATE_LATCH_IDENTITY_V1"
IDENTITY_ANCHOR = "\t * A52_PHASE240_CX_FROZEN_LATCH_V1\n"


def patch(text: str, label: str) -> str:
    if NEW in text and MARKER in text:
        return text
    if text.count(OLD) != 1:
        raise RuntimeError(f"{label}: expected exactly one Phase 239 boot identity")
    text = text.replace(OLD, NEW, 1)
    if text.count(IDENTITY_ANCHOR) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one Phase 240 identity-chain anchor, "
            f"found {text.count(IDENTITY_ANCHOR)}"
        )
    text = text.replace(
        IDENTITY_ANCHOR,
        IDENTITY_ANCHOR + "\t * " + MARKER + "\n",
        1,
    )
    return text


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches: list[Path] = []
    for root in candidate_roots(args, base):
        path = root / RECORDER
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if OLD in text or (NEW in text and MARKER in text):
            if "A52_PHASE240_CX_FROZEN_LATCH_V1" in text:
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
        raise RuntimeError(f"expected one generated Phase 240 recorder root, found {rendered}")
    return unique[0]


def self_test() -> None:
    fixture = (
        "/* recorder identity\n"
        + IDENTITY_ANCHOR
        + " */\n"
        + "/* A52_PHASE240_CX_FROZEN_LATCH_V1 helper */\n"
        + OLD
        + "\n"
    )
    if fixture.count("A52_PHASE240_CX_FROZEN_LATCH_V1") != 2:
        raise AssertionError("Phase 240 identity fixture must model both latch markers")
    out = patch(fixture, "fixture")
    if NEW not in out or MARKER not in out or OLD in out:
        raise AssertionError("Phase 240 identity replacement failed")
    if patch(out, "fixture/idempotent") != out:
        raise AssertionError("Phase 240 identity patch is not idempotent")
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        root = repo / "gki/common"
        path = root / RECORDER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture, encoding="utf-8")
        if locate_generated([], cwd=repo).resolve() != root.resolve():
            raise AssertionError("Phase 240 identity locator failed")
    print("Phase 240 runtime identity self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    path = root / RECORDER
    path.write_text(patch(path.read_text(encoding="utf-8"), str(path)), encoding="utf-8")
    print("Phase 240 runtime identity applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
