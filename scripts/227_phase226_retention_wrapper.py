#!/usr/bin/env python3
"""Load Phase 233 with the authoritative-config locator correction."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
source_path = root / "233_phase232_final_graphics_parity_wrapper.py"
if not source_path.is_file():
    payload = root / "233_payload.py"
    if not payload.is_file():
        raise SystemExit(f"missing Phase 233 payload: {payload}")
    subprocess.run([sys.executable, str(payload)], check=True)
if not source_path.is_file():
    raise SystemExit(f"missing Phase 233 wrapper after payload: {source_path}")

source = source_path.read_text(encoding="utf-8")
old = '''def locate_config(root: Path) -> Path:
    candidates = (
        Path.cwd() / CONFIG_REL,
        root.parent.parent / "workspace/gki-phase199-out/.config",
        root / ".config",
    )
    matches: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.is_file():
            continue
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        matches.append(path)
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(f"expected one generated kernel config, found {rendered}")
    return matches[0]
'''
new = '''def locate_config(root: Path) -> Path:
    # A52_PHASE233_AUTHORITATIVE_CONFIG_V2
    # The cumulative build can retain both O=/.config and an in-tree .config.
    # O= is the config used for the final Image and therefore has priority.
    candidates = (
        Path.cwd() / CONFIG_REL,
        root.parent.parent / "workspace/gki-phase199-out/.config",
        root / ".config",
    )
    existing: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        key = path.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            existing.append(path)
    if not existing:
        rendered = ", ".join(str(path) for path in candidates)
        raise RuntimeError(
            f"no generated kernel config found; checked: {rendered}"
        )
    selected = existing[0]
    print(
        "Phase 233 config candidates: "
        + ", ".join(str(path) for path in existing)
        + f"; selected authoritative config: {selected}",
        flush=True,
    )
    return selected
'''
if source.count(old) != 1:
    raise SystemExit(
        "Phase 233 config-locator correction expected exactly one source block, "
        f"found {source.count(old)}"
    )
source = source.replace(old, new, 1)
exec(compile(source, str(source_path), "exec"), globals(), globals())
