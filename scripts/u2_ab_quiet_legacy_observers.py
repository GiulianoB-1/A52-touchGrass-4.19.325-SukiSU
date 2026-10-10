#!/usr/bin/env python3
"""U2-AB: shut OFF known legacy diagnostic samplers, not functional drivers.

Applied after the U2 observer patches and before the final make Image.
Every instrumented C function remains compiled behind a READ_ONCE(true)
runtime guard, avoiding C90 declaration-order warnings and retaining existing
source markers used by the baseline build contracts.

Not claimed as a complete inventory of all Phase2xx..Phase4xx observers:
the audit report explicitly identifies absent targets. U2 USB code is not
changed. A/B purpose: screen for observer-induced MMIO/NoC stalls.
"""
import argparse
import json
import re
from pathlib import Path

MARK = "A52_U2_AB_OBSERVER_QUIET_V1"
TARGETS = {
    "drivers/a52_display/msm/a52_phase445.c": [
        ("a52_p446_mark", "void", True),
        ("p446_workfn", "void", True),
        ("p446_burst_threadfn", "int", True),
        ("a52_p446i_hw_pre", "void", True),
        ("a52_p446i_hw_post", "void", True),
        ("a52_p446i_sw_entry", "void", True),
    ],
    "drivers/a52_secure/a52_ack_secure_flight_recorder.c": [
        ("a52_p430_sampler_fn", "int", False),
        ("a52_p431_exec_fn", "int", False),
        ("a52_p430_ufs_compact", "void", False),
    ],
    "block/blk-mq.c": [
        ("a52_r385_observer_fn", "int", False),
    ],
    "drivers/usb/gadget/udc/core.c": [
        ("a52_r385_usb_observer_fn", "int", False),
    ],
}

def match_bracket(text, pos, opening, closing):
    if text[pos] != opening:
        raise ValueError("opening bracket absent")
    level = 0
    mode = "code"
    i = pos
    while i < len(text):
        c = text[i]
        d = text[i + 1] if i + 1 < len(text) else ""
        if mode == "code":
            if c == '"':
                mode = "string"
            elif c == "'":
                mode = "char"
            elif c == "/" and d == "*":
                mode = "comment"; i += 1
            elif c == "/" and d == "/":
                mode = "line"; i += 1
            elif c == opening:
                level += 1
            elif c == closing:
                level -= 1
                if level == 0:
                    return i
        elif mode == "string":
            if c == "\\": i += 1
            elif c == '"': mode = "code"
        elif mode == "char":
            if c == "\\": i += 1
            elif c == "'": mode = "code"
        elif mode == "comment":
            if c == "*" and d == "/": mode = "code"; i += 1
        elif mode == "line":
            if c == "\n": mode = "code"
        i += 1
    raise ValueError("unterminated "+opening)

def find_body(text, name):
    out = []
    for match in re.finditer(r"\b"+re.escape(name)+r"\s*\(", text):
        op = text.find("(", match.start())
        close_paren = match_bracket(text, op, "(", ")")
        k = close_paren + 1
        while k < len(text) and text[k].isspace(): k += 1
        if k < len(text) and text[k] == "{":
            out.append((k, match_bracket(text, k, "{", "}")))
    if len(out) > 1:
        raise ValueError(f"{name}: found {len(out)} function bodies")
    return out[0] if out else None

def patch_file(path, specs, check_only):
    src = path.read_text()
    installed = {}
    for name, kind, mandatory in specs:
        body = find_body(src, name)
        if body is None:
            if mandatory:
                raise SystemExit(f"U2-AB missing mandatory function {name} in {path}")
            installed[name] = "absent (not modified)"
            continue
        op, close = body
        key = f"a52_u2_ab_disable_{name}"
        if key in src[op:close]:
            installed[name] = "disabled (already patched)"
            continue
        if check_only:
            raise SystemExit(f"U2-AB missing guard on {name} in {path}")
        original = src[op+1:close]
        # Original declarations remain first inside the nested if block.
        guard = (
            f"\n\t/* {MARK} */\n"
            f"\tstatic bool {key} = true;\n"
            f"\tif (!READ_ONCE({key})) {{\n"
            + original +
            f"\n\t}}\n\treturn{' 0' if kind == 'int' else ''};\n"
        )
        src = src[:op+1] + guard + src[close:]
        installed[name] = "disabled"
    if not check_only:
        path.write_text(src)
    return installed

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--check-only", action="store_true")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    report = {}
    for rel, funcs in TARGETS.items():
        target = root / rel
        if not target.is_file():
            raise SystemExit("U2-AB missing kernel source: "+str(target))
        report[rel] = patch_file(target, funcs, args.check_only)
    # The Phase446 function names above are from the archived U2-AB failed\n    # build’s effective a52_phase445.c (GitHub Actions run 38047069220).\n    # Retired historical UFS sampler must not be invoked by this boot.
    ufs = (root/"drivers/scsi/ufs/ufshcd.c").read_text()
    if re.search(r"(?m)^\s*a52_r380_start_sampler\s*\(hba\)\s*;", ufs):
        raise SystemExit("U2-AB UNRETIRED Phase380 UFS sampler: do not flash")
    report["Phase380 UFS"] = "no active a52_r380_start_sampler(hba) call"
    report["scope"] = (
        "Diagnostic-only U2 observer quiet test; native UFS/DSI/MMIO "
        "drivers and U2 USB handover preserved. Not exhaustive legacy observer removal."
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("U2-AB quiet-source audit PASS")

if __name__ == "__main__":
    main()
