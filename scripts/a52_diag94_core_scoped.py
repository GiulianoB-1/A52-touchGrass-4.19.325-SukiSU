#!/usr/bin/env python3
from __future__ import annotations

import a52_diag94_core_scoped_base as _impl


def _function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    """Find a function definition matching anchor, skipping prototypes."""
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"{label}: function definition anchor not found")
        brace = text.find("{", start)
        semicolon = text.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        search = start + len(anchor)

    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"{label}: function closing brace not found")


# The preserved implementation resolves this global at call time.
_impl._function_bounds = _function_bounds


def instrument_ufshcd(core: str) -> str:
    return _impl.instrument_ufshcd(core)
