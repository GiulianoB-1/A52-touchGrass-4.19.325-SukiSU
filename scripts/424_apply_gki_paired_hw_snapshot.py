#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE424_PAIRED_HW_SNAPSHOT_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n=text.count(old)
    if n != 1:
        raise SystemExit(f"Phase424 {label}: expected 1 anchor, found {n}")
    return text.replace(old,new,1)


COMMON = r'''
/* A52_PHASE424_PAIRED_HW_SNAPSHOT_V1
 *
 * Read-only paired Golden/GKI snapshot at the exact first F0 command-DMA
 * pre-trigger point.  No register write, reset, vote, clock, regulator or
 * RSC state change is performed here.
 *
 * Physical mappings are deliberately identical to Phase424G Golden:
 *   DISP_CC 0x0af00000, GCC 0x00100000, VBIF 0x0aeb0000,
 *   RSC drv 0x0af20000, RSC wrapper 0x0af30000.
 */
#define A52_P424_DISPCC_PHYS  0x0af00000ULL
#define A52_P424_GCC_PHYS     0x00100000ULL
#define A52_P424_VBIF_PHYS    0x0aeb0000ULL
#define A52_P424_RSC_DRV_PHYS 0x0af20000ULL
#define A52_P424_RSC_WRP_PHYS 0x0af30000ULL

struct a52_p424_snapshot {
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

static struct a52_p424_snapshot a52_p424_snap;
static atomic_t a52_p424_taken = ATOMIC_INIT(0);
static void __iomem *a52_p424_dispcc;
static void __iomem *a52_p424_gcc;
static void __iomem *a52_p424_vbif;
static void __iomem *a52_p424_rsc_drv;
static void __iomem *a52_p424_rsc_wrp;

static int __init a52_p424_map_init(void)
{
	a52_p424_dispcc = ioremap(A52_P424_DISPCC_PHYS, 0x3000);
	a52_p424_gcc = ioremap(A52_P424_GCC_PHYS, 0x18000);
	a52_p424_vbif = ioremap(A52_P424_VBIF_PHYS, 0x1000);
	a52_p424_rsc_drv = ioremap(A52_P424_RSC_DRV_PHYS, 0x2000);
	a52_p424_rsc_wrp = ioremap(A52_P424_RSC_WRP_PHYS, 0x100);
	return 0;
}
subsys_initcall(a52_p424_map_init);

static inline u32 a52_p424_r(void __iomem *b, u32 o)
{
	return b ? readl_relaxed((u8 __iomem *)b + o) : ~0U;
}

void a52_p424_snapshot(struct dsi_ctrl_hw *ctrl)
{
	struct a52_p424_snapshot *s=&a52_p424_snap;

	if (!a52_p421_target_active() || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p424_taken,0,1) != 0)
		return;

	memset(s,0,sizeof(*s));
	s->ns = ktime_get_boottime_ns();

	/* Small DSI alignment anchor. Full DSI/PHY prestate was already covered
	 * by the Phase314/315G line of experiments.
	 */
	s->dsi[0]=DSI_R32(ctrl,DSI_STATUS);
	s->dsi[1]=DSI_R32(ctrl,DSI_LANE_STATUS);
	s->dsi[2]=DSI_R32(ctrl,DSI_CLK_STATUS);
	s->dsi[3]=DSI_R32(ctrl,DSI_INT_CTRL);
	s->dsi[4]=DSI_R32(ctrl,DSI_COMMAND_MODE_DMA_CTRL);
	s->dsi[5]=DSI_R32(ctrl,DSI_DMA_CMD_OFFSET);
	s->dsi[6]=DSI_R32(ctrl,DSI_DMA_CMD_LENGTH);
	s->dsi[7]=DSI_R32(ctrl,DSI_AXI2AHB_CTRL);

	/* DISP_CC branch state + GCC DISP fabric clocks. */
	s->clk0[0]=a52_p424_r(a52_p424_dispcc,0x100c); /* pclk0 */
	s->clk0[1]=a52_p424_r(a52_p424_dispcc,0x1010); /* mdp */
	s->clk0[2]=a52_p424_r(a52_p424_dispcc,0x102c); /* byte0 */
	s->clk0[3]=a52_p424_r(a52_p424_dispcc,0x1030); /* byte0 intf */
	s->clk0[4]=a52_p424_r(a52_p424_dispcc,0x1034); /* esc0 */
	s->clk0[5]=a52_p424_r(a52_p424_dispcc,0x104c); /* mdss ahb */
	s->clk0[6]=a52_p424_r(a52_p424_gcc,0x1700c);   /* gcc disp ahb */
	s->clk0[7]=a52_p424_r(a52_p424_gcc,0x1701c);   /* gcc disp axi */

	/* Active RCG command/config registers. */
	s->clk1[0]=a52_p424_r(a52_p424_dispcc,0x1064);
	s->clk1[1]=a52_p424_r(a52_p424_dispcc,0x1068);
	s->clk1[2]=a52_p424_r(a52_p424_dispcc,0x107c);
	s->clk1[3]=a52_p424_r(a52_p424_dispcc,0x1080);
	s->clk1[4]=a52_p424_r(a52_p424_dispcc,0x10c4);
	s->clk1[5]=a52_p424_r(a52_p424_dispcc,0x10c8);
	s->clk1[6]=a52_p424_r(a52_p424_dispcc,0x10e0);
	s->clk1[7]=a52_p424_r(a52_p424_dispcc,0x10e4);

	/* MDSS GDSC and RSCC branch clocks. */
	s->pwr[0]=a52_p424_r(a52_p424_dispcc,0x1004);
	s->pwr[1]=a52_p424_r(a52_p424_dispcc,0x2004);
	s->pwr[2]=a52_p424_r(a52_p424_dispcc,0x2008);
	s->pwr[3]=a52_p424_r(a52_p424_dispcc,0x200c);

	/* VBIF read-only state. This is retained as an exploratory fabric
	 * reference; DSI command DMA is not treated as a proven VBIF client.
	 */
	s->vbif0[0]=a52_p424_r(a52_p424_vbif,0x0000);
	s->vbif0[1]=a52_p424_r(a52_p424_vbif,0x0008);
	s->vbif0[2]=a52_p424_r(a52_p424_vbif,0x000c);
	s->vbif0[3]=a52_p424_r(a52_p424_vbif,0x0190);
	s->vbif0[4]=a52_p424_r(a52_p424_vbif,0x0194);
	s->vbif0[5]=a52_p424_r(a52_p424_vbif,0x0200);
	s->vbif0[6]=a52_p424_r(a52_p424_vbif,0x0204);
	s->vbif0[7]=a52_p424_r(a52_p424_vbif,0x00b0);
	s->vbif1[0]=a52_p424_r(a52_p424_vbif,0x0020);
	s->vbif1[1]=a52_p424_r(a52_p424_vbif,0x0024);
	s->vbif1[2]=a52_p424_r(a52_p424_vbif,0x0550);
	s->vbif1[3]=a52_p424_r(a52_p424_vbif,0x0590);

	/* Display RSC hardware state. */
	s->rsc0[0]=a52_p424_r(a52_p424_rsc_drv,0x0404); /* seq busy */
	s->rsc0[1]=a52_p424_r(a52_p424_rsc_drv,0x0408); /* seq PC */
	s->rsc0[2]=a52_p424_r(a52_p424_rsc_drv,0x00d0); /* error irq */
	s->rsc0[3]=a52_p424_r(a52_p424_rsc_drv,0x0c14); /* solver override */
	s->rsc0[4]=a52_p424_r(a52_p424_rsc_drv,0x0c20); /* modes enabled */
	s->rsc0[5]=a52_p424_r(a52_p424_rsc_drv,0x0c24); /* solver status0 */
	s->rsc0[6]=a52_p424_r(a52_p424_rsc_drv,0x0c28); /* solver status1 */
	s->rsc0[7]=a52_p424_r(a52_p424_rsc_drv,0x0c2c); /* solver status2 */
	s->rsc1[0]=a52_p424_r(a52_p424_rsc_drv,0x1c00); /* AMC IRQ */
	s->rsc1[1]=a52_p424_r(a52_p424_rsc_drv,0x1c14); /* TCS control */
	s->rsc1[2]=a52_p424_r(a52_p424_rsc_wrp,0x0000); /* wrapper ctrl */
	s->rsc1[3]=a52_p424_r(a52_p424_rsc_wrp,0x0004); /* override ctrl */
	s->rsc1[4]=a52_p424_r(a52_p424_rsc_wrp,0x0024); /* power ctrl */
	s->rsc1[5]=a52_p424_r(a52_p424_rsc_wrp,0x0048); /* BW indication */
}
EXPORT_SYMBOL_GPL(a52_p424_snapshot);

void a52_p424_dump_snapshot(void)
{
	const struct a52_p424_snapshot *s=&a52_p424_snap;

	if (!atomic_read(&a52_p424_taken))
		return;

	a52_ackfr_record("P424 T ns=%llu",(unsigned long long)s->ns);
	a52_ackfr_record("P424 D %x %x %x %x %x %x %x %x",
		s->dsi[0],s->dsi[1],s->dsi[2],s->dsi[3],
		s->dsi[4],s->dsi[5],s->dsi[6],s->dsi[7]);
	a52_ackfr_record("P424 C0 %x %x %x %x %x %x %x %x",
		s->clk0[0],s->clk0[1],s->clk0[2],s->clk0[3],
		s->clk0[4],s->clk0[5],s->clk0[6],s->clk0[7]);
	a52_ackfr_record("P424 C1 %x %x %x %x %x %x %x %x",
		s->clk1[0],s->clk1[1],s->clk1[2],s->clk1[3],
		s->clk1[4],s->clk1[5],s->clk1[6],s->clk1[7]);
	a52_ackfr_record("P424 P %x %x %x %x",
		s->pwr[0],s->pwr[1],s->pwr[2],s->pwr[3]);
	a52_ackfr_record("P424 V0 %x %x %x %x %x %x %x %x",
		s->vbif0[0],s->vbif0[1],s->vbif0[2],s->vbif0[3],
		s->vbif0[4],s->vbif0[5],s->vbif0[6],s->vbif0[7]);
	a52_ackfr_record("P424 V1 %x %x %x %x",
		s->vbif1[0],s->vbif1[1],s->vbif1[2],s->vbif1[3]);
	a52_ackfr_record("P424 R0 %x %x %x %x %x %x %x %x",
		s->rsc0[0],s->rsc0[1],s->rsc0[2],s->rsc0[3],
		s->rsc0[4],s->rsc0[5],s->rsc0[6],s->rsc0[7]);
	a52_ackfr_record("P424 R1 %x %x %x %x %x %x",
		s->rsc1[0],s->rsc1[1],s->rsc1[2],
		s->rsc1[3],s->rsc1[4],s->rsc1[5]);
}
EXPORT_SYMBOL_GPL(a52_p424_dump_snapshot);
'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1",
        "extern bool a52_p421_target_active(void);",
        "a52_p421_survival_record(7U, 0U",
        "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
    ):
        if token not in text:
            raise SystemExit("Phase424 HWC prerequisite missing: "+token)
    if "#include <linux/ktime.h>\n" not in text:
        text=one(text,'#include "sde_dbg.h"\n',
                 '#include "sde_dbg.h"\n#include <linux/ktime.h>\n#include <linux/init.h>\n',
                 "HWC includes")
    decl='''extern void a52_p421_survival_record(u8 stage, u8 valid, u32 flags,
				     int rc, u32 irq, int ret);
'''
    text=one(text,decl,decl+
             'extern void a52_ackfr_record(const char *fmt, ...);\n'+COMMON+"\n",
             "snapshot helper insertion")
    old='''		if (a52_p421_target_active())
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new='''		if (a52_p421_target_active()) {
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
			a52_p424_snapshot(ctrl);
		}
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    return one(text,old,new,"pre-trigger snapshot call")


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    token='''extern void a52_p422_display_timeout_snapshot(u64 cmd_buffer_iova);
'''
    if token not in text:
        raise SystemExit("Phase424 CTRL Phase422 declaration missing")
    text=one(text,token,token+'extern void a52_p424_dump_snapshot(void);\n',
             "dump declaration")
    old='''	if (a52_p421_target_active())
		a52_p422_display_timeout_snapshot((u64)dsi_ctrl->cmd_buffer_iova);
'''
    new=old+'''	if (a52_p421_target_active())
		a52_p424_dump_snapshot();
'''
    return one(text,old,new,"post-timeout dump")


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1" not in text:
        raise SystemExit("Phase424 recorder requires Phase423")
    for old,new,label in (
        ('\t    strncmp(fmt, "P423", 4) &&\n',
         '\t    strncmp(fmt, "P424", 4) &&\n\t    strncmp(fmt, "P423", 4) &&\n',
         "retained admission"),
        ('if (strncmp(fmt, "P423", 4) &&\n',
         'if (strncmp(fmt, "P424", 4) &&\n    strncmp(fmt, "P423", 4) &&\n',
         "normal admission"),
        ('\t    strncmp(fmt, "P423", 4) &&\n\t    strncmp(fmt, "P420", 4) &&\n',
         '\t    strncmp(fmt, "P424", 4) &&\n\t    strncmp(fmt, "P423", 4) &&\n\t    strncmp(fmt, "P420", 4) &&\n',
         "phase402 admission"),
    ):
        if old in text:
            text=one(text,old,new,label)
        elif 'strncmp(fmt, "P424", 4)' not in text:
            raise SystemExit("Phase424 recorder anchor missing: "+label)
    text += "\n/* "+MARK+": P424 post-timeout dump admitted to sequential recorder. */\n"
    return text


def validate(root: Path) -> None:
    h=(root/HWC).read_text(errors="replace")
    c=(root/CTRL).read_text(errors="replace")
    r=(root/REC).read_text(errors="replace")
    alltxt=h+c+r
    for token in (
        MARK,
        "A52_P424_DISPCC_PHYS  0x0af00000ULL",
        "A52_P424_VBIF_PHYS    0x0aeb0000ULL",
        "A52_P424_RSC_DRV_PHYS 0x0af20000ULL",
        "a52_p424_snapshot(ctrl);",
        "a52_p424_dump_snapshot();",
        "P424 C0 %x %x %x %x %x %x %x %x",
        "P424 V0 %x %x %x %x %x %x %x %x",
        "P424 R0 %x %x %x %x %x %x %x %x",
        'strncmp(fmt, "P424", 4)',
    ):
        if token not in alltxt:
            raise SystemExit("Phase424 validation missing: "+token)
    if any(x in h for x in ("writel_relaxed(","regmap_write(","DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_p424_snapshot")):
        raise SystemExit("Phase424 snapshot must remain pre-trigger/read-only")


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    ns=ap.parse_args()
    for rel in (HWC,CTRL,REC):
        if not (ns.root/rel).is_file():
            raise SystemExit("Phase424 source missing: "+str(rel))
    if not ns.check_only:
        p=ns.root/HWC; p.write_text(patch_hwc(p.read_text(errors="replace")))
        p=ns.root/CTRL; p.write_text(patch_ctrl(p.read_text(errors="replace")))
        p=ns.root/REC; p.write_text(patch_rec(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase424 paired hardware snapshot (GKI): PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
