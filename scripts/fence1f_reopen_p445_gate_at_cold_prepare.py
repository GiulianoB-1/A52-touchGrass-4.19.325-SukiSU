#!/usr/bin/env python3
"""FENCE1F: bounded Phase445 gate release only after successful cold preparation.

Keep Phase445 fail-stop through the initial splash F0, and through all failed
cold-prepare attempts. After the first successful cont=0 prepare that reached
the real F0B P6 checkpoint, reopen once for the actual Samsung panel ON.
If that first post-cold real F0 returns an error, close the gate again.
If it completes successfully, leave the tested normal panel path available.

No panel regulator, PLL, PHY, reset, DMA packet or mode programming changes.
"""
import argparse
import re
from pathlib import Path

DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
P445 = Path("drivers/a52_display/msm/a52_phase445.c")
MARK = "A52_FENCE1F_COLD_SUCCESS_GATE_WINDOW_V2"
RMARK = "A52_FENCE1F_POSTCOLD_F0_REBLOCK_V2"

DECL = r'''/* A52_FENCE1F_COLD_SUCCESS_GATE_WINDOW_V2 */
extern void a52_fence1f_cold_reopen(void);
'''
GATE = r'''
/* A52_FENCE1F_POSTCOLD_F0_REBLOCK_V2
 * 0 untouched, 1 first cold-ON test active, 2 F0 failed/block restored,
 * 3 first cold-ON F0 succeeded; do not interfere with later traffic.
 */
static atomic_t a52_fence1f_window = ATOMIC_INIT(0);
void a52_fence1f_cold_reopen(void)
{
    if (!a52_p445_samsung_blocked() ||
        atomic_cmpxchg(&a52_fence1f_window, 0, 1) != 0)
        return;
    a52_p445_gate_off();
    pr_warn("FENCE1F postcold gate OPEN: first successful P6/prepare; real Samsung ON allowed\n");
}
EXPORT_SYMBOL_GPL(a52_fence1f_cold_reopen);
'''
REB = r'''    /* First postcold real F0 return is the decisive result.
     * Preserve Phase445's original fail-stop if the cold attempt also stalls.
     */
    if (marker == 0x1acU && (s32)arg0 < 0 &&
        atomic_cmpxchg(&a52_fence1f_window, 1, 2) == 1) {
        a52_p445_gate_block();
        pr_warn("FENCE1F postcold F0 failed rc=%d: Phase445 gate BLOCK restored\n",
                (s32)arg0);
    } else if (marker == 0x1acU && (s32)arg0 >= 0 &&
               atomic_cmpxchg(&a52_fence1f_window, 1, 3) == 1) {
        pr_warn("FENCE1F postcold F0 completed rc=%d: gate left OPEN\n",
                (s32)arg0);
    }
'''

def patch_display(s):
    if MARK in s:
        return s
    sig = "int dsi_display_prepare(struct dsi_display *display)\n"
    if s.count(sig) != 1:
        raise RuntimeError("FENCE1F prepare function count != 1")
    start = s.index(sig)
    end = s.find("\n}\n", start)
    if end < 0:
        raise RuntimeError("FENCE1F prepare function end missing")
    end += 3
    fn = s[start:end]
    if fn.count("\tint rc = 0;\n") != 1:
        raise RuntimeError("FENCE1F prepare rc declaration changed")
    fn = fn.replace("\tint rc = 0;\n",
                    "\tint rc = 0;\n\tbool a52_fence1f_p6_seen = false;\n", 1)
    p6 = list(re.finditer(
        r"(?m)^[ \t]*f0b_snap\s*\(\s*display\s*,\s*6\s*,\s*[^)]*\);[^\n]*\n",
        fn))
    if len(p6) != 1:
        raise RuntimeError("FENCE1F requires one real F0BISECT P6 hook, got %d" % len(p6))
    at = p6[0].end()
    fn = fn[:at] + "\ta52_fence1f_p6_seen = true;\n" + fn[at:]
    anchor = "\nerror:\n\tmutex_unlock(&display->display_lock);\n"
    if fn.count(anchor) != 1:
        raise RuntimeError("FENCE1F prepare success exit label missing")
    hook = ( "\nerror:\n"
        "\t/* Reopen ONLY after the successful cold P6 and panel prepare. */\n"
        "\tif (!rc && a52_fence1f_p6_seen &&\n"
        "\t    !display->is_cont_splash_enabled)\n"
        "\t\ta52_fence1f_cold_reopen();\n"
        "\tmutex_unlock(&display->display_lock);\n")
    fn = fn.replace(anchor,hook,1)
    return s[:start] + fn + s[end:].replace(sig, DECL + sig, 1) if False else \
           s[:start] + fn + s[end:] if DECL in s else \
           s[:start] + fn.replace(sig, DECL + sig, 1) + s[end:]

def patch_p445(s):
    if RMARK in s:
        return s
    marker = "static atomic_t a52_fence1e_f0_seq = ATOMIC_INIT(0);"
    if s.count(marker) != 1:
        raise RuntimeError("FENCE1F existing FENCE1E marker source changed")
    s=s.replace(marker,GATE + "\n" + marker,1)
    retline='    pr_warn("FENCE1E F0 attempt=%d seq=%d stage=%03x arg0=%08x arg1=%08x\\n",'
    if s.count(retline) != 1:
        raise RuntimeError("FENCE1F FENCE1E return print anchor changed")
    s=s.replace(retline,REB + retline,1)
    return s

def audit(a,b):
    required_a=(MARK,"a52_fence1f_p6_seen = true;",
                "if (!rc && a52_fence1f_p6_seen &&",
                "a52_fence1f_cold_reopen();",
                "f0b_snap(display, 1, 0);")
    for x in required_a:
        if x not in a:
            raise RuntimeError("FENCE1F missing display guarantee: "+x)
    if a.count(MARK)!=1:
        raise RuntimeError("FENCE1F display marker duplicated")
    required_b=(RMARK,"a52_fence1f_window",
                "atomic_cmpxchg(&a52_fence1f_window, 1, 2)",
                "a52_p445_gate_off();","a52_p445_gate_block();",
                "F1E_F0_TX")
    for x in required_b:
        if x not in b:
            raise RuntimeError("FENCE1F missing persistent fail-stop: "+x)
    if b.count(RMARK)!=1:
        raise RuntimeError("FENCE1F P445 marker duplicated")
    print("FENCE1F PASS: first successful cont=0 P6+prepare reopens once; first real F0 failure reblocks")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    pa=a.root/DISPLAY
    pb=a.root/P445
    if not a.check_only:
        pa.write_text(patch_display(pa.read_text()))
        pb.write_text(patch_p445(pb.read_text()))
    audit(pa.read_text(),pb.read_text())

if __name__ == "__main__":
    main()
