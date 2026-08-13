#!/usr/bin/env python3
"""Restore the hardware-proven Phase221 SCSI-generic config in fast rebuilds.

The one-compile Phase257/258 reconstruction starts from a pre-Phase221 config
snapshot. That silently dropped CONFIG_CHR_DEV_SG=y, reintroducing the old
qseecomd/librpmb boot blocker. This helper restores only that proven config
mutation and fails closed on unexpected input state.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE258_RESTORE_PHASE221_CHR_DEV_SG_V1"
SYMBOL = "CONFIG_CHR_DEV_SG"


def locate(args: list[str]) -> Path:
    candidates: list[Path] = []
    for value in args:
        p = Path(value)
        if p.is_dir():
            p = p / ".config"
        candidates.append(p)
    candidates.extend(
        [
            Path("workspace/gki-phase199-out/.config"),
            Path("workspace/gki-phase199-src/.config"),
            Path("gki/common/.config"),
        ]
    )
    hits = [p for p in candidates if p.is_file()]
    if not hits:
        raise RuntimeError("authoritative kernel .config not found")
    return hits[0]


def apply(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"{SYMBOL}=y"
    disabled = f"# {SYMBOL} is not set"
    assignments = [
        line for line in text.splitlines()
        if line == disabled or line.startswith(f"{SYMBOL}=")
    ]
    if len(assignments) != 1:
        raise RuntimeError(
            f"{path}: expected exactly one {SYMBOL} state, found {assignments!r}"
        )
    current = assignments[0]
    if current == enabled:
        print(f"{MARKER}: {SYMBOL}=y already present", flush=True)
        return
    if current != disabled:
        raise RuntimeError(f"{path}: unexpected {SYMBOL} state: {current}")
    path.write_text(text.replace(disabled, enabled, 1), encoding="utf-8")
    verify(path)
    print(f"{MARKER}: restored hardware-proven {SYMBOL}=y", flush=True)


def verify(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(f"{SYMBOL}=y") != 1:
        raise RuntimeError(f"{path}: {SYMBOL}=y verification failed")
    if f"# {SYMBOL} is not set" in text:
        raise RuntimeError(f"{path}: stale disabled {SYMBOL} remains")


def main() -> int:
    path = locate(sys.argv[1:])
    apply(path)
    verify(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
