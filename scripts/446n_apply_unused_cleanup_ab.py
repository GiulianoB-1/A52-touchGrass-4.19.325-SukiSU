#!/usr/bin/env python3
"""Phase446n: kernel-compiled disable-unused clock+genpd AB experiment.

The only behavior toggled is the two late_initcall_sync unused-resource
cleanups. The existing 446l2 FIFO transaction, timing, and x16 recorder
remain intact. Read-only lagoon DISP_CC CBCR witnesses are separate x16
self-checksummed N_CBCR sections.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE446N_UNUSED_CLEANUP_COMPILED_OFF_V1"

def once(s: str, src: str, dst: str, label: str) -> str:
    n = s.count(src)
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 anchor, got {n}")
    return s.replace(src, dst, 1)

CBCR_HELPER = r'''/* A52_PHASE446N_LAGOON_CBCR_READONLY_V1
 * Physical offsets verified against dispcc-lagoon.c branch definitions.
 * CBCR bit0 is branch enable; bit31 is halt. Never modify these registers.
 * GCC clocks and actual on-chip frequency are deliberately not inferred.
 */
#define P446N_CBCR_COUNT 7U
#define P446N_CBCR_REPLICAS 16U
struct p446n_cbcr_record {
    u32 magic, magic_inv, version, pre_valid, post_valid;
    u64 pre_ns, post_ns;
    u32 pre[P446N_CBCR_COUNT], post[P446N_CBCR_COUNT];
    u32 crc, crc_inv;
} __packed;
static struct p446n_cbcr_record p446n_cbcr;
static const u32 p446n_cbcr_offset[P446N_CBCR_COUNT] = {
    0x100cU, /* DISPCC MDSS PCLK0 */
    0x1010U, /* DISPCC MDSS MDP */
    0x102cU, /* DISPCC MDSS BYTE0 */
    0x1030U, /* DISPCC MDSS BYTE0_INTF */
    0x1034U, /* DISPCC MDSS ESC0 */
    0x104cU, /* DISPCC MDSS AHB */
    0x2004U, /* DISPCC MDSS NON_GDSC_AHB */
};
static void p446n_capture_cbcr(bool post)
{
    void __iomem *base;
    u32 i, *dst;
    base = ioremap(0x0af00000ULL, 0x3000U);
    if (!base)
        return;
    dst = post ? p446n_cbcr.post : p446n_cbcr.pre;
    for (i = 0; i < P446N_CBCR_COUNT; i++)
        dst[i] = readl_relaxed(base + p446n_cbcr_offset[i]);
    if (post) {
        p446n_cbcr.post_valid = 1U;
        p446n_cbcr.post_ns = ktime_get_ns();
    } else {
        p446n_cbcr.pre_valid = 1U;
        p446n_cbcr.pre_ns = ktime_get_ns();
    }
    iounmap(base);
}
static void p446n_publish_cbcr(void)
{
    u32 i;
    p446n_cbcr.magic = 0x4e424331U; /* NBC1 */
    p446n_cbcr.magic_inv = ~p446n_cbcr.magic;
    p446n_cbcr.version = 1;
    p446n_cbcr.crc = crc32_le(~0U, (const u8 *)&p446n_cbcr,
                            offsetof(struct p446n_cbcr_record, crc)) ^ ~0U;
    p446n_cbcr.crc_inv = ~p446n_cbcr.crc;
    for (i = 0; i < P446N_CBCR_REPLICAS; i++) {
        a52_p445_store_section(0x446e0001U, 0x22U, "N_CBCR",
                              i, P446N_CBCR_REPLICAS,
                              &p446n_cbcr, sizeof(p446n_cbcr));
        if (i + 1U < P446N_CBCR_REPLICAS)
            a52_p445_store_section(0x446e0002U, 0x23U, "N_SEP",
                                  i, 0, p446l2_padding, 2048U);
    }
    pr_err("P446N CBCR prevalid=%u postvalid=%u pre_byte=%08x post_byte=%08x pre_esc=%08x post_esc=%08x\n",
           p446n_cbcr.pre_valid, p446n_cbcr.post_valid,
           p446n_cbcr.pre[2], p446n_cbcr.post[2],
           p446n_cbcr.pre[4], p446n_cbcr.post[4]);
}

'''

def patch_tree(root: Path):
    clock = root / "drivers/clk/clk.c"
    power = root / "drivers/base/power/domain.c"
    display = root / "drivers/a52_display/msm/dsi/dsi_display.c"
    for p in (clock, power, display):
        if not p.is_file():
            raise RuntimeError(f"missing kernel source {p}")

    s = clock.read_text()
    if MARK not in s:
        s = once(s, "static int __init clk_disable_unused(void)\n{",
                 "static int __init clk_disable_unused(void)\n{",
                 "clock cleanup entry audit")  # verify exact hook
        s = once(s,
                 'if (clk_ignore_unused) {\n\t\tpr_warn("clk: Not disabling unused clocks\\n");',
                 '/* ' + MARK + ' */\n'
                 '\tclk_ignore_unused = true; /* compiled in, not boot args */\n'
                 '\tpr_warn("P446N clk_disable_unused SKIPPED (compile-time)\\n");\n'
                 '\tif (clk_ignore_unused) {\n\t\tpr_warn("clk: Not disabling unused clocks\\n");',
                 "clock cleanup suppression")
        if "late_initcall_sync(clk_disable_unused);" not in s:
            raise RuntimeError("clock cleanup initcall missing")
        clock.write_text(s)
    s = power.read_text()
    if MARK not in s:
        s = once(s,
                 'if (pd_ignore_unused) {\n\t\tpr_warn("genpd: Not disabling unused power domains\\n");',
                 '/* ' + MARK + ' */\n'
                 '\tpd_ignore_unused = true; /* compiled in, not boot args */\n'
                 '\tpr_warn("P446N genpd_power_off_unused SKIPPED (compile-time)\\n");\n'
                 '\tif (pd_ignore_unused) {\n\t\tpr_warn("genpd: Not disabling unused power domains\\n");',
                 "power-domain cleanup suppression")
        if "late_initcall_sync(genpd_power_off_unused);" not in s:
            raise RuntimeError("power cleanup initcall missing")
        power.write_text(s)
    s = display.read_text()
    if "A52_PHASE446N_LAGOON_CBCR_READONLY_V1" not in s:
        if "A52_PHASE446L2_FIFO_LIVE_SPLASH_V1" not in s:
            raise RuntimeError("Phase446l2 FIFO baseline absent")
        s = once(s, "static void p446l2_publish(struct p446l2_result *p)\n",
                 CBCR_HELPER + "static void p446l2_publish(struct p446l2_result *p)\n",
                 "CBCR helper insertion")
        s = once(s,
                 "    p446l2_snapshot(&v.pre, c, intf);\n",
                 "    p446l2_snapshot(&v.pre, c, intf);\n"
                 "    p446n_capture_cbcr(false);\n",
                 "CBCR PRE")
        s = once(s,
                 "    p446l2_snapshot(&v.post, c, intf);\n",
                 "    p446l2_snapshot(&v.post, c, intf);\n"
                 "    p446n_capture_cbcr(true);\n",
                 "CBCR POST")
        s = once(s,
                 "    p446l2_publish(&v);\n",
                 "    p446l2_publish(&v);\n"
                 "    p446n_publish_cbcr();\n",
                 "CBCR evidence publication")
        display.write_text(s)

def validate(root: Path):
    clk = (root / "drivers/clk/clk.c").read_text()
    pd = (root / "drivers/base/power/domain.c").read_text()
    ds = (root / "drivers/a52_display/msm/dsi/dsi_display.c").read_text()
    for text, key in ((clk, "clk_ignore_unused = true; /* compiled in"),
                      (pd, "pd_ignore_unused = true; /* compiled in"),
                      (ds, "A52_PHASE446N_LAGOON_CBCR_READONLY_V1"),
                      (ds, 'a52_p445_store_section(0x446e0001U'),
                      (ds, 'p446n_publish_cbcr();')):
        if key not in text:
            raise RuntimeError("Phase446n validation missing " + key)
    if clk.count(MARK) != 1 or pd.count(MARK) != 1:
        raise RuntimeError("compiled cleanup markers not unique")
    if ds.count('schedule_delayed_work(&p446l2_work') != 1:
        raise RuntimeError("Phase446l2 timing altered")
    if "msecs_to_jiffies(7000)" not in ds:
        raise RuntimeError("Phase446l2 7-second delay changed")
    print("Phase446n PASS: compiled clock+genpd skip, Phase446l2 identical FIFO, x16 CBCR")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if not args.check_only:
        patch_tree(args.root)
    validate(args.root)

if __name__ == "__main__":
    main()
