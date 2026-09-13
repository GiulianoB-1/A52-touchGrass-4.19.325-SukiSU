#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path("dsi_ctrl.c")
HWC = Path("dsi_ctrl_hw_cmn.c")
MARK = "A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase344G {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


HELPER = r'''/* A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1
 *
 * Read-only high-resolution recorder for the known-good TouchGrass exact-F0
 * command-DMA transaction.  The source audit proved the SW_TRIGGER write,
 * register map, interrupt status/clear/enable path and waiter logic are
 * semantically the same as GKI once GKI-only observers are removed.
 *
 * Therefore this phase measures the silicon transition itself:
 *   q0     : last compact sample immediately before SW_TRIGGER
 *   q1..q6 : six back-to-back read-only samples immediately after SW_TRIGGER
 *   qIRQ   : first DMA_DONE ISR observation before normal state mutation
 *   qWAIT  : completion-wait outcome before dumping the captured samples
 *
 * Samples are stored in RAM first.  No printk occurs between q0 and q6, so
 * logging cannot create an artificial delay between SW_TRIGGER and the first
 * post-trigger samples.  The retained buffer remains readable for the whole
 * boot at /proc/a52_phase344g, so early evidence cannot be lost to printk-ring
 * overwrite before ADB becomes available.  A post-completion printk dump is
 * retained only as a secondary transport.
 * No DSI/PHY/clock/power/reset register write, delay, retry or return value is
 * changed by this recorder.
 */
#define A52_G344_MAX_SAMPLES 16U

struct a52_g344_sample {
	u64 ns;
	u32 point;
	u32 status;
	u32 fifo;
	u32 clk_ctrl;
	u32 clk_status;
	u32 int_ctrl;
	u32 lane_status;
	u32 dma_ctrl;
	u32 dma_offset;
	u32 dma_length;
	u32 sw_trigger;
	u32 trig_ctrl;
	u32 ack_err;
	u32 timeout;
	u32 phy_err;
	u32 axi2ahb;
};

static struct a52_g344_sample a52_g344_samples[A52_G344_MAX_SAMPLES];
static atomic_t a52_g344_count = ATOMIC_INIT(0);

void a52_g344_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	struct a52_g344_sample *s;
	unsigned int index;

	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;

	index = (unsigned int)atomic_inc_return(&a52_g344_count) - 1U;
	if (index >= A52_G344_MAX_SAMPLES)
		return;

	s = &a52_g344_samples[index];
	memset(s, 0, sizeof(*s));
	s->ns = ktime_get_ns();
	s->point = point;
	s->status = DSI_R32(ctrl, DSI_STATUS);
	s->fifo = DSI_R32(ctrl, DSI_FIFO_STATUS);
	s->clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	s->int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
	s->lane_status = DSI_R32(ctrl, DSI_LANE_STATUS);
	s->dma_ctrl = DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL);
	s->dma_offset = DSI_R32(ctrl, DSI_DMA_CMD_OFFSET);
	s->dma_length = DSI_R32(ctrl, DSI_DMA_CMD_LENGTH);
	s->sw_trigger = DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER);
	s->trig_ctrl = DSI_R32(ctrl, DSI_TRIG_CTRL);
	s->ack_err = DSI_R32(ctrl, DSI_ACK_ERR_STATUS);
	s->timeout = DSI_R32(ctrl, DSI_TIMEOUT_STATUS);
	s->phy_err = DSI_R32(ctrl, DSI_DLN0_PHY_ERR);
	s->axi2ahb = DSI_R32(ctrl, DSI_AXI2AHB_CTRL);
}

static int a52_g344_proc_show(struct seq_file *m, void *unused)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_g344_count);

	if (n > A52_G344_MAX_SAMPLES)
		n = A52_G344_MAX_SAMPLES;

	seq_printf(m, "A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V2\\n");
	seq_printf(m, "count=%u capacity=%u\\n", n, A52_G344_MAX_SAMPLES);
	for (i = 0; i < n; i++) {
		const struct a52_g344_sample *s = &a52_g344_samples[i];

		seq_printf(m,
			"S i=%u p=%u t=%llu st=%08x fs=%08x cc=%08x ck=%08x in=%08x ln=%08x\\n",
			i, s->point, (unsigned long long)s->ns, s->status,
			s->fifo, s->clk_ctrl, s->clk_status, s->int_ctrl,
			s->lane_status);
		seq_printf(m,
			"D i=%u dc=%08x o=%08x l=%08x sw=%08x tg=%08x ae=%08x to=%08x pe=%08x ax=%08x\\n",
			i, s->dma_ctrl, s->dma_offset, s->dma_length,
			s->sw_trigger, s->trig_ctrl, s->ack_err, s->timeout,
			s->phy_err, s->axi2ahb);
	}
	return 0;
}

static int a52_g344_proc_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_g344_proc_show, NULL);
}

static const struct file_operations a52_g344_proc_fops = {
	.open = a52_g344_proc_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init a52_g344_proc_init(void)
{
	if (!proc_create("a52_phase344g", 0444, NULL, &a52_g344_proc_fops))
		return -ENOMEM;
	return 0;
}
late_initcall(a52_g344_proc_init);

void a52_g344_dump_samples(void)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_g344_count);

	if (n > A52_G344_MAX_SAMPLES)
		n = A52_G344_MAX_SAMPLES;

	for (i = 0; i < n; i++) {
		const struct a52_g344_sample *s = &a52_g344_samples[i];

		pr_info("TG344 S i=%u p=%u t=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x\n",
			i, s->point, (unsigned long long)s->ns, s->status, s->fifo,
			s->clk_ctrl, s->clk_status, s->int_ctrl, s->lane_status);
		pr_info("TG344 D i=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x\n",
			i, s->dma_ctrl, s->dma_offset, s->dma_length, s->sw_trigger,
			s->trig_ctrl, s->ack_err, s->timeout, s->phy_err, s->axi2ahb);
	}
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1",
        "A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1",
        "a52_g319_debugbus_snapshot(ctrl, 0);",
        "a52_g319_debugbus_snapshot(ctrl, 1);",
    ):
        if token not in text:
            raise SystemExit("Phase344G HWC prerequisite missing: " + token)

    text = one(text, "#include <linux/iopoll.h>\n",
               "#include <linux/iopoll.h>\n#include <linux/ktime.h>\n#include <linux/proc_fs.h>\n#include <linux/seq_file.h>\n",
               "ktime include")

    anchor = """void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,
			      unsigned int point)
{
"""
    text = one(text, anchor, HELPER + anchor, "recorder helper insertion")

    memory_old = """	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		a52_g315_full_snapshot(ctrl);
		a52_g319_debugbus_snapshot(ctrl, 0);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		a52_g319_debugbus_snapshot(ctrl, 1);
		a52_g315_launch_snapshot(ctrl, 1);
	}
"""
    memory_new = """	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		a52_g315_full_snapshot(ctrl);
		a52_g319_debugbus_snapshot(ctrl, 0);
		a52_g344_snapshot(ctrl, 0);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		a52_g344_snapshot(ctrl, 1);
		a52_g344_snapshot(ctrl, 2);
		a52_g344_snapshot(ctrl, 3);
		a52_g344_snapshot(ctrl, 4);
		a52_g344_snapshot(ctrl, 5);
		a52_g344_snapshot(ctrl, 6);
		a52_g319_debugbus_snapshot(ctrl, 1);
		a52_g315_launch_snapshot(ctrl, 1);
	}
"""
    text = one(text, memory_old, memory_new, "memory trigger burst")

    deferred_old = """void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	a52_g315_full_snapshot(ctrl);
	a52_g319_debugbus_snapshot(ctrl, 0);
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
	a52_g319_debugbus_snapshot(ctrl, 1);
	a52_g315_launch_snapshot(ctrl, 1);
}
"""
    deferred_new = """void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	a52_g315_full_snapshot(ctrl);
	a52_g319_debugbus_snapshot(ctrl, 0);
	a52_g344_snapshot(ctrl, 0);
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
	a52_g344_snapshot(ctrl, 1);
	a52_g344_snapshot(ctrl, 2);
	a52_g344_snapshot(ctrl, 3);
	a52_g344_snapshot(ctrl, 4);
	a52_g344_snapshot(ctrl, 5);
	a52_g344_snapshot(ctrl, 6);
	a52_g319_debugbus_snapshot(ctrl, 1);
	a52_g315_launch_snapshot(ctrl, 1);
}
"""
    return one(text, deferred_old, deferred_new, "deferred trigger burst")


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1",
        "a52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);",
        'pr_info("TG315 DONE ret=%d irq=%d\\n", ret,',
    ):
        if token not in text:
            raise SystemExit("Phase344G CTRL prerequisite missing: " + token)

    decl = "extern void a52_g319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n"
    text = one(text, decl, decl +
               "extern void a52_g344_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n"
               "extern void a52_g344_dump_samples(void);\n",
               "recorder declarations")

    isr_old = """	if (status & DSI_CMD_MODE_DMA_DONE) {
		atomic_set(&dsi_ctrl->dma_irq_trig, 1);
"""
    isr_new = """	if (status & DSI_CMD_MODE_DMA_DONE) {
		if (a52_g315_armed(dsi_ctrl))
			a52_g344_snapshot(&dsi_ctrl->hw, 8);
		atomic_set(&dsi_ctrl->dma_irq_trig, 1);
"""
    text = one(text, isr_old, isr_new, "DMA_DONE ISR snapshot")

    done_old = """	if (a52_g315_armed(dsi_ctrl)) {
		a52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);
		a52_g315_launch_snapshot(&dsi_ctrl->hw, 2);
		pr_info("TG315 DONE ret=%d irq=%d\\n", ret,
			atomic_read(&dsi_ctrl->dma_irq_trig));
		atomic_set(&a52_g315_state, 2);
	}
"""
    done_new = """	if (a52_g315_armed(dsi_ctrl)) {
		a52_g344_snapshot(&dsi_ctrl->hw, 9);
		a52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);
		a52_g315_launch_snapshot(&dsi_ctrl->hw, 2);
		a52_g344_dump_samples();
		pr_info("TG344 DONE n=%u ret=%d irq=%d\\n",
			A52_G344_MAX_SAMPLES, ret,
			atomic_read(&dsi_ctrl->dma_irq_trig));
		pr_info("TG315 DONE ret=%d irq=%d\\n", ret,
			atomic_read(&dsi_ctrl->dma_irq_trig));
		atomic_set(&a52_g315_state, 2);
	}
"""
    # A52_G344_MAX_SAMPLES is private to HWC. Keep CTRL independent by printing
    # a fixed schema-capacity literal rather than importing the macro.
    done_new = done_new.replace("A52_G344_MAX_SAMPLES", "16U")
    return one(text, done_old, done_new, "completion dump")


def validate(ctrl: str, hwc: str) -> None:
    both = ctrl + hwc
    for token in (
        MARK,
        "TG344 S i=%u p=%u t=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x",
        "TG344 D i=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x",
        "TG344 DONE n=%u ret=%d irq=%d",
        "a52_g344_snapshot(ctrl, 0);",
        "a52_g344_snapshot(ctrl, 6);",
        "a52_g344_snapshot(&dsi_ctrl->hw, 8);",
        "a52_g344_snapshot(&dsi_ctrl->hw, 9);",
        "a52_g344_dump_samples();",
        "ktime_get_ns();",
        'proc_create("a52_phase344g", 0444, NULL, &a52_g344_proc_fops)',
        "A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V2",
        "seq_read",
    ):
        if token not in both:
            raise SystemExit("Phase344G required token missing: " + token)

    if hwc.count("a52_g344_snapshot(ctrl, 0);") != 2:
        raise SystemExit("Phase344G expected q0 on both trigger paths")
    for point in range(1, 7):
        if hwc.count(f"a52_g344_snapshot(ctrl, {point});") != 2:
            raise SystemExit(f"Phase344G expected q{point} on both trigger paths")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True,
                    help="TouchGrass techpack/display/msm/dsi directory")
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    cp = ns.root / CTRL
    hp = ns.root / HWC
    for p in (cp, hp):
        if not p.is_file():
            raise SystemExit("Phase344G source missing: " + str(p))

    if not ns.check_only:
        hp.write_text(patch_hwc(hp.read_text()))
        cp.write_text(patch_ctrl(cp.read_text()))

    validate(cp.read_text(), hp.read_text())
    print("Phase344G Golden DMA transition recorder: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
