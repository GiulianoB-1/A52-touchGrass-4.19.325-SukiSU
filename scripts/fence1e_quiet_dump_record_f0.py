#!/usr/bin/env python3
"""FENCE1E: quiet enormous synchronous SDE diagnostic dumps and record real F0.

The original Samsung F0 (0x29 [f0 5a 5a]) is unchanged. Stage P1..P6
F0BISECT 16x records stay untouched; CRASH1/FENCE1D remain enabled.
Intercept the Phase446h F0 lifecycle callbacks BEFORE the U2-AB disabled
legacy sampler, and persist only F0 enter, DMA wait, and return markers.
No PLL, regulator, DSI transfer, TE, or ESD functional paths change.
"""
import argparse
from pathlib import Path

P445 = Path("drivers/a52_display/msm/a52_phase445.c")
SDE = Path("drivers/a52_display/msm/sde_dbg.c")
MARK = "A52_FENCE1E_F0_TRACE_16X_V1"
DUMP_MARK = "A52_FENCE1E_SKIP_HEAVY_SDE_DUMP_V1"

HELPER = r'''
/* A52_FENCE1E_F0_TRACE_16X_V1. Passive, bounded 16x diagnostics. */
#include <linux/atomic.h>
#include <linux/ktime.h>
struct a52_fence1e_f0_entry {
    u32 magic, version, sequence, attempt, marker, arg0, arg1, marker_inv;
    u64 ns, ns_inv;
};
static atomic_t a52_fence1e_f0_seq = ATOMIC_INIT(0);
static atomic_t a52_fence1e_f0_attempt = ATOMIC_INIT(0);
static void a52_fence1e_record_f0(u32 marker, u32 arg0, u32 arg1)
{
    struct a52_fence1e_f0_entry e = {0};
    struct a52_fence1e_f0_entry copies[16];
    int seq, attempt, i;

    /* 0x1a0 = F0 detected; 0x1a9 = DMA wait result when active;
     * 0x1ac = original dsi_message_tx error/return label.
     * Legacy Phase446 callbacks for other events remain disabled.
     */
    if (marker != 0x1a0U && marker != 0x1a9U &&
        marker != 0x1acU)
        return;
    if (!p445_image)
        return;
    seq = atomic_inc_return(&a52_fence1e_f0_seq);
    if (seq > 96)
        return;
    if (marker == 0x1a0U)
        attempt = atomic_inc_return(&a52_fence1e_f0_attempt);
    else
        attempt = atomic_read(&a52_fence1e_f0_attempt);
    e.magic = 0x45314630U;  /* ASCII 0F1E, decoder marker */
    e.version = 1U;
    e.sequence = (u32)seq;
    e.attempt = (u32)attempt;
    e.marker = marker;
    e.arg0 = arg0;
    e.arg1 = arg1;
    e.marker_inv = ~marker;
    e.ns = ktime_get_ns();
    e.ns_inv = ~e.ns;
    for (i = 0; i < 16; i++)
        copies[i] = e;
    (void)a52_p445_store_section(0xe10f0001U, 0xe10fU,
                   "F1E_F0_TX", (u32)seq, marker,
                   copies, sizeof(copies));
    pr_warn("FENCE1E F0 attempt=%d seq=%d stage=%03x arg0=%08x arg1=%08x\n",
            attempt, seq, marker, arg0, arg1);
}
'''

def patch_p445(src):
    if MARK in src:
        return src
    sig = "void a52_p446_mark(u32 event,u32 aux0,u32 aux1)"
    # The source uses the same function name and argument types after Phase446;
    # allow formatting whitespace, but insist on exactly one implementation.
    import re
    pat = r"(?m)^void\s+a52_p446_mark\s*\(\s*u32\s+event\s*,\s*u32\s+aux0\s*,\s*u32\s+aux1\s*\)\s*\{"
    matches = list(re.finditer(pat, src))
    if len(matches) != 1:
        raise RuntimeError("FENCE1E Phase446 mark body count=%d" % len(matches))
    init = "static int __init p445_init(void)\n"
    if src.count(init) != 1:
        raise RuntimeError("FENCE1E P445 helper-placement anchor changed")
    src = src.replace(init, HELPER + "\n" + init, 1)
    # Definition may occur before the newly inserted helper.
    matches = list(re.finditer(pat, src))
    start = matches[0].start()
    src = src[:start] + "static void a52_fence1e_record_f0(u32, u32, u32);\n" + src[start:]
    guard = "static bool a52_u2_ab_disable_a52_p446_mark = true;"
    if src.count(guard) != 1:
        raise RuntimeError("FENCE1E missing U2AB disabled marker body")
    src = src.replace(guard,
        guard + "\n\t/* FENCE1E is independent of the quieted old sampler. */"
              "\n\ta52_fence1e_record_f0(event, aux0, aux1);", 1)
    return src

def patch_sde(src):
    if DUMP_MARK in src:
        return src
    key = "static void _sde_dump_array(struct sde_dbg_reg_base *blk_arr[],"
    if src.count(key) != 1:
        raise RuntimeError("FENCE1E heavy dump function missing or ambiguous")
    src = src.replace(key,
        "/* "+ DUMP_MARK + ": do not flood ttyGS0 on display timeout. */\n"
        "static bool a52_fence1e_skip_sde_debug_dump = true;\n" + key, 1)
    start = src.index(key)
    end = src.index("static void _sde_dump_work(", start)
    func = src[start:end]
    anchor = "\tmutex_lock(&sde_dbg_base.mutex);\n"
    if func.count(anchor) != 1:
        raise RuntimeError("FENCE1E SDE dump mutex anchor changed")
    func = func.replace(anchor, anchor +
        "\tif (READ_ONCE(a52_fence1e_skip_sde_debug_dump)) {\n"
        "\t\tpr_warn_once(\"FENCE1E SDE_DBG_DUMP heavy console/register/debug-bus dump skipped; evtlog retained in RAM\\n\");\n"
        "\t\tmutex_unlock(&sde_dbg_base.mutex);\n"
        "\t\treturn;\n"
        "\t}\n", 1)
    return src[:start] + func + src[end:]

def audit(root):
    a = (root / P445).read_text()
    b = (root / SDE).read_text()
    for desc, ok in [
        ("16x persisted real F0 marks", a.count(MARK)==1 and
            'copies[16]' in a and '"F1E_F0_TX"' in a and
            'a52_fence1e_record_f0(event, aux0, aux1);' in a),
        ("U2AB legacy quiet still intact", "a52_u2_ab_disable_a52_p446_mark = true;" in a),
        ("SDE heavy dump suppressed", b.count(DUMP_MARK)==1 and
            "if (READ_ONCE(a52_fence1e_skip_sde_debug_dump))" in b),
        ("SDE event ring not removed", "sde_dbg_base.evtlog" in b),
        ("SDE panic fix preserved", "A52_FENCE1B_SDE_NO_PANIC_V1" in b),
    ]:
        if not ok:
            raise RuntimeError("FENCE1E audit failed: "+desc)
    print("FENCE1E PASS: real Samsung F0 entry/wait/error 16x; heavy SDE dumps skipped; panel and PLL unchanged")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--check-only", action="store_true")
    a = p.parse_args()
    pa = a.root / P445
    pb = a.root / SDE
    if not a.check_only:
        pa.write_text(patch_p445(pa.read_text()))
        pb.write_text(patch_sde(pb.read_text()))
    audit(a.root)

if __name__ == "__main__":
    main()
