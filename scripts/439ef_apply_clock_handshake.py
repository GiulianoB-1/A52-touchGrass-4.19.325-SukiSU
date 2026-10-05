#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE439EF_CLOCK_HANDSHAKE_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")

def die(msg: str) -> None:
    raise SystemExit("Phase439EF: " + msg)

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

COMMON = r'''
/* A52_PHASE439EF_CLOCK_HANDSHAKE_V1
 *
 * Layered only on the proven Phase439A autopsy.
 * E: force Qualcomm DSI dynamic clock-force bits immediately before the
 *    exact F0 SW_TRIGGER, then restore the original CLK_CTRL on done/timeout.
 * F: no functional writes; sample DSI-local clock/engine state at ~1 us
 *    cadence for the first ~64 us after SW_TRIGGER.
 *
 * Upstream disp_cc reads are deliberately kept OUT of the F hot loop. They
 * are taken only pre-trigger, after the hot window, and at terminal state.
 * Phase424 / SM6350 offsets:
 *   PCLK0 CBCR 0x100c; BYTE0 0x102c; BYTE0_INTF 0x1030; ESC0 0x1034
 *   PCLK0 CMD/CFG 0x1064/0x1068; BYTE0 0x10c4/0x10c8; ESC0 0x10e0/0x10e4
 */
#define A52_P439EF_DENSE_MAX       64U
#define A52_P439EF_PROVIDER_MAX     4U
#define A52_P439E_FORCE_VALUE      (0x23FU | BIT(8) | BIT(9) | BIT(11) | BIT(21))

struct a52_p439ef_dense_sample {
	u64 ns;
	u32 idx, status, clk_status, int_ctrl, dbg171;
};

struct a52_p439ef_provider_sample {
	u64 ns;
	u32 point;
	u32 status, clk_ctrl, clk_status, dbg171;
	u32 pclk0_cbcr, byte0_cbcr, byte0_intf_cbcr, esc0_cbcr;
	u32 pclk0_cmd, pclk0_cfg, byte0_cmd, byte0_cfg;
	u32 esc0_cmd, esc0_cfg, pll_status;
};

static struct a52_p439ef_dense_sample a52_p439ef_dense[A52_P439EF_DENSE_MAX];
static struct a52_p439ef_provider_sample a52_p439ef_provider[A52_P439EF_PROVIDER_MAX];
static atomic_t a52_p439ef_dense_count = ATOMIC_INIT(0);
static atomic_t a52_p439ef_provider_count = ATOMIC_INIT(0);
static atomic_t a52_p439ef_force_active __maybe_unused = ATOMIC_INIT(0);
static atomic_t a52_p439ef_finished = ATOMIC_INIT(0);
static u64 a52_p439ef_trigger_ns;
static u32 a52_p439ef_saved_clk_ctrl;
static u32 a52_p439ef_forced_clk_ctrl;
static u32 a52_p439ef_force_readback;

static __always_inline u32 a52_p439ef_disp_r(struct dsi_ctrl_hw *ctrl, u32 off)
{
	return (ctrl && ctrl->disp_cc_base) ? DSI_DISP_CC_R32(ctrl, off) : ~0U;
}

static void a52_p439ef_provider_capture(struct dsi_ctrl_hw *ctrl, u32 point)
{
	struct a52_p439ef_provider_sample *s;
	unsigned int i;

	if (!ctrl || !ctrl->base)
		return;
	i = (unsigned int)atomic_inc_return(&a52_p439ef_provider_count) - 1U;
	if (i >= A52_P439EF_PROVIDER_MAX)
		return;

	s = &a52_p439ef_provider[i];
	memset(s, 0, sizeof(*s));
	s->ns = ktime_get_ns();
	s->point = point;
	s->status = DSI_R32(ctrl, DSI_STATUS);
	s->clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	s->pclk0_cbcr = a52_p439ef_disp_r(ctrl, 0x100c);
	s->byte0_cbcr = a52_p439ef_disp_r(ctrl, 0x102c);
	s->byte0_intf_cbcr = a52_p439ef_disp_r(ctrl, 0x1030);
	s->esc0_cbcr = a52_p439ef_disp_r(ctrl, 0x1034);
	s->pclk0_cmd = a52_p439ef_disp_r(ctrl, 0x1064);
	s->pclk0_cfg = a52_p439ef_disp_r(ctrl, 0x1068);
	s->byte0_cmd = a52_p439ef_disp_r(ctrl, 0x10c4);
	s->byte0_cfg = a52_p439ef_disp_r(ctrl, 0x10c8);
	s->esc0_cmd = a52_p439ef_disp_r(ctrl, 0x10e0);
	s->esc0_cfg = a52_p439ef_disp_r(ctrl, 0x10e4);
	s->pll_status = a52_p439_pr(0xba0);
}

static void a52_p439ef_pre_trigger(struct dsi_ctrl_hw *ctrl)
{
	atomic_set(&a52_p439ef_finished, 0);
	a52_p439ef_provider_capture(ctrl, 0U);
#if A52_P439EF_FORCE_MODE
	a52_p439ef_saved_clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	a52_p439ef_forced_clk_ctrl =
		a52_p439ef_saved_clk_ctrl | A52_P439E_FORCE_VALUE;
	DSI_W32(ctrl, DSI_CLK_CTRL, a52_p439ef_forced_clk_ctrl);
	wmb();
	a52_p439ef_force_readback = DSI_R32(ctrl, DSI_CLK_CTRL);
	atomic_set(&a52_p439ef_force_active, 1);
#endif
}

static __always_inline void a52_p439ef_note_trigger(void)
{
	WRITE_ONCE(a52_p439ef_trigger_ns, ktime_get_ns());
}

static __maybe_unused void a52_p439ef_after_hot(struct dsi_ctrl_hw *ctrl)
{
	if (!ctrl || !ctrl->base)
		return;
	if (DSI_R32(ctrl, DSI_STATUS) != 0)
		a52_p439ef_provider_capture(ctrl, 50U);
}

static __maybe_unused void a52_p439ef_dense_run(struct dsi_ctrl_hw *ctrl)
{
	u64 start = READ_ONCE(a52_p439ef_trigger_ns);
	unsigned int i;

	if (!ctrl || !ctrl->base || !start)
		return;

	atomic_set(&a52_p439ef_dense_count, 0);
	for (i = 0; i < A52_P439EF_DENSE_MAX; i++) {
		struct a52_p439ef_dense_sample *s = &a52_p439ef_dense[i];
		u64 target = start + (u64)i * 1000ULL;

		while (ktime_get_ns() < target)
			cpu_relax();

		s->ns = ktime_get_ns();
		s->idx = i;
		s->status = DSI_R32(ctrl, DSI_STATUS);
		s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
		s->int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
		s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
		atomic_set(&a52_p439ef_dense_count, i + 1U);
	}

	a52_p439ef_provider_capture(ctrl, 60U);
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p439_saved_dbg);
	wmb();
}

void a52_p439ef_finish(struct dsi_ctrl_hw *ctrl, u32 point)
{
	if (!ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p439ef_finished, 0, 1) != 0)
		return;

	a52_p439ef_provider_capture(ctrl, point);
#if A52_P439EF_FORCE_MODE
	if (atomic_cmpxchg(&a52_p439ef_force_active, 1, 0) == 1) {
		DSI_W32(ctrl, DSI_CLK_CTRL, a52_p439ef_saved_clk_ctrl);
		wmb();
	}
#endif
}
EXPORT_SYMBOL_GPL(a52_p439ef_finish);

static void a52_p439ef_flush(void)
{
	unsigned int i, n;

	a52_ackfr_record(
		"P439 E force=%u saved=%x forced=%x rb=%x forceval=%x",
		(unsigned int)A52_P439EF_FORCE_MODE,
		a52_p439ef_saved_clk_ctrl, a52_p439ef_forced_clk_ctrl,
		a52_p439ef_force_readback, (u32)A52_P439E_FORCE_VALUE);

	n = (unsigned int)atomic_read(&a52_p439ef_provider_count);
	if (n > A52_P439EF_PROVIDER_MAX)
		n = A52_P439EF_PROVIDER_MAX;
	for (i = 0; i < n; i++) {
		const struct a52_p439ef_provider_sample *s = &a52_p439ef_provider[i];

		a52_ackfr_record(
			"P439 V0 i=%u p=%u st=%x cc=%x ck=%x db=%x pc=%x bc=%x bi=%x ec=%x",
			i, s->point, s->status, s->clk_ctrl, s->clk_status,
			s->dbg171, s->pclk0_cbcr, s->byte0_cbcr,
			s->byte0_intf_cbcr, s->esc0_cbcr);
		a52_ackfr_record(
			"P439 V1 i=%u p=%u pm=%x pf=%x bm=%x bf=%x em=%x ef=%x ps=%x",
			i, s->point, s->pclk0_cmd, s->pclk0_cfg,
			s->byte0_cmd, s->byte0_cfg, s->esc0_cmd, s->esc0_cfg,
			s->pll_status);
	}

	n = (unsigned int)atomic_read(&a52_p439ef_dense_count);
	if (n > A52_P439EF_DENSE_MAX)
		n = A52_P439EF_DENSE_MAX;
	for (i = 0; i < n; i++) {
		const struct a52_p439ef_dense_sample *s = &a52_p439ef_dense[i];

		a52_ackfr_record(
			"P439 F i=%u us=%llu st=%x ck=%x in=%x db=%x",
			i,
			(unsigned long long)
				((s->ns - a52_p439ef_trigger_ns) / 1000ULL),
			s->status, s->clk_status, s->int_ctrl, s->dbg171);
	}
}
'''

SEQ = r'''
static void a52_p439ef_seq_dump(struct seq_file *m)
{
	unsigned int i, n;

	seq_printf(m,
		"P439EF variant=%c force=%u saved=%08x forced=%08x rb=%08x trigger_ns=%llu\n",
		A52_P439EF_VARIANT_CHAR, (unsigned int)A52_P439EF_FORCE_MODE,
		a52_p439ef_saved_clk_ctrl, a52_p439ef_forced_clk_ctrl,
		a52_p439ef_force_readback,
		(unsigned long long)a52_p439ef_trigger_ns);

	n = (unsigned int)atomic_read(&a52_p439ef_provider_count);
	if (n > A52_P439EF_PROVIDER_MAX)
		n = A52_P439EF_PROVIDER_MAX;
	for (i = 0; i < n; i++) {
		const struct a52_p439ef_provider_sample *s = &a52_p439ef_provider[i];

		seq_printf(m,
			"V i=%u p=%u ns=%llu st=%08x cc=%08x ck=%08x db=%08x pc=%08x bc=%08x bi=%08x ec=%08x pm=%08x pf=%08x bm=%08x bf=%08x em=%08x ef=%08x ps=%08x\n",
			i, s->point, (unsigned long long)s->ns,
			s->status, s->clk_ctrl, s->clk_status, s->dbg171,
			s->pclk0_cbcr, s->byte0_cbcr, s->byte0_intf_cbcr,
			s->esc0_cbcr, s->pclk0_cmd, s->pclk0_cfg,
			s->byte0_cmd, s->byte0_cfg, s->esc0_cmd, s->esc0_cfg,
			s->pll_status);
	}

	n = (unsigned int)atomic_read(&a52_p439ef_dense_count);
	if (n > A52_P439EF_DENSE_MAX)
		n = A52_P439EF_DENSE_MAX;
	for (i = 0; i < n; i++) {
		const struct a52_p439ef_dense_sample *s = &a52_p439ef_dense[i];

		seq_printf(m,
			"F i=%u ns=%llu delta_ns=%llu st=%08x ck=%08x in=%08x db=%08x\n",
			i, (unsigned long long)s->ns,
			(unsigned long long)(s->ns - a52_p439ef_trigger_ns),
			s->status, s->clk_status, s->int_ctrl, s->dbg171);
	}
}
'''

def patch_hwc(text: str, variant: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439_DSI_AUTOPSY_V3" not in text:
        die("Phase439A baseline marker missing in HWC")

    defs = (
        "#define A52_P439EF_FORCE_MODE 1U\n"
        "#define A52_P439EF_VARIANT_CHAR 'E'\n"
        if variant == "E"
        else "#define A52_P439EF_FORCE_MODE 0U\n"
             "#define A52_P439EF_VARIANT_CHAR 'F'\n"
    )

    anchor = "void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl)\n"
    text = one(text, anchor, defs + COMMON + "\n" + anchor, "helper insertion")

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
    new = old[:-2] + "\n\ta52_p439ef_flush();\n}\n"
    text = one(text, old, new, "flush hook")

    inc = "#include <linux/seq_file.h>\n"
    text = one(text, inc, inc + SEQ + "\n", "seq helper")

    old = '''\t}
\treturn 0;
}

static int a52_p439_dbg_open'''
    new = '''\t}
\ta52_p439ef_seq_dump(m);
\treturn 0;
}

static int a52_p439_dbg_open'''
    text = one(text, old, new, "debugfs dump hook")

    if variant == "F":
        old_unused = "static void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)"
        if old_unused not in text:
            die("F baseline post-trigger helper anchor missing")
        text = text.replace(
            old_unused,
            "static __maybe_unused void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)",
            1,
        )

    if variant == "E":
        after1 = (
            "\t\tif (a52_p439_f0_active())\n"
            "\t\t\ta52_p439_post_trigger(ctrl);\n"
            "\t\tif (a52_p439_f0_active())\n"
            "\t\t\ta52_p439ef_after_hot(ctrl);"
        )
        after2 = (
            "\tif (a52_p439_f0_active())\n"
            "\t\ta52_p439_post_trigger(ctrl);\n"
            "\tif (a52_p439_f0_active())\n"
            "\t\ta52_p439ef_after_hot(ctrl);"
        )
    else:
        after1 = (
            "\t\tif (a52_p439_f0_active())\n"
            "\t\t\ta52_p439ef_dense_run(ctrl);"
        )
        after2 = (
            "\tif (a52_p439_f0_active())\n"
            "\t\ta52_p439ef_dense_run(ctrl);"
        )

    old = '''\t\tif (a52_p439_f0_active())
\t\t\ta52_p439_begin(ctrl);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\tif (a52_p439_f0_active())
\t\t\ta52_p439_post_trigger(ctrl);'''
    new = '''\t\tif (a52_p439_f0_active()) {
\t\t\ta52_p439_begin(ctrl);
\t\t\ta52_p439ef_pre_trigger(ctrl);
\t\t\ta52_p439ef_note_trigger();
\t\t}
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
''' + after1
    text = one(text, old, new, "memory trigger hook")

    old = '''\tif (a52_p439_f0_active())
\t\ta52_p439_begin(ctrl);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\tif (a52_p439_f0_active())
\t\ta52_p439_post_trigger(ctrl);'''
    new = '''\tif (a52_p439_f0_active()) {
\t\ta52_p439_begin(ctrl);
\t\ta52_p439ef_pre_trigger(ctrl);
\t\ta52_p439ef_note_trigger();
\t}
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
''' + after2
    text = one(text, old, new, "deferred trigger hook")

    return text + (
        f'\nstatic const char a52_p439ef_variant[] __used = '
        f'"{MARK}:VARIANT_{variant}";\n'
    )

def patch_ctrl(text: str, variant: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439_DSI_AUTOPSY_V3" not in text:
        die("Phase439A baseline marker missing in CTRL")

    decl = "extern void a52_p439_allow_next(void);\n"
    text = one(
        text, decl,
        decl + "extern void a52_p439ef_finish(struct dsi_ctrl_hw *ctrl, u32 point);\n",
        "finish declaration",
    )

    old = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig))
\t\ta52_p411_timeout_panic(dsi_ctrl);

done:
\tdsi_ctrl->dma_wait_queued = false;'''
    new = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {
\t\ta52_p439ef_finish(&dsi_ctrl->hw, 200U);
\t\ta52_p411_timeout_panic(dsi_ctrl);
\t}

done:
\ta52_p439ef_finish(&dsi_ctrl->hw, 300U);
\tdsi_ctrl->dma_wait_queued = false;'''
    text = one(text, old, new, "terminal finish hook")

    return text + (
        f'\n/* {MARK}: terminal restore/capture variant {variant}. */\n'
    )

def validate(root: Path, variant: str) -> None:
    hp = root / HWC
    cp = root / CTRL
    if not hp.is_file() or not cp.is_file():
        die("required source missing")

    text = hp.read_text(errors="replace") + "\n" + cp.read_text(errors="replace")
    for token in (
        MARK, "a52_p439ef_finish",
        "0x100c", "0x102c", "0x1030", "0x1034",
        "0x1064", "0x1068", "0x10c4", "0x10c8", "0x10e0", "0x10e4",
        "P439 V0", "P439 V1",
    ):
        if token not in text:
            die("missing token: " + token)

    if variant == "E":
        for token in (
            "A52_P439EF_FORCE_MODE 1U",
            "A52_P439E_FORCE_VALUE",
            "a52_p439ef_after_hot",
            "DSI_W32(ctrl, DSI_CLK_CTRL, a52_p439ef_saved_clk_ctrl)",
        ):
            if token not in text:
                die("E missing: " + token)
        if "a52_p439ef_dense_run(ctrl);" in text:
            die("E unexpectedly enables dense sampler")
    else:
        for token in (
            "A52_P439EF_FORCE_MODE 0U",
            "a52_p439ef_dense_run(ctrl);",
            "P439 F i=%u",
        ):
            if token not in text:
                die("F missing: " + token)

    print("Phase439" + variant + " clock-handshake layer: PASS")

def apply(root: Path, variant: str) -> None:
    hp = root / HWC
    cp = root / CTRL
    hp.write_text(patch_hwc(hp.read_text(errors="replace"), variant))
    cp.write_text(patch_ctrl(cp.read_text(errors="replace"), variant))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--variant", choices=["E", "F"], required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()
    if not ns.check_only:
        apply(root, ns.variant)
    validate(root, ns.variant)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
