#!/usr/bin/env python3
from __future__ import annotations
import argparse,re
from pathlib import Path

MARK="A52_PHASE446D_BURST_PLL_PROVIDER_V2"

def die(s): raise SystemExit("Phase446d-extra: "+s)
def read(p):
    if not p.is_file(): die(f"missing {p}")
    return p.read_text(errors="replace")
def write(p,s): p.write_text(s)
def add_after_includes(s,decl,marker):
    if marker in s:return s
    inc=list(re.finditer(r"(?m)^#include[^\n]*\n",s))
    if not inc: die(marker+" includes missing")
    p=inc[-1].end()
    return s[:p]+"\n"+decl.rstrip()+"\n/* "+marker+" */\n"+s[p:]

def patch_central(s):
    if MARK+"_CENTRAL" in s:return s
    incs = ["#include <linux/kthread.h>", "#include <linux/delay.h>", "#include <linux/wait.h>"]
    m=list(re.finditer(r"(?m)^#include[^\n]*\n",s))
    if not m: die("central includes missing")
    p=m[-1].end()
    missing=[x for x in incs if x not in s]
    if missing:
        s=s[:p]+"".join(x+"\n" for x in missing)+s[p:]
    anchor="static atomic_t p446_drop = ATOMIC_INIT(0);"
    if anchor not in s: die("p446_drop anchor missing")
    inject=anchor+'''\nstatic atomic_t p446_burst = ATOMIC_INIT(0);\nstatic DECLARE_WAIT_QUEUE_HEAD(p446_burst_wq);\nstatic struct task_struct *p446_burst_task;\n/* A52_PHASE446D_BURST_PLL_PROVIDER_V2_CENTRAL */\n'''
    s=s.replace(anchor,inject,1)

    old="(void)w; a52_p446_mark(P446_EVT_PERIODIC,0,0); age=ktime_get_boottime_ns()-p446_start_ns;"
    new="(void)w; if (!atomic_read(&p446_burst)) a52_p446_mark(P446_EVT_PERIODIC,0,0); age=ktime_get_boottime_ns()-p446_start_ns;"
    if old not in s: die("periodic work anchor missing")
    s=s.replace(old,new,1)

    anchor="static int __init p446_sampler_init(void){ mod_delayed_work(system_unbound_wq,&p446_work,0); return 0; }"
    if anchor not in s: die("sampler init anchor missing")
    block='''static int p446_burst_threadfn(void *unused)\n{\n    u64 deadline;\n    (void)unused;\n    while (!kthread_should_stop()) {\n        wait_event_interruptible(p446_burst_wq,\n            kthread_should_stop() || atomic_read(&p446_burst));\n        if (kthread_should_stop())\n            break;\n        deadline = ktime_get_boottime_ns() + 120000000ULL;\n        while (!kthread_should_stop() && atomic_read(&p446_burst)) {\n            a52_p446_mark(P446_EVT_PERIODIC, 1U, 0U);\n            if (ktime_get_boottime_ns() >= deadline) {\n                atomic_set(&p446_burst, 0);\n                a52_p446_mark(0x181U, 1U, 120U);\n                break;\n            }\n            usleep_range(900, 1100);\n        }\n    }\n    return 0;\n}\nvoid a52_p446_set_burst(u32 enable)\n{\n    atomic_set(&p446_burst, !!enable);\n    a52_p446_mark(0x180U, !!enable, 0U);\n    if (enable)\n        wake_up_interruptible(&p446_burst_wq);\n}\nEXPORT_SYMBOL_GPL(a52_p446_set_burst);\nstatic int __init p446_sampler_init(void)\n{\n    p446_burst_task = kthread_run(p446_burst_threadfn, NULL, "p446-burst");\n    if (IS_ERR(p446_burst_task))\n        p446_burst_task = NULL;\n    mod_delayed_work(system_unbound_wq, &p446_work, 0);\n    return 0;\n}\n'''
    s=s.replace(anchor,block,1)
    return s

def patch_msm_drv(s):
    marker=MARK+"_DRM_BIND"
    if marker in s:return s
    s=add_after_includes(s,"extern void a52_p446_set_burst(u32 enable);",marker)
    pre="\ta52_p446_mark(3U,0U,0U);\n"
    post="\ta52_p446_mark(3U,1U,(u32)rc);\n"
    if pre not in s or post not in s: die("DRM bind marker anchors missing")
    s=s.replace(pre,pre+"\ta52_p446_set_burst(1U);\n",1)
    s=s.replace(post,"\ta52_p446_set_burst(0U);\n"+post,1)
    return s

def patch_pll_drv(s):
    marker=MARK+"_PLL_DRV"
    if marker in s:return s
    s=add_after_includes(s,"extern void a52_p446_mark(u32 event, u32 aux0, u32 aux1);",marker)
    old="\trc = mdss_pll_clock_register(pdev, pll_res);\n"
    if old not in s: die("mdss_pll_clock_register anchor missing")
    new=("\ta52_p446_mark(0x182U, (u32)pll_res->index, (u32)pll_res->pll_interface_type);\n"
         +old+
         "\ta52_p446_mark(0x183U, (u32)pll_res->index, (u32)rc);\n")
    return s.replace(old,new,1)

def patch_dsi_pll(s):
    marker=MARK+"_DSI_PLL7"
    if marker in s:return s
    s=add_after_includes(s,"extern void a52_p446_mark(u32 event, u32 aux0, u32 aux1);",marker)
    old='''\t\trc = of_clk_add_provider(pdev->dev.of_node,\n\t\t\t\tof_clk_src_onecell_get, clk_data);'''
    n=s.count(old)
    if n != 2: die(f"DSI PLL provider anchors={n}, expected 2")
    new='''\t\ta52_p446_mark(0x184U, (u32)ndx, (u32)clk_data->clk_num);\n\t\trc = of_clk_add_provider(pdev->dev.of_node,\n\t\t\t\tof_clk_src_onecell_get, clk_data);\n\t\ta52_p446_mark(0x185U, (u32)ndx, (u32)rc);'''
    return s.replace(old,new)

def patch_dispcc(s):
    marker=MARK+"_DISPCC"
    if marker in s:return s
    s=add_after_includes(s,"extern void a52_p446_mark(u32 event, u32 aux0, u32 aux1);",marker)
    old="\tclk_fabia_pll_configure(&disp_cc_pll0, regmap, &disp_cc_pll0_config);\n"
    if old in s:
        s=s.replace(old,"\ta52_p446_mark(0x186U, 0U, 0U);\n"+old+"\ta52_p446_mark(0x187U, 0U, 0U);\n",1)
    for call in [
        "\tret = qcom_cc_really_probe(pdev, &disp_cc_lagoon_desc, regmap);\n",
        "\tret = qcom_cc_probe(pdev, &disp_cc_lagoon_desc);\n",
    ]:
        if call in s:
            s=s.replace(call,"\ta52_p446_mark(0x188U, 0U, 0U);\n"+call+"\ta52_p446_mark(0x189U, (u32)ret, 0U);\n",1)
            break
    else:
        die("dispcc registration call anchor missing")
    m=re.search(r"static void\s+([A-Za-z0-9_]*sync_state)\s*\([^)]*\)\s*\{",s)
    if m:
        op=s.find("{",m.start())
        depth=0; end=None
        for i in range(op,len(s)):
            if s[i]=="{": depth+=1
            elif s[i]=="}":
                depth-=1
                if depth==0:
                    end=i; break
        if end is None: die("sync_state close missing")
        s=s[:m.end()]+"\n\ta52_p446_mark(0x18aU, 0U, 0U);"+s[m.end():end]+"\ta52_p446_mark(0x18bU, 0U, 0U);\n"+s[end:]
    return s

def pick(root,cands,required=True,basename=None):
    for rel in cands:
        p=root/rel
        if p.is_file(): return p
    if basename:
        hits=[p for p in root.rglob(basename) if "/pll/" in p.as_posix()]
        if len(hits)==1:
            return hits[0]
        if len(hits)>1:
            # Prefer the ported display tree over unrelated copies.
            ranked=[p for p in hits if "a52_display" in p.as_posix() or "techpack/display" in p.as_posix()]
            if len(ranked)==1:
                return ranked[0]
            die("ambiguous "+basename+": "+", ".join(str(p) for p in hits))
    if required: die("none of candidate paths exist: "+", ".join(cands))
    return None

def check(paths):
    c=read(paths["central"])
    for t in ("p446_burst_threadfn","a52_p446_set_burst","usleep_range(900, 1100)","120000000ULL","0x180U","0x181U"):
        if t not in c: die("burst contract missing "+t)
    m=read(paths["msm_drv"])
    if "a52_p446_set_burst(1U)" not in m or "a52_p446_set_burst(0U)" not in m: die("DRM burst bracket missing")
    p=read(paths["pll_drv"]); d=read(paths["dsi_pll"]); cc=read(paths["dispcc"])
    for t in ("0x182U","0x183U"):
        if t not in p: die("pll drv marker missing "+t)
    for t in ("0x184U","0x185U"):
        if t not in d: die("provider marker missing "+t)
    for t in ("0x188U","0x189U"):
        if t not in cc: die("dispcc marker missing "+t)
    print("Phase446d-extra: burst + PLL/provider + DISPCC markers PASS")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--kind",choices=("gki","tg"),required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if a.kind=="gki":
        central=a.root/"drivers/a52_display/msm/a52_phase445.c"
        msm=a.root/"drivers/a52_display/msm/msm_drv.c"
        pll=pick(a.root,["drivers/a52_display/pll/pll_drv.c","drivers/a52_display/msm/pll/pll_drv.c"],basename="pll_drv.c")
        dsi=pick(a.root,["drivers/a52_display/pll/dsi_pll_7nm.c","drivers/a52_display/msm/pll/dsi_pll_7nm.c"],basename="dsi_pll_7nm.c")
    else:
        central=a.root/"techpack/display/msm/a52_phase446g.c"
        msm=a.root/"techpack/display/msm/msm_drv.c"
        pll=a.root/"techpack/display/pll/pll_drv.c"
        dsi=a.root/"techpack/display/pll/dsi_pll_7nm.c"
    paths={"central":central,"msm_drv":msm,"pll_drv":pll,"dsi_pll":dsi,
           "dispcc":a.root/"drivers/clk/qcom/dispcc-lagoon.c"}
    for p in paths.values():
        if not p.is_file(): die(f"missing {p}")
    if not a.check_only:
        write(central,patch_central(read(central)))
        write(msm,patch_msm_drv(read(msm)))
        write(pll,patch_pll_drv(read(pll)))
        write(dsi,patch_dsi_pll(read(dsi)))
        write(paths["dispcc"],patch_dispcc(read(paths["dispcc"])))
    check(paths)

if __name__=="__main__":
    main()
