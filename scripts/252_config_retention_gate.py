#!/usr/bin/env python3
"""Phase 252 semantic replacement for the inherited Phase 217 config cmp.

The inherited Phase 217 CI uses a byte-for-byte cmp between its saved config
snapshot and final.config. Phase 252 intentionally enables exactly two new
Kconfig symbols, and the cumulative wrapper may refresh the snapshot before the
final olddefconfig rewrites formatting/order. Compare semantic CONFIG_* states
instead and remain fail-closed for every symbol outside the Phase 252 delta.
"""
from __future__ import annotations

import sys
from pathlib import Path

ALLOWED = {
    "CONFIG_QCOM_BUS_SCALING",
    "CONFIG_QCOM_BUS_CONFIG_RPMH",
}


def parse_config(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(f"Phase 252 config retention gate: missing {path}")
    states: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("CONFIG_") and "=" in line:
            symbol, value = line.split("=", 1)
            if symbol in states:
                raise SystemExit(
                    f"Phase 252 config retention gate: duplicate {symbol} in {path}:{lineno}"
                )
            states[symbol] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            symbol = line[2:-11]
            if symbol in states:
                raise SystemExit(
                    f"Phase 252 config retention gate: duplicate {symbol} in {path}:{lineno}"
                )
            states[symbol] = "n"
    return states


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: 252_config_retention_gate.py BEFORE_CONFIG FINAL_CONFIG"
        )

    before_path = Path(sys.argv[1])
    final_path = Path(sys.argv[2])
    before = parse_config(before_path)
    final = parse_config(final_path)

    changed = {
        symbol
        for symbol in set(before) | set(final)
        if before.get(symbol, "n") != final.get(symbol, "n")
    }
    unexpected = sorted(changed - ALLOWED)
    if unexpected:
        details = ", ".join(
            f"{symbol}: {before.get(symbol, 'n')} -> {final.get(symbol, 'n')}"
            for symbol in unexpected
        )
        raise SystemExit(
            "Phase 252 config retention gate refused unexpected semantic drift: "
            + details
        )

    wrong_final = sorted(symbol for symbol in ALLOWED if final.get(symbol) != "y")
    if wrong_final:
        raise SystemExit(
            "Phase 252 config retention gate: required final bus symbols are not y: "
            + ", ".join(wrong_final)
        )

    invalid_allowed_transition = sorted(
        symbol
        for symbol in (changed & ALLOWED)
        if before.get(symbol, "n") != "n" or final.get(symbol) != "y"
    )
    if invalid_allowed_transition:
        raise SystemExit(
            "Phase 252 config retention gate: invalid allowed transition: "
            + ", ".join(invalid_allowed_transition)
        )

    rendered = ", ".join(sorted(changed)) if changed else "no semantic changes"
    print(
        "Phase 252 semantic config retention: PASS; delta=" + rendered,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
