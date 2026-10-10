#!/usr/bin/env python3
"""Shared F0-BISECT v2 source transform; same code is used on GKI and TG.

Transforms the decompressed V1 patch script in memory, preserving its one-shot
FIFO packet and trigger, physical-register snapshots, and 16-fold recorder.
The original record layout stays byte-compatible: upper 16 bits of 'flags'
encode the per-condition refusal mask. Lower bits retain their V1 meanings.

Mask bits: 0 no cont_splash, 1 controller enable, 2 status active,
3 stale DMA_DONE, 4 trigger != 4, 5 missing idle CLK 0x2343,
6 clock BIT16 unexpectedly set.
"""
OLD_GATE = r'''    /* Conservative observed-good F0 clock status gate, not a guarantee. */
    if (!(r.flags & 1) ||
        (r.ctrl & (BIT(0) | BIT(2))) != (BIT(0) | BIT(2)) ||
        (r.status & 7U) || r.before_done ||
        (r.trigger & 7U) != 4U ||
        !(r.clock_status & BIT(23))) {
        r.rc = 3; goto out;
    }'''
NEW_GATE = r'''    /* F0B_GUARD_V2: 0x2343 is the verified TG+GKI idle-before-F0 state.
     * DSI CLK_STATUS bit23 asserts during the command; do NOT require it
     * before the FIFO trigger. Preserve independent controller/IRQ gates.
     */
    if (!(r.flags & 1U)) fail_mask |= BIT(0);
    if ((r.ctrl & (BIT(0) | BIT(2))) != (BIT(0) | BIT(2)))
        fail_mask |= BIT(1);
    if (r.status & 7U) fail_mask |= BIT(2);
    if (r.before_done) fail_mask |= BIT(3);
    if ((r.trigger & 7U) != 4U) fail_mask |= BIT(4);
    if ((r.clock_status & 0x2343U) != 0x2343U)
        fail_mask |= BIT(5);
    if (r.clock_status & BIT(16)) fail_mask |= BIT(6);
    r.flags |= (fail_mask << 16); /* persist mask in existing 16x record */
    if (fail_mask) {
        r.rc = 3; goto out;
    }'''
OLD_DECL='    u32 old_dma, old_length, old_tpg, irq, st, i;'
NEW_DECL='    u32 old_dma, old_length, old_tpg, irq, st, i, fail_mask = 0;'
OLD_LOG=r'''    pr_warn("F0B ACTIVE P%u rc=%u sent=%u DONE=%u st=%08x clk=%08x lane=%08x\n",
            phase, r.rc, !!(r.flags & 4), r.after_done,
            r.status, r.clock_status, r.lane);'''
NEW_LOG=r'''    pr_warn("F0B ACTIVE P%u rc=%u sent=%u DONE=%u refuse=%04x"
            " st=%08x clk=%08x lane=%08x trig=%08x dma=%08x irq=%08x\n",
            phase, r.rc, !!(r.flags & 4), r.after_done,
            r.flags >> 16, r.status, r.clock_status, r.lane,
            r.trigger, r.dma, r.irq);'''

def once(src, old, new, title):
    n=src.count(old)
    if n != 1:
        raise RuntimeError("F0B_V2 %s: expected exact one anchor; got %d"%(title,n))
    return src.replace(old,new,1)

def transform(src):
    if 'F0B_GUARD_V2' in src:
        return src
    src=once(src,OLD_GATE,NEW_GATE,"old faulty bit23 gate")
    src=once(src,OLD_DECL,NEW_DECL,"unique one-shot declarations")
    src=once(src,OLD_LOG,NEW_LOG,"single ACTIVE report")
    assert "!(r.clock_status & BIT(23))" not in src
    assert "(r.clock_status & 0x2343U) != 0x2343U" in src
    assert "r.flags |= (fail_mask << 16)" in src
    assert "refuse=%04x" in src and "trig=%08x dma=%08x" in src
    return src
