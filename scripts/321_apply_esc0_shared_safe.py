#!/usr/bin/env python3
import argparse
import difflib
import hashlib
import re
from pathlib import Path

MARKER = "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1"
ESC0_TOKEN = "static struct clk_rcg2 disp_cc_mdss_esc0_clk_src = {"


def esc0_span(text: str):
    start = text.find(ESC0_TOKEN)
    if start < 0:
        raise SystemExit("Phase321: ESC0 RCG initializer not found")
    end = text.find("\n};", start)
    if end < 0:
        raise SystemExit("Phase321: ESC0 RCG initializer terminator not found")
    return start, end + len("\n};")


def esc0_parent_map_name(block: str) -> str:
    m = re.search(r"(?m)^\s*\.parent_map\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*$", block)
    if not m:
        raise SystemExit("Phase321: ESC0 parent_map assignment not found")
    return m.group(1)


def parent_map_body(text: str, map_name: str) -> str:
    decl = re.compile(
        rf"(?m)^\s*(?:static\s+)?(?:const\s+)?struct\s+parent_map\s+{re.escape(map_name)}\s*\[\s*\]\s*=\s*\{{"
    )
    m = decl.search(text)
    if not m:
        raise SystemExit(f"Phase321: parent map declaration not found: {map_name}")

    open_brace = text.find("{", m.start(), m.end())
    if open_brace < 0:
        raise SystemExit(f"Phase321: parent map opening brace not found: {map_name}")

    depth = 0
    for pos in range(open_brace, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1:pos]
    raise SystemExit(f"Phase321: parent map closing brace not found: {map_name}")


def validate_parent_map(text: str, block: str) -> str:
    map_name = esc0_parent_map_name(block)
    body = parent_map_body(text, map_name)
    entries = re.findall(
        r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(0[xX][0-9A-Fa-f]+|\d+)\s*\}",
        body,
    )
    if not entries:
        raise SystemExit(f"Phase321: no parseable entries in parent map: {map_name}")
    zero_sources = [src for src, idx in entries if int(idx, 0) == 0]
    if zero_sources != ["P_BI_TCXO"]:
        raise SystemExit(
            f"Phase321: safe_src_index 0 is not uniquely P_BI_TCXO in {map_name}: {zero_sources!r}"
        )
    return map_name


def validate_patched(text: str):
    start, end = esc0_span(text)
    block = text[start:end]
    map_name = validate_parent_map(text, block)
    if map_name != "disp_cc_parent_map_1":
        raise SystemExit(f"Phase321: ESC0 parent map changed unexpectedly: {map_name}")
    if block.count(".safe_src_index = 0,") != 1:
        raise SystemExit("Phase321: ESC0 must contain exactly one safe_src_index = 0")
    if block.count(".ops = &clk_rcg2_shared_ops,") != 1:
        raise SystemExit("Phase321: ESC0 must contain exactly one clk_rcg2_shared_ops assignment")
    if ".ops = &clk_rcg2_ops," in block:
        raise SystemExit("Phase321: ESC0 still uses clk_rcg2_ops")
    if MARKER not in block:
        raise SystemExit("Phase321: marker missing from ESC0 block")


def apply(text: str):
    start, end = esc0_span(text)
    before = text[start:end]
    map_name = validate_parent_map(text, before)
    if map_name != "disp_cc_parent_map_1":
        raise SystemExit(f"Phase321: ESC0 parent map is not disp_cc_parent_map_1: {map_name}")
    if ".safe_src_index" in before:
        raise SystemExit("Phase321: ESC0 already contains safe_src_index; refusing ambiguous patch")
    if before.count(".ops = &clk_rcg2_ops,") != 1:
        raise SystemExit("Phase321: expected exactly one ESC0 clk_rcg2_ops assignment")

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


def resolve_target(args) -> Path:
    if args.file:
        return Path(args.file)
    return Path(args.root) / "drivers/clk/qcom/dispcc-lagoon.c"


def main():
    ap = argparse.ArgumentParser()
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--root")
    target.add_argument("--file")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    path = resolve_target(args)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    print(
        "Phase321 target "
        f"path={path.resolve()} bytes={len(raw)} sha256={hashlib.sha256(raw).hexdigest()} "
        f"esc0={text.count(ESC0_TOKEN)} map1={text.count('disp_cc_parent_map_1')}"
    )

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
