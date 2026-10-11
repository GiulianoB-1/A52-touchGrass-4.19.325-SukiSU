#!/usr/bin/env python3
"""FENCE1C cold-rescue PLL fix for 4.19 display port on Android GKI 5.10.

The clock core discards clk_hw.init after clock registration. The legacy PLL
prepare callbacks call hw->init->ops->set_rate during cold panel initialization
(not during continuous-splash handoff), dereferencing NULL at address 0x8.

Replace those callbacks with their actual local set_rate implementations. The
prepare/cached-rate predicates, resource votes and PLL programming stay intact.
"""
import argparse
import re
from pathlib import Path

MARK = "A52_FENCE1C_GKI_PLL_HW_INIT_NULL_V1"
CASES = [
    ("dsi_pll_10nm.c", "vco_10nm_set_rate", True, "pll"),
    ("dsi_pll_7nm.c", "vco_7nm_set_rate", False, "pll"),
    ("dsi_pll_14nm_util.c", "pll_vco_set_rate_14nm", False, "pll"),
    ("dsi_pll_28nm_util.c", "vco_28nm_set_rate", True, "rsc"),
]
REL_ROOTS = [
    Path("drivers/a52_display/pll"),
    Path("drivers/a52_display/msm/pll"),
    Path("drivers/a52_display/msm/dsi/pll"),
]
OLD_TMPL = "rc = hw->init->ops->set_rate(hw, {cache}->vco_cached_rate,"
NEW_TMPL = "rc = {func}(hw, {cache}->vco_cached_rate,"

def find_file(root, name, required):
    hits = [root / p / name for p in REL_ROOTS if (root / p / name).is_file()]
    if len(hits) > 1 or (required and not hits):
        raise RuntimeError("%s: expected %s target, found %s" % (
            name, "one" if required else "zero or one", hits))
    return hits[0] if hits else None

def audit_code(src, name, fn, cache):
    expect = NEW_TMPL.format(func=fn, cache=cache)
    old = OLD_TMPL.format(cache=cache)
    if src.count(MARK) != 1 or src.count(expect) != 1:
        raise RuntimeError("%s: fixed call or marker absent/duplicated" % name)
    if old in src or "hw->init->ops" in src:
        raise RuntimeError("%s: unsafe clk_hw.init use remains" % name)
    if not re.search(r"\b(?:static\s+)?int\s+" + re.escape(fn) + r"\s*\(\s*struct clk_hw\s*\*hw", src):
        raise RuntimeError("%s: local %s definition not found" % (name, fn))
    if cache + "->vco_cached_rate == clk_hw_get_rate(hw)" not in src:
        raise RuntimeError("%s: legacy cold-rate predicate changed" % name)

def audit_inactive_8996_divider(display):
    """Only waive the 8996-only inline helper when no built source calls it."""
    pll_dir = display / "pll"
    header = pll_dir / "pll_drv.h"
    makefile = pll_dir / "Makefile"
    if not header.is_file() or not makefile.is_file():
        raise RuntimeError("Unable to establish legacy divider build reachability")
    hdr = header.read_text()
    unsafe = "return hw->init->ops->set_rate(hw, clk_hw_get_rate(hw),"
    if hdr.count(unsafe) != 1 or hdr.count("hw->init->") != 1:
        raise RuntimeError("Legacy divider helper changed; inspect before waiving")
    if hdr.count("static inline int mdss_pll_div_prepare(") != 1:
        raise RuntimeError("Legacy divider helper definition changed")
    if "dsi_pll_8996.o" in makefile.read_text():
        raise RuntimeError("DSI 8996 now built; legacy divider helper must be ported")
    for target in display.rglob("*.c"):
        if target.name != "dsi_pll_8996.c":
            if "mdss_pll_div_prepare" in target.read_text(errors="replace"):
                raise RuntimeError("Active source references legacy divider: " + str(target))
    print("FENCE1C PLL: skipped dormant 8996-only divider helper (not linked)")
    return header


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()
    root = args.root
    core = (root / "drivers/clk/clk.c").read_text()
    if "hw->init = NULL;" not in core:
        raise RuntimeError("GKI clock registration no longer clears hw->init; inspect this port")

    fixed = []
    for name, fn, mandatory, cache in CASES:
        target = find_file(root, name, mandatory)
        if target is None:
            print("FENCE1C PLL optional absent: " + name)
            continue
        src = target.read_text()
        old = OLD_TMPL.format(cache=cache)
        if not args.check_only and MARK not in src:
            if src.count(old) != 1:
                raise RuntimeError("%s: expected one old callback, found %d" % (name, src.count(old)))
            src = src.replace(
                old,
                "/* " + MARK + ": clk_hw.init is NULL after registration. */\n\t\t"
                + NEW_TMPL.format(func=fn, cache=cache),
                1)
            target.write_text(src)
        audit_code(src, name, fn, cache)
        fixed.append(name)
    print("FENCE1C PLL PASS: " + ", ".join(fixed))

    # Fail closed on active users, while validating the unlinked 8996-only helper.
    display = root / "drivers/a52_display"
    inactive_header = audit_inactive_8996_divider(display)
    suspect = []
    for target in display.rglob("*.[ch]"):
        data = target.read_text(errors="replace")
        for i, line in enumerate(data.splitlines(), 1):
            if re.search(r"(?:\bhw|->hw)\s*->\s*init\s*->", line):
                if target == inactive_header and "hw->init->ops->set_rate" in line:
                    continue
                suspect.append("%s:%d:%s" % (target.relative_to(root), i, line.strip()))
    if suspect:
        raise RuntimeError("Other post-registration clk_hw.init access: " + "; ".join(suspect))
    print("FENCE1C PLL PASS: no other active hw->init-> access under drivers/a52_display")

if __name__ == "__main__":
    main()
