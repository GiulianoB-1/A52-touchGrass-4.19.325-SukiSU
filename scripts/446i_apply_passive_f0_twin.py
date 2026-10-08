#!/usr/bin/env python3
from __future__ import annotations

import argparse, re
from pathlib import Path

MARK = "A52_PHASE446I_MATCHED_PASSIVE_F0_TWIN_V1"

def die(msg: str) -> None:
    raise SystemExit("Phase446i: " + msg)

def one(s: str, old: str, new: str, what: str) -> str:
    n=s.count(old)
    if n != 1:
        die(f"{what}: expected 1 anchor, found {n}")
    return s.replace(old,new,1)

def msm_dir(root: Path, kind: str) -> Path:
    p=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    if not p.is_dir(): die(f'missing {p}')
    return p

def central_path(root: Path, kind: str) -> Path:
    candidates = []
    if kind == "gki":
        candidates += [
            root/"drivers/a52_display/msm/a52_phase444.c",
            root/"drivers/a52_secure/a52_phase444.c",
        ]
    else:
        candidates += [
            root/"techpack/display/msm/a52_phase444.c",
            root/"drivers/a52_display/msm/a52_phase444.c",
        ]
    for p in candidates:
        if p.is_file() and "A52_PHASE444_DEEP_PASSIVE_COMPARE_V1" in p.read_text(errors="replace"):
            return p
    for base in (root/"drivers", root/"techpack"):
        if not base.exists():
            continue
        for p in base.rglob("*.c"):
            try:
                t=p.read_text(errors="replace")
            except OSError:
                continue
            if "A52_PHASE444_DEEP_PASSIVE_COMPARE_V1" in t and "a52_p444_store_section" in t:
                return p
    die("Phase444 central C file not found by marker")

def parse_kona_selectors(sde: str) -> list[int]:
    m=re.search(r"\bu32\s+dsi_dbg_bus_kona\s*\[\]\s*=\s*\{(.*?)\};",sde,re.S)
    if not m: die('dsi_dbg_bus_kona table missing')
    vals=[int(x,16) for x in re.findall(r"0x[0-9a-fA-F]+",m.group(1))]
    if len(vals) < 100 or len(vals) > 256:
        die(f'unexpected Kona selector count {len(vals)}')
    return vals

def patch_central(s: str, selectors: list[int]) -> str:
    if MARK in s: return s

    anchor='static u32 p444_saved_dbg;\n'
    if anchor not in s: die('Phase444 central saved-dbg anchor missing')
    sel=', '.join(f'0x{x:04x}U' for x in selectors)
    inject=f'''static u32 p444_saved_dbg;

/* {MARK}
 * Matched GKI/TG F0 environment capture. Hot-path code is MMIO-only.
 */
#define P446I_REFGEN_PHYS 0x088e7000ULL
#define P446I_REFGEN_BYTES 0x100U
#define P446I_DISPCC_PHYS 0x0af00000ULL
#define P446I_DISPCC_BYTES 0x3000U
#define P446I_MDP_PHYS 0x0ae00000ULL
#define P446I_MDP_BYTES 0x85000U
#define P446I_TYPE_RAW 10U
#define P446I_TYPE_BUS 11U
#define P446I_TYPE_SW 12U

struct p446i_bus_pair {{ u32 selector, value; }} __packed;
struct p446i_sw {{
    u32 flags, msg_flags, panel_mode, power_state;
    u32 host_initialized, controller_state, cmd_engine_state, vid_engine_state;
    u32 byte_rate, pix_rate, byte_intf_rate, esc_rate;
}} __packed;
struct p446i_raw {{
    u64 ns;
    u32 dsi_status, lane_status, clk_status, int_ctrl, dbg_ctl;
    u32 refgen[64];
    u32 rcg[30];
    u32 pll0[9];
    u32 mdp[12];
    u32 compact[8];
}} __packed;

static void __iomem *p446i_refgen, *p446i_dispcc, *p446i_mdp;
static atomic_t p446i_seen = ATOMIC_INIT(0);
static atomic_t p446i_target = ATOMIC_INIT(0);
static struct p446i_sw p446i_sw;
static struct p446i_raw p446i_pre, p446i_post;
static struct p446i_bus_pair p446i_bus_pre[{len(selectors)}];
static struct p446i_bus_pair p446i_bus_post[{len(selectors)}];
static const u32 p446i_sel[{len(selectors)}] = {{ {sel} }};
static const u32 p446i_compact_sel[8] = {{
    0x0151U,0x0171U,0x0181U,0x0191U,0x01a1U,0x01b1U,0x01c1U,0x0211U
}};
static const u32 p446i_rcg_base[6] = {{
    0x107cU,0x10c4U,0x1064U,0x10e0U,0x10acU,0x115cU
}};
static const u32 p446i_pll_off[9] = {{
    0x00U,0x04U,0x08U,0x0cU,0x10U,0x14U,0x18U,0x1cU,0x2cU
}};
'''
    s=one(s,anchor,inject,'Phase444 central Phase446i globals')

    fn_anchor='static void p444_hot_one(struct dsi_ctrl_hw *ctrl, u32 point, u64 base)\n'
    if fn_anchor not in s: die('p444_hot_one anchor missing')
    helper=r'''static inline u32 p446i_r(void __iomem *b, u32 o)
{
    return b ? readl_relaxed((u8 __iomem *)b + o) : ~0U;
}

static bool p446i_exact_f0(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg)
{
    const u8 *p;
    if (!ctrl || !msg || ctrl->cell_index != 0 || msg->type != 0x29 ||
        msg->tx_len != 3 || !msg->tx_buf) return false;
    p=msg->tx_buf;
    return p[0]==0xf0 && p[1]==0x5a && p[2]==0x5a;
}

void a52_p446i_sw_entry(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags)
{
    struct p446i_sw *m=&p446i_sw;
    if (!p446i_exact_f0(ctrl,msg)) return;
    if (atomic_cmpxchg(&p446i_seen,0,1) != 0) return;
    memset(m,0,sizeof(*m));
    m->flags=flags; m->msg_flags=(u32)msg->flags;
    m->panel_mode=(u32)ctrl->host_config.panel_mode;
    m->power_state=(u32)ctrl->current_state.power_state;
    m->host_initialized=(u32)ctrl->current_state.host_initialized;
    m->controller_state=(u32)ctrl->current_state.controller_state;
    m->cmd_engine_state=(u32)ctrl->current_state.cmd_engine_state;
    m->vid_engine_state=(u32)ctrl->current_state.vid_engine_state;
    m->byte_rate=ctrl->clk_freq.byte_clk_rate;
    m->pix_rate=ctrl->clk_freq.pix_clk_rate;
    m->byte_intf_rate=ctrl->clk_freq.byte_intf_clk_rate;
    m->esc_rate=ctrl->clk_freq.esc_clk_rate;
    atomic_set(&p446i_target,1);
    a52_p444_store_section(P444_STAGE_PRE_TRIGGER,P446I_TYPE_SW,
        "I_SW_ENTRY",flags,(u32)msg->flags,m,sizeof(*m));
}
EXPORT_SYMBOL_GPL(a52_p446i_sw_entry);

static void p446i_fill_raw(struct p446i_raw *r, struct dsi_ctrl_hw *ctrl)
{
    u32 i,j,saved;
    static const u32 mdp_off[12] = {
        0x1008U,0x100cU,0x1010U,0x1014U,
        0x6b800U,0x6b8a8U,0x6b8acU,0x6b8b0U,
        0x71014U,0x71028U,0x7102cU,0x71030U
    };
    memset(r,0,sizeof(*r)); r->ns=ktime_get_ns();
    if (ctrl && ctrl->base) {
        r->dsi_status=DSI_R32(ctrl,DSI_STATUS);
        r->lane_status=DSI_R32(ctrl,DSI_LANE_STATUS);
        r->clk_status=DSI_R32(ctrl,DSI_CLK_STATUS);
        r->int_ctrl=DSI_R32(ctrl,DSI_INT_CTRL);
        saved=DSI_R32(ctrl,DSI_DEBUG_BUS_CTL); r->dbg_ctl=saved;
        for(i=0;i<ARRAY_SIZE(p446i_compact_sel);i++){
            DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,p446i_compact_sel[i]); wmb();
            r->compact[i]=DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS);
        }
        DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,saved); wmb();
    }
    for(i=0;i<64;i++) r->refgen[i]=p446i_r(p446i_refgen,i*4U);
    for(i=0;i<ARRAY_SIZE(p446i_rcg_base);i++)
        for(j=0;j<5;j++) r->rcg[i*5+j]=p446i_r(p446i_dispcc,p446i_rcg_base[i]+j*4U);
    for(i=0;i<ARRAY_SIZE(p446i_pll_off);i++) r->pll0[i]=p446i_r(p446i_dispcc,p446i_pll_off[i]);
    for(i=0;i<ARRAY_SIZE(mdp_off);i++) r->mdp[i]=p446i_r(p446i_mdp,mdp_off[i]);
}

static void p446i_sweep(struct p446i_bus_pair *out, struct dsi_ctrl_hw *ctrl)
{
    u32 i,saved;
    if (!ctrl || !ctrl->base) return;
    saved=DSI_R32(ctrl,DSI_DEBUG_BUS_CTL);
    for(i=0;i<ARRAY_SIZE(p446i_sel);i++){
        out[i].selector=p446i_sel[i];
        DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,p446i_sel[i]); wmb();
        out[i].value=DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS);
    }
    DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,saved); wmb();
}

void a52_p446i_hw_pre(struct dsi_ctrl_hw *ctrl)
{
    if (!atomic_read(&p446i_target)) return;
    p446i_fill_raw(&p446i_pre,ctrl);
    p446i_sweep(p446i_bus_pre,ctrl);
}
EXPORT_SYMBOL_GPL(a52_p446i_hw_pre);

void a52_p446i_hw_post(struct dsi_ctrl_hw *ctrl)
{
    if (!atomic_read(&p446i_target)) return;
    p446i_fill_raw(&p446i_post,ctrl);
    p446i_sweep(p446i_bus_post,ctrl);
    a52_p444_store_section(P444_STAGE_PRE_TRIGGER,P446I_TYPE_RAW,"I_RAW_PRE",
        (u32)p446i_pre.refgen[0x80/4],p446i_pre.compact[1],&p446i_pre,sizeof(p446i_pre));
    a52_p444_store_section(P444_STAGE_HOT,P446I_TYPE_RAW,"I_RAW_POST",
        (u32)p446i_post.refgen[0x80/4],p446i_post.compact[1],&p446i_post,sizeof(p446i_post));
    a52_p444_store_section(P444_STAGE_PRE_TRIGGER,P446I_TYPE_BUS,"I_BUS_PRE",
        ARRAY_SIZE(p446i_sel),p446i_pre.dbg_ctl,p446i_bus_pre,sizeof(p446i_bus_pre));
    a52_p444_store_section(P444_STAGE_HOT,P446I_TYPE_BUS,"I_BUS_POST",
        ARRAY_SIZE(p446i_sel),p446i_post.dbg_ctl,p446i_bus_post,sizeof(p446i_bus_post));
    atomic_set(&p446i_target,0);
}
EXPORT_SYMBOL_GPL(a52_p446i_hw_post);

'''
    s=one(s,fn_anchor,helper+fn_anchor,'Phase446i helpers')

    init_anchor='    p444_phy=ioremap(0x0ae94000ULL,0x1000U);\n'
    init_new=(init_anchor+
        "    p446i_refgen=ioremap(P446I_REFGEN_PHYS,P446I_REFGEN_BYTES);\n"
        "    p446i_dispcc=ioremap(P446I_DISPCC_PHYS,P446I_DISPCC_BYTES);\n"
        "    p446i_mdp=ioremap(P446I_MDP_PHYS,P446I_MDP_BYTES);\n")
    s=one(s,init_anchor,init_new,'Phase446i ioremap')
    return s

def patch_ctrl(s: str) -> str:
    if MARK in s: return s
    sig='static int dsi_message_tx(struct dsi_ctrl *dsi_ctrl,'
    p=s.find(sig)
    if p<0: die('dsi_message_tx missing')
    decl=('/* '+MARK+' */\n'
          "extern void a52_p446i_sw_entry(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags);\n"
          "extern void a52_p446i_hw_pre(struct dsi_ctrl_hw *ctrl);\n"
          "extern void a52_p446i_hw_post(struct dsi_ctrl_hw *ctrl);\n")
    s=s[:p]+decl+s[p:]

    # Both current trees have exactly one natural-F0 arm call after flag validation.
    arm=None
    for cand in ('\ta52_p445_try_arm(dsi_ctrl, msg, *flags);\n','\ta52_p444_try_arm(dsi_ctrl, msg, *flags);\n'):
        if cand in s: arm=cand; break
    if not arm: die('F0 arm anchor missing')
    s=one(s,arm,arm+'\ta52_p446i_sw_entry(dsi_ctrl,msg,*flags);\n','F0 sw-entry hook')

    # Patch the last memory-fetch kickoff in dsi_kickoff_msg_tx: this is the
    # non-deferred natural command trigger in both current GKI and TG trees.
    fs=s.find('static void dsi_kickoff_msg_tx(')
    fe=s.find('\nstatic ',fs+20)
    if fs<0 or fe<0: die('dsi_kickoff_msg_tx bounds missing')
    fn=s[fs:fe]
    call='''dsi_hw_ops.kickoff_command(
\t\t\t\t\t\t&dsi_ctrl->hw,
\t\t\t\t\t\tcmd_mem,
\t\t\t\t\t\thw_flags);'''
    k=fn.rfind(call)
    if k<0: die('natural memory kickoff call missing')
    repl='''a52_p446i_hw_pre(&dsi_ctrl->hw);
\t\t\t\tdsi_hw_ops.kickoff_command(
\t\t\t\t\t\t&dsi_ctrl->hw,
\t\t\t\t\t\tcmd_mem,
\t\t\t\t\t\thw_flags);
\t\t\t\ta52_p446i_hw_post(&dsi_ctrl->hw);'''
    fn=fn[:k]+repl+fn[k+len(call):]
    return s[:fs]+fn+s[fe:]

def check(root: Path, kind: str) -> None:
    msm=msm_dir(root,kind)
    cp=central_path(root,kind)
    c=cp.read_text(errors='replace')
    d=(msm/'dsi/dsi_ctrl.c').read_text(errors='replace')
    for tok in (MARK,'I_SW_ENTRY','I_RAW_PRE','I_RAW_POST','I_BUS_PRE','I_BUS_POST',
                'P446I_REFGEN_PHYS 0x088e7000ULL','a52_p446i_hw_pre','a52_p446i_hw_post'):
        if tok not in c+d: die('contract missing: '+tok)
    if d.count('a52_p446i_sw_entry(dsi_ctrl,msg,*flags);') != 1:
        die('sw-entry hook count wrong')
    if d.count('a52_p446i_hw_pre(&dsi_ctrl->hw);') != 1 or d.count('a52_p446i_hw_post(&dsi_ctrl->hw);') != 1:
        die('HW hook count wrong')

    ref=(root/'drivers/regulator/refgen.c')
    if not ref.is_file(): die('refgen.c missing')
    rs=ref.read_text(errors='replace')
    if 'qcom,refgen-kona-regulator' not in rs or 'REFGEN_REG_PWRDWN_CTRL5' not in rs:
        die('Kona refgen contract missing')
    if kind=='gki':
        # Current port is intentionally Kona-only. Fail if generic write path appears.
        if 'REFGEN_REG_BIAS_EN' in rs or 'REFGEN_REG_BG_CTRL' in rs:
            die('GKI refgen unexpectedly contains generic +0x08/+0x14 path')
        if 'vreg->rdesc.ops = &refgen_kona_ops;' not in rs:
            die('GKI refgen is not hard-wired to Kona ops')
    print(f'Phase446i {kind}: matched passive F0 twin PASS')

def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--kind',choices=('gki','tg'),required=True)
    ap.add_argument('--check-only',action='store_true')
    a=ap.parse_args(); root=a.root.resolve(); msm=msm_dir(root,a.kind)
    if not a.check_only:
        sde=(msm/'sde_dbg.c').read_text(errors='replace')
        selectors=parse_kona_selectors(sde)
        cp=central_path(root,a.kind); cp.write_text(patch_central(cp.read_text(errors='replace'),selectors))
        dp=msm/'dsi/dsi_ctrl.c'; dp.write_text(patch_ctrl(dp.read_text(errors='replace')))
    check(root,a.kind)

if __name__=="__main__": main()
