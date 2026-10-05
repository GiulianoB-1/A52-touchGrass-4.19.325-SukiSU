#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2"
CTRL = Path("dsi_ctrl.c")
HWC = Path("dsi_ctrl_hw_cmn.c")


def die(msg: str) -> None:
    raise SystemExit("Phase439G: " + msg)


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


HELPER = r'''
/* A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2
 *
 * Known-good TouchGrass oracle for Phase439.
 *
 * The +15/+35/+50 us hot path stores only in normal RAM.  Selector 0x0171 is
 * the single timed internal-state read so that the successful ~35 us transfer
 * is minimally perturbed.  The broad 256-selector sweep runs only after the
 * successful command has completed.
 *
 * 0x100 and 0x104 are captured at every timed point so the GKI stale
 * command-DMA scheduling hypothesis can be compared directly with Golden.
 */
#define A52_G439_PHY_PHYS          0x0ae94000ULL
#define A52_G439_PHY_SIZE          0x1000U
#define A52_G439_DMA_SCHED_CTRL    0x0100U
#define A52_G439_DMA_SCHED_CTRL2   0x0104U
#define A52_G439_MAX               4U

struct a52_g439_sample {
	u64 ns;
	u32 point;
	u32 status, fifo, clk_ctrl, clk_status, int_ctrl;
	u32 lane_status, lane_ctrl;
	u32 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl;
	u32 sched1, sched2, disp_misc;
	u32 ack_err, timeout, phy_err, axi2ahb, dbg171;
	u32 pll_status_one, phy_pll_ctrl, phy_ctrl0, phy_rbuf, phy_clk_cfg1;
	u32 phy_lane0, phy_lane1;
};

static struct a52_g439_sample a52_g439_samples[A52_G439_MAX];
static atomic_t a52_g439_count = ATOMIC_INIT(0);
static atomic_t a52_g439_active = ATOMIC_INIT(0);
static void __iomem *a52_g439_phy;
static u32 a52_g439_saved_dbg;

static int __init a52_g439_map_init(void)
{
	a52_g439_phy = ioremap(A52_G439_PHY_PHYS, A52_G439_PHY_SIZE);
	return 0;
}
subsys_initcall(a52_g439_map_init);

static __always_inline u32 a52_g439_pr(u32 off)
{
	return a52_g439_phy ?
		readl_relaxed((u8 __iomem *)a52_g439_phy + off) : ~0U;
}

static void a52_g439_store(struct dsi_ctrl_hw *ctrl, u32 point)
{
	struct a52_g439_sample *s;
	unsigned int i;

	if (!a52_g315_trace_active() ||
	    atomic_read(&a52_g439_active) != 1 ||
	    !ctrl || !ctrl->base)
		return;

	i = (unsigned int)atomic_inc_return(&a52_g439_count) - 1U;
	if (i >= A52_G439_MAX)
		return;

	s = &a52_g439_samples[i];
	memset(s, 0, sizeof(*s));
	s->ns = ktime_get_ns();
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

	s->sched1 = DSI_R32(ctrl, A52_G439_DMA_SCHED_CTRL);
	s->sched2 = DSI_R32(ctrl, A52_G439_DMA_SCHED_CTRL2);
	s->disp_misc = ctrl->disp_cc_base ? DSI_DISP_CC_R32(ctrl, 0x0) : ~0U;

	s->ack_err = DSI_R32(ctrl, DSI_ACK_ERR_STATUS);
	s->timeout = DSI_R32(ctrl, DSI_TIMEOUT_STATUS);
	s->phy_err = DSI_R32(ctrl, DSI_DLN0_PHY_ERR);
	s->axi2ahb = DSI_R32(ctrl, DSI_AXI2AHB_CTRL);
	s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);

	s->phy_clk_cfg1 = a52_g439_pr(0x414);
	s->phy_rbuf = a52_g439_pr(0x41c);
	s->phy_ctrl0 = a52_g439_pr(0x424);
	s->phy_pll_ctrl = a52_g439_pr(0x438);
	s->phy_lane0 = a52_g439_pr(0x4f4);
	s->phy_lane1 = a52_g439_pr(0x4f8);
	s->pll_status_one = a52_g439_pr(0xba0);
}

static __always_inline void a52_g439_wait_until(u64 start, u64 delta)
{
	while ((ktime_get_ns() - start) < delta)
		cpu_relax();
}

static void a52_g439_begin(struct dsi_ctrl_hw *ctrl)
{
	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_g439_active, 0, 1) != 0)
		return;

	atomic_set(&a52_g439_count, 0);
	a52_g439_saved_dbg = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, 0x0171);
	wmb();
	a52_g439_store(ctrl, 0U);
}

static void a52_g439_post_trigger(struct dsi_ctrl_hw *ctrl)
{
	u64 start;

	if (atomic_read(&a52_g439_active) != 1 || !ctrl || !ctrl->base)
		return;

	start = ktime_get_ns();
	a52_g439_wait_until(start, 15000ULL);
	a52_g439_store(ctrl, 15U);
	a52_g439_wait_until(start, 35000ULL);
	a52_g439_store(ctrl, 35U);
	a52_g439_wait_until(start, 50000ULL);
	a52_g439_store(ctrl, 50U);

	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_g439_saved_dbg);
	wmb();
}

static void a52_g439_debug_sweep(struct dsi_ctrl_hw *ctrl)
{
	u32 saved, block, test, ctl, value;

	if (!ctrl || !ctrl->base)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	for (block = 0; block < 4; block++) {
		for (test = 0; test < 64; test++) {
			ctl = ((block & 0x3U) << 12) |
			      ((test & 0x3fU) << 4) | BIT(0);
			DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, ctl);
			wmb();
			value = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
			pr_info("TG439 B b=%u t=%u c=%x v=%x\n",
				block, test, ctl, value);
		}
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
}

static void a52_g439_seq_dump(struct seq_file *m)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_g439_count);

	if (n > A52_G439_MAX)
		n = A52_G439_MAX;

	seq_printf(m, "%s count=%u active=%d\n",
		   "A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2",
		   n, atomic_read(&a52_g439_active));

	for (i = 0; i < n; i++) {
		const struct a52_g439_sample *s = &a52_g439_samples[i];

		seq_printf(
			m,
			"TG439 S i=%u p=%u ns=%llu st=%08x fs=%08x cc=%08x ck=%08x in=%08x ln=%08x lc=%08x db=%08x\n",
			i, s->point, (unsigned long long)s->ns,
			s->status, s->fifo, s->clk_ctrl, s->clk_status,
			s->int_ctrl, s->lane_status, s->lane_ctrl, s->dbg171);

		seq_printf(
			m,
			"TG439 D i=%u dc=%08x o=%08x l=%08x sw=%08x tg=%08x ae=%08x to=%08x pe=%08x ax=%08x\n",
			i, s->dma_ctrl, s->dma_offset, s->dma_length,
			s->sw_trigger, s->trig_ctrl, s->ack_err,
			s->timeout, s->phy_err, s->axi2ahb);

		seq_printf(
			m,
			"TG439 Q i=%u s1=%08x s2=%08x dm=%08x\n",
			i, s->sched1, s->sched2, s->disp_misc);

		seq_printf(
			m,
			"TG439 P i=%u ps=%08x pc=%08x c0=%08x rb=%08x cf=%08x l0=%08x l1=%08x\n",
			i, s->pll_status_one, s->phy_pll_ctrl, s->phy_ctrl0,
			s->phy_rbuf, s->phy_clk_cfg1,
			s->phy_lane0, s->phy_lane1);
	}
}

static int a52_g439_proc_show(struct seq_file *m, void *unused)
{
	a52_g439_seq_dump(m);
	return 0;
}

static int a52_g439_proc_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_g439_proc_show, NULL);
}

static const struct file_operations a52_g439_proc_fops = {
	.open = a52_g439_proc_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init a52_g439_proc_init(void)
{
	if (!proc_create("a52_phase439g", 0444, NULL, &a52_g439_proc_fops))
		return -ENOMEM;
	return 0;
}
late_initcall(a52_g439_proc_init);

void a52_g439_dump_samples(struct dsi_ctrl_hw *ctrl)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_g439_count);

	if (n > A52_G439_MAX)
		n = A52_G439_MAX;

	for (i = 0; i < n; i++) {
		const struct a52_g439_sample *s = &a52_g439_samples[i];

		pr_info(
			"TG439 S i=%u p=%u ns=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x lc=%x db=%x\n",
			i, s->point, (unsigned long long)s->ns,
			s->status, s->fifo, s->clk_ctrl, s->clk_status,
			s->int_ctrl, s->lane_status, s->lane_ctrl, s->dbg171);

		pr_info(
			"TG439 D i=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x\n",
			i, s->dma_ctrl, s->dma_offset, s->dma_length,
			s->sw_trigger, s->trig_ctrl, s->ack_err,
			s->timeout, s->phy_err, s->axi2ahb);

		pr_info("TG439 Q i=%u s1=%x s2=%x dm=%x\n",
			i, s->sched1, s->sched2, s->disp_misc);

		pr_info(
			"TG439 P i=%u ps=%x pc=%x c0=%x rb=%x cf=%x l0=%x l1=%x\n",
			i, s->pll_status_one, s->phy_pll_ctrl,
			s->phy_ctrl0, s->phy_rbuf, s->phy_clk_cfg1,
			s->phy_lane0, s->phy_lane1);
	}

	/* Broad oracle sweep is post-success, never in the 0-50 us hot path. */
	a52_g439_debug_sweep(ctrl);
}
'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text

    for token in (
        "A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1",
        "A52_PHASE424G_PAIRED_HW_SNAPSHOT_V1",
        "a52_g344_snapshot(ctrl, 0);",
        "a52_g424_snapshot(ctrl);",
    ):
        if token not in text:
            die("HWC prerequisite missing: " + token)

    inc = "#include <linux/iopoll.h>\n"
    if "#include <linux/io.h>\n" not in text:
        text = one(text, inc, inc + "#include <linux/io.h>\n", "io include")

    anchor = "void a52_g344_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)\n"
    text = one(text, anchor, HELPER + "\n" + anchor, "helper insertion")

    old = '''\t\ta52_g344_snapshot(ctrl, 0);
\t\ta52_g424_snapshot(ctrl);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\ta52_g344_snapshot(ctrl, 1);
'''
    new = '''\t\ta52_g344_snapshot(ctrl, 0);
\t\ta52_g424_snapshot(ctrl);
\t\ta52_g439_begin(ctrl);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\ta52_g439_post_trigger(ctrl);
\t\ta52_g344_snapshot(ctrl, 1);
'''
    text = one(text, old, new, "memory trigger")

    old = '''\ta52_g344_snapshot(ctrl, 0);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\ta52_g344_snapshot(ctrl, 1);
'''
    new = '''\ta52_g344_snapshot(ctrl, 0);
\ta52_g439_begin(ctrl);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\ta52_g439_post_trigger(ctrl);
\ta52_g344_snapshot(ctrl, 1);
'''
    text = one(text, old, new, "deferred trigger")

    return text + "\n/* " + MARK + ": timed Golden oracle. */\n"


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text

    decl = "extern void a52_g344_dump_samples(void);\n"
    text = one(
        text,
        decl,
        decl + "extern void a52_g439_dump_samples(struct dsi_ctrl_hw *ctrl);\n",
        "dump declaration",
    )

    old = "\t\ta52_g344_dump_samples();\n"
    new = (
        old
        + "\t\ta52_g439_dump_samples(dsi_ctrl ? &dsi_ctrl->hw : NULL);\n"
    )
    text = one(text, old, new, "completion dump")

    return text + "\n/* " + MARK + ": post-success Golden autopsy dump. */\n"


def validate(root: Path) -> None:
    ctrl = root / CTRL
    hwc = root / HWC

    for path in (ctrl, hwc):
        if not path.is_file():
            die("missing " + str(path))

    c = ctrl.read_text(errors="replace")
    h = hwc.read_text(errors="replace")
    both = c + h

    for token in (
        MARK,
        "A52_G439_PHY_PHYS          0x0ae94000ULL",
        "A52_G439_DMA_SCHED_CTRL2   0x0104U",
        "DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, 0x0171);",
        "a52_g439_wait_until(start, 15000ULL)",
        "a52_g439_wait_until(start, 35000ULL)",
        "a52_g439_wait_until(start, 50000ULL)",
        "TG439 S i=%u p=%u ns=%llu",
        "TG439 D i=%u dc=%x",
        "TG439 Q i=%u s1=%x s2=%x dm=%x",
        "TG439 P i=%u ps=%x",
        'proc_create("a52_phase439g", 0444, NULL, &a52_g439_proc_fops)',
        "a52_g439_dump_samples(dsi_ctrl ? &dsi_ctrl->hw : NULL);",
        "TG439 B b=%u t=%u c=%x v=%x",
    ):
        if token not in both:
            die("validation token missing: " + token)

    if h.count("a52_g439_begin(ctrl);") != 2:
        die("expected both trigger paths")

    print("Phase439G Golden DSI autopsy: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    if not ns.check_only:
        hwc = ns.root / HWC
        ctrl = ns.root / CTRL
        hwc.write_text(patch_hwc(hwc.read_text(errors="replace")))
        ctrl.write_text(patch_ctrl(ctrl.read_text(errors="replace")))

    validate(ns.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
