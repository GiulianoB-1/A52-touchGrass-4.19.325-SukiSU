#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

FILES = {
    "clk-branch.c": {
        "path": "drivers/clk/qcom/clk-branch.c",
        "functions": ("clk_branch_wait", "clk_branch_toggle", "clk_branch2_check_halt",
                      "clk_branch2_enable", "clk_branch2_disable"),
        "tokens": ("clk_branch2_ops", "BRANCH_HALT", "BRANCH_HALT_VOTED"),
    },
    "clk-regmap.c": {
        "path": "drivers/clk/qcom/clk-regmap.c",
        "functions": ("clk_is_enabled_regmap", "clk_enable_regmap", "clk_disable_regmap"),
        "tokens": ("clk_regmap_enable", "enable_reg", "enable_mask"),
    },
    "common.c": {
        "path": "drivers/clk/qcom/common.c",
        "functions": ("qcom_cc_really_probe", "qcom_cc_probe"),
        "tokens": ("devm_regmap_init_mmio", "of_clk_add_provider"),
    },
    "gdsc.c": {
        "path": "drivers/clk/qcom/gdsc.c",
        "functions": ("gdsc_check_status", "gdsc_poll_status", "gdsc_toggle_logic",
                      "gdsc_enable", "gdsc_disable"),
        "tokens": ("PWR_ON_MASK", "SW_COLLAPSE_MASK"),
    },
    "runtime.c": {
        "path": "drivers/base/power/runtime.c",
        "functions": ("rpm_resume", "rpm_suspend", "__pm_runtime_resume", "__pm_runtime_suspend"),
        "tokens": ("RPM_ACTIVE", "RPM_SUSPENDED"),
    },
}

def read(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""

def mask_c(text: str) -> str:
    out = list(text)
    i = 0
    state = "normal"
    esc = False
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "normal":
            if c == "/" and n == "/":
                out[i] = out[i + 1] = " "
                state = "line"
                i += 2
                continue
            if c == "/" and n == "*":
                out[i] = out[i + 1] = " "
                state = "block"
                i += 2
                continue
            if c == '"':
                out[i] = " "; state = "str"; esc = False
            elif c == "'":
                out[i] = " "; state = "char"; esc = False
        elif state == "line":
            if c == "\n":
                state = "normal"
            else:
                out[i] = " "
        elif state == "block":
            if c == "*" and n == "/":
                out[i] = out[i + 1] = " "
                state = "normal"
                i += 2
                continue
            if c != "\n":
                out[i] = " "
        else:
            q = '"' if state == "str" else "'"
            if c != "\n":
                out[i] = " "
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == q:
                    state = "normal"
        i += 1
    return "".join(out)

def extract_function(text: str, name: str) -> str | None:
    masked = mask_c(text)
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        p = match.end() - 1
        depth = 0
        close = -1
        for i in range(p, len(masked)):
            if masked[i] == "(":
                depth += 1
            elif masked[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close < 0:
            continue
        tail = masked[close + 1: close + 4097]
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
                    return text[start:i + 1]
    return None

def normalize(source: str | None) -> str | None:
    if source is None:
        return None
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)

def digest(source: str | None) -> str | None:
    return hashlib.sha256(source.encode()).hexdigest() if source is not None else None

def diff(a: str | None, b: str | None, limit: int = 140) -> list[str]:
    if a is None or b is None:
        return []
    return list(difflib.unified_diff(
        a.splitlines(), b.splitlines(),
        fromfile="TouchGrass-4.19", tofile="GKI-5.10", lineterm=""
    ))[:limit]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gki", type=Path, required=True)
    ap.add_argument("--tg", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ns = ap.parse_args()
    ns.out.mkdir(parents=True, exist_ok=True)

    rows = []
    for label, spec in FILES.items():
        gp = ns.gki / spec["path"]
        tp = ns.tg / spec["path"]
        gs, ts = read(gp), read(tp)
        sub = ns.out / label.replace(".", "_")
        sub.mkdir(exist_ok=True)

        for fn in spec["functions"]:
            gf = extract_function(gs, fn)
            tf = extract_function(ts, fn)
            gn, tn = normalize(gf), normalize(tf)
            equal = gn == tn if gn is not None and tn is not None else None
            row = {
                "file": spec["path"],
                "function": fn,
                "gki_present": gf is not None,
                "touchgrass_present": tf is not None,
                "normalized_equal": equal,
                "gki_sha256": digest(gn),
                "touchgrass_sha256": digest(tn),
                "gki_len": len(gn) if gn is not None else None,
                "touchgrass_len": len(tn) if tn is not None else None,
            }
            rows.append(row)
            if equal is False:
                (sub / (fn + ".diff")).write_text("\n".join(diff(tf, gf)) + "\n")

        token_rows = [{"token": t, "gki": t in gs, "touchgrass": t in ts}
                      for t in spec["tokens"]]
        (sub / "tokens.json").write_text(json.dumps(token_rows, indent=2) + "\n")

    (ns.out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    exact = [r for r in rows if r["normalized_equal"] is True]
    different = [r for r in rows if r["normalized_equal"] is False]
    missing = [r for r in rows if r["normalized_equal"] is None]

    md = [
        "# A52 hidden DSI framework audit", "",
        "Source-only audit; no boot image is produced.", "",
        "- exact normalized functions: **%d**" % len(exact),
        "- differing functions: **%d**" % len(different),
        "- missing on one side: **%d**" % len(missing), "",
        "| Layer | Function | Result |",
        "|---|---|---|",
    ]
    for row in rows:
        result = "exact" if row["normalized_equal"] is True else (
            "different" if row["normalized_equal"] is False else "missing")
        md.append("| %s | %s | **%s** |" %
                  (row["file"], row["function"], result))
    md += ["", "## Interpretation guard", "",
           "A source difference is not automatically causal. Hardware evidence takes precedence.",
           "This audit identifies lower-layer differences that remain after the Phase328 RCG port."]
    (ns.out / "REPORT.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
