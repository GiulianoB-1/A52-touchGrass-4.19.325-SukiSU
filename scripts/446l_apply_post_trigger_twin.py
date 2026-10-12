#!/usr/bin/env python3
"""Phase446L paired trigger-time DSI observation, for TG and GKI.

Extends 446K after 446i/446j. One first cold-ON command per boot.
All samples are passive; no writes except the pre-existing DSI debug-bus
selector sweep, which is restored. Records wall time spent sampling.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE446L_FIRST_COLD_ON_TRIGGER_V1"

def one(s, old, new, why):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"446L {why}: anchor count={n}")
    return s.replace(old, new, 1)

IMPL = r'''
/* A52_PHASE446L_FIRST_COLD_ON_TRIGGER_V1 */
#define P446L_WORDS 256U
#define P446L_DISPCC_WORDS 128U
struct p446l_meta {
    u64 iova, phys;
    u32 len, flags, msg_flags, domain_found, prefix_len;
    u8 prefix[16];
} __packed;
struct p446l_sample {
    u64 ns_begin, ns_end;
    u32 tag, hw_flags, elapsed_us, status, lane, clk, int_ctrl;
    u32 ctrl[P446L_WORDS];
    u32 dispcc[P446L_DISPCC_WORDS]; /* offsets 0x1000..0x11fc */
    u32 gdsc;  /* real MDSS GDSC @0x0af01004 */
    u32 cal_l; /* actual Fabia CAL_L readback */
    u32 gcc[6]; /* 1b7040/44 votes, 1700c/1c/70/34 CBCRs */
    u32 hf0_tbu; /* 0x15182210 */
    u32 tcs[2][2][6]; /* disp/apps, first 2 TCS: ctl/st/en/cmd0 addr/data/status */
    u32 cb2_route,cb2_fsr,cb2_fsynr0,cb2_sctlr;
    u64 cb2_far,cb2_ttbr0;
    struct p446i_bus_pair bus[ARRAY_SIZE(p446i_sel)];
} __packed;
static struct p446l_meta p446l_cmd;
static struct p446l_sample p446l_t3,p446l_t4;
struct p446l_fast {
    u64 ns; u32 deadline_us,status,irq,lane,clk;
} __packed;
static struct p446l_fast p446l_fast[7];
static const u32 p446l_deadline_us[7]={0U,1U,2U,5U,10U,20U,50U};
static void __iomem *p446l_votes,*p446l_gcc,*p446l_gdsc,*p446l_hf0;
static void __iomem *p446l_disp_rsc,*p446l_apps_rsc;
static int __init p446l_map_init(void)
{
    p446l_votes=ioremap(0x001b7000ULL,0x1000);
    p446l_gcc=ioremap(0x00117000ULL,0x1000);
    p446l_gdsc=ioremap(0x0af01000ULL,0x1000);
    p446l_hf0=ioremap(0x15182000ULL,0x1000);
    p446l_disp_rsc=ioremap(0x0af20000ULL,0x3000);
    p446l_apps_rsc=ioremap(0x18220000ULL,0x3000);
    return 0;
}
late_initcall(p446l_map_init);
static void p446l_take_fast(struct dsi_ctrl_hw *hw, u64 start)
{
    u32 i;
    for (i=0;i<ARRAY_SIZE(p446l_fast);i++) {
        struct p446l_fast *f=&p446l_fast[i];
        while (ktime_get_ns()-start <
               (u64)p446l_deadline_us[i]*1000ULL)
            cpu_relax();
        f->ns=ktime_get_ns();
        f->deadline_us=p446l_deadline_us[i];
        f->status=DSI_R32(hw,DSI_STATUS);
        f->irq=DSI_R32(hw,DSI_INT_CTRL);
        f->lane=DSI_R32(hw,DSI_LANE_STATUS);
        f->clk=DSI_R32(hw,DSI_CLK_STATUS);
    }
}
static void p446l_take_tcs(struct p446l_sample *s)
{
    void __iomem *base[2]={p446l_disp_rsc,p446l_apps_rsc};
    const u32 off[2]={0x1c00U,0x0d00U};
    u32 i,t;
    for (i=0;i<2;i++) for(t=0;t<2;t++) {
        u32 o=off[i]+t*672U;
        s->tcs[i][t][0]=p446i_r(base[i],o+0x14U);
        s->tcs[i][t][1]=p446i_r(base[i],o+0x18U);
        s->tcs[i][t][2]=p446i_r(base[i],o+0x1cU);
        s->tcs[i][t][3]=p446i_r(base[i],o+0x34U);
        s->tcs[i][t][4]=p446i_r(base[i],o+0x38U);
        s->tcs[i][t][5]=p446i_r(base[i],o+0x3cU);
    }
}

static atomic_t p446l_state=ATOMIC_INIT(0); /* 0 idle; 1 armed; 2 taken */

void a52_p446l_arm(struct dsi_ctrl_hw *ctrl)
{
    if (!ctrl || ctrl->index!=0 || !ctrl->base)
        return;
    (void)atomic_cmpxchg(&p446l_state,0,1);
}
EXPORT_SYMBOL_GPL(a52_p446l_arm);

bool a52_p446l_is_armed(void)
{
    return atomic_read(&p446l_state)==1;
}
EXPORT_SYMBOL_GPL(a52_p446l_is_armed);
void a52_p446l_note(struct dsi_ctrl_hw *ctrl, u64 iova, u64 phys,
                    u32 len, u32 flags, u32 msg_flags,
                    u32 domain_found, const u8 *prefix)
{
    if (!ctrl || ctrl->index!=0 || atomic_read(&p446l_state)!=1)
        return;
    p446l_cmd.iova=iova;
    p446l_cmd.phys=phys;
    p446l_cmd.len=len;
    p446l_cmd.flags=flags;
    p446l_cmd.msg_flags=msg_flags;
    p446l_cmd.domain_found=domain_found;
    p446l_cmd.prefix_len=min(len,16U);
    memset(p446l_cmd.prefix,0,sizeof(p446l_cmd.prefix));
    if (prefix && p446l_cmd.prefix_len)
        memcpy(p446l_cmd.prefix,prefix,p446l_cmd.prefix_len);
}
EXPORT_SYMBOL_GPL(a52_p446l_note);

extern void a52_p446l_cb2_live(u32 *,u32 *,u64 *,u32 *,u32 *,u64 *);
static void p446l_take(struct dsi_ctrl_hw *hw,
                        struct p446l_sample *s,u32 tag,u32 flags)
{
    u32 i;
    s->ns_begin=ktime_get_ns();
    s->tag=tag;
    s->hw_flags=flags;
    s->status=DSI_R32(hw,DSI_STATUS);
    s->lane=DSI_R32(hw,DSI_LANE_STATUS);
    s->clk=DSI_R32(hw,DSI_CLK_STATUS);
    s->int_ctrl=DSI_R32(hw,DSI_INT_CTRL);
    for(i=0;i<P446L_WORDS;i++)
        s->ctrl[i]=DSI_R32(hw,i*4U);
    for(i=0;i<P446L_DISPCC_WORDS;i++)
        s->dispcc[i]=p446i_r(p446i_dispcc,0x1000U+i*4U);
    s->gdsc=p446i_r(p446l_gdsc,0x004U);
    s->cal_l=p446i_r(p446i_dispcc,0x008U);
    s->gcc[0]=p446i_r(p446l_votes,0x040U); /* HF0 vote GDSC */
    s->gcc[1]=p446i_r(p446l_votes,0x044U); /* SF vote GDSC */
    s->gcc[2]=p446i_r(p446l_gcc,0x00cU); /* disp AHB */
    s->gcc[3]=p446i_r(p446l_gcc,0x01cU); /* disp AXI */
    s->gcc[4]=p446i_r(p446l_gcc,0x070U); /* CC XO */
    s->gcc[5]=p446i_r(p446l_gcc,0x034U); /* disp XO */
    s->hf0_tbu=p446i_r(p446l_hf0,0x210U);
    p446l_take_tcs(s);
    s->cb2_route=~0U;
    s->cb2_fsr=~0U;
    s->cb2_fsynr0=~0U;
    s->cb2_sctlr=~0U;
    s->cb2_far=~0ULL;
    s->cb2_ttbr0=~0ULL;
    a52_p446l_cb2_live(&s->cb2_route,&s->cb2_fsr,&s->cb2_far,
                        &s->cb2_fsynr0,&s->cb2_sctlr,&s->cb2_ttbr0);
    p446i_sweep(s->bus,hw);
    s->ns_end=ktime_get_ns();
    s->elapsed_us=(u32)((s->ns_end-s->ns_begin)/1000ULL);
}

void a52_p446l_after_trigger(struct dsi_ctrl_hw *hw,u32 hw_flags)
{
    u64 trigger_ns;
    u32 c3,c4;
    if (!hw || hw->index!=0 || !hw->base ||
        atomic_cmpxchg(&p446l_state,1,2)!=1)
        return;
    trigger_ns=ktime_get_ns();
    p446l_take_fast(hw,trigger_ns);
    p446l_take(hw,&p446l_t3,3U,hw_flags);
    /* Approx. +200 us from trigger. If t3 itself costs >200 us,
     * retain the measured timestamp instead of pretending timing matched.
     */
    while(ktime_get_ns()-trigger_ns<200000ULL)
        cpu_relax();
    p446l_take(hw,&p446l_t4,4U,hw_flags);
    c3=p446k_hash(p446l_t3.ctrl,P446L_WORDS);
    c4=p446k_hash(p446l_t4.ctrl,P446L_WORDS);
    __STORE__(0x446c0030U,16U,"L_CMD_META",p446l_cmd.flags,
        p446l_cmd.domain_found,&p446l_cmd,sizeof(p446l_cmd));
    __STORE__(0x446c0035U,16U,"L_FAST_0_50",7U,50U,
        p446l_fast,sizeof(p446l_fast));
    __STORE__(0x446c0031U,16U,"L_T3_CTRL",3U,c3,
        p446l_t3.ctrl,sizeof(p446l_t3.ctrl));
    __STORE__(0x446c0032U,16U,"L_T3_CLK",3U,p446l_t3.elapsed_us,
        p446l_t3.dispcc,sizeof(p446l_t3.dispcc));
    __STORE__(0x446c0033U,16U,"L_T3_BUS",3U,ARRAY_SIZE(p446i_sel),
        p446l_t3.bus,sizeof(p446l_t3.bus));
    __STORE__(0x446c0034U,16U,"L_T3_META",3U,p446l_t3.elapsed_us,
        &p446l_t3,offsetof(struct p446l_sample,ctrl));
    __STORE__(0x446c0036U,16U,"L_T3_PLATFORM",3U,p446l_t3.cal_l,
        &p446l_t3.gdsc,offsetof(struct p446l_sample,bus)-
        offsetof(struct p446l_sample,gdsc));
    __STORE__(0x446c0041U,16U,"L_T4_CTRL",4U,c4,
        p446l_t4.ctrl,sizeof(p446l_t4.ctrl));
    __STORE__(0x446c0042U,16U,"L_T4_CLK",4U,p446l_t4.elapsed_us,
        p446l_t4.dispcc,sizeof(p446l_t4.dispcc));
    __STORE__(0x446c0043U,16U,"L_T4_BUS",4U,ARRAY_SIZE(p446i_sel),
        p446l_t4.bus,sizeof(p446l_t4.bus));
    __STORE__(0x446c0044U,16U,"L_T4_META",4U,p446l_t4.elapsed_us,
        &p446l_t4,offsetof(struct p446l_sample,ctrl));
    __STORE__(0x446c0046U,16U,"L_T4_PLATFORM",4U,p446l_t4.cal_l,
        &p446l_t4.gdsc,offsetof(struct p446l_sample,bus)-
        offsetof(struct p446l_sample,gdsc));
    pr_warn("P446L FAST 0us st=%08x irq=%08x lane=%08x clk=%08x; 5us st=%08x irq=%08x lane=%08x clk=%08x; 50us st=%08x irq=%08x lane=%08x clk=%08x\n",
        p446l_fast[0].status,p446l_fast[0].irq,p446l_fast[0].lane,p446l_fast[0].clk,
        p446l_fast[3].status,p446l_fast[3].irq,p446l_fast[3].lane,p446l_fast[3].clk,
        p446l_fast[6].status,p446l_fast[6].irq,p446l_fast[6].lane,p446l_fast[6].clk);
    pr_warn("P446L PLATFORM cal=%08x/%08x gdsc=%08x/%08x hf_vote=%08x/%08x sf_vote=%08x/%08x hf0=%08x/%08x gcc_ahb=%08x/%08x gcc_axi=%08x/%08x\n",
        p446l_t3.cal_l,p446l_t4.cal_l,p446l_t3.gdsc,p446l_t4.gdsc,
        p446l_t3.gcc[0],p446l_t4.gcc[0],p446l_t3.gcc[1],p446l_t4.gcc[1],
        p446l_t3.hf0_tbu,p446l_t4.hf0_tbu,p446l_t3.gcc[2],p446l_t4.gcc[2],
        p446l_t3.gcc[3],p446l_t4.gcc[3]);
    pr_warn("P446L CB2 t3 route=%08x fsr=%08x fsynr=%08x far=%llx sctlr=%08x ttbr=%llx t4 fsr=%08x fsynr=%08x\n",
        p446l_t3.cb2_route,p446l_t3.cb2_fsr,p446l_t3.cb2_fsynr0,
        (unsigned long long)p446l_t3.cb2_far,p446l_t3.cb2_sctlr,
        (unsigned long long)p446l_t3.cb2_ttbr0,
        p446l_t4.cb2_fsr,p446l_t4.cb2_fsynr0);
    pr_warn("P446L post-SW_TRIGGER +%lluus t3=%llu..%llu t4=%llu..%llu st=%08x/%08x clk=%08x/%08x lane=%08x/%08x int=%08x/%08x CTRL=%08x/%08x IOVA=%llx PHYS=%llx valid=%u len=%u flags=%08x\n",
        (unsigned long long)((p446l_t4.ns_begin-trigger_ns)/1000ULL),
        (unsigned long long)p446l_t3.ns_begin,
        (unsigned long long)p446l_t3.ns_end,
        (unsigned long long)p446l_t4.ns_begin,
        (unsigned long long)p446l_t4.ns_end,
        p446l_t3.status,p446l_t4.status,
        p446l_t3.clk,p446l_t4.clk,
        p446l_t3.lane,p446l_t4.lane,
        p446l_t3.int_ctrl,p446l_t4.int_ctrl,c3,c4,
        (unsigned long long)p446l_cmd.iova,
        (unsigned long long)p446l_cmd.phys,
        p446l_cmd.domain_found,p446l_cmd.len,p446l_cmd.flags);
}
EXPORT_SYMBOL_GPL(a52_p446l_after_trigger);
'''

def patch(root, kind):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    central=base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")
    panel=base/"dsi/dsi_panel.c"
    ctrl=base/"dsi/dsi_ctrl.c"
    hw=base/"dsi/dsi_ctrl_hw_cmn.c"
    s=central.read_text()
    if MARK not in s:
        store="a52_p445_store_section" if kind=="gki" else "a52_p444_store_section"
        s=one(s,"EXPORT_SYMBOL_GPL(a52_p446k_capture);\n",
              "EXPORT_SYMBOL_GPL(a52_p446k_capture);\n"+IMPL.replace("__STORE__",store),
              "central post-446K")
        central.write_text(s)
    s=panel.read_text()
    if "A52_PHASE446L_ARM_ON" not in s:
        anchor="a52_p446k_capture(&display->ctrl[0].ctrl->hw,2U);"
        addition=("\n        /* A52_PHASE446L_ARM_ON: first actual cold ON packet only. */\n"
            "        if (type==DSI_CMD_SET_ON && !display->is_cont_splash_enabled &&\n"
            "            display->ctrl[0].ctrl)\n"
            "            a52_p446l_arm(&display->ctrl[0].ctrl->hw);")
        s=one(s,anchor,anchor+addition,"panel ON arm")
        sig="extern void a52_p446k_capture(struct dsi_ctrl_hw *,u32);\n"
        s=one(s,sig,sig+"extern void a52_p446l_arm(struct dsi_ctrl_hw *);\n",
              "panel prototype")
        panel.write_text(s)
    s=ctrl.read_text()
    if "A52_PHASE446L_IOVA_CONTEXT" not in s:
        # The actual display GEM memory domain is accessed before triggering,
        # never from the MMIO snapshot's time-critical section.
        helper=r'''/* A52_PHASE446L_IOVA_CONTEXT */
extern bool a52_p446l_is_armed(void);
extern void a52_p446l_note(struct dsi_ctrl_hw *,u64,u64,u32,u32,u32,u32,const u8 *);
static void p446l_note_cmd(struct dsi_ctrl *c,
    const struct dsi_ctrl_cmd_dma_info *cmd,
    const struct mipi_dsi_msg *msg,u32 flags)
{
    struct msm_gem_address_space *as;
    struct iommu_domain *domain=NULL;
    phys_addr_t phys=0;
    u32 found=0;
    if (!c || !cmd || !msg || !a52_p446l_is_armed())
        return;
    as=dsi_ctrl_get_aspace(c,MSM_SMMU_DOMAIN_UNSECURE);
    if (as && as->mmu && as->mmu->dev)
        domain=iommu_get_domain_for_dev(as->mmu->dev);
    if (domain) {
        found=1;
        phys=iommu_iova_to_phys(domain,cmd->offset);
    }
    a52_p446l_note(&c->hw,cmd->offset,(u64)phys,
        cmd->length,flags,(u32)msg->flags,found,c->vaddr);
}
'''
        s=one(s,"#include <linux/of_device.h>\n",
              "#include <linux/of_device.h>\n#include <linux/iommu.h>\n#include \"msm_gem.h\"\n",
              "iommu and gem includes")
        s=one(s,"static void dsi_kickoff_msg_tx(",
              helper+"\nstatic void dsi_kickoff_msg_tx(","note helper")
        s=one(s,"a52_p446i_hw_pre(&dsi_ctrl->hw);",
              "p446l_note_cmd(dsi_ctrl,cmd_mem,msg,flags);\n\t\t\t\ta52_p446i_hw_pre(&dsi_ctrl->hw);",
              "pre-kickoff memory context")
        ctrl.write_text(s)
    s=hw.read_text()
    if "A52_PHASE446L_HW_TRIGGER" not in s:
        decl="/* A52_PHASE446L_HW_TRIGGER */\nextern void a52_p446l_after_trigger(struct dsi_ctrl_hw *,u32);\n"
        s=one(s,"void dsi_ctrl_hw_cmn_kickoff_command(",
              decl+"void dsi_ctrl_hw_cmn_kickoff_command(",
              "hardware declaration")
        # Three physical trigger points: memory, FIFO, deferred.
        token="DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);"
        occurrences=s.count(token)
        if occurrences!=3:
            raise RuntimeError(f"446L hardware trigger count {occurrences}, expected 3")
        conditional="if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))\n\t\t"+token
        wrapped=("if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\t"
                 +token+"\n\t\ta52_p446l_after_trigger(ctrl,flags);\n\t}")
        if s.count(conditional)!=2:
            raise RuntimeError("446L expected exactly 2 conditional trigger sites")
        s=s.replace(conditional,wrapped)
        # Deferred trigger function has no flags parameter.
        s=one(s,"void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
              "void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_p446l_after_trigger(ctrl,0x80000000U);",
              "deferred trigger")
        hw.write_text(s)

def check(root,kind):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    c=(base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")).read_text()
    p=(base/"dsi/dsi_panel.c").read_text()
    d=(base/"dsi/dsi_ctrl.c").read_text()
    h=(base/"dsi/dsi_ctrl_hw_cmn.c").read_text()
    for tok in (MARK,"L_CMD_META","L_T3_CTRL","L_T4_CTRL","L_T3_BUS","L_T4_BUS","L_T3_META","L_T4_META","L_T3_PLATFORM","L_T4_PLATFORM","L_FAST_0_50","P446L post-SW_TRIGGER"):
        if tok not in c: raise RuntimeError("446L central missing "+tok)
    if p.count("A52_PHASE446L_ARM_ON")!=1: raise RuntimeError("446L ON arm not unique")
    if d.count("A52_PHASE446L_IOVA_CONTEXT")!=1: raise RuntimeError("446L IOVA helper not unique")
    if h.count("a52_p446l_after_trigger(ctrl,")!=3:
        raise RuntimeError("446L expected 3 real HW trigger hooks")
    print(f"Phase446L {kind} PASS: first cold ON trigger twins, timestamps, CTRL+DBG+DISPCC+IOVA")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--kind",choices=("gki","tg"),required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only: patch(a.root,a.kind)
    check(a.root,a.kind)
