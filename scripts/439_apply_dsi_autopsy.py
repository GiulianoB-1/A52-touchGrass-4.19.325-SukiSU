#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE439_DSI_AUTOPSY_V3"
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
DISP = Path("drivers/a52_display/msm/dsi/dsi_display.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def die(msg: str) -> None:
    raise SystemExit("Phase439: " + msg)


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


COMMON_HWC = r'''
/* A52_PHASE439_DSI_AUTOPSY_V3
 *
 * Exact-F0 DSI autopsy.
 *
 * Hot path:
 *   PRE, +15 us, +35 us, +50 us and timeout snapshots are stored in normal
 *   RAM only.  No persistent recorder calls happen inside the first 50 us.
 *
 * Slow path:
 *   only after the normal ~200 ms DMA_DONE timeout do we persist the samples,
 *   sweep the link controller debug bus and deliberately panic for ramoops.
 *
 * 0x100/0x104 are Qualcomm's command-DMA scheduling controls.  The inherited
 * 4.19 driver only knows 0x100 and never initializes 0x104.  Continuous splash
 * can skip host_setup(), so both are first-class evidence for stale bootloader
 * state.
 */
#define A52_P439_PHY_PHYS          0x0ae94000ULL
#define A52_P439_PHY_SIZE          0x1000U
#define A52_P439_DMA_SCHED_CTRL    0x0100U
#define A52_P439_DMA_SCHED_CTRL2   0x0104U
#define A52_P439_MAX               10U

extern bool a52_p439_f0_active(void);
extern void a52_ackfr_record(const char *fmt, ...);

struct a52_p439_sample {
	u64 ns;
	u32 epoch, point;
	u32 status, fifo, clk_ctrl, clk_status, int_ctrl;
	u32 lane_status, lane_ctrl;
	u32 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl;
	u32 sched1, sched2, disp_misc;
	u32 ack_err, timeout, phy_err, axi2ahb, dbg171;
	u32 pll_status_one, phy_pll_ctrl, phy_ctrl0, phy_rbuf, phy_clk_cfg1;
	u32 phy_lane0, phy_lane1;
};

static struct a52_p439_sample a52_p439_samples[A52_P439_MAX];
static atomic_t a52_p439_count = ATOMIC_INIT(0);
static atomic_t a52_p439_active = ATOMIC_INIT(0);
static atomic_t a52_p439_post_pending = ATOMIC_INIT(0);
static atomic_t a52_p439_epoch = ATOMIC_INIT(0);
static u32 a52_p439_current_epoch;
static void __iomem *a52_p439_phy;
static u32 a52_p439_saved_dbg;

static int __init a52_p439_map_init(void)
{
	a52_p439_phy = ioremap(A52_P439_PHY_PHYS, A52_P439_PHY_SIZE);
	return 0;
}
subsys_initcall(a52_p439_map_init);

static __always_inline u32 a52_p439_pr(u32 off)
{
	return a52_p439_phy ?
		readl_relaxed((u8 __iomem *)a52_p439_phy + off) : ~0U;
}

static void a52_p439_store(struct dsi_ctrl_hw *ctrl, u32 point)
{
	struct a52_p439_sample *s;
	unsigned int i;

	if (atomic_read(&a52_p439_active) != 1 || !ctrl || !ctrl->base)
		return;

	i = (unsigned int)atomic_inc_return(&a52_p439_count) - 1U;
	if (i >= A52_P439_MAX)
		return;

	s = &a52_p439_samples[i];
	memset(s, 0, sizeof(*s));
	s->ns = ktime_get_ns();
	s->epoch = READ_ONCE(a52_p439_current_epoch);
	s->point = point;

	s->status = DSI_R32(ctrl, DSI_STATUS);
	s->fifo = DSI_R32(ctrl, DSI_FIFO_STATUS);
	s->clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	s->int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
	s->lane_status = DSI_R32(ctrl, DSI_LANE_STATUS);
	s->lane_ctrl = DSI_R32(ctrl, DSI_LANE_CTRL);

	s->dma_ctrl = DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL);
	s->dma_offset = DSI_R32(ctrl, DSI_DMA_CMD_OFFSET);
	s->dma_length = DSI_R32(ctrl, DSI_DMA_CMD_LENGTH);
	s->sw_trigger = DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER);
	s->trig_ctrl = DSI_R32(ctrl, DSI_TRIG_CTRL);

	s->sched1 = DSI_R32(ctrl, A52_P439_DMA_SCHED_CTRL);
	s->sched2 = DSI_R32(ctrl, A52_P439_DMA_SCHED_CTRL2);
	s->disp_misc = ctrl->disp_cc_base ? DSI_DISP_CC_R32(ctrl, 0x0) : ~0U;

	s->ack_err = DSI_R32(ctrl, DSI_ACK_ERR_STATUS);
	s->timeout = DSI_R32(ctrl, DSI_TIMEOUT_STATUS);
	s->phy_err = DSI_R32(ctrl, DSI_DLN0_PHY_ERR);
	s->axi2ahb = DSI_R32(ctrl, DSI_AXI2AHB_CTRL);
	s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);

	s->phy_clk_cfg1 = a52_p439_pr(0x414);
	s->phy_rbuf = a52_p439_pr(0x41c);
	s->phy_ctrl0 = a52_p439_pr(0x424);
	s->phy_pll_ctrl = a52_p439_pr(0x438);
	s->phy_lane0 = a52_p439_pr(0x4f4);
	s->phy_lane1 = a52_p439_pr(0x4f8);
	s->pll_status_one = a52_p439_pr(0xba0);
}

static __always_inline void a52_p439_wait_until(u64 start, u64 delta)
{
	while ((ktime_get_ns() - start) < delta)
		cpu_relax();
}

static void a52_p439_begin(struct dsi_ctrl_hw *ctrl)
{
	if (!a52_p439_f0_active() || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p439_active, 0, 1) != 0)
		return;

	WRITE_ONCE(a52_p439_current_epoch,
		   (u32)atomic_inc_return(&a52_p439_epoch));

	/*
	 * Selector 0x0171 is our one precisely timed internal-state probe.
	 * A broad selector sweep is intentionally deferred until timeout.
	 */
	a52_p439_saved_dbg = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, 0x0171);
	wmb();

	atomic_set(&a52_p439_post_pending, 1);
	a52_p439_store(ctrl, 0U);
}

static void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)
{
	u64 start;

	if (atomic_cmpxchg(&a52_p439_post_pending, 1, 0) != 1 ||
	    !ctrl || !ctrl->base)
		return;

	start = ktime_get_ns();
	a52_p439_wait_until(start, 15000ULL);
	a52_p439_store(ctrl, 15U);
	a52_p439_wait_until(start, 35000ULL);
	a52_p439_store(ctrl, 35U);
	a52_p439_wait_until(start, 50000ULL);
	a52_p439_store(ctrl, 50U);

	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p439_saved_dbg);
	wmb();
}

void a52_p439_allow_next(void)
{
	/*
	 * Variant B calls this only after the early F0 unlock and matching
	 * relock have both returned.  Keep the first epoch's samples, but re-arm
	 * the hot-path state so the later Android F0 becomes epoch 2.
	 */
	atomic_set(&a52_p439_post_pending, 0);
	atomic_set(&a52_p439_active, 0);
}
EXPORT_SYMBOL_GPL(a52_p439_allow_next);

void a52_p439_timeout_snapshot(struct dsi_ctrl_hw *ctrl)
{
	if (atomic_read(&a52_p439_active) != 1 || !a52_p439_f0_active())
		return;
	a52_p439_store(ctrl, 200U);
}

void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_p439_count);
	u64 base = 0;

	if (n > A52_P439_MAX)
		n = A52_P439_MAX;
	if (n)
		base = a52_p439_samples[0].ns;

	for (i = 0; i < n; i++) {
		const struct a52_p439_sample *s = &a52_p439_samples[i];

		a52_ackfr_record(
			"P439 S i=%u e=%u p=%u us=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x db=%x",
			i, s->epoch, s->point,
			(unsigned long long)((s->ns - base) / 1000ULL),
			s->status, s->fifo, s->clk_ctrl, s->clk_status,
			s->int_ctrl, s->lane_status, s->dbg171);

		a52_ackfr_record(
			"P439 D i=%u e=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x",
			i, s->epoch, s->dma_ctrl, s->dma_offset,
			s->dma_length, s->sw_trigger, s->trig_ctrl,
			s->ack_err, s->timeout, s->phy_err, s->axi2ahb);

		a52_ackfr_record(
			"P439 Q i=%u e=%u s1=%x s2=%x dm=%x lc=%x",
			i, s->epoch, s->sched1, s->sched2,
			s->disp_misc, s->lane_ctrl);

		a52_ackfr_record(
			"P439 P i=%u e=%u ps=%x pc=%x c0=%x rb=%x cf=%x l0=%x l1=%x",
			i, s->epoch, s->pll_status_one, s->phy_pll_ctrl,
			s->phy_ctrl0, s->phy_rbuf, s->phy_clk_cfg1,
			s->phy_lane0, s->phy_lane1);
	}

	if (ctrl && ctrl->base)
		a52_ackfr_record(
			"P439 T st=%x in=%x cc=%x ck=%x s1=%x s2=%x tg=%x n=%u",
			DSI_R32(ctrl, DSI_STATUS),
			DSI_R32(ctrl, DSI_INT_CTRL),
			DSI_R32(ctrl, DSI_CLK_CTRL),
			DSI_R32(ctrl, DSI_CLK_STATUS),
			DSI_R32(ctrl, A52_P439_DMA_SCHED_CTRL),
			DSI_R32(ctrl, A52_P439_DMA_SCHED_CTRL2),
			DSI_R32(ctrl, DSI_TRIG_CTRL), n);
}

void a52_p439_debug_sweep(struct dsi_ctrl_hw *ctrl)
{
	u32 saved, block, test, ctl, value;

	if (!ctrl || !ctrl->base)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);

	/*
	 * This is deliberately post-timeout.  It cannot perturb the 15/35/50 us
	 * timing experiment because the command has already failed.
	 */
	for (block = 0; block < 4; block++) {
		for (test = 0; test < 64; test++) {
			ctl = ((block & 0x3U) << 12) |
			      ((test & 0x3fU) << 4) | BIT(0);
			DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, ctl);
			wmb();
			value = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
			a52_ackfr_record("P439 B b=%u t=%u c=%x v=%x",
					 block, test, ctl, value);
		}
	}

	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
}

#ifdef CONFIG_DEBUG_FS
#include <linux/debugfs.h>
#include <linux/seq_file.h>

static int a52_p439_dbg_show(struct seq_file *m, void *unused)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_p439_count);

	if (n > A52_P439_MAX)
		n = A52_P439_MAX;

	seq_printf(m, "%s count=%u active=%d epoch=%d\n",
		   "A52_PHASE439_DSI_AUTOPSY_V3", n,
		   atomic_read(&a52_p439_active),
		   atomic_read(&a52_p439_epoch));

	for (i = 0; i < n; i++) {
		const struct a52_p439_sample *s = &a52_p439_samples[i];

		seq_printf(m,
			   "i=%u e=%u p=%u ns=%llu st=%08x in=%08x ck=%08x cc=%08x db=%08x s1=%08x s2=%08x dm=%08x ps=%08x pc=%08x\n",
			   i, s->epoch, s->point,
			   (unsigned long long)s->ns,
			   s->status, s->int_ctrl,
			   s->clk_status, s->clk_ctrl, s->dbg171,
			   s->sched1, s->sched2, s->disp_misc,
			   s->pll_status_one, s->phy_pll_ctrl);
	}
	return 0;
}

static int a52_p439_dbg_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_p439_dbg_show, NULL);
}

static const struct file_operations a52_p439_dbg_fops = {
	.owner = THIS_MODULE,
	.open = a52_p439_dbg_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init a52_p439_dbg_init(void)
{
	debugfs_create_file("a52_phase439_dsi", 0444, NULL, NULL,
			    &a52_p439_dbg_fops);
	return 0;
}
late_initcall(a52_p439_dbg_init);
#endif
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        'return !strncmp(message, "P434 ", 5) ||',
        'return !strncmp(message, "P439 ", 5) ||\n'
        '       !strncmp(message, "P434 ", 5) ||',
        "P439 admission helper",
    )

    anchor = 'strncmp(fmt, "P434", 4) &&'
    if anchor not in text:
        die("P439 format admission anchor missing")
    text = text.replace(
        anchor,
        'strncmp(fmt, "P439", 4) &&\n\t    ' + anchor,
    )

    return text + "\n/* " + MARK + ": P439 records admitted. */\n"


def patch_hwc(text: str, variant: str) -> str:
    if MARK in text:
        return text

    inc = "#include <linux/init.h>\n"
    if inc not in text:
        die("HWC init include anchor missing")
    text = one(
        text,
        inc,
        inc + "#include <linux/module.h>\n",
        "HWC module include",
    )

    anchor = "/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */\n"
    text = one(text, anchor, COMMON_HWC + "\n" + anchor, "HWC helper")

    old = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
\t\tif (a52_p421_target_active()) {
\t\t\ta52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
\t\t\ta52_p424_snapshot(ctrl);
\t\t\ta52_p427_road_snapshot(ctrl, 0U);
\t\t\ta52_p430_dma_arm(ctrl);
\t\t}
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\tif (a52_p421_target_active()) {
\t\t\ta52_p430_dma_triggered(ctrl);
\t\t\ta52_p421_survival_record(8U, 0U, 0U, 0, 0U, 0);
\t\t}
'''
    new = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
\t\tif (a52_p421_target_active()) {
\t\t\ta52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
\t\t\ta52_p424_snapshot(ctrl);
\t\t\ta52_p427_road_snapshot(ctrl, 0U);
\t\t\ta52_p430_dma_arm(ctrl);
\t\t}
\t\tif (a52_p439_f0_active())
\t\t\ta52_p439_begin(ctrl);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\tif (a52_p439_f0_active())
\t\t\ta52_p439_post_trigger(ctrl);
\t\tif (a52_p421_target_active()) {
\t\t\ta52_p430_dma_triggered(ctrl);
\t\t\ta52_p421_survival_record(8U, 0U, 0U, 0, 0U, 0);
\t\t}
'''
    text = one(text, old, new, "memory trigger")

    old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
\tif (a52_p439_f0_active())
\t\ta52_p439_begin(ctrl);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\tif (a52_p439_f0_active())
\t\ta52_p439_post_trigger(ctrl);
'''
    text = one(text, old, new, "deferred trigger")

    text += (
        "\nstatic const char a52_p439_variant[] __used = \""
        + MARK + ":VARIANT_" + variant + "\";\n"
    )
    return text


def patch_ctrl(text: str, variant: str) -> str:
    if MARK in text:
        return text

    decl = (
        "extern void a52_p345_flush(struct dsi_ctrl_hw *ctrl); "
        "/* A52_PHASE345_DMA_US_FRONTIER_V1 */\n"
    )
    extra = decl + '''extern void a52_p439_timeout_snapshot(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_debug_sweep(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_allow_next(void);
extern void a52_p434_dump_rails(const char *tag);
'''
    text = one(text, decl, extra, "CTRL declarations")

    anchor = "/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */\n"
    state = r'''/* A52_PHASE439_DSI_AUTOPSY_V3 */
static atomic_t a52_p439_f0_inflight = ATOMIC_INIT(0);

bool a52_p439_f0_active(void)
{
	return atomic_read(&a52_p439_f0_inflight) != 0;
}
EXPORT_SYMBOL_GPL(a52_p439_f0_active);

/*
 * Variant D uses the later Qualcomm DSI 2.2/2.3/2.4 reset semantics.
 *
 * Our older captures already prove TRIG_CTRL == 0x4 on both GKI and Golden,
 * so the trigger-mux theory is already ruled out.  We still perform the
 * exact later-driver initialization sequence here and record before/after.
 * If TRIG_CTRL remains 0x4, only 0x100/0x104 can be the effective delta.
 */
static __maybe_unused void
a52_p439d_reset_schedule_state(struct dsi_ctrl *dsi_ctrl)
{
	static const u8 trigger_map[DSI_TRIGGER_MAX] =
		{ 0x0, 0x2, 0x1, 0x4, 0x5, 0x6 };
	u32 trig, sched1, sched2, post_trig;
	u32 trigger = dsi_ctrl->host_config.common_config.dma_cmd_trigger;

	if (!dsi_ctrl || !dsi_ctrl->hw.base)
		return;

	trig = readl_relaxed(dsi_ctrl->hw.base + DSI_TRIG_CTRL);
	sched1 = readl_relaxed(dsi_ctrl->hw.base + 0x100);
	sched2 = readl_relaxed(dsi_ctrl->hw.base + 0x104);

	a52_ackfr_record(
		"P439 R pre tg=%x s1=%x s2=%x dma=%x",
		trig, sched1, sched2, trigger);

	/*
	 * Qualcomm 2022+ reset_trigger_controls() for controller versions
	 * 2.2/2.3/2.4:
	 *   - clear DMA_TRG_MUX bit 16,
	 *   - restore configured DMA_TRIGGER_SEL,
	 *   - clear both DMA scheduling registers.
	 */
	post_trig = trig;
	post_trig &= ~BIT(16);
	post_trig &= ~0xFU;
	if (trigger < DSI_TRIGGER_MAX)
		post_trig |= trigger_map[trigger] & 0xFU;

	writel_relaxed(post_trig, dsi_ctrl->hw.base + DSI_TRIG_CTRL);
	writel_relaxed(0, dsi_ctrl->hw.base + 0x104);
	writel_relaxed(0, dsi_ctrl->hw.base + 0x100);
	wmb();

	a52_ackfr_record(
		"P439 R post tg=%x s1=%x s2=%x chg=%x",
		readl_relaxed(dsi_ctrl->hw.base + DSI_TRIG_CTRL),
		readl_relaxed(dsi_ctrl->hw.base + 0x100),
		readl_relaxed(dsi_ctrl->hw.base + 0x104),
		trig ^ post_trig);
}

'''
    text = one(text, anchor, state + anchor, "P439 exact-F0 state")

    old = '''\tu32 hw_flags = 0;
\tu32 line_no = 0x1;
\tstruct dsi_ctrl_hw_ops dsi_hw_ops = dsi_ctrl->hw.ops;
'''
    new = '''\tu32 hw_flags = 0;
\tu32 line_no = 0x1;
\tstruct dsi_ctrl_hw_ops dsi_hw_ops = dsi_ctrl->hw.ops;
\tbool a52_p439_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
'''
    text = one(text, old, new, "kickoff exact-F0 local")

    samsung_decls = '''#if defined(CONFIG_DISPLAY_SAMSUNG)
\tu8 *tx_buf = (u8 *)msg->tx_buf;
#endif
'''
    text = one(
        text,
        samsung_decls,
        samsung_decls +
        "\tif (a52_p439_f0)\n"
        "\t\tatomic_set(&a52_p439_f0_inflight, 1);\n\n",
        "kickoff arm after declarations",
    )

    old = '''\t}
}

static void dsi_ctrl_validate_msg_flags'''
    new = '''\t}
\tif (a52_p439_f0)
\t\tatomic_set(&a52_p439_f0_inflight, 0);
}

static void dsi_ctrl_validate_msg_flags'''
    text = one(text, old, new, "kickoff exact-F0 clear")

    if variant == "D":
        old = '''\t\ta52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
\t\ta52_p426_capture_f0();
'''
        new = old + "\t\ta52_p439d_reset_schedule_state(dsi_ctrl);\n"
        text = one(text, old, new, "D scheduling reset")

    start = text.find(
        "static __noreturn void a52_p411_timeout_panic("
        "struct dsi_ctrl *dsi_ctrl)"
    )
    if start < 0:
        die("old timeout panic missing")
    brace = text.find("{", start)
    depth = 0
    end = None
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        die("old timeout panic close missing")

    repl = r'''static __noreturn void
a52_p411_timeout_panic(struct dsi_ctrl *dsi_ctrl)
{
	a52_p439_timeout_snapshot(dsi_ctrl ? &dsi_ctrl->hw : NULL);
	a52_p439_flush_samples(dsi_ctrl ? &dsi_ctrl->hw : NULL);

	/*
	 * P426/P427 already provide one-shot SMMU sanity.  The expensive
	 * evidence collected here is deliberately link-local and post-timeout.
	 */
	if (dsi_ctrl)
		a52_p439_debug_sweep(&dsi_ctrl->hw);

	a52_p434_dump_rails("p439");
	a52_ackfr_record(
		"P439 PANIC ctrl=%d irq=%u q=%u",
		dsi_ctrl ? dsi_ctrl->cell_index : -1,
		dsi_ctrl ?
			(unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig) : 0U,
		dsi_ctrl ? dsi_ctrl->dma_wait_queued : 0U);

	/* Force the persistent recorder transport before ramoops/panic. */
	a52_ackfr_retain_timeout_snapshot();
	dump_stack();
	panic("A52P439 DSI F0 DMA_DONE timeout");
}'''
    text = text[:start] + repl + text[end:]

    if variant == "C":
        old = "\tbool a52_p421_f0 = false;\n"
        new = old + "\tstruct mipi_dsi_msg a52_p439_lpm_msg;\n"
        text = one(text, old, new, "C local msg")

        old = '''\ta52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
\tif (a52_p421_f0) {
'''
        new = '''\ta52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
\tif (a52_p421_f0) {
\t\ta52_p439_lpm_msg = *msg;
\t\ta52_p439_lpm_msg.flags |= MIPI_DSI_MSG_USE_LPM;
\t\tmsg = &a52_p439_lpm_msg;
\t\ta52_ackfr_record("P439 C force_lpm m=%x",
\t\t\t\t (unsigned int)msg->flags);
'''
        text = one(text, old, new, "C semantic LPM")

    return text + "\n/* " + MARK + ": timeout autopsy variant " + variant + ". */\n"


def patch_disp(text: str, variant: str) -> str:
    if variant != "B" or MARK in text:
        return text

    anchor = '''#if defined(CONFIG_DISPLAY_SAMSUNG)
extern bool pba_regulator_control_ss;
#endif
'''
    helper = r'''#if defined(CONFIG_DISPLAY_SAMSUNG)
extern bool pba_regulator_control_ss;
#endif
extern void a52_p439_allow_next(void);

/*
 * Variant B: one early normal-host-path F0 unlock followed by the matching
 * F0 A5 A5 relock.  If the early unlock hangs, the common exact-F0 timeout
 * path captures and panics.  If both return, retain epoch 1 and re-arm for the
 * normal Android late F0 as epoch 2.
 */
static atomic_t a52_p439_early_once = ATOMIC_INIT(0);

static int a52_p439_early_f0(struct dsi_display *display)
{
	static const u8 unlock[] = { 0xF0, 0x5A, 0x5A };
	static const u8 relock[] = { 0xF0, 0xA5, 0xA5 };
	struct mipi_dsi_msg msg = { 0 };
	int rc;

	if (!display || strcmp(display->display_type, "primary") ||
	    atomic_cmpxchg(&a52_p439_early_once, 0, 1) != 0)
		return 0;

	msg.channel = 0;
	msg.type = MIPI_DSI_GENERIC_LONG_WRITE;
	msg.flags = MIPI_DSI_MSG_LASTCOMMAND;
	msg.tx_len = ARRAY_SIZE(unlock);
	msg.tx_buf = unlock;

	a52_ackfr_record("P439 E unlock pre");
	rc = (int)dsi_host_transfer(&display->host, &msg);
	a52_ackfr_record("P439 E unlock rc=%d", rc);
	if (rc)
		return rc;

	msg.tx_len = ARRAY_SIZE(relock);
	msg.tx_buf = relock;
	a52_ackfr_record("P439 E relock pre");
	rc = (int)dsi_host_transfer(&display->host, &msg);
	a52_ackfr_record("P439 E relock rc=%d", rc);
	if (rc)
		panic("A52P439B early F0 relock failed");

	a52_p439_allow_next();
	a52_ackfr_record("P439 E rearmed late=1");
	return 0;
}
'''
    text = one(text, anchor, helper, "B helper insertion")

    old = '''\tdsi_config_host_engine_state_for_cont_splash(display);
\tmutex_unlock(&display->display_lock);

\t/* Set the current brightness level */
'''
    new = '''\tdsi_config_host_engine_state_for_cont_splash(display);
\tmutex_unlock(&display->display_lock);

\t/* Phase439B: normal DSI host path immediately after splash takeover. */
\trc = a52_p439_early_f0(display);
\tif (rc)
\t\treturn rc;

\t/* Set the current brightness level */
'''
    text = one(text, old, new, "B continuous-splash hook")

    return text + "\n/* " + MARK + ": early-vs-late HS F0 variant B. */\n"


def validate(root: Path, variant: str) -> None:
    files = [CTRL, HWC, REC]
    if variant == "B":
        files.append(DISP)

    for rel in files:
        if not (root / rel).is_file():
            die("missing " + str(rel))

    all_text = "\n".join(
        (root / rel).read_text(errors="replace") for rel in files
    )

    for token in (
        MARK,
        "P439 S i=%u e=%u p=%u",
        "P439 Q i=%u e=%u",
        "P439 B b=%u t=%u c=%x v=%x",
        "A52P439 DSI F0 DMA_DONE timeout",
        "A52_P439_DMA_SCHED_CTRL2",
        "0x0ae94000ULL",
        "0x0171",
        "a52_p439_f0_active",
        "a52_p430_dma_arm(ctrl);",
        "a52_p430_dma_triggered(ctrl);",
    ):
        if token not in all_text:
            die("missing token: " + token)

    if variant == "B":
        for token in (
            "P439 E unlock pre",
            "P439 E relock pre",
            "a52_p439_allow_next();",
            "dsi_host_transfer(&display->host, &msg)",
        ):
            if token not in all_text:
                die("B missing: " + token)

    if variant == "C":
        for token in ("MIPI_DSI_MSG_USE_LPM", "P439 C force_lpm"):
            if token not in all_text:
                die("C missing: " + token)

    if variant == "D":
        for token in (
            "a52_p439d_reset_schedule_state(dsi_ctrl);",
            "P439 R pre",
            "P439 R post",
            "dsi_ctrl->hw.base + 0x100",
            "dsi_ctrl->hw.base + 0x104",
            "post_trig &= ~BIT(16)",
        ):
            if token not in all_text:
                die("D missing: " + token)

    devfreq = root / "drivers/devfreq/devfreq.c"
    if devfreq.is_file() and MARK in devfreq.read_text(errors="replace"):
        die("unrelated generic devfreq core modified")

    print("Phase439 DSI autopsy variant " + variant + ": PASS")


def apply(root: Path, variant: str) -> None:
    patches = (
        (REC, patch_rec),
        (HWC, lambda s: patch_hwc(s, variant)),
        (CTRL, lambda s: patch_ctrl(s, variant)),
        (DISP, lambda s: patch_disp(s, variant)),
    )

    for rel, fn in patches:
        path = root / rel
        if not path.is_file():
            if rel == DISP and variant != "B":
                continue
            die("missing " + str(rel))
        path.write_text(fn(path.read_text(errors="replace")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--variant", choices=["A", "B", "C", "D"], required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    root = ns.root.resolve()
    if not ns.check_only:
        apply(root, ns.variant)
    validate(root, ns.variant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
