#!/usr/bin/env python3
"""Phase 238 preflight: make the platform include bundle deterministic.

Phase 237 can leave more than one recorder-header include in drivers/base/platform.c.
The broad Phase 238 overlay intentionally uses strict anchors elsewhere, so prepare
its three extra generic-kernel headers without requiring the recorder include to be
unique. This changes includes only and does not alter runtime behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

PHASE237_MARKER = "A52_PHASE237_OFPOP_PLATFORM_PROBE_RECORDER_V1"
PHASE238_MARKER = "A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1"
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PLATFORM_REL = Path("drivers/base/platform.c")
ANCHOR = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
BUNDLE = (
    "#include <linux/workqueue.h>\n"
    "#include <linux/jiffies.h>\n"
    "#include <linux/string.h>\n"
)


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        path = Path(value)
        roots.extend((path, path.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        recorder = root / RECORDER_REL
        platform = root / PLATFORM_REL
        if not recorder.is_file() or not platform.is_file():
            continue
        text = recorder.read_text(encoding="utf-8")
        if PHASE237_MARKER not in text and PHASE238_MARKER not in text:
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
        rendered = ", ".join(str(path) for path in unique) or "none"
        raise RuntimeError(
            f"expected one generated Phase 237 kernel root, found {len(unique)}: {rendered}"
        )
    return unique[0]


def main() -> int:
    root = locate_root(sys.argv[1:])
    path = root / PLATFORM_REL
    text = path.read_text(encoding="utf-8")

    if BUNDLE in text:
        print(f"Phase 238 platform include preflight already satisfied: {path}")
        return 0

    count = text.count(ANCHOR)
    if count < 1:
        raise RuntimeError(f"{path}: recorder include anchor missing")

    offset = text.find(ANCHOR) + len(ANCHOR)
    text = text[:offset] + BUNDLE + text[offset:]
    path.write_text(text, encoding="utf-8")
    print(
        "Phase 238 platform include preflight applied: "
        f"{path} recorder_include_count={count}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 238 platform include preflight failed: {exc}", file=sys.stderr)
        raise
