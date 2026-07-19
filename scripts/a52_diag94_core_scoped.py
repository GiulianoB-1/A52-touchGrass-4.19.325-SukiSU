#!/usr/bin/env python3
from __future__ import annotations

from a52_diag94_core import instrument_ufshcd as _instrument_ufshcd


def _scope_statement(
    text: str, function_anchor: str, statement: str, label: str
) -> str:
    """Leave one statement spelling inside its owning function.

    The base recorder deliberately uses strict, auditable exact anchors. Some
    UFS helper calls occur in more than one function, so make non-target copies
    text-distinct with a harmless source comment before running the base
    instrumenter. Runtime behavior is unchanged.
    """
    function_pos = text.find(function_anchor)
    if function_pos < 0:
        raise SystemExit(f"{label}: owning function anchor not found")

    target_pos = text.find(statement, function_pos)
    if target_pos < 0:
        raise SystemExit(f"{label}: target statement not found after function anchor")

    positions: list[int] = []
    cursor = 0
    while True:
        pos = text.find(statement, cursor)
        if pos < 0:
            break
        positions.append(pos)
        cursor = pos + len(statement)

    if target_pos not in positions:
        raise SystemExit(f"{label}: target statement position audit failed")

    if len(positions) <= 1:
        return text

    replacement = statement.rstrip("\n") + f" /* A52 scoped non-target: {label} */\n"
    for pos in reversed(positions):
        if pos == target_pos:
            continue
        text = text[:pos] + replacement + text[pos + len(statement):]

    if text.count(statement) != 1:
        raise SystemExit(
            f"{label}: expected one exact target after scoping, found {text.count(statement)}"
        )
    return text


def instrument_ufshcd(core: str) -> str:
    core = _scope_statement(
        core,
        "int ufshcd_init(struct ufs_hba *hba,",
        "\terr = ufshcd_hba_enable(hba);\n",
        "ufshcd_init hba_enable",
    )
    core = _scope_statement(
        core,
        "static int ufshcd_probe_hba(struct ufs_hba *hba, bool async)",
        "\t\tret = ufshcd_config_pwr_mode(hba, &hba->max_pwr_info.info);\n",
        "ufshcd_probe_hba config_pwr_mode",
    )
    return _instrument_ufshcd(core)
