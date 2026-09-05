#!/usr/bin/env python3
import argparse
import difflib
import re
from pathlib import Path

MARKER = "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1"


def esc0_span(text: str):
    start_token = "static struct clk_rcg2 disp_cc_mdss_esc0_clk_src = {"
    start = text.find(start_token)
    if start < 0:
        raise SystemExit("Phase321: ESC0 RCG initializer not found")
    end = text.find("\n};", start)
    if end < 0:
        raise SystemExit("Phase321: ESC0 RCG initializer terminator not found")
    end += len("\n};")
    return start, end


def validate_parent_map(text: str):
    m = re.search(
        r"static const struct parent_map disp_cc_parent_map_1\[\]\s*=\s*\{(?P<body>.*?)\n\};",
        text,
        flags=re.S,
    )
    if not m:
        raise SystemExit("Phase321: disp_cc_parent_map_1 not found")
    body = m.group("body")
    if not re.search(r"\{\s*P_BI_TCXO\s*,\s*0\s*\}", body):
        raise SystemExit("Phase321: safe_src_index 0 is not proven to map to P_BI_TCXO")


def validate_patched(text: str):
    validate_parent_map(text)
    start, end = esc0_span(text)
    block = text[start:end]
    if block.count(".safe_src_index = 0,") != 1:
        raise SystemExit("Phase321: ESC0 must contain exactly one safe_src_index = 0")
    if block.count(".ops = &clk_rcg2_shared_ops,") != 1:
        raise SystemExit("Phase321: ESC0 must contain exactly one clk_rcg2_shared_ops assignment")
    if ".ops = &clk_rcg2_ops," in block:
        raise SystemExit("Phase321: ESC0 still uses clk_rcg2_ops")
    if MARKER not in block:
        raise SystemExit("Phase321: marker missing from ESC0 block")


def apply(text: str):
    validate_parent_map(text)
    start, end = esc0_span(text)
    before = text[start:end]

    if ".safe_src_index" in before:
        raise SystemExit("Phase321: ESC0 already contains safe_src_index; refusing ambiguous patch")
    if before.count(".ops = &clk_rcg2_ops,") != 1:
        raise SystemExit("Phase321: expected exactly one ESC0 clk_rcg2_ops assignment")
    if ".parent_map = disp_cc_parent_map_1," not in before:
        raise SystemExit("Phase321: ESC0 parent map is not disp_cc_parent_map_1")

    hid = re.search(r"(?m)^(?P<i>\s*)\.hid_width\s*=\s*\d+\s*,\s*$", before)
    if not hid:
        raise SystemExit("Phase321: ESC0 hid_width line not found")
    insert_at = hid.end()
    indent = hid.group("i")
    after = before[:insert_at] + (
        f"\n{indent}.safe_src_index = 0,\n"
        f"{indent}/* {MARKER}: native 5.10 safe-source park/restore lifecycle */"
    ) + before[insert_at:]
    after = after.replace(".ops = &clk_rcg2_ops,", ".ops = &clk_rcg2_shared_ops,", 1)

    patched = text[:start] + after + text[end:]
    validate_patched(patched)
    return patched, before, after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    path = Path(args.root) / "drivers/clk/qcom/dispcc-lagoon.c"
    text = path.read_text()

    if args.check_only:
        validate_patched(text)
        print("Phase321 ESC0 shared safe lifecycle check: PASS")
        return

    patched, before, after = apply(text)
    path.write_text(patched)

    diff = "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True),
        fromfile="ESC0-before", tofile="ESC0-after"
    ))
    print(diff, end="")
    print("Phase321 ESC0 shared safe lifecycle patch: PASS")


if __name__ == "__main__":
    main()
