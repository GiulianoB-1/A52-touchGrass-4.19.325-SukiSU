#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK="A52_PHASE424G_PAIRED_HW_SNAPSHOT_V1"
CTRL=Path("dsi_ctrl.c")
HWC=Path("dsi_ctrl_hw_cmn.c")


def one(text:str,old:str,new:str,label:str)->str:
    n=text.count(old)
    if n!=1:
        raise SystemExit(f"Phase424G {label}: expected 1 anchor, found {n}")
    return text.replace(old,new,1)


HELPER=r'''
/* A52_PHASE424G_PAIRED_HW_SNAPSHOT_V1
 *
 * Byte-for-byte register list paired with GKI Phase424.  Reads only.
 * The snapshot is taken once on the known-good exact F0 immediately before
 * SW_TRIGGER and printed only after DMA_DONE/wait completion.
 */
#define A52_G424_DISPCC_PHYS  0x0af00000ULL
#define A52_G424_GCC_PHYS     0x00100000ULL
#define A52_G424_VBIF_PHYS    0x0aeb0000ULL
#define A52_G424_RSC_DRV_PHYS 0x0af20000ULL
#define A52_G424_RSC_WRP_PHYS 0x0af30000ULL

struct a52_g424_snapshot {
	u64 ns;
	u32 dsi[8];
	u32 clk0[8];
	u32 clk1[8];
	u32 pwr[4];
	u32 vbif0[8];
	u32 vbif1[4];
	u32 rsc0[8];
	u32 rsc1[6];
};

static struct a52_g424_snapshot a52_g424_snap;
static atomic_t a52_g424_taken=ATOMIC_INIT(0);
static void __iomem *a52_g424_dispcc;
static void __iomem *a52_g424_gcc;
static void __iomem *a52_g424_vbif;
static void __iomem *a52_g424_rsc_drv;
static void __iomem *a52_g424_rsc_wrp;

static int __init a52_g424_map_init(void)
{
	a52_g424_dispcc=ioremap(A52_G424_DISPCC_PHYS,0x3000);
	a52_g424_gcc=ioremap(A52_G424_GCC_PHYS,0x18000);
	a52_g424_vbif=ioremap(A52_G424_VBIF_PHYS,0x1000);
	a52_g424_rsc_drv=ioremap(A52_G424_RSC_DRV_PHYS,0x2000);
	a52_g424_rsc_wrp=ioremap(A52_G424_RSC_WRP_PHYS,0x100);
	return 0;
}
subsys_initcall(a52_g424_map_init);

static inline u32 a52_g424_r(void __iomem *b,u32 o)
{
	return b ? readl_relaxed((u8 __iomem *)b+o) : ~0U;
}

void a52_g424_snapshot(struct dsi_ctrl_hw *ctrl)
{
	struct a52_g424_snapshot *s=&a52_g424_snap;

	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_g424_taken,0,1)!=0)
		return;

	memset(s,0,sizeof(*s));
	s->ns=ktime_get_boottime_ns();

	s->dsi[0]=DSI_R32(ctrl,DSI_STATUS);
	s->dsi[1]=DSI_R32(ctrl,DSI_LANE_STATUS);
	s->dsi[2]=DSI_R32(ctrl,DSI_CLK_STATUS);
	s->dsi[3]=DSI_R32(ctrl,DSI_INT_CTRL);
	s->dsi[4]=DSI_R32(ctrl,DSI_COMMAND_MODE_DMA_CTRL);
	s->dsi[5]=DSI_R32(ctrl,DSI_DMA_CMD_OFFSET);
	s->dsi[6]=DSI_R32(ctrl,DSI_DMA_CMD_LENGTH);
	s->dsi[7]=DSI_R32(ctrl,DSI_AXI2AHB_CTRL);

	s->clk0[0]=a52_g424_r(a52_g424_dispcc,0x100c);
	s->clk0[1]=a52_g424_r(a52_g424_dispcc,0x1010);
	s->clk0[2]=a52_g424_r(a52_g424_dispcc,0x102c);
	s->clk0[3]=a52_g424_r(a52_g424_dispcc,0x1030);
	s->clk0[4]=a52_g424_r(a52_g424_dispcc,0x1034);
	s->clk0[5]=a52_g424_r(a52_g424_dispcc,0x104c);
	s->clk0[6]=a52_g424_r(a52_g424_gcc,0x1700c);
	s->clk0[7]=a52_g424_r(a52_g424_gcc,0x1701c);

	s->clk1[0]=a52_g424_r(a52_g424_dispcc,0x1064);
	s->clk1[1]=a52_g424_r(a52_g424_dispcc,0x1068);
	s->clk1[2]=a52_g424_r(a52_g424_dispcc,0x107c);
	s->clk1[3]=a52_g424_r(a52_g424_dispcc,0x1080);
	s->clk1[4]=a52_g424_r(a52_g424_dispcc,0x10c4);
	s->clk1[5]=a52_g424_r(a52_g424_dispcc,0x10c8);
	s->clk1[6]=a52_g424_r(a52_g424_dispcc,0x10e0);
	s->clk1[7]=a52_g424_r(a52_g424_dispcc,0x10e4);

	s->pwr[0]=a52_g424_r(a52_g424_dispcc,0x1004);
	s->pwr[1]=a52_g424_r(a52_g424_dispcc,0x2004);
	s->pwr[2]=a52_g424_r(a52_g424_dispcc,0x2008);
	s->pwr[3]=a52_g424_r(a52_g424_dispcc,0x200c);

	s->vbif0[0]=a52_g424_r(a52_g424_vbif,0x0000);
	s->vbif0[1]=a52_g424_r(a52_g424_vbif,0x0008);
	s->vbif0[2]=a52_g424_r(a52_g424_vbif,0x000c);
	s->vbif0[3]=a52_g424_r(a52_g424_vbif,0x0190);
	s->vbif0[4]=a52_g424_r(a52_g424_vbif,0x0194);
	s->vbif0[5]=a52_g424_r(a52_g424_vbif,0x0200);
	s->vbif0[6]=a52_g424_r(a52_g424_vbif,0x0204);
	s->vbif0[7]=a52_g424_r(a52_g424_vbif,0x00b0);
	s->vbif1[0]=a52_g424_r(a52_g424_vbif,0x0020);
	s->vbif1[1]=a52_g424_r(a52_g424_vbif,0x0024);
	s->vbif1[2]=a52_g424_r(a52_g424_vbif,0x0550);
	s->vbif1[3]=a52_g424_r(a52_g424_vbif,0x0590);

	s->rsc0[0]=a52_g424_r(a52_g424_rsc_drv,0x0404);
	s->rsc0[1]=a52_g424_r(a52_g424_rsc_drv,0x0408);
	s->rsc0[2]=a52_g424_r(a52_g424_rsc_drv,0x00d0);
	s->rsc0[3]=a52_g424_r(a52_g424_rsc_drv,0x0c14);
	s->rsc0[4]=a52_g424_r(a52_g424_rsc_drv,0x0c20);
	s->rsc0[5]=a52_g424_r(a52_g424_rsc_drv,0x0c24);
	s->rsc0[6]=a52_g424_r(a52_g424_rsc_drv,0x0c28);
	s->rsc0[7]=a52_g424_r(a52_g424_rsc_drv,0x0c2c);
	s->rsc1[0]=a52_g424_r(a52_g424_rsc_drv,0x1c00);
	s->rsc1[1]=a52_g424_r(a52_g424_rsc_drv,0x1c14);
	s->rsc1[2]=a52_g424_r(a52_g424_rsc_wrp,0x0000);
	s->rsc1[3]=a52_g424_r(a52_g424_rsc_wrp,0x0004);
	s->rsc1[4]=a52_g424_r(a52_g424_rsc_wrp,0x0024);
	s->rsc1[5]=a52_g424_r(a52_g424_rsc_wrp,0x0048);
}

void a52_g424_dump_snapshot(void)
{
	const struct a52_g424_snapshot *s=&a52_g424_snap;
	if (!atomic_read(&a52_g424_taken))
		return;

	pr_info("TG424 T ns=%llu\n",(unsigned long long)s->ns);
	pr_info("TG424 D %x %x %x %x %x %x %x %x\n",
		s->dsi[0],s->dsi[1],s->dsi[2],s->dsi[3],
		s->dsi[4],s->dsi[5],s->dsi[6],s->dsi[7]);
	pr_info("TG424 C0 %x %x %x %x %x %x %x %x\n",
		s->clk0[0],s->clk0[1],s->clk0[2],s->clk0[3],
		s->clk0[4],s->clk0[5],s->clk0[6],s->clk0[7]);
	pr_info("TG424 C1 %x %x %x %x %x %x %x %x\n",
		s->clk1[0],s->clk1[1],s->clk1[2],s->clk1[3],
		s->clk1[4],s->clk1[5],s->clk1[6],s->clk1[7]);
	pr_info("TG424 P %x %x %x %x\n",
		s->pwr[0],s->pwr[1],s->pwr[2],s->pwr[3]);
	pr_info("TG424 V0 %x %x %x %x %x %x %x %x\n",
		s->vbif0[0],s->vbif0[1],s->vbif0[2],s->vbif0[3],
		s->vbif0[4],s->vbif0[5],s->vbif0[6],s->vbif0[7]);
	pr_info("TG424 V1 %x %x %x %x\n",
		s->vbif1[0],s->vbif1[1],s->vbif1[2],s->vbif1[3]);
	pr_info("TG424 R0 %x %x %x %x %x %x %x %x\n",
		s->rsc0[0],s->rsc0[1],s->rsc0[2],s->rsc0[3],
		s->rsc0[4],s->rsc0[5],s->rsc0[6],s->rsc0[7]);
	pr_info("TG424 R1 %x %x %x %x %x %x\n",
		s->rsc1[0],s->rsc1[1],s->rsc1[2],
		s->rsc1[3],s->rsc1[4],s->rsc1[5]);
}
'''


def patch_hwc(text:str)->str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1",
        "void a52_g344_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)",
        "a52_g344_snapshot(ctrl, 0);",
        "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
    ):
        if token not in text:
            raise SystemExit("Phase424G HWC prerequisite missing: "+token)
    anchor="void a52_g344_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)\n"
    text=one(text,anchor,HELPER+"\n"+anchor,"helper insertion")
    old='''		a52_g344_snapshot(ctrl, 0);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new='''		a52_g344_snapshot(ctrl, 0);
		a52_g424_snapshot(ctrl);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    if text.count(old)!=2:
        raise SystemExit(f"Phase424G expected 2 trigger paths, found {text.count(old)}")
    text=text.replace(old,new)
    return text


def patch_ctrl(text:str)->str:
    if MARK in text:
        return text
    decl='''extern void a52_g344_dump_samples(void);
'''
    if decl not in text:
        raise SystemExit("Phase424G dump declaration anchor missing")
    text=one(text,decl,decl+'extern void a52_g424_dump_snapshot(void);\n',"dump declaration")
    old='''		a52_g344_dump_samples();
'''
    new=old+'''		a52_g424_dump_snapshot();
'''
    return one(text,old,new,"completion dump")


def validate(ctrl:str,hwc:str)->None:
    alltxt=ctrl+hwc
    for token in (
        MARK,
        "A52_G424_DISPCC_PHYS  0x0af00000ULL",
        "A52_G424_VBIF_PHYS    0x0aeb0000ULL",
        "a52_g424_snapshot(ctrl);",
        "a52_g424_dump_snapshot();",
        "TG424 C0 %x %x %x %x %x %x %x %x",
        "TG424 V0 %x %x %x %x %x %x %x %x",
        "TG424 R0 %x %x %x %x %x %x %x %x",
    ):
        if token not in alltxt:
            raise SystemExit("Phase424G validation missing: "+token)
    if hwc.count("a52_g424_snapshot(ctrl);") != 2:
        raise SystemExit("Phase424G snapshot not present on both trigger paths")
    if "writel_relaxed(" in HELPER or "DSI_W32(" in HELPER:
        raise SystemExit("Phase424G helper must remain read-only")


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True,
                    help="TouchGrass techpack/display/msm/dsi directory")
    ap.add_argument("--check-only",action="store_true")
    ns=ap.parse_args()
    cp=ns.root/CTRL; hp=ns.root/HWC
    for p in (cp,hp):
        if not p.is_file():
            raise SystemExit("Phase424G source missing: "+str(p))
    if not ns.check_only:
        hp.write_text(patch_hwc(hp.read_text(errors="replace")))
        cp.write_text(patch_ctrl(cp.read_text(errors="replace")))
    validate(cp.read_text(errors="replace"),hp.read_text(errors="replace"))
    print("Phase424G paired hardware snapshot (Golden): PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
