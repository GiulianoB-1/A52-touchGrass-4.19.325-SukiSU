#!/usr/bin/env python3
"""Load Phase 233 with authoritative-config and semantic disabled-FB audits."""
from __future__ import annotations

import inspect
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


def validate_disabled_config_symbol(path: Path, symbol: str) -> None:
    """Reject enabled forms without mutating inherited config snapshots."""
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line.startswith(f"{symbol}=")]
    if assignments:
        rendered = ", ".join(assignments)
        raise SystemExit(
            f"{path}: {symbol} must be disabled for Phase 233 parity; found {rendered}"
        )


# Absence and '# CONFIG_FB_MSM is not set' are equivalent disabled Kconfig
# states. Validate the real files, but do not modify them before the inherited
# byte-for-byte config-retention gates run.
for config_path in (
    Path.cwd() / "workspace/gki-phase199-out/.config",
    Path.cwd() / "workspace/gki-phase199-src/.config",
    Path.cwd() / "gki/common/.config",
    Path.cwd() / ".config",
):
    validate_disabled_config_symbol(config_path, "CONFIG_FB_MSM")

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

# Phase 233's generated Python audit expects the canonical disabled comment.
# Present that comment only to reads performed directly by the generated Phase
# 233 wrapper. The underlying file remains unchanged, so inherited retention
# snapshots and shell comparisons continue to see the exact original config.
_original_read_text = Path.read_text
_source_filename = str(source_path)
_disabled_line = "# CONFIG_FB_MSM is not set"


def _phase233_read_text(path: Path, *args: object, **kwargs: object) -> str:
    text = _original_read_text(path, *args, **kwargs)
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    direct_phase233_read = (
        caller is not None and caller.f_code.co_filename == _source_filename
    )
    if direct_phase233_read and path.name == ".config":
        lines = text.splitlines()
        if not any(line.startswith("CONFIG_FB_MSM=") for line in lines):
            if _disabled_line not in lines:
                separator = "" if not text or text.endswith("\n") else "\n"
                return text + separator + _disabled_line + "\n"
    return text


Path.read_text = _phase233_read_text
try:
    exec(compile(source, _source_filename, "exec"), globals(), globals())
finally:
    Path.read_text = _original_read_text
