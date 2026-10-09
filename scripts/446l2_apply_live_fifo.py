#!/usr/bin/env python3
"""Phase446l2: one-shot live-splash RAW FIFO test; no Linux DSI state changes.

Requires Phase446j first. Disjoint from Phase446l v1, and does not call
dsi_display_cmd_engine_enable/disable(), host_init, clock-control, R1/R2, or
dsi_ctrl_cmd_transfer(). Repeats a self-CRC32 record 16 times in P445.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE446L2_FIFO_LIVE_SPLASH_V1"
SIG = "int dsi_display_cont_splash_config(void *dsi_display)"
END = "clks_disabled:"

def replace_once(s: str, before: str, after: str, what: str) -> str:
    n = s.count(before)
    if n != 1:
        raise RuntimeError(f"{what} expected once, found {n}")
    return s.replace(before, after, 1)

HELPER = r'''/* A52_PHASE446L2_FIFO_LIVE_SPLASH_V1
 * Deliberately no software-state or clock-vote transitions.
 * Hardware FIFO test uses exactly the known 8-byte 0x29 F0 5A 5A packet.
 */
#include <linux/io.h>
#include <linux/delay.h>
#include <linux/crc32.h>
#include <linux/ktime.h>
#include "dsi_ctrl_reg.h"

extern int a52_p445_store_section(u32, u32, const char *, u32, u32,
                                   const void *, u32);

#define P446L2_MAGIC 0x3046324cU /* little endian "L2F0" */
#define P446L2_REPLICAS 16U
struct p446l2_regs {
    u64 ns;
    u32 frame, ctrl, status, lane, clk, irq, trig, dma, tpg, len;
} __packed;
struct p446l2_result {
    u32 magic, magic_inv, version, stage, outcome, reason;
    u32 sent, raw_done, busy_samples, idle_samples;
    u32 packet0, packet1, restore_mask, write_mismatch;
    u32 frame0, frame1, frame_after;
    struct p446l2_regs pre, loaded, post, restored;
    u32 crc, crc_inv;
} __packed;
static struct dsi_display *p446l2_display;
static atomic_t p446l2_once = ATOMIC_INIT(0);
static void p446l2_worker(struct work_struct *unused);
static DECLARE_DELAYED_WORK(p446l2_work, p446l2_worker);
static const u8 p446l2_padding[2048]; /* physical separation of payload replicas */

static inline u32 p446l2_dsi(struct dsi_ctrl *c, u32 off)
{
    return readl_relaxed(c->hw.base + off);
}
static void p446l2_snapshot(struct p446l2_regs *p, struct dsi_ctrl *c,
                            void __iomem *intf)
{
    p->ns = ktime_get_ns();
    p->frame = intf ? readl_relaxed(intf + 0x8ac) : ~0U;
    p->ctrl = p446l2_dsi(c, DSI_CTRL);
    p->status = p446l2_dsi(c, DSI_STATUS);
    p->lane = p446l2_dsi(c, DSI_LANE_STATUS);
    p->clk = p446l2_dsi(c, DSI_CLK_STATUS);
    p->irq = p446l2_dsi(c, DSI_INT_CTRL);
    p->trig = p446l2_dsi(c, DSI_TRIG_CTRL);
    p->dma = p446l2_dsi(c, DSI_COMMAND_MODE_DMA_CTRL);
    p->tpg = p446l2_dsi(c, DSI_TEST_PATTERN_GEN_CTRL);
    p->len = p446l2_dsi(c, DSI_DMA_CMD_LENGTH);
}
static void p446l2_publish(struct p446l2_result *p)
{
    u32 n;
    p->magic = P446L2_MAGIC;
    p->magic_inv = ~P446L2_MAGIC;
    p->version = 2;
    p->crc = crc32_le(~0U, (const u8 *)p,
                      offsetof(struct p446l2_result, crc)) ^ ~0U;
    p->crc_inv = ~p->crc;
    /* Duplicates share checksum but occupy different DDR cachelines/pages.
     * All writes happen after the hardware observation, not near kickoff.
     */
    for (n = 0; n < P446L2_REPLICAS; n++) {
        a52_p445_store_section(0x446c0200U, 0x20U, "L2_FIFO",
                               n, P446L2_REPLICAS, p, sizeof(*p));
        if (n + 1 < P446L2_REPLICAS)
            a52_p445_store_section(0x446c0201U, 0x21U,
                                   "L2_SEP", n, 0, p446l2_padding,
                                   sizeof(p446l2_padding));
    }
}
static void p446l2_worker(struct work_struct *unused)
{
    struct dsi_display *d = READ_ONCE(p446l2_display);
    struct dsi_ctrl *c = NULL;
    struct p446l2_result v = {0};
    struct dsi_ctrl_cmd_dma_fifo_info info = {0};
    u32 packet[2] = { 0xc0000329U, 0xff5a5af0U };
    void __iomem *intf = NULL;
    u32 orig_dma = 0, orig_tpg = 0, orig_len = 0;
    u32 st, irq, i;
    bool prepared = false, safe_to_restore = false;

    (void)unused;
    if (!d || atomic_cmpxchg(&p446l2_once, 0, 1))
        return;
    v.packet0 = packet[0]; v.packet1 = packet[1];
    v.stage = 1;
    mutex_lock(&d->display_lock);
    if (!d->is_cont_splash_enabled || !d->panel ||
        !d->ctrl_count || d->cmd_master_idx >= d->ctrl_count) {
        v.reason = 1; goto finish;
    }
    c = d->ctrl[d->cmd_master_idx].ctrl;
    if (!c || !c->hw.base || c->cell_index != 0) {
        v.reason = 2; goto finish;
    }
    intf = ioremap(0x0ae6b000ULL, 0x1000);
    if (!intf) { v.reason = 3; goto finish; }
    v.stage = 2;
    v.frame0 = readl_relaxed(intf + 0x8ac);
    msleep(27);
    v.frame1 = readl_relaxed(intf + 0x8ac);
    if (!d->is_cont_splash_enabled || v.frame0 == v.frame1) {
        v.reason = 4; goto finish;
    }
    v.stage = 3;
    /* Require bootloader CMD_MODE_EN and DSI_EN already set; no enable. */
    st = p446l2_dsi(c, DSI_CTRL);
    if ((st & (BIT(0) | BIT(2))) != (BIT(0) | BIT(2))) {
        v.reason = 5; goto finish;
    }
    if (!c->hw.ops.kickoff_fifo_command) {
        v.reason = 6; goto finish;
    }
    if ((p446l2_dsi(c, DSI_TRIG_CTRL) & 0x7U) != 0x4U) {
        v.reason = 7; goto finish;
    }
    /* No software panel_mode guard. Poll only physical MDP_BUSY (bit2). */
    v.stage = 4;
    for (i = 0; i < 8000; ++i) {
        if (!(p446l2_dsi(c, DSI_STATUS) & BIT(2)))
            break;
        udelay(3);
    }
    if (i == 8000) { v.reason = 8; goto finish; }
    p446l2_snapshot(&v.pre, c, intf);
    if ((v.pre.status & (BIT(0) | BIT(1) | BIT(2))) != 0 ||
        (v.pre.irq & BIT(0)) != 0 ||
        (v.pre.ctrl & (BIT(0) | BIT(2))) != (BIT(0) | BIT(2))) {
        v.reason = 9; goto finish;
    }
    if (!d->is_cont_splash_enabled) { v.reason = 10; goto finish; }

    orig_dma = v.pre.dma;
    orig_tpg = v.pre.tpg;
    orig_len = v.pre.len;
    info.command = packet;
    info.size = sizeof(packet);
    info.en_broadcast = false;
    info.is_master = false;
    info.use_lpm = false;
    v.stage = 5;
    /* Deferred kickoff: writing FIFO does not trigger hardware. */
    c->hw.ops.kickoff_fifo_command(&c->hw, &info,
                                  DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER);
    prepared = true;
    p446l2_snapshot(&v.loaded, c, intf);
    /* Recheck for collision just before SW_TRIGGER. No frame stoppage. */
    if (v.loaded.status & BIT(2)) {
        v.reason = 11;
        safe_to_restore = true;
        goto restore;
    }
    v.stage = 6;
    wmb();
    writel_relaxed(1U, c->hw.base + DSI_CMD_MODE_DMA_SW_TRIGGER);
    wmb();
    v.sent = 1;
    /* Detect DMA_DONE with raw INT bit0. Also track busy and idle. */
    for (i = 0; i < 250; ++i) {
        irq = p446l2_dsi(c, DSI_INT_CTRL);
        st = p446l2_dsi(c, DSI_STATUS);
        if (st & 3U) v.busy_samples++;
        else v.idle_samples++;
        if (irq & BIT(0)) { v.raw_done = 1; break; }
        udelay(4);
    }
    v.stage = 7;
    p446l2_snapshot(&v.post, c, intf);
    v.outcome = v.raw_done ? 1U : 2U;
    /* Do not restore DMA/TPG while hardware is still BUSY. */
    safe_to_restore = !(v.post.status & 3U);

restore:
    if (prepared && safe_to_restore) {
        /* No write to DSI_CTRL, TRIG_CTRL, INT_CTRL or SW_TRIGGER.
         * INT_CTRL is W1C and must never be blindly restored.
         * FIFO data is write-only; TPG disable leaves it inert.
         */
        writel_relaxed(orig_dma, c->hw.base + DSI_COMMAND_MODE_DMA_CTRL);
        writel_relaxed(orig_len, c->hw.base + DSI_DMA_CMD_LENGTH);
        writel_relaxed(orig_tpg, c->hw.base + DSI_TEST_PATTERN_GEN_CTRL);
        wmb();
        v.restore_mask = 7U;
        p446l2_snapshot(&v.restored, c, intf);
        v.write_mismatch = (v.restored.dma != orig_dma) |
            ((v.restored.len != orig_len) << 1) |
            ((v.restored.tpg != orig_tpg) << 2);
    } else if (prepared) {
        v.reason = 12; /* hardware busy; restoration unsafe */
    }
    if (v.sent && !v.raw_done && safe_to_restore && !v.reason)
        v.reason = 13; /* transfer did not expose raw DONE: inconclusive */

finish:
    if (intf) {
        msleep(40);
        v.frame_after = readl_relaxed(intf + 0x8ac);
        iounmap(intf);
    }
    mutex_unlock(&d->display_lock);
    p446l2_publish(&v);
    pr_err("P446L2 stage=%u reason=%u sent=%u done=%u outcome=%u restore=%x mismatch=%x frame=%u/%u/%u\n",
           v.stage, v.reason, v.sent, v.raw_done, v.outcome,
           v.restore_mask, v.write_mismatch, v.frame0, v.frame1, v.frame_after);
    pr_err("P446L2 PRE ctrl=%08x status=%08x lane=%08x clk=%08x irq=%08x trig=%08x POST status=%08x clk=%08x irq=%08x busy=%u idle=%u\n",
           v.pre.ctrl, v.pre.status, v.pre.lane, v.pre.clk,
           v.pre.irq, v.pre.trig, v.post.status, v.post.clk,
           v.post.irq, v.busy_samples, v.idle_samples);
    a52_ackfr_record("P446L2 st=%u err=%u kick=%u done=%u busy=%u fr=%u>%u>%u",
                     v.stage, v.reason, v.sent, v.raw_done,
                     v.busy_samples, v.frame0, v.frame1, v.frame_after);
}

'''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    path = args.root / "drivers/a52_display/msm/dsi/dsi_display.c"
    txt = path.read_text()
    if not args.check_only and MARK not in txt:
        # The existing Phase446j trace uses the same compiled display tree.
        txt = replace_once(txt, SIG, HELPER + SIG, "live splash helper")
        st = txt.index(SIG)
        en = txt.index(END, st)
        fn = txt[st:en]
        anchor = "\treturn rc;\n\n"
        if fn.count(anchor) != 1:
            raise RuntimeError("continuous splash success return changed")
        fn = fn.replace(anchor, '''\tif (!rc && display->is_cont_splash_enabled &&
\t    !atomic_read(&p446l2_once)) {
\t\tWRITE_ONCE(p446l2_display, display);
\t\tschedule_delayed_work(&p446l2_work, msecs_to_jiffies(7000));
\t}
\treturn rc;\n\n''', 1)
        txt = txt[:st] + fn + txt[en:]
        path.write_text(txt)
    txt = path.read_text()
    for needle in (MARK, 'P446L2 stage=', 'L2_FIFO', 'P446L2_REPLICAS 16U',
                   'msecs_to_jiffies(7000)', 'DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER',
                   'DSI_CMD_MODE_DMA_SW_TRIGGER', 'crc32_le('):
        if needle not in txt:
            raise RuntimeError("missing " + needle)
    for forbidden in ('dsi_display_cmd_engine_enable(', 'dsi_display_cmd_engine_disable(',
                      'dsi_display_clk_ctrl(', 'dsi_ctrl_cmd_transfer('):
        # These occur elsewhere in display.c: only examine helper segment.
        helper = txt[txt.index(MARK):txt.index(SIG, txt.index(MARK))]
        if forbidden in helper:
            raise RuntimeError("forbidden state change " + forbidden)
    if txt.count(MARK) != 1 or txt.count('schedule_delayed_work(&p446l2_work') != 1:
        raise RuntimeError("duplicate probe")
    print("Phase446l2: direct FIFO, no host/clock state changes; x16 CRC records PASS")

if __name__ == "__main__":
    main()
