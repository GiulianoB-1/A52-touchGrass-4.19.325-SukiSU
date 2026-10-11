#!/usr/bin/env python3
"""FENCE1F: one-shot release of Phase445 fail-stop gate at cold recovery.

Phase445 blocks all future panel transfers once the initial F0 ladder has
exhausted R1. That correctly aborts its initial retries, but also prevents
the later legitimate TouchGrass/Samsung ESD cold-recovery ON command from
reaching the DSI controller. Reopen ONLY at first blocked cont=0 prepare.

No panel/regulator/PLL/PHY operations or F0 packets are modified. The
Phase445 safety mechanism can block again if the post-cold F0 times out.
"""
import argparse
import re
from pathlib import Path

DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
MARK = "A52_FENCE1F_REOPEN_P445_GATE_COLD_V1"
DECL = r'''/* A52_FENCE1F_REOPEN_P445_GATE_COLD_V1: one shot at blocked cold prepare. */
extern bool a52_p445_samsung_blocked(void);
extern void a52_p445_gate_off(void);
static atomic_t a52_fence1f_cold_once = ATOMIC_INIT(0);
'''
HOOK = '''\t/* FENCE1F: the failed Phase445 ladder must not veto a real cold
\t * recovery. Do not touch the initial continuous-splash F0 attempt.
\t * Re-arm at most once, allowing a single decisive cold-ON test.
\t */
\tif (!display->is_cont_splash_enabled &&
\t    a52_p445_samsung_blocked() &&
\t    atomic_cmpxchg(&a52_fence1f_cold_once, 0, 1) == 0) {
\t\ta52_p445_gate_off();
\t\tpr_warn("FENCE1F cold prepare: Phase445 BLOCK reopened once; real Samsung ON commands allowed\\n");
\t}
'''

def patch(s):
    if MARK in s:
        return s
    needle = "int dsi_display_prepare(struct dsi_display *display)\n"
    if s.count(needle) != 1:
        raise RuntimeError("FENCE1F dsi_display_prepare signature not unique")
    if "a52_p445_gate_check" not in s:
        # Phase445 may declare gate_check only in dsi_ctrl.c; use the
        # symbol actually needed by FENCE1F as our source dependency.
        if "a52_p445_gate_off" not in s:
            raise RuntimeError("FENCE1F Phase445 gate helper declaration missing")
    if "#include <linux/atomic.h>" not in s:
        s = "#include <linux/atomic.h>\n" + s
    s = s.replace(needle, DECL + "\n" + needle, 1)
    start = s.index(needle)
    boundary = s.find("\nint dsi_display_enable(", start)
    if boundary < 0:
        boundary = s.find("\nint dsi_display_unprepare(", start)
    if boundary < 0:
        boundary = len(s)
    fn = s[start:boundary]
    # Phase446 F0B installs a P1 marker immediately before the normal
    # dsi_display_set_ctrl_esd_check_flag() call, after all declarations.
    p1 = re.search(r"(?m)^[ \t]*f0b_snap\s*\(\s*display\s*,\s*1\s*,\s*0\s*\);[^\n]*\n", fn)
    if p1:
        at = p1.start()
    else:
        anchor = "\tdsi_display_set_ctrl_esd_check_flag(display, false);\n"
        if fn.count(anchor) != 1:
            raise RuntimeError("FENCE1F cannot find unique cold-prepare P1 boundary")
        at = fn.index(anchor)
    fn = fn[:at] + HOOK + fn[at:]
    return s[:start] + fn + s[boundary:]

def audit(s):
    required = (MARK, "a52_fence1f_cold_once", "a52_p445_gate_off();",
                "!display->is_cont_splash_enabled", "a52_p445_samsung_blocked()",
                "atomic_cmpxchg(&a52_fence1f_cold_once, 0, 1)")
    for v in required:
        if v not in s:
            raise RuntimeError("FENCE1F audit missing " + v)
    if s.count(MARK) != 1:
        raise RuntimeError("FENCE1F duplicated declaration")
    if s.count("a52_p445_gate_off();") != 1:
        raise RuntimeError("FENCE1F gate re-open must occur exactly once")
    if "f0b_snap(display, 1, 0);" not in s:
        raise RuntimeError("FENCE1F original F0B P1 instrumentation lost")
    print("FENCE1F PASS: Phase445 BLOCK reopened at most once on cont=0; original F0 and recovery unchanged")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--check-only", action="store_true")
    a = p.parse_args()
    file = a.root / DISPLAY
    text = file.read_text()
    if not a.check_only:
        text = patch(text)
        file.write_text(text)
    audit(text)

if __name__ == "__main__":
    main()
