#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

TOUCH_ROOT = Path("techpack/display/msm")
ACK_ROOT = Path("drivers/a52_display/msm")

TARGETS: dict[str, tuple[str, ...]] = {
    "dsi/dsi_clk_manager.c": (
        "dsi_clk_update_link_clk_state",
        "dsi_update_core_clks",
        "dsi_update_clk_state",
        "dsi_recheck_clk_state",
        "dsi_clk_request_state",
    ),
    "dsi/dsi_display.c": (
        "dsi_display_bind",
        "dsi_display_probe",
        "dsi_display_prepare",
        "dsi_display_enable",
        "dsi_display_post_enable",
        "dsi_display_pre_disable",
        "dsi_display_disable",
        "dsi_display_unprepare",
    ),
    "dsi/dsi_panel.c": (
        "dsi_panel_prepare",
        "dsi_panel_enable",
        "dsi_panel_post_enable",
        "dsi_panel_pre_disable",
        "dsi_panel_disable",
        "dsi_panel_unprepare",
        "dsi_panel_tx_cmd_set",
    ),
    "sde/sde_crtc.c": (
        "sde_crtc_atomic_enable",
        "sde_crtc_atomic_disable",
        "sde_crtc_atomic_flush",
        "sde_crtc_commit_kickoff",
    ),
    "sde/sde_encoder.c": (
        "sde_encoder_kickoff",
        "sde_encoder_prepare_for_kickoff",
        "sde_encoder_virt_atomic_enable",
        "sde_encoder_virt_atomic_disable",
    ),
    "sde/sde_kms.c": (
        "sde_kms_hw_init",
        "sde_kms_prepare_commit",
        "sde_kms_commit",
        "sde_kms_complete_commit",
    ),
    "msm_drv.c": (
        "msm_drm_bind",
        "msm_drm_init",
    ),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def mask_c(text: str) -> str:
    out = list(text)
    state = "normal"
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "normal":
            if ch == "/" and nxt == "/":
                out[i] = out[i + 1] = " "
                state = "line"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                out[i] = out[i + 1] = " "
                state = "block"
                i += 2
                continue
            if ch == '"':
                out[i] = " "
                state = "string"
                escaped = False
            elif ch == "'":
                out[i] = " "
                state = "char"
                escaped = False
        elif state == "line":
            if ch == "\n":
                state = "normal"
            else:
                out[i] = " "
        elif state == "block":
            if ch == "*" and nxt == "/":
                out[i] = out[i + 1] = " "
                state = "normal"
                i += 2
                continue
            if ch != "\n":
                out[i] = " "
        else:
            quote = '"' if state == "string" else "'"
            if ch == "\n":
                escaped = False
            else:
                out[i] = " "
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    state = "normal"
        i += 1
    return "".join(out)


def top_level_before(masked: str, pos: int) -> bool:
    depth = 0
    for ch in masked[:pos]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth == 0


def extract_function(text: str, name: str) -> str | None:
    masked = mask_c(text)
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        if not top_level_before(masked, match.start()):
            continue
        paren = match.end() - 1
        depth = 0
        close = -1
        for i in range(paren, len(masked)):
            if masked[i] == "(":
                depth += 1
            elif masked[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close < 0:
            continue
        tail = masked[close + 1 : close + 2048]
        brace_rel = tail.find("{")
        semi_rel = tail.find(";")
        if brace_rel < 0 or (semi_rel >= 0 and semi_rel < brace_rel):
            continue
        opening = close + 1 + brace_rel
        depth = 0
        for i in range(opening, len(masked)):
            if masked[i] == "{":
                depth += 1
            elif masked[i] == "}":
                depth -= 1
                if depth == 0:
                    start = text.rfind("\n", 0, match.start()) + 1
                    return text[start : i + 1]
    return None


def normalize_function(text: str) -> str:
    text = re.sub(r"A52_ACKFR_SCOPE\([^;]+;", "", text)
    text = re.sub(r"a52_ackfr_record\([^;]+;", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def diff_excerpt(a: str, b: str, a_name: str, b_name: str, limit: int = 120) -> list[str]:
    lines = list(difflib.unified_diff(
        a.splitlines(), b.splitlines(), fromfile=a_name, tofile=b_name, lineterm=""
    ))
    return lines[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ack", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    files_report: list[dict[str, object]] = []
    function_report: list[dict[str, object]] = []

    for rel, funcs in TARGETS.items():
        t_path = args.touchgrass / TOUCH_ROOT / rel
        a_path = args.ack / ACK_ROOT / rel
        t_exists = t_path.is_file()
        a_exists = a_path.is_file()
        entry: dict[str, object] = {
            "relative_path": rel,
            "touchgrass_path": str(t_path),
            "ack_path": str(a_path),
            "touchgrass_exists": t_exists,
            "ack_exists": a_exists,
        }
        if not (t_exists and a_exists):
            files_report.append(entry)
            continue
        t_text = read(t_path)
        a_text = read(a_path)
        entry.update({
            "touchgrass_sha256": sha256_text(t_text),
            "ack_sha256": sha256_text(a_text),
            "byte_identical": t_text == a_text,
            "touchgrass_lines": len(t_text.splitlines()),
            "ack_lines": len(a_text.splitlines()),
        })
        files_report.append(entry)

        file_diff = diff_excerpt(t_text, a_text, f"touchgrass/{rel}", f"ack/{rel}", 2000)
        (args.output / (rel.replace("/", "__") + ".diff")).write_text(
            "\n".join(file_diff) + ("\n" if file_diff else ""), encoding="utf-8"
        )

        for func in funcs:
            t_func = extract_function(t_text, func)
            a_func = extract_function(a_text, func)
            f_entry: dict[str, object] = {
                "file": rel,
                "function": func,
                "touchgrass_present": t_func is not None,
                "ack_present": a_func is not None,
            }
            if t_func is not None and a_func is not None:
                t_norm = normalize_function(t_func)
                a_norm = normalize_function(a_func)
                f_entry.update({
                    "normalized_equal": t_norm == a_norm,
                    "touchgrass_sha256": sha256_text(t_norm),
                    "ack_sha256": sha256_text(a_norm),
                    "diff_excerpt": diff_excerpt(
                        t_func, a_func, f"touchgrass:{func}", f"ack:{func}", 120
                    ),
                })
            function_report.append(f_entry)

    t_clk = read(args.touchgrass / TOUCH_ROOT / "dsi/dsi_clk_manager.c")
    a_clk = read(args.ack / ACK_ROOT / "dsi/dsi_clk_manager.c")
    and_expr = "(DSI_LINK_LP_CLK & DSI_LINK_HS_CLK)"
    or_expr = "(DSI_LINK_LP_CLK | DSI_LINK_HS_CLK)"

    report = {
        "status": "a52-display-touchgrass-parity-audit-v1",
        "touchgrass_commit_expected": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "active_ack_root": str(args.ack / ACK_ROOT),
        "touchgrass_root": str(args.touchgrass / TOUCH_ROOT),
        "clock_combination": {
            "touchgrass_has_and": and_expr in t_clk,
            "ack_has_and": and_expr in a_clk,
            "touchgrass_has_or": or_expr in t_clk,
            "ack_has_or": or_expr in a_clk,
            "conclusion": (
                "shared-with-working-touchgrass-not-a-standalone-root-cause"
                if and_expr in t_clk and and_expr in a_clk else
                "trees-differ-review-required"
            ),
        },
        "files": files_report,
        "functions": function_report,
        "summary": {
            "files_compared": sum(1 for x in files_report if x.get("touchgrass_exists") and x.get("ack_exists")),
            "byte_identical_files": sum(1 for x in files_report if x.get("byte_identical")),
            "functions_compared": sum(1 for x in function_report if x.get("touchgrass_present") and x.get("ack_present")),
            "normalized_equal_functions": sum(1 for x in function_report if x.get("normalized_equal")),
            "normalized_different_functions": sum(1 for x in function_report if x.get("normalized_equal") is False),
            "missing_ack_functions": [
                f"{x['file']}:{x['function']}" for x in function_report
                if x.get("touchgrass_present") and not x.get("ack_present")
            ],
        },
    }
    (args.output / "a52-display-touchgrass-parity-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(json.dumps(report["clock_combination"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
