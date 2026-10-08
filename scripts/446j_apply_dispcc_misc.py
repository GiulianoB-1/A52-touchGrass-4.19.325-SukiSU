#!/usr/bin/env python3
"""Read-only DISP_CC_MISC_CMD trace across clock handoff and first natural F0."""
import argparse
from pathlib import Path

MARK='A52_PHASE446J_DISPCC_MISC_READONLY_V1'

def one(s,a,b,why):
    n=s.count(a)
    if n != 1: raise RuntimeError('%s: anchor count %d'%(why,n))
    return s.replace(a,b,1)

def modify_func(s,signature,edit):
    start=s.find(signature)
    if start<0: raise RuntimeError('missing '+signature)
    brace=s.find('{',start)
    if brace<0: raise RuntimeError('no brace '+signature)
    depth=0
    for i in range(brace,len(s)):
        if s[i]=='{': depth+=1
        elif s[i]=='}':
            depth-=1
            if depth==0: break
    else: raise RuntimeError('unclosed '+signature)
    body=s[start:i+1]
    replacement=edit(body)
    if replacement==body: raise RuntimeError('no changes '+signature)
    return s[:start]+replacement+s[i+1:]

def central(s,kind):
    if MARK in s: return s
    if 'A52_PHASE446I_MATCHED_PASSIVE_F0_TWIN_V1' not in s:
        raise RuntimeError('Phase446i required')
    store='a52_p445_store_section' if kind=='gki' else 'a52_p444_store_section'
    helper=r'''/* A52_PHASE446J_DISPCC_MISC_READONLY_V1
 * DISP_CC_MISC_CMD is already mapped via ctrl->disp_cc_base.
 * Do not change reset or clock-gating bits.
 */
#define P446J_RING_N 128U
struct p446j_point { u64 ns; u32 tag, misc, a, b; } __packed;
struct p446j_dump {
    u32 total, kept;
    struct p446j_point point[P446J_RING_N];
} __packed;
static struct p446j_point p446j_ring[P446J_RING_N];
static struct p446j_dump p446j_dump;
static atomic_t p446j_seq = ATOMIC_INIT(0);
static atomic_t p446j_sealed = ATOMIC_INIT(0);

void a52_p446j_misc_event(struct dsi_ctrl_hw *ctrl, u32 tag, u32 a, u32 b)
{
    struct p446j_point *p;
    u32 seq;
    if (!ctrl || ctrl->index != 0 || atomic_read(&p446j_sealed)) return;
    seq=(u32)atomic_inc_return(&p446j_seq)-1U;
    p=&p446j_ring[seq % P446J_RING_N];
    p->ns=ktime_get_ns();
    p->tag=tag;
    p->misc=ctrl->disp_cc_base ? readl_relaxed(ctrl->disp_cc_base) : ~0U;
    p->a=a;
    p->b=b;
}
EXPORT_SYMBOL_GPL(a52_p446j_misc_event);

static void p446j_save(void)
{
    u32 total, n, i, first;
    atomic_set(&p446j_sealed,1);
    total=(u32)atomic_read(&p446j_seq);
    n=min(total,P446J_RING_N);
    first=total-n;
    p446j_dump.total=total;
    p446j_dump.kept=n;
    for(i=0;i<n;i++)
        p446j_dump.point[i]=p446j_ring[(first+i)%P446J_RING_N];
    __STORE__(0x446a0001U,14U,"J_MISC_TRACE",total,n,
        &p446j_dump,8U+n*sizeof(struct p446j_point));
}

'''.replace('__STORE__',store)
    s=one(s,'static inline u32 p446i_r(void __iomem *b, u32 o)\n',helper+
          'static inline u32 p446i_r(void __iomem *b, u32 o)\n','central insertion')
    s=modify_func(s,'void a52_p446i_hw_pre(',lambda x:one(x,
        '    p446i_fill_raw(&p446i_pre,ctrl);',
        '    a52_p446j_misc_event(ctrl,0x4a01U,0,0);\n    p446i_fill_raw(&p446i_pre,ctrl);','F0 pre'))
    def post(x):
        x=one(x,'    p446i_fill_raw(&p446i_post,ctrl);',
             '    a52_p446j_misc_event(ctrl,0x4a02U,0,0);\n    p446i_fill_raw(&p446i_post,ctrl);','F0 post')
        return one(x,'    atomic_set(&p446i_target,0);',
                   '    p446j_save();\n    atomic_set(&p446i_target,0);','trace save')
    return modify_func(s,'void a52_p446i_hw_post(',post)

def display(s):
    if MARK in s: return s
    decl='''/* A52_PHASE446J_DISPCC_MISC_READONLY_V1 */
extern void a52_p446j_misc_event(struct dsi_ctrl_hw *,u32,u32,u32);
static void a52_p446j_point(struct dsi_display *d, u32 tag, u32 a, u32 b)
{
    if (!d || !d->ctrl || !d->ctrl[0].ctrl) return;
    a52_p446j_misc_event(&d->ctrl[0].ctrl->hw,tag,a,b);
}

'''
    s=one(s,'int dsi_pre_clkoff_cb(void *priv,',decl+
          'int dsi_pre_clkoff_cb(void *priv,','display helper')
    def at_return(body,line):
        idx=body.rfind('\treturn rc;')
        if idx<0: raise RuntimeError('no final rc return')
        return body[:idx]+line+body[idx:]
    def pre(body):
        body=one(body,'\tstruct dsi_display *display = priv;',
            '\tstruct dsi_display *display = priv;\n'
            '\ta52_p446j_point(display,0x4a10U,(u32)clk,(u32)l_type);',
            'clkoff entry')
        return at_return(body,'\ta52_p446j_point(display,0x4a11U,(u32)clk,(u32)l_type);\n')
    s=modify_func(s,'int dsi_pre_clkoff_cb(void *priv,',pre)
    def post(body):
        body=one(body,'\tstruct dsi_display *display = priv;',
            '\tstruct dsi_display *display = priv;\n'
            '\ta52_p446j_point(display,0x4a20U,(u32)clk,(u32)l_type);',
            'clkon entry')
        fifo='\t\t\tdsi_display_toggle_resync_fifo(display);'
        body=one(body,fifo,fifo+
            '\n\t\ta52_p446j_point(display,0x4a22U,(u32)clk,(u32)l_type);',
            'fifo decision')
        return at_return(body,
            '\ta52_p446j_point(display,0x4a21U,(u32)clk,(u32)l_type);\n')
    s=modify_func(s,'int dsi_post_clkon_cb(void *priv,',post)
    def splash(body):
        body=one(body,'\tint rc = 0;',
            '\tint rc = 0;\n'
            '\ta52_p446j_point(display,0x4a30U,(u32)display->is_cont_splash_enabled,0);',
            'cleanup entry')
        return at_return(body,
            '\ta52_p446j_point(display,0x4a31U,(u32)display->is_cont_splash_enabled,(u32)rc);\n')
    return modify_func(s,'int dsi_display_splash_res_cleanup(struct  dsi_display *display)',splash)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--kind',choices=('gki','tg'),required=True)
    ap.add_argument('--check-only',action='store_true')
    a=ap.parse_args()
    path=a.root/('drivers/a52_display/msm' if a.kind=='gki' else 'techpack/display/msm')
    c=path/('a52_phase445.c' if a.kind=='gki' else 'a52_phase444.c')
    d=path/'dsi/dsi_display.c'
    if not a.check_only:
        c.write_text(central(c.read_text(),a.kind))
        d.write_text(display(d.read_text()))
    for name,file in [('recorder',c),('display',d)]:
        if MARK not in file.read_text(): raise RuntimeError(name+' marker missing')
    for token in ('J_MISC_TRACE','0x4a01U','0x4a02U'):
        if token not in c.read_text(): raise RuntimeError('recorder '+token)
    for token in ('0x4a10U','0x4a11U','0x4a20U','0x4a21U',
                  '0x4a22U','0x4a30U','0x4a31U'):
        if token not in d.read_text(): raise RuntimeError('display '+token)
    print('Phase446j',a.kind,'DISP_CC_MISC_CMD read-only trace PASS')

if __name__=='__main__': main()
