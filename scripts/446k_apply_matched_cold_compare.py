#!/usr/bin/env python3
"""Matched GKI/TG Phase446K: cold prepare and first ON packet CTRL/PHY traces.
Passive reads; no changes to panel commands, PHY programming or recovery.
Existing Phase446i Kona debug sweep saves/restores its selector.
"""
import argparse
from pathlib import Path

MARK="A52_PHASE446K_COLD_PAIR_V1"
def one(s,a,b,tag):
    if s.count(a)!=1: raise RuntimeError("446K %s anchor=%d"%(tag,s.count(a)))
    return s.replace(a,b,1)

def bounds(s,needle):
    p=s.find(needle)
    if p<0: raise RuntimeError("446K missing "+needle)
    start=s.find("{",p); depth=0
    for i in range(start,len(s)):
        if s[i]=="{": depth+=1
        elif s[i]=="}":
            depth-=1
            if not depth: return p,i+1
    raise RuntimeError("446K unclosed "+needle)

HELPER=r'''
/* A52_PHASE446K_COLD_PAIR_V1: full CTRL 0..3fc / PHY 0..ffc */
#define KCTRL 256U
#define KPHY 1024U
struct p446k_capture {
    u64 ns;
    u32 tag,misc;
    u32 ctrl[KCTRL],phy[KPHY];
    struct p446i_raw aux;
    struct p446i_bus_pair bus[ARRAY_SIZE(p446i_sel)];
} __packed;
static struct p446k_capture p446k_pre,p446k_on;
static void __iomem *p446k_phy;
static atomic_t p446k_pre_once=ATOMIC_INIT(0),p446k_on_once=ATOMIC_INIT(0);
static int __init p446k_map_init(void)
{
    p446k_phy=ioremap(0x0ae94000ULL,0x1000U);
    if (!p446k_phy) pr_warn("P446K PHY MMIO mapping failed\n");
    return 0;
}
late_initcall(p446k_map_init);
static u32 p446k_hash(const u32 *p,u32 n)
{
    u32 i,h=2166136261U;
    for(i=0;i<n;i++) h=(h^p[i])*16777619U;
    return h;
}
void a52_p446k_capture(struct dsi_ctrl_hw *ctrl,u32 tag)
{
    u32 i,hc,hp;
    struct p446k_capture *s;
    if (!ctrl || ctrl->index!=0 || !ctrl->base) return;
    if (tag==1U) {
        if (atomic_cmpxchg(&p446k_pre_once,0,1)!=0) return;
        s=&p446k_pre;
    } else if(tag==2U) {
        if (atomic_read(&p446k_pre_once)!=1 ||
            atomic_cmpxchg(&p446k_on_once,0,1)!=0) return;
        s=&p446k_on;
    } else return;
    s->ns=ktime_get_ns();
    s->tag=tag;
    s->misc=ctrl->disp_cc_base ? readl_relaxed(ctrl->disp_cc_base) : ~0U;
    for(i=0;i<KCTRL;i++) s->ctrl[i]=DSI_R32(ctrl,i*4U);
    for(i=0;i<KPHY;i++) s->phy[i]=p446i_r(p446k_phy,i*4U);
    p446i_fill_raw(&s->aux,ctrl);
    p446i_sweep(s->bus,ctrl);
    hc=p446k_hash(s->ctrl,KCTRL);
    hp=p446k_hash(s->phy,KPHY);
    pr_warn("P446K tag=%u ns=%llu misc=%08x refgen80=%08x clk=%08x lane=%08x db171=%08x CTRL=%08x PHY=%08x\n",
       tag,(unsigned long long)s->ns,s->misc,s->aux.refgen[32],
       s->aux.clk_status,s->aux.lane_status,s->aux.compact[1],hc,hp);
    if (tag==1U) {
        __STORE__(0x446b0011U,15U,"K_PRE_CTRL",tag,hc,s->ctrl,sizeof(s->ctrl));
        __STORE__(0x446b0012U,15U,"K_PRE_PHY",tag,hp,s->phy,sizeof(s->phy));
        __STORE__(0x446b0013U,15U,"K_PRE_AUX",tag,s->misc,&s->aux,sizeof(s->aux));
        __STORE__(0x446b0014U,15U,"K_PRE_BUS",tag,ARRAY_SIZE(p446i_sel),s->bus,sizeof(s->bus));
    } else {
        __STORE__(0x446b0021U,15U,"K_ON_CTRL",tag,hc,s->ctrl,sizeof(s->ctrl));
        __STORE__(0x446b0022U,15U,"K_ON_PHY",tag,hp,s->phy,sizeof(s->phy));
        __STORE__(0x446b0023U,15U,"K_ON_AUX",tag,s->misc,&s->aux,sizeof(s->aux));
        __STORE__(0x446b0024U,15U,"K_ON_BUS",tag,ARRAY_SIZE(p446i_sel),s->bus,sizeof(s->bus));
    }
}
EXPORT_SYMBOL_GPL(a52_p446k_capture);

'''
def patch(root,kind):
    d=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    c=d/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")
    dis=d/"dsi/dsi_display.c"
    pan=d/"dsi/dsi_panel.c"
    s=c.read_text()
    if MARK not in s:
        if "A52_PHASE446I_MATCHED_PASSIVE_F0_TWIN_V1" not in s: raise RuntimeError("446K needs Phase446i")
        backend="a52_p445_store_section" if kind=="gki" else "a52_p444_store_section"
        s=one(s,"void a52_p446i_hw_pre(struct dsi_ctrl_hw *ctrl)\n",
            HELPER.replace("__STORE__",backend)+"void a52_p446i_hw_pre(struct dsi_ctrl_hw *ctrl)\n","central")
        c.write_text(s)
    s=dis.read_text()
    if "A52_PHASE446K_PREPARED" not in s:
        start,end=bounds(s,"int dsi_display_prepare(struct dsi_display *display)")
        fn=s[start:end]
        fn=one(fn,"\nerror:\n","\nerror:\n"
           "\t/* A52_PHASE446K_PREPARED: matched end-of-cold-prepare. */\n"
           "\tif (!rc && !display->is_cont_splash_enabled &&\n"
           "\t    display->ctrl[0].ctrl) {\n"
           "\t\ta52_p446k_capture(&display->ctrl[0].ctrl->hw,1U);\n"
           "\t}\n","prepare success")
        dis.write_text(s[:start]+"extern void a52_p446k_capture(struct dsi_ctrl_hw *,u32);\n"+fn+s[end:])
    s=pan.read_text()
    if "A52_PHASE446K_ON" not in s:
        pre=('''        /* A52_PHASE446K_ON */
#if defined(CONFIG_DISPLAY_SAMSUNG)
        if (type==DSI_CMD_SET_ON && !display->is_cont_splash_enabled &&
            display->ctrl[0].ctrl)
            a52_p446k_capture(&display->ctrl[0].ctrl->hw,2U);
#endif
''')
        s=one(s,"\t\tlen = ops->transfer(panel->host, &cmds->msg);\n",
              pre+"\t\tlen = ops->transfer(panel->host, &cmds->msg);\n","ON transfer")
        anchor=s.find("int dsi_panel_tx_cmd_set(")
        if anchor<0: raise RuntimeError("446K panel declaration missing")
        pan.write_text(s[:anchor]+"extern void a52_p446k_capture(struct dsi_ctrl_hw *,u32);\n"+s[anchor:])
def audit(root,kind):
    d=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    c=(d/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")).read_text()
    dis=(d/"dsi/dsi_display.c").read_text()
    p=(d/"dsi/dsi_panel.c").read_text()
    for tok in (MARK,"K_PRE_PHY","K_ON_PHY","K_PRE_CTRL","K_ON_CTRL",
                "p446i_sweep(s->bus,ctrl)"):
        if tok not in c: raise RuntimeError("446K central missing "+tok)
    if dis.count("A52_PHASE446K_PREPARED")!=1 or p.count("A52_PHASE446K_ON")!=1:
        raise RuntimeError("446K cold/ON hooks absent")
    print("Phase446K",kind,"PASS matched CTRL+PHY+refgen+DISPCC+debug-bus capture")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--kind",required=True,choices=["gki","tg"])
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only: patch(a.root,a.kind)
    audit(a.root,a.kind)
if __name__=="__main__":main()
