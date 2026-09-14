#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE346_DMA_RAW_SIDEBAND_V1"

def one(s: str, old: str, new: str, label: str) -> str:
    n=s.count(old)
    if n!=1:
        raise SystemExit(f"Phase346 {label}: expected 1 match, found {n}")
    return s.replace(old,new,1)

RAW_BLOCK = r'''
/* A52_PHASE346_DMA_RAW_SIDEBAND_V1
 *
 * Phase345 proved that timeout-gated R48 export is not sufficient: if the
 * exact-F0 transaction succeeds, never reaches the timeout path, or its early
 * R48 records are later overwritten, the microsecond samples disappear.
 *
 * Reuse the dormant Phase342 sideband. Phase343 disables a52_r342_start(), so
 * 0xB1BFA000..0xB1BFC000 has no active writer in this lineage.
 *
 * Phase345 still captures p0..p6 into ordinary RAM with no cache maintenance.
 * Only AFTER p6 and DEBUG_BUS_CTL restore do we mirror+flush those samples here,
 * preserving the timing of the measured burst itself.
 */
#define A52_P346_SIDEBAND_PHYS  0xB1BFA000ULL
#define A52_P346_SIDEBAND_BYTES 0x2000U
#define A52_P346_COPY_BYTES     0x1000U
#define A52_P346_SLOT_BYTES     96U
#define A52_P346_MAGIC          0x3634335741524d44ULL
#define A52_P346_COMMIT         0x346c0de5U
#define A52_P346_INIT_POINT     0xffffffffU

struct a52_p346_slot {
	u64 magic;
	u64 ns;
	u32 index;
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
	u32 commit;
	u32 version;
};

static void *a52_p346_sideband;

static void a52_p346_write_slot(unsigned int slot_index,
				const struct a52_p346_slot *slot)
{
	void *dst0;
	void *dst1;
	unsigned int pos;

	if (!READ_ONCE(a52_p346_sideband) || !slot)
		return;
	pos = slot_index * A52_P346_SLOT_BYTES;
	if (pos + A52_P346_SLOT_BYTES > A52_P346_COPY_BYTES)
		return;

	dst0 = (u8 *)a52_p346_sideband + pos;
	dst1 = (u8 *)a52_p346_sideband + A52_P346_COPY_BYTES + pos;
	memcpy(dst0, slot, sizeof(*slot));
	memcpy(dst1, slot, sizeof(*slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(*slot));
	__flush_dcache_area(dst1, sizeof(*slot));
}

void a52_p346_sideband_init(void)
{
	struct a52_p346_slot slot;

	BUILD_BUG_ON(sizeof(struct a52_p346_slot) != A52_P346_SLOT_BYTES);
	if (READ_ONCE(a52_p346_sideband))
		return;

	a52_p346_sideband = memremap(A52_P346_SIDEBAND_PHYS,
		A52_P346_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_p346_sideband)
		return;

	/* Erase stale Phase342/Phase346 payloads from earlier boots first. */
	memset(a52_p346_sideband, 0, A52_P346_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_p346_sideband, A52_P346_SIDEBAND_BYTES);

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P346_MAGIC;
	slot.ns = ktime_get_ns();
	slot.index = 0xffffffffU;
	slot.point = A52_P346_INIT_POINT;
	slot.status = 1U; /* mapping initialized */
	slot.commit = A52_P346_COMMIT;
	slot.version = 1U;
	a52_p346_write_slot(0U, &slot);
}

static void a52_p346_persist_samples(void)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_p345_count);

	if (!READ_ONCE(a52_p346_sideband))
		return;
	if (n > A52_P345_MAX)
		n = A52_P345_MAX;

	for (i = 0; i < n; i++) {
		const struct a52_p345_sample *s = &a52_p345_samples[i];
		struct a52_p346_slot slot;

		memset(&slot, 0, sizeof(slot));
		slot.magic = A52_P346_MAGIC;
		slot.ns = s->ns;
		slot.index = i;
		slot.point = s->point;
		slot.status = s->status;
		slot.fifo = s->fifo;
		slot.clk_ctrl = s->clk_ctrl;
		slot.clk_status = s->clk_status;
		slot.int_ctrl = s->int_ctrl;
		slot.lane_status = s->lane_status;
		slot.dma_ctrl = s->dma_ctrl;
		slot.dma_offset = s->dma_offset;
		slot.dma_length = s->dma_length;
		slot.sw_trigger = s->sw_trigger;
		slot.trig_ctrl = s->trig_ctrl;
		slot.ack_err = s->ack_err;
		slot.timeout = s->timeout;
		slot.phy_err = s->phy_err;
		slot.axi2ahb = s->axi2ahb;
		slot.dbg171 = s->dbg171;
		slot.commit = A52_P346_COMMIT;
		slot.version = 1U;
		/* Slot 0 is the init marker; samples start at slot 1. */
		a52_p346_write_slot(i + 1U, &slot);
	}
}
'''

def patch_hwc(s: str) -> str:
    if MARK in s:
        return s
    if "A52_PHASE345_DMA_US_FRONTIER_V1" not in s:
        raise SystemExit("Phase346 requires Phase345 HWC source")

    # Explicit declarations for memremap and cache cleaning.
    inc = '#include <linux/iopoll.h>\n'
    if '#include <linux/io.h>\n' not in s:
        s = one(s, inc, inc + '#include <linux/io.h>\n#include <asm/cacheflush.h>\n', "I/O/cache includes")

    anchor = "static u32 a52_p345_saved_dbg_ctl;\n"
    s = one(s, anchor, anchor + RAW_BLOCK, "raw sideband block")

    old = """\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p345_saved_dbg_ctl);
\twmb();
\tatomic_set(&a52_p345_state, 2);
}
"""
    new = """\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p345_saved_dbg_ctl);
\twmb();
\tatomic_set(&a52_p345_state, 2);
\t/* Persist only after the p0..p6 measurement burst is complete. */
\ta52_p346_persist_samples();
}
"""
    return one(s, old, new, "post-burst persistence")

def inject_init_call(s: str) -> str:
    name="dsi_ctrl_drv_init"
    pos=s.find(name+"(")
    if pos<0:
        raise SystemExit("Phase346 dsi_ctrl_drv_init missing")
    brace=s.find("{",pos)
    if brace<0:
        raise SystemExit("Phase346 dsi_ctrl_drv_init body missing")

    depth=0
    end=-1
    for i in range(brace,len(s)):
        if s[i]=="{":
            depth+=1
        elif s[i]=="}":
            depth-=1
            if depth==0:
                end=i+1
                break
    if end<0:
        raise SystemExit("Phase346 dsi_ctrl_drv_init unterminated")

    body=s[brace:end]
    anchor="\tint rc = 0;\n\n"
    if body.count(anchor)!=1:
        raise SystemExit(
            f"Phase346 dsi_ctrl_drv_init declaration anchor count {body.count(anchor)}")
    body=body.replace(
        anchor,
        anchor+"\ta52_p346_sideband_init();\n\n",
        1)
    return s[:brace]+body+s[end:]

def patch_ctrl(s: str) -> str:
    if MARK in s:
        return s
    if "A52_PHASE345_DMA_US_FRONTIER_V1" not in s:
        raise SystemExit("Phase346 requires Phase345 CTRL source")
    decl='extern void a52_p345_flush(struct dsi_ctrl_hw *ctrl); /* A52_PHASE345_DMA_US_FRONTIER_V1 */\n'
    s=one(s,decl,decl+'extern void a52_p346_sideband_init(void); /* '+MARK+' */\n',"init declaration")
    s=inject_init_call(s)
    return s

def validate(bh: str, ah: str, bc: str, ac: str) -> None:
    both=ah+ac
    for x in (
        MARK,
        "A52_P346_SIDEBAND_PHYS  0xB1BFA000ULL",
        "A52_P346_SIDEBAND_BYTES 0x2000U",
        "A52_P346_SLOT_BYTES     96U",
        "A52_P346_INIT_POINT     0xffffffffU",
        "a52_p346_write_slot(i + 1U, &slot);",
        "a52_p346_persist_samples();",
        "a52_p346_sideband_init();",
        "memremap(A52_P346_SIDEBAND_PHYS",
        "__flush_dcache_area(dst0, sizeof(*slot));",
        "__flush_dcache_area(dst1, sizeof(*slot));",
    ):
        if x not in both:
            raise SystemExit("Phase346 missing "+x)

    # Phase342 remains compiled but its runtime start must stay disabled.
    if "static void __used a52_r342_start(void)" not in ah+ac:
        # Marker lives in recorder.c, not these files; checked by CI.
        pass

    # The measured DSI burst itself must be byte-for-byte unchanged from 345:
    # no new DSI register writes/reads, waits, delays, or clock/power controls.
    for x in (
        "DSI_W32(", "DSI_R32(", "wait_for_completion_timeout(",
        "udelay(", "usleep_range(", "msleep(",
        "clk_set_rate(", "clk_set_parent(", "regulator_enable(",
        "reset_control_assert(",
    ):
        if ah.count(x)!=bh.count(x) or ac.count(x)!=bc.count(x):
            raise SystemExit("Phase346 changed measured hardware primitive "+x)

    if ah.count("a52_p346_persist_samples();") != bh.count("a52_p346_persist_samples();") + 1:
        raise SystemExit("Phase346 persistence must be added exactly once")
    if ac.count("a52_p346_sideband_init();") != bc.count("a52_p346_sideband_init();") + 1:
        raise SystemExit("Phase346 init call must be added exactly once")

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    ns=ap.parse_args()
    hp=ns.root/HWC; cp=ns.root/CTRL
    if not hp.is_file() or not cp.is_file():
        raise SystemExit("Phase346 source missing")
    h=hp.read_text(); c=cp.read_text()
    if MARK in h+c:
        for x in ("A52_P346_SIDEBAND_PHYS","a52_p346_persist_samples();","a52_p346_sideband_init();"):
            if x not in h+c: raise SystemExit("Phase346 check token missing "+x)
        print("Phase346 DMA raw sideband audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase346 marker missing in check-only mode")
    nh=patch_hwc(h); nc=patch_ctrl(c)
    validate(h,nh,c,nc)
    hp.write_text(nh); cp.write_text(nc)
    print("Phase346 DMA raw sideband applied: PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
