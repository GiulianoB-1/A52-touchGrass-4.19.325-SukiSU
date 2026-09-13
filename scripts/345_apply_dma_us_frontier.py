#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE345_DMA_US_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase345 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


HELPER = r'''/* A52_PHASE345_DMA_US_FRONTIER_V1
 *
 * High-resolution exact-F0 command-DMA observer matched to the successful
 * Phase344G TouchGrass capture.
 *
 * Golden established:
 *   p1 @ +18.802 us : STATUS=3, INT_CTRL low bit already asserted
 *   p2 @ +35.625 us : STATUS=0
 *   DMA_DONE ISR @ +46.042 us
 *
 * The old 0x8027c3/0x8037c3 difference is NOT DSI_CLK_STATUS.  It is
 * DSI_DEBUG_BUS_STATUS with selector 0x0171.  Therefore Phase345 keeps only
 * selector 0x0171 selected across the short p0..p6 burst, records the same
 * direct register set used by Phase344G, then restores the original selector.
 *
 * Samples stay in RAM during the transaction and are emitted into the retained
 * P276 recorder only at the existing exact-F0 200 ms timeout, immediately
 * before Phase280 freezes the evidence.  No printk occurs in the burst.
 */
#define A52_P345_MAX 8U

struct a52_p345_sample {
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
	u32 dbg171;
};

static struct a52_p345_sample a52_p345_samples[A52_P345_MAX];
static atomic_t a52_p345_state = ATOMIC_INIT(0); /* 0 idle, 1 burst, 2 captured */
static atomic_t a52_p345_count = ATOMIC_INIT(0);
static u32 a52_p345_saved_dbg_ctl;

static void a52_p345_store(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	struct a52_p345_sample *s;
	unsigned int i;

	if (atomic_read(&a52_p345_state) != 1 || !ctrl || !ctrl->base)
		return;

	i = (unsigned int)atomic_inc_return(&a52_p345_count) - 1U;
	if (i >= A52_P345_MAX)
		return;

	s = &a52_p345_samples[i];
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
	s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
}

static void a52_p345_begin(struct dsi_ctrl_hw *ctrl)
{
	if (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p345_state, 0, 1) != 0)
		return;

	atomic_set(&a52_p345_count, 0);
	a52_p345_saved_dbg_ctl = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, 0x0171);
	wmb();
	a52_p345_store(ctrl, 0);
}

static void a52_p345_end(struct dsi_ctrl_hw *ctrl)
{
	if (atomic_read(&a52_p345_state) != 1 || !ctrl || !ctrl->base)
		return;

	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p345_saved_dbg_ctl);
	wmb();
	atomic_set(&a52_p345_state, 2);
}

void a52_p345_flush(struct dsi_ctrl_hw *ctrl)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_p345_count);
	u64 base;

	if (atomic_read(&a52_p345_state) < 2 || !n)
		return;
	if (n > A52_P345_MAX)
		n = A52_P345_MAX;

	base = a52_p345_samples[0].ns;
	for (i = 0; i < n; i++) {
		const struct a52_p345_sample *s = &a52_p345_samples[i];
		a52_ackfr_record("P276 345R %x %x %llx %x %x %x %x",
			i, s->point, (unsigned long long)(s->ns - base),
			s->status, s->int_ctrl, s->clk_status, s->dbg171);
	}

	if (ctrl && ctrl->base)
		a52_ackfr_record("P276 345T %x %x %x %x %x %x", n,
			DSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_INT_CTRL),
			DSI_R32(ctrl, DSI_ACK_ERR_STATUS),
			DSI_R32(ctrl, DSI_TIMEOUT_STATUS),
			DSI_R32(ctrl, DSI_DLN0_PHY_ERR));
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1",
        "a52_p319_debugbus_snapshot(ctrl, 0);",
        "a52_p319_debugbus_snapshot(ctrl, 1);",
        "a52_p293_gdm_trace_active()",
    ):
        if token not in text:
            raise SystemExit("Phase345 HWC prerequisite missing: " + token)

    anchor = "static const u32 a52_p319_selectors[6] = {\n"
    text = one(text, anchor, HELPER + anchor, "helper insertion")

    old = """		a52_p319_debugbus_snapshot(ctrl, 0);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		a52_p319_debugbus_snapshot(ctrl, 1);
"""
    new = """		a52_p319_debugbus_snapshot(ctrl, 0);
		a52_p345_begin(ctrl);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		a52_p345_store(ctrl, 1);
		a52_p345_store(ctrl, 2);
		a52_p345_store(ctrl, 3);
		a52_p345_store(ctrl, 4);
		a52_p345_store(ctrl, 5);
		a52_p345_store(ctrl, 6);
		a52_p345_end(ctrl);
		a52_p319_debugbus_snapshot(ctrl, 1);
"""
    # Both the immediate memory-kickoff path and deferred trigger path have this
    # exact Phase319 sequence.
    if text.count(old) != 2:
        raise SystemExit(f"Phase345 expected two trigger anchors, found {text.count(old)}")
    text = text.replace(old, new)
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1",
        'P276 332A q=2 g=1 d=%u st=%x m=%x',
        "a52_ackfr_retain_timeout_snapshot();",
    ):
        if token not in text:
            raise SystemExit("Phase345 CTRL prerequisite missing: " + token)

    decl_anchor = "extern void a52_p319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n"
    text = one(
        text, decl_anchor,
        decl_anchor + "extern void a52_p345_flush(struct dsi_ctrl_hw *ctrl); /* " + MARK + " */\n",
        "flush declaration")

    old = """		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
"""
    new = """		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_p345_flush(&dsi_ctrl->hw);
			a52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
"""
    return one(text, old, new, "pre-retention flush")


def validate(before_h: str, after_h: str, before_c: str, after_c: str) -> None:
    both = after_h + after_c
    for token in (
        MARK,
        "P276 345R %x %x %llx %x %x %x %x",
        "P276 345T %x %x %x %x %x %x",
        "DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, 0x0171);",
        "DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p345_saved_dbg_ctl);",
        "a52_p345_store(ctrl, 0);",
        "a52_p345_store(ctrl, 6);",
        "a52_p345_flush(&dsi_ctrl->hw);",
        "ktime_get_ns()",
    ):
        if token not in both:
            raise SystemExit("Phase345 required token missing: " + token)

    if after_h.count("a52_p345_begin(ctrl);") != 2:
        raise SystemExit("Phase345 expected two possible trigger-path hooks")
    if after_h.count("a52_p345_end(ctrl);") != 2:
        raise SystemExit("Phase345 expected two possible trigger-path restores")
    for p in range(1, 7):
        if after_h.count(f"a52_p345_store(ctrl, {p});") != 2:
            raise SystemExit(f"Phase345 expected point {p} on both trigger paths")

    # Existing SW_TRIGGER count/order is unchanged. Only DEBUG_BUS_CTL selection
    # and restore writes are added by this diagnostic.
    sw = "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);"
    if after_h.count(sw) != before_h.count(sw):
        raise SystemExit("Phase345 changed SW_TRIGGER write count")

    protected = (
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(", "wait_for_completion_timeout(",
    )
    for token in protected:
        if after_h.count(token) != before_h.count(token):
            raise SystemExit("Phase345 changed protected HWC behavior: " + token)
        if after_c.count(token) != before_c.count(token):
            raise SystemExit("Phase345 changed protected CTRL behavior: " + token)

    if after_c.count("a52_ackfr_retain_timeout_snapshot();") != before_c.count("a52_ackfr_retain_timeout_snapshot();"):
        raise SystemExit("Phase345 changed retention call count")
    if not (after_c.index("a52_p345_flush(&dsi_ctrl->hw);") <
            after_c.index("P276 332A q=2") <
            after_c.index("a52_ackfr_retain_timeout_snapshot();")):
        raise SystemExit("Phase345 flush must precede Phase332/Phase280 retention")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    hp = ns.root / HWC
    cp = ns.root / CTRL
    for p in (hp, cp):
        if not p.is_file():
            raise SystemExit("Phase345 source missing: " + str(p))

    h = hp.read_text()
    c = cp.read_text()
    if MARK in h + c:
        for token in ("P276 345R", "a52_p345_flush(&dsi_ctrl->hw);"):
            if token not in h + c:
                raise SystemExit("Phase345 check-only token missing: " + token)
        print("Phase345 DMA us frontier audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase345 marker missing in check-only mode")

    nh = patch_hwc(h)
    nc = patch_ctrl(c)
    validate(h, nh, c, nc)
    hp.write_text(nh)
    cp.write_text(nc)
    print("Phase345 DMA us frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
