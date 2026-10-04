#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE439EF2_CLOCK_ISOLATION_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def die(msg: str) -> None:
    raise SystemExit("Phase439EF2: " + msg)


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


COMMON = r'''
/* A52_PHASE439EF2_CLOCK_ISOLATION_V1
 *
 * Clean follow-up after Phase439E/F accidentally applied global Lagoon
 * DISP_CC offsets to ctrl->disp_cc_base, which is a different DSI-specific
 * MMIO resource.  This phase deliberately separates three one-variable boots:
 *
 * E2: force Qualcomm DSI dynamic-clock bits only. No provider MMIO reads.
 * F2: dense ~1 us DSI-local sampler only. No provider MMIO reads.
 * P : breadcrumbed provider probe using the proven Phase424 physical mapping
 *     of global Lagoon DISP_CC at 0x0af00000. No force-on and no dense loop.
 */
#define A52_P439EF2_DENSE_MAX       64U
#define A52_P439E2_FORCE_VALUE      (0x23FU | BIT(8) | BIT(9) | BIT(11) | BIT(21))
#define A52_P439P_DISPCC_PHYS       0x0af00000ULL
#define A52_P439P_DISPCC_SIZE       0x3000U

struct a52_p439ef2_dense_sample {
	u64 ns;
	u32 idx, status, clk_status, int_ctrl, dbg171;
};

static struct a52_p439ef2_dense_sample a52_p439ef2_dense[A52_P439EF2_DENSE_MAX];
static atomic_t a52_p439ef2_dense_count = ATOMIC_INIT(0);
static u64 a52_p439ef2_trigger_ns;
static u32 a52_p439ef2_saved_clk_ctrl;
static u32 a52_p439ef2_forced_clk_ctrl;
static u32 a52_p439ef2_force_readback;
static atomic_t a52_p439ef2_force_active = ATOMIC_INIT(0);
static atomic_t a52_p439ef2_finished = ATOMIC_INIT(0);
static void __iomem *a52_p439p_dispcc;

static int __init a52_p439p_map_init(void)
{
#if A52_P439EF2_PROVIDER_MODE
	a52_p439p_dispcc = ioremap(A52_P439P_DISPCC_PHYS,
				     A52_P439P_DISPCC_SIZE);
#endif
	return 0;
}
subsys_initcall(a52_p439p_map_init);

static __always_inline void a52_p439ef2_note_trigger(void)
{
	WRITE_ONCE(a52_p439ef2_trigger_ns, ktime_get_ns());
}

static void a52_p439e2_force_pre(struct dsi_ctrl_hw *ctrl)
{
#if A52_P439EF2_FORCE_MODE
	if (!ctrl || !ctrl->base)
		return;
	a52_p439ef2_saved_clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	a52_p439ef2_forced_clk_ctrl =
		a52_p439ef2_saved_clk_ctrl | A52_P439E2_FORCE_VALUE;
	DSI_W32(ctrl, DSI_CLK_CTRL, a52_p439ef2_forced_clk_ctrl);
	wmb();
	a52_p439ef2_force_readback = DSI_R32(ctrl, DSI_CLK_CTRL);
	atomic_set(&a52_p439ef2_force_active, 1);
	/* Phase406 makes this reserved-RAM breadcrumb cache-clean/persistent. */
	a52_ackfr_record("P439E2 pre saved=%x forced=%x rb=%x",
		a52_p439ef2_saved_clk_ctrl, a52_p439ef2_forced_clk_ctrl,
		a52_p439ef2_force_readback);
#endif
}

static void a52_p439f2_dense_run(struct dsi_ctrl_hw *ctrl)
{
#if A52_P439EF2_DENSE_MODE
	u64 start = READ_ONCE(a52_p439ef2_trigger_ns);
	unsigned int i;

	if (!ctrl || !ctrl->base || !start)
		return;
	atomic_set(&a52_p439ef2_dense_count, 0);
	for (i = 0; i < A52_P439EF2_DENSE_MAX; i++) {
		struct a52_p439ef2_dense_sample *s = &a52_p439ef2_dense[i];
		u64 target = start + (u64)i * 1000ULL;

		while (ktime_get_ns() < target)
			cpu_relax();
		s->ns = ktime_get_ns();
		s->idx = i;
		s->status = DSI_R32(ctrl, DSI_STATUS);
		s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
		s->int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
		s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
		atomic_set(&a52_p439ef2_dense_count, i + 1U);
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p439_saved_dbg);
	wmb();
#endif
}

#if A52_P439EF2_PROVIDER_MODE
static u32 a52_p439p_read(u32 point, const char *name, u32 off)
{
	u32 v;

	a52_ackfr_record("P439P about p=%u n=%s off=%x", point, name, off);
	if (!a52_p439p_dispcc) {
		a52_ackfr_record("P439P nomap p=%u n=%s", point, name);
		return ~0U;
	}
	v = readl_relaxed((u8 __iomem *)a52_p439p_dispcc + off);
	a52_ackfr_record("P439P got p=%u n=%s v=%x", point, name, v);
	return v;
}

static void a52_p439p_probe(struct dsi_ctrl_hw *ctrl, u32 point)
{
	/* Exact register set already proven safe by Phase424, now breadcrumbed. */
	a52_ackfr_record("P439P begin p=%u st=%x ck=%x", point,
		ctrl ? DSI_R32(ctrl, DSI_STATUS) : ~0U,
		ctrl ? DSI_R32(ctrl, DSI_CLK_STATUS) : ~0U);
	a52_p439p_read(point, "pclk_cbcr", 0x100c);
	a52_p439p_read(point, "byte_cbcr", 0x102c);
	a52_p439p_read(point, "byte_intf", 0x1030);
	a52_p439p_read(point, "esc_cbcr", 0x1034);
	a52_p439p_read(point, "pclk_cmd", 0x1064);
	a52_p439p_read(point, "pclk_cfg", 0x1068);
	a52_p439p_read(point, "byte_cmd", 0x10c4);
	a52_p439p_read(point, "byte_cfg", 0x10c8);
	a52_p439p_read(point, "esc_cmd", 0x10e0);
	a52_p439p_read(point, "esc_cfg", 0x10e4);
	a52_ackfr_record("P439P about p=%u n=pll_status off=ba0", point);
	a52_ackfr_record("P439P got p=%u n=pll_status v=%x", point,
		a52_p439_pr(0xba0));
	a52_ackfr_record("P439P end p=%u", point);
}
#else
static __always_inline void a52_p439p_probe(struct dsi_ctrl_hw *ctrl, u32 point)
{
}
#endif

void a52_p439ef2_finish(struct dsi_ctrl_hw *ctrl, u32 point)
{
	if (!ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p439ef2_finished, 0, 1) != 0)
		return;
#if A52_P439EF2_FORCE_MODE
	if (atomic_cmpxchg(&a52_p439ef2_force_active, 1, 0) == 1) {
		DSI_W32(ctrl, DSI_CLK_CTRL, a52_p439ef2_saved_clk_ctrl);
		wmb();
		a52_ackfr_record("P439E2 restore p=%u cc=%x ck=%x", point,
			DSI_R32(ctrl, DSI_CLK_CTRL), DSI_R32(ctrl, DSI_CLK_STATUS));
	}
#endif
}
EXPORT_SYMBOL_GPL(a52_p439ef2_finish);

static void a52_p439ef2_flush(void)
{
	unsigned int i, n;

	a52_ackfr_record("P439EF2 v=%c force=%u dense=%u provider=%u trig=%llu",
		A52_P439EF2_VARIANT_CHAR,
		(unsigned int)A52_P439EF2_FORCE_MODE,
		(unsigned int)A52_P439EF2_DENSE_MODE,
		(unsigned int)A52_P439EF2_PROVIDER_MODE,
		(unsigned long long)a52_p439ef2_trigger_ns);
	if (A52_P439EF2_FORCE_MODE)
		a52_ackfr_record("P439E2 state saved=%x forced=%x rb=%x active=%d",
			a52_p439ef2_saved_clk_ctrl, a52_p439ef2_forced_clk_ctrl,
			a52_p439ef2_force_readback,
			atomic_read(&a52_p439ef2_force_active));

	n = (unsigned int)atomic_read(&a52_p439ef2_dense_count);
	if (n > A52_P439EF2_DENSE_MAX)
		n = A52_P439EF2_DENSE_MAX;
	for (i = 0; i < n; i++) {
		const struct a52_p439ef2_dense_sample *s = &a52_p439ef2_dense[i];
		a52_ackfr_record("P439F2 i=%u ns=%llu dn=%llu st=%x ck=%x in=%x db=%x",
			i, (unsigned long long)s->ns,
			(unsigned long long)(s->ns - a52_p439ef2_trigger_ns),
			s->status, s->clk_status, s->int_ctrl, s->dbg171);
	}
}
'''


def defs_for(variant: str) -> str:
    if variant == "E2":
        return "#define A52_P439EF2_FORCE_MODE 1U\n#define A52_P439EF2_DENSE_MODE 0U\n#define A52_P439EF2_PROVIDER_MODE 0U\n#define A52_P439EF2_VARIANT_CHAR 'E'\n"
    if variant == "F2":
        return "#define A52_P439EF2_FORCE_MODE 0U\n#define A52_P439EF2_DENSE_MODE 1U\n#define A52_P439EF2_PROVIDER_MODE 0U\n#define A52_P439EF2_VARIANT_CHAR 'F'\n"
    return "#define A52_P439EF2_FORCE_MODE 0U\n#define A52_P439EF2_DENSE_MODE 0U\n#define A52_P439EF2_PROVIDER_MODE 1U\n#define A52_P439EF2_VARIANT_CHAR 'P'\n"


def patch_hwc(text: str, variant: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439_DSI_AUTOPSY_V3" not in text:
        die("Phase439A baseline marker missing in HWC")

    anchor = "void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl)\n"
    text = one(text, anchor, defs_for(variant) + COMMON + "\n" + anchor, "helper insertion")

    old = '''\tif (ctrl && ctrl->base)
\t\ta52_ackfr_record(
\t\t\t"P439 T st=%x in=%x cc=%x ck=%x s1=%x s2=%x tg=%x n=%u",
\t\t\tDSI_R32(ctrl, DSI_STATUS),
\t\t\tDSI_R32(ctrl, DSI_INT_CTRL),
\t\t\tDSI_R32(ctrl, DSI_CLK_CTRL),
\t\t\tDSI_R32(ctrl, DSI_CLK_STATUS),
\t\t\tDSI_R32(ctrl, A52_P439_DMA_SCHED_CTRL),
\t\t\tDSI_R32(ctrl, A52_P439_DMA_SCHED_CTRL2),
\t\t\tDSI_R32(ctrl, DSI_TRIG_CTRL), n);
}
'''
    new = old[:-2] + "\n\ta52_p439ef2_flush();\n}\n"
    text = one(text, old, new, "flush hook")

    if variant == "F2":
        old_unused = "static void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)"
        text = one(text, old_unused,
                   "static __maybe_unused void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)",
                   "F2 unused post helper")

    if variant == "E2":
        after_mem = "\t\tif (a52_p439_f0_active())\n\t\t\ta52_p439_post_trigger(ctrl);"
        after_def = "\tif (a52_p439_f0_active())\n\t\ta52_p439_post_trigger(ctrl);"
    elif variant == "F2":
        after_mem = "\t\tif (a52_p439_f0_active())\n\t\t\ta52_p439f2_dense_run(ctrl);"
        after_def = "\tif (a52_p439_f0_active())\n\t\ta52_p439f2_dense_run(ctrl);"
    else:
        after_mem = ("\t\tif (a52_p439_f0_active()) {\n"
                     "\t\t\ta52_p439_post_trigger(ctrl);\n"
                     "\t\t\ta52_p439p_probe(ctrl, 60U);\n"
                     "\t\t}")
        after_def = ("\tif (a52_p439_f0_active()) {\n"
                     "\t\ta52_p439_post_trigger(ctrl);\n"
                     "\t\ta52_p439p_probe(ctrl, 60U);\n"
                     "\t}")

    old = '''\t\tif (a52_p439_f0_active())
\t\t\ta52_p439_begin(ctrl);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\tif (a52_p439_f0_active())
\t\t\ta52_p439_post_trigger(ctrl);'''
    pre = '''\t\tif (a52_p439_f0_active()) {
\t\t\ta52_p439_begin(ctrl);
'''
    if variant == "E2":
        pre += "\t\t\ta52_p439e2_force_pre(ctrl);\n"
    elif variant == "F2":
        pre += "\t\t\ta52_p439ef2_note_trigger();\n"
    else:
        pre += "\t\t\ta52_p439p_probe(ctrl, 0U);\n"
    pre += "\t\t}\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n" + after_mem
    text = one(text, old, pre, "memory trigger hook")

    old = '''\tif (a52_p439_f0_active())
\t\ta52_p439_begin(ctrl);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\tif (a52_p439_f0_active())
\t\ta52_p439_post_trigger(ctrl);'''
    pre = '''\tif (a52_p439_f0_active()) {
\t\ta52_p439_begin(ctrl);
'''
    if variant == "E2":
        pre += "\t\ta52_p439e2_force_pre(ctrl);\n"
    elif variant == "F2":
        pre += "\t\ta52_p439ef2_note_trigger();\n"
    else:
        pre += "\t\ta52_p439p_probe(ctrl, 0U);\n"
    pre += "\t}\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n" + after_def
    text = one(text, old, pre, "deferred trigger hook")

    return text + f'\nstatic const char a52_p439ef2_variant[] __used = "{MARK}:VARIANT_{variant}";\n'


def patch_ctrl(text: str, variant: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439_DSI_AUTOPSY_V3" not in text:
        die("Phase439A baseline marker missing in CTRL")

    decl = "extern void a52_p439_allow_next(void);\n"
    text = one(text, decl,
               decl + "extern void a52_p439ef2_finish(struct dsi_ctrl_hw *ctrl, u32 point);\n",
               "finish declaration")

    old = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig))
\t\ta52_p411_timeout_panic(dsi_ctrl);

done:
\tdsi_ctrl->dma_wait_queued = false;'''
    new = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {
\t\ta52_p439ef2_finish(&dsi_ctrl->hw, 200U);
\t\ta52_p411_timeout_panic(dsi_ctrl);
\t}

done:
\ta52_p439ef2_finish(&dsi_ctrl->hw, 300U);
\tdsi_ctrl->dma_wait_queued = false;'''
    text = one(text, old, new, "terminal finish hook")
    return text + f"\n/* {MARK}: terminal capture/restore variant {variant}. */\n"


def validate(root: Path, variant: str) -> None:
    hp, cp = root / HWC, root / CTRL
    if not hp.is_file() or not cp.is_file():
        die("required source missing")
    text = hp.read_text(errors="replace") + "\n" + cp.read_text(errors="replace")
    for token in (MARK, "a52_p439ef2_finish", "P439EF2 v=%c"):
        if token not in text:
            die("missing token: " + token)
    if variant == "E2":
        for token in ("A52_P439EF2_FORCE_MODE 1U", "P439E2 pre", "A52_P439E2_FORCE_VALUE"):
            if token not in text: die("E2 missing: " + token)
        if "a52_p439p_probe(ctrl, 0U);" in text or "a52_p439f2_dense_run(ctrl);" in text:
            die("E2 contaminated by provider/dense path")
    elif variant == "F2":
        for token in ("A52_P439EF2_DENSE_MODE 1U", "a52_p439f2_dense_run(ctrl);", "P439F2 i=%u"):
            if token not in text: die("F2 missing: " + token)
        if "a52_p439p_probe(ctrl, 0U);" in text or "a52_p439e2_force_pre(ctrl);" in text:
            die("F2 contaminated by provider/force path")
    else:
        for token in ("A52_P439EF2_PROVIDER_MODE 1U", "A52_P439P_DISPCC_PHYS", "P439P about", "0x0af00000ULL"):
            if token not in text: die("P missing: " + token)
        if "DSI_DISP_CC_R32(ctrl, off)" in text:
            die("P must not use DSI-specific disp_cc_base for Lagoon offsets")
        if "a52_p439e2_force_pre(ctrl);" in text or "a52_p439f2_dense_run(ctrl);" in text:
            die("P contaminated by force/dense path")
    print("Phase439" + variant + " clock isolation: PASS")


def apply(root: Path, variant: str) -> None:
    hp, cp = root / HWC, root / CTRL
    hp.write_text(patch_hwc(hp.read_text(errors="replace"), variant))
    cp.write_text(patch_ctrl(cp.read_text(errors="replace"), variant))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--variant", choices=["E2", "F2", "P"], required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()
    if not ns.check_only:
        apply(root, ns.variant)
    validate(root, ns.variant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
