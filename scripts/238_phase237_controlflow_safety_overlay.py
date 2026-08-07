#!/usr/bin/env python3
"""Phase 238 control-flow safety pass.

Runs immediately after the broad Phase 238 overlay. It converts standalone
G238 stage records around probe operations into GNU statement-expression
checkpoints, so diagnostics cannot steal the body of a single-line if/else.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PLATFORM_REL = Path("drivers/base/platform.c")
GDSC_REL = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE238_CONTROLFLOW_SAFE_V1"


def candidates(args: list[str]) -> list[Path]:
    out = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        out += [p, p.parent]
    out += [Path("workspace/gki-phase199-src"), Path("gki/common")]
    seen = set()
    uniq = []
    for p in out:
        k = p.resolve(strict=False)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def locate(args: list[str]) -> Path:
    found = []
    for root in candidates(args):
        rec = root / RECORDER_REL
        plat = root / PLATFORM_REL
        gd = root / GDSC_REL
        if not rec.is_file() or not plat.is_file() or not gd.is_file():
            continue
        if "A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1" not in rec.read_text(encoding="utf-8"):
            continue
        found.append(root)
    uniq = []
    seen = set()
    for root in found:
        k = root.resolve()
        if k not in seen:
            seen.add(k)
            uniq.append(root)
    if len(uniq) != 1:
        raise RuntimeError(f"expected one Phase 238 root, found {len(uniq)}")
    return uniq[0]


def find_function(text: str, pattern: str) -> tuple[int, int]:
    m = re.search(pattern, text, re.M)
    if not m:
        raise RuntimeError(f"function missing: {pattern}")
    brace = text.find("{", m.start(), m.end() + 4)
    if brace < 0:
        raise RuntimeError("opening brace missing")
    depth = 0
    state = "code"
    i = brace
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                state = "block"; i += 2; continue
            if c == "/" and n == "/":
                state = "line"; i += 2; continue
            if c == '"':
                state = "string"
            elif c == "'":
                state = "char"
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"; i += 2; continue
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state in ("string", "char"):
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2; continue
            if c == quote:
                state = "code"
        i += 1
    raise RuntimeError("unterminated function")


def call_close(text: str, open_pos: int) -> int:
    depth = 0
    state = "code"
    i = open_pos
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                state = "block"; i += 2; continue
            if c == "/" and n == "/":
                state = "line"; i += 2; continue
            if c == '"':
                state = "string"
            elif c == "'":
                state = "char"
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"; i += 2; continue
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state in ("string", "char"):
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2; continue
            if c == quote:
                state = "code"
        i += 1
    raise RuntimeError("unterminated call")


SUSPICIOUS = (
    ("devm_regulator_get", 110, "devm-reg-get"),
    ("regulator_get", 111, "reg-get"),
    ("platform_get_resource", 120, "resource"),
    ("devm_ioremap_resource", 121, "ioremap"),
    ("devm_regulator_register", 130, "devm-reg-register"),
    ("regulator_register", 131, "reg-register"),
    ("syscon_node_to_regmap", 140, "syscon"),
    ("devm_regmap_init_mmio", 141, "regmap-mmio"),
    ("of_parse_phandle", 142, "phandle"),
    ("of_property_read", 150, "of-prop"),
    ("regulator_enable", 160, "reg-enable"),
    ("regulator_set_voltage", 161, "reg-voltage"),
    ("regulator_set_load", 162, "reg-load"),
    ("clk_prepare_enable", 170, "clk-enable"),
    ("readl", 180, "readl"),
    ("writel", 181, "writel"),
)


def wrap_calls(fn: str, pdev: str) -> str:
    fn = re.sub(
        r'(?m)^[ \t]*a52_g238_gd_stage\([^;\n]+\);[ \t]*\n',
        "",
        fn,
    )

    spans = []
    for token, sid, op in SUSPICIOUS:
        needle = token + "("
        pos = 0
        while True:
            at = fn.find(needle, pos)
            if at < 0:
                break
            close = call_close(fn, at + len(token))
            spans.append((at, close + 1, sid, op))
            pos = close + 1

    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    chosen = []
    last_end = -1
    for span in spans:
        if span[0] >= last_end:
            chosen.append(span)
            last_end = span[1]

    for begin, end, sid, op in reversed(chosen):
        call = fn[begin:end]
        fn = (
            fn[:begin]
            + f'({{ a52_g238_gd_stage({pdev}, {sid}, "{op}", __LINE__); {call}; }})'
            + fn[end:]
        )
    return fn


def patch_platform(text: str) -> str:
    if MARKER in text:
        return text

    anchor = "/* A52_PHASE238_PLATFORM_GPU_TRACE_V1 */\n"
    if anchor not in text:
        raise RuntimeError("Phase 238 platform helper marker missing")
    text = text.replace(anchor, anchor + f"/* {MARKER} */\n", 1)

    start, end = find_function(
        text,
        r"static\s+int\s+platform_drv_probe\s*\(\s*struct\s+device\s*\*\s*_dev\s*\)\s*\{",
    )
    fn = text[start:end]

    fn = re.sub(
        r'(?m)^[ \t]*a52_g238_platform_stage\(_dev,\s*(10|20|30)\);[ \t]*\n',
        "",
        fn,
    )

    clk = "ret = of_clk_set_defaults(_dev->of_node, false);"
    if clk in fn and "({ a52_g238_platform_stage(_dev, 10);" not in fn:
        fn = fn.replace(
            clk,
            "ret = ({ a52_g238_platform_stage(_dev, 10); "
            "of_clk_set_defaults(_dev->of_node, false); });",
            1,
        )

    pm = "ret = dev_pm_domain_attach(_dev, true);"
    if pm in fn and "({ a52_g238_platform_stage(_dev, 20);" not in fn:
        fn = fn.replace(
            pm,
            "ret = ({ a52_g238_platform_stage(_dev, 20); "
            "dev_pm_domain_attach(_dev, true); });",
            1,
        )

    cb = "drv->probe(dev)"
    if cb in fn and "({ a52_g238_platform_stage(_dev, 30);" not in fn:
        fn = fn.replace(
            cb,
            "({ a52_g238_platform_stage(_dev, 30); drv->probe(dev); })",
            1,
        )

    text = text[:start] + fn + text[end:]
    return text


def patch_gdsc(text: str) -> str:
    if MARKER in text:
        return text

    anchor = "/* A52_PHASE238_GDSC_PROVIDER_TRACE_V1 */\n"
    if anchor not in text:
        raise RuntimeError("Phase 238 GDSC helper marker missing")
    text = text.replace(anchor, anchor + f"/* {MARKER} */\n", 1)

    pattern = (
        r"static\s+int\s+([A-Za-z0-9_]*gdsc[A-Za-z0-9_]*probe)"
        r"\s*\(\s*struct\s+platform_device\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\{"
    )
    m = re.search(pattern, text, re.M)
    if not m:
        pattern = (
            r"static\s+int\s+([A-Za-z0-9_]*probe)"
            r"\s*\(\s*struct\s+platform_device\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\{"
        )
        m = re.search(pattern, text, re.M)
    if not m:
        raise RuntimeError("GDSC probe missing")
    pdev = m.group(2)
    start, end = find_function(text, pattern)
    fn = wrap_calls(text[start:end], pdev)
    text = text[:start] + fn + text[end:]
    return text


def main() -> int:
    root = locate(sys.argv[1:])
    platform = root / PLATFORM_REL
    gdsc = root / GDSC_REL
    ptxt = patch_platform(platform.read_text(encoding="utf-8"))
    gtxt = patch_gdsc(gdsc.read_text(encoding="utf-8"))
    platform.write_text(ptxt, encoding="utf-8")
    gdsc.write_text(gtxt, encoding="utf-8")
    if MARKER not in ptxt or MARKER not in gtxt:
        raise RuntimeError("control-flow-safe marker missing after patch")
    print("Phase 238 control-flow safety pass applied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 238 safety pass failed: {exc}", file=sys.stderr)
        raise
