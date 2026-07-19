#!/usr/bin/env python3
from __future__ import annotations

import a52_diag94_core as _base
from a52_diag94_common import triplet


def _scope_statement(
    text: str, function_anchor: str, statement: str, label: str
) -> str:
    """Leave one exact statement spelling inside its owning function."""
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


def _function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"{label}: function anchor not found")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"{label}: function opening brace not found")

    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"{label}: function closing brace not found")


def _find_unique_in_function(
    text: str, function_anchor: str, statement: str, label: str
) -> tuple[int, int, int]:
    start, end = _function_bounds(text, function_anchor, label)
    pos = text.find(statement, start, end)
    if pos < 0:
        raise SystemExit(f"{label}: statement not found in function")
    if text.find(statement, pos + len(statement), end) >= 0:
        raise SystemExit(f"{label}: statement occurs more than once in function")
    return start, end, pos


def _insert_after_in_function(
    text: str, function_anchor: str, statement: str, insertion: str, label: str
) -> str:
    _, _, pos = _find_unique_in_function(text, function_anchor, statement, label)
    point = pos + len(statement)
    return text[:point] + insertion + text[point:]


def _insert_around_in_function(
    text: str,
    function_anchor: str,
    statement: str,
    before: str,
    after: str,
    label: str,
) -> str:
    _, _, pos = _find_unique_in_function(text, function_anchor, statement, label)
    return text[:pos] + before + statement + after + text[pos + len(statement):]


def _insert_after_label_in_function(
    text: str, function_anchor: str, label_anchor: str, insertion: str, label: str
) -> str:
    start, end = _function_bounds(text, function_anchor, label)
    pos = text.rfind(label_anchor, start, end)
    if pos < 0:
        raise SystemExit(f"{label}: label anchor not found in function")
    point = pos + len(label_anchor)
    return text[:point] + insertion + text[point:]


def _insert_before_last_return_in_function(
    text: str, function_anchor: str, statement: str, insertion: str, label: str
) -> str:
    start, end = _function_bounds(text, function_anchor, label)
    pos = text.rfind(statement, start, end)
    if pos < 0:
        raise SystemExit(f"{label}: final return statement not found in function")
    return text[:pos] + insertion + text[pos:]


def _insert_before_function_end(
    text: str, function_anchor: str, insertion: str, label: str
) -> str:
    _, end = _function_bounds(text, function_anchor, label)
    close = end - 1
    return text[:close] + insertion + text[close:]


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

    original_replace_once = _base.replace_once

    def scoped_replace_once(text: str, old: str, new: str, label: str) -> str:
        if label == "instrument ufshcd_init success return":
            return _insert_after_in_function(
                text,
                "int ufshcd_init(struct ufs_hba *hba,",
                "\tdevice_enable_async_suspend(dev);\n",
                triplet(
                    "CORE stage=init_return ret=0 state=%d host_no=%d",
                    "hba->ufshcd_state, hba->host->host_no",
                    "\t",
                ),
                label,
            )
        if label == "instrument ufshcd_init failure return":
            return _insert_after_label_in_function(
                text,
                "int ufshcd_init(struct ufs_hba *hba,",
                "out_error:\n",
                triplet(
                    "CORE stage=init_fail ret=%d state=%d cap=0x%x version=0x%x host_no=%d",
                    "err, hba->ufshcd_state, hba->capabilities, hba->ufs_version, "
                    "hba->host ? hba->host->host_no : -1",
                    "\t",
                ),
                label,
            )
        if label == "instrument UFS link startup":
            return _insert_around_in_function(
                text,
                "static int ufshcd_probe_hba(struct ufs_hba *hba, bool async)",
                "\tret = ufshcd_link_startup(hba);\n",
                "\thba->ufshcd_state = UFSHCD_STATE_RESET;\n"
                + triplet(
                    "CORE stage=probe_hba_begin async=%d state=%d link=%d devp=%d",
                    "async, hba->ufshcd_state, hba->uic_link_state, "
                    "ufshcd_is_device_present(hba)",
                    "\t",
                ),
                triplet(
                    "CORE stage=link_startup ret=%d state=%d link=%d devp=%d intr=0x%x hcs=0x%x",
                    "ret, hba->ufshcd_state, hba->uic_link_state, "
                    "ufshcd_is_device_present(hba), "
                    "ufshcd_readl(hba, REG_INTERRUPT_STATUS), "
                    "ufshcd_readl(hba, REG_CONTROLLER_STATUS)",
                    "\t",
                ),
                label,
            ).replace(
                "\thba->ufshcd_state = UFSHCD_STATE_RESET;\n"
                "\thba->ufshcd_state = UFSHCD_STATE_RESET;\n",
                "\thba->ufshcd_state = UFSHCD_STATE_RESET;\n",
                1,
            )
        if label == "instrument UFS probe_hba final result":
            return _insert_before_last_return_in_function(
                text,
                "static int ufshcd_probe_hba(struct ufs_hba *hba, bool async)",
                "\treturn ret;\n",
                triplet(
                    "CORE stage=probe_hba_end ret=%d state=%d link=%d devp=%d manufacturer=0x%x",
                    "ret, hba->ufshcd_state, hba->uic_link_state, "
                    "ufshcd_is_device_present(hba), hba->dev_info.wmanufacturerid",
                    "\t",
                ),
                label,
            )
        if label == "instrument async UFS scan entry":
            return _insert_after_in_function(
                text,
                "static void ufshcd_async_scan(",
                "\tint ret;\n",
                triplet(
                    "CORE stage=async_scan_begin host_no=%d state=%d",
                    "hba->host->host_no, hba->ufshcd_state",
                    "\t",
                ),
                label,
            )
        if label == "instrument async UFS scan exit":
            return _insert_before_function_end(
                text,
                "static void ufshcd_async_scan(",
                triplet(
                    "CORE stage=async_scan_end ret=%d state=%d link=%d",
                    "ret, hba->ufshcd_state, hba->uic_link_state",
                    "\t",
                ),
                label,
            )
        return original_replace_once(text, old, new, label)

    _base.replace_once = scoped_replace_once
    try:
        return _base.instrument_ufshcd(core)
    finally:
        _base.replace_once = original_replace_once
