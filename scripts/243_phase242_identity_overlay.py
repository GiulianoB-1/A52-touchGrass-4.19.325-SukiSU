#!/usr/bin/env python3
"""Phase 243 runtime identity for live CX/GX own-supplier diagnostics."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OLD = "BOOT rs=ready phase=242 focus=cx-sticky-state roots=%u copies=3 crc=crc32c"
NEW = "BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers roots=%u copies=3 crc=crc32c"
MARKER = "A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1"
IDENTITY_ANCHOR = "\t * A52_PHASE243_CXGX_LIVE_SUPPLIER_V1\n"


def patch(text: str, label: str) -> str:
    if NEW in text and MARKER in text:
        return text
    if text.count(OLD) != 1:
        raise RuntimeError(f"{label}: expected exactly one Phase 242 boot identity, found {text.count(OLD)}")
    text = text.replace(OLD, NEW, 1)
    if text.count(IDENTITY_ANCHOR) != 1:
        raise RuntimeError(f"{label}: expected exactly one Phase 243 live-supplier anchor, found {text.count(IDENTITY_ANCHOR)}")
    return text.replace(IDENTITY_ANCHOR, IDENTITY_ANCHOR + "\t * " + MARKER + "\n", 1)


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
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        path = root / RECORDER
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "A52_PHASE243_CXGX_LIVE_SUPPLIER_V1" not in text:
            continue
        if OLD not in text and not (NEW in text and MARKER in text):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(root) for root in unique) or "none"
        raise RuntimeError(f"expected one generated Phase 243 recorder root, found {rendered}")
    return unique[0]


def self_test() -> None:
    fixture = "/*\n" + IDENTITY_ANCHOR + " */\n/* A52_PHASE243_CXGX_LIVE_SUPPLIER_V1 */\n" + OLD + "\n"
    out = patch(fixture, "fixture")
    if NEW not in out or MARKER not in out or OLD in out:
        raise AssertionError("identity replacement failed")
    if patch(out, "fixture/idempotent") != out:
        raise AssertionError("identity patch is not idempotent")
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        root = repo / "gki/common"
        path = root / RECORDER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture, encoding="utf-8")
        if locate_generated([], cwd=repo).resolve() != root.resolve():
            raise AssertionError("identity locator failed")
    print("Phase 243 runtime identity self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    path = root / RECORDER
    path.write_text(patch(path.read_text(encoding="utf-8"), str(path)), encoding="utf-8")
    print("Phase 243 runtime identity applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())