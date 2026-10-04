#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE439_DSI_AUTOPSY_V1'
CTRL=Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HWC=Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
DISP=Path('drivers/a52_display/msm/dsi/dsi_display.c')
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
SDE=Path('drivers/a52_display/msm/sde/sde_dbg.c')

def die(m): raise SystemExit('Phase439: '+m)
def one(s,o,n,l):
    c=s.count(o)
    if c!=1: die(f'{l}: expected 1 anchor, found {c}')
    return s.replace(o,n,1)

COMMON_HWC=r'''
/* A52_PHASE439_DSI_AUTOPSY_V1
 * Exact-F0 DSI autopsy. Hot path stores compact snapshots only; no printk or
 * persistent-recorder write occurs until the normal 200 ms timeout path.
 * The debug selector is held at 0x0171 only across the 0..50 us measurement
 * window and restored afterwards. Physical PHY reads are from the already
 * powered v3 PHY common page on A52/SM7225.
 */
#define A52_P439_PHY_PHYS 0x0ae94000ULL
#define A52_P439_PHY_SIZE 0x1000U
#define A52_P439_MAX 5U
struct a52_p439_sample {
	u64 ns;
	u32 point;
	u32 status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status, lane_ctrl;
	u32 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl;
	u32 ack_err, timeout, phy_err, axi2ahb, dbg171;
	u32 pll_status_one, phy_pll_ctrl, phy_ctrl0, phy_rbuf, phy_clk_cfg1;
	u32 phy_lane0, phy_lane1;
};
static struct a52_p439_sample a52_p439_samples[A52_P439_MAX];
static atomic_t a52_p439_count=ATOMIC_INIT(0);
static atomic_t a52_p439_active=ATOMIC_INIT(0);
static void __iomem *a52_p439_phy;
static u32 a52_p439_saved_dbg;

static int __init a52_p439_map_init(void)
{
	a52_p439_phy=ioremap(A52_P439_PHY_PHYS,A52_P439_PHY_SIZE);
	return 0;
}
subsys_initcall(a52_p439_map_init);

static __always_inline u32 a52_p439_pr(u32 off)
{
	return a52_p439_phy ? readl_relaxed((u8 __iomem *)a52_p439_phy+off) : ~0U;
}
static void a52_p439_store(struct dsi_ctrl_hw *ctrl,u32 point)
{
	struct a52_p439_sample *s; unsigned int i;
	if(atomic_read(&a52_p439_active)!=1 || !ctrl || !ctrl->base) return;
	i=(unsigned int)atomic_inc_return(&a52_p439_count)-1U;
	if(i>=A52_P439_MAX) return;
	s=&a52_p439_samples[i]; memset(s,0,sizeof(*s));
	s->ns=ktime_get_ns(); s->point=point;
	s->status=DSI_R32(ctrl,DSI_STATUS); s->fifo=DSI_R32(ctrl,DSI_FIFO_STATUS);
	s->clk_ctrl=DSI_R32(ctrl,DSI_CLK_CTRL); s->clk_status=DSI_R32(ctrl,DSI_CLK_STATUS);
	s->int_ctrl=DSI_R32(ctrl,DSI_INT_CTRL); s->lane_status=DSI_R32(ctrl,DSI_LANE_STATUS);
	s->lane_ctrl=DSI_R32(ctrl,DSI_LANE_CTRL);
	s->dma_ctrl=DSI_R32(ctrl,DSI_COMMAND_MODE_DMA_CTRL); s->dma_offset=DSI_R32(ctrl,DSI_DMA_CMD_OFFSET);
	s->dma_length=DSI_R32(ctrl,DSI_DMA_CMD_LENGTH); s->sw_trigger=DSI_R32(ctrl,DSI_CMD_MODE_DMA_SW_TRIGGER);
	s->trig_ctrl=DSI_R32(ctrl,DSI_TRIG_CTRL); s->ack_err=DSI_R32(ctrl,DSI_ACK_ERR_STATUS);
	s->timeout=DSI_R32(ctrl,DSI_TIMEOUT_STATUS); s->phy_err=DSI_R32(ctrl,DSI_DLN0_PHY_ERR);
	s->axi2ahb=DSI_R32(ctrl,DSI_AXI2AHB_CTRL); s->dbg171=DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS);
	s->phy_clk_cfg1=a52_p439_pr(0x414); s->phy_rbuf=a52_p439_pr(0x41c);
	s->phy_ctrl0=a52_p439_pr(0x424); s->phy_pll_ctrl=a52_p439_pr(0x438);
	s->phy_lane0=a52_p439_pr(0x4f4); s->phy_lane1=a52_p439_pr(0x4f8);
	s->pll_status_one=a52_p439_pr(0xba0);
}
static __always_inline void a52_p439_wait_until(u64 start,u64 delta)
{
	while((ktime_get_ns()-start)<delta) cpu_relax();
}
static void a52_p439_begin(struct dsi_ctrl_hw *ctrl)
{
	if(!a52_p421_target_active() || !ctrl || !ctrl->base) return;
	if(atomic_cmpxchg(&a52_p439_active,0,1)!=0) return;
	atomic_set(&a52_p439_count,0);
	a52_p439_saved_dbg=DSI_R32(ctrl,DSI_DEBUG_BUS_CTL);
	DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,0x0171); wmb();
	a52_p439_store(ctrl,0U);
}
static void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)
{
	u64 start;
	if(atomic_read(&a52_p439_active)!=1 || !ctrl || !ctrl->base) return;
	start=ktime_get_ns();
	a52_p439_wait_until(start,15000ULL); a52_p439_store(ctrl,15U);
	a52_p439_wait_until(start,35000ULL); a52_p439_store(ctrl,35U);
	a52_p439_wait_until(start,50000ULL); a52_p439_store(ctrl,50U);
	DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,a52_p439_saved_dbg); wmb();
}
void a52_p439_timeout_snapshot(struct dsi_ctrl_hw *ctrl)
{
	if(atomic_read(&a52_p439_active)!=1) return;
	a52_p439_store(ctrl,200U);
}
void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl)
{
	unsigned int i,n=(unsigned int)atomic_read(&a52_p439_count); u64 base=0;
	if(n>A52_P439_MAX) n=A52_P439_MAX; if(n) base=a52_p439_samples[0].ns;
	for(i=0;i<n;i++) { const struct a52_p439_sample *s=&a52_p439_samples[i];
		a52_ackfr_record("P439 S i=%u p=%u us=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x lc=%x db=%x",
			i,s->point,(unsigned long long)((s->ns-base)/1000ULL),s->status,s->fifo,s->clk_ctrl,s->clk_status,s->int_ctrl,s->lane_status,s->lane_ctrl,s->dbg171);
		a52_ackfr_record("P439 D i=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x",
			i,s->dma_ctrl,s->dma_offset,s->dma_length,s->sw_trigger,s->trig_ctrl,s->ack_err,s->timeout,s->phy_err,s->axi2ahb);
		a52_ackfr_record("P439 P i=%u ps=%x pc=%x c0=%x rb=%x cf=%x l0=%x l1=%x",
			i,s->pll_status_one,s->phy_pll_ctrl,s->phy_ctrl0,s->phy_rbuf,s->phy_clk_cfg1,s->phy_lane0,s->phy_lane1);
	}
	if(ctrl && ctrl->base) a52_ackfr_record("P439 T st=%x in=%x cc=%x ck=%x db=%x n=%u",
		DSI_R32(ctrl,DSI_STATUS),DSI_R32(ctrl,DSI_INT_CTRL),DSI_R32(ctrl,DSI_CLK_CTRL),DSI_R32(ctrl,DSI_CLK_STATUS),DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS),n);
}
void a52_p439_debug_sweep(struct dsi_ctrl_hw *ctrl)
{
	static const u32 q[]={0x0171,0x0181,0x0191,0x01a1,0x01e1,0x0211};
	u32 saved,i,v;
	if(!ctrl || !ctrl->base) return; saved=DSI_R32(ctrl,DSI_DEBUG_BUS_CTL);
	for(i=0;i<ARRAY_SIZE(q);i++){DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,q[i]);wmb();v=DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS);a52_ackfr_record("P439 B i=%u c=%x v=%x",i,q[i],v);}
	DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,saved);wmb();
}
#ifdef CONFIG_DEBUG_FS
#include <linux/debugfs.h>
#include <linux/seq_file.h>
static int a52_p439_dbg_show(struct seq_file *m,void *v)
{
	unsigned int i,n=(unsigned int)atomic_read(&a52_p439_count); if(n>A52_P439_MAX)n=A52_P439_MAX;
	seq_printf(m,"%s count=%u active=%d\n", "A52_PHASE439_DSI_AUTOPSY_V1",n,atomic_read(&a52_p439_active));
	for(i=0;i<n;i++){const struct a52_p439_sample*s=&a52_p439_samples[i];seq_printf(m,"i=%u p=%u ns=%llu st=%08x in=%08x ck=%08x cc=%08x db=%08x ps=%08x pc=%08x l0=%08x l1=%08x\n",i,s->point,(unsigned long long)s->ns,s->status,s->int_ctrl,s->clk_status,s->clk_ctrl,s->dbg171,s->pll_status_one,s->phy_pll_ctrl,s->phy_lane0,s->phy_lane1);} return 0;
}
static int a52_p439_dbg_open(struct inode*i,struct file*f){return single_open(f,a52_p439_dbg_show,NULL);}
static const struct file_operations a52_p439_dbg_fops={.owner=THIS_MODULE,.open=a52_p439_dbg_open,.read=seq_read,.llseek=seq_lseek,.release=single_release};
static int __init a52_p439_dbg_init(void){debugfs_create_file("a52_phase439_dsi",0444,NULL,NULL,&a52_p439_dbg_fops);return 0;} late_initcall(a52_p439_dbg_init);
#endif
'''

def patch_rec(s):
    if MARK in s:return s
    s=s.replace('return !strncmp(message, "P434 ", 5) ||','return !strncmp(message, "P439 ", 5) ||\n       !strncmp(message, "P434 ", 5) ||',1)
    s=s.replace('strncmp(fmt, "P434", 4) &&','strncmp(fmt, "P439", 4) &&\n\t    strncmp(fmt, "P434", 4) &&')
    return s+'\n/* '+MARK+': P439 autopsy records admitted to persistent recorder. */\n'

def patch_hwc(s,variant):
    if MARK in s:return s
    inc='#include <linux/init.h>\n'
    if inc not in s: die('HWC init include anchor missing')
    s=one(s,inc,inc+'#include <linux/module.h>\n','HWC module include')
    anchor='/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */\n'
    s=one(s,anchor,COMMON_HWC+'\n'+anchor,'HWC helper')
    old='''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
\t\tif (a52_p421_target_active()) {
\t\t\ta52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
\t\t\ta52_p424_snapshot(ctrl);
\t\t\ta52_p427_road_snapshot(ctrl, 0U);
\t\t}
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new='''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
\t\tif (a52_p421_target_active()) {
\t\t\ta52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
\t\t\ta52_p424_snapshot(ctrl);
\t\t\ta52_p427_road_snapshot(ctrl, 0U);
\t\t\ta52_p439_begin(ctrl);
\t\t}
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\tif (a52_p421_target_active())
\t\t\ta52_p439_post_trigger(ctrl);
'''
    s=one(s,old,new,'memory trigger')
    old2='''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new2='''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
\tif (a52_p421_target_active())
\t\ta52_p439_begin(ctrl);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\tif (a52_p421_target_active())
\t\ta52_p439_post_trigger(ctrl);
'''
    s=one(s,old2,new2,'deferred trigger')
    s += f'\nstatic const char a52_p439_variant[] __used = "{MARK}:VARIANT_{variant}";\n'
    return s

def patch_sde(s):
    if MARK in s:return s
    anchor='void sde_dbg_ctrl(const char *name, ...)\n'
    block=r'''/* A52_PHASE439_DSI_AUTOPSY_V1
 * Invoke Qualcomm's platform DSI debug-bus list after the timed experiment.
 * If this target has no registered list this is intentionally a no-op.
 */
void a52_p439_sde_dsi_full_dump(void)
{
	if (sde_dbg_base.dbgbus_dsi.entries && sde_dbg_base.dbgbus_dsi.size)
		dsi_ctrl_debug_dump(sde_dbg_base.dbgbus_dsi.entries,
				    sde_dbg_base.dbgbus_dsi.size);
}

'''
    s=one(s,anchor,block+anchor,'SDE full-dump helper')
    return s+'\n/* '+MARK+': platform DSI debug-bus export for timeout autopsy. */\n'

def patch_ctrl(s,variant):
    if MARK in s:return s
    a='extern void a52_p345_flush(struct dsi_ctrl_hw *ctrl); /* A52_PHASE345_DMA_US_FRONTIER_V1 */\n'
    b=a+'''extern void a52_p439_timeout_snapshot(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_debug_sweep(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_sde_dsi_full_dump(void);
extern void a52_p434_dump_rails(const char *tag);
'''
    s=one(s,a,b,'CTRL declarations')
    start=s.find('static __noreturn void a52_p411_timeout_panic(struct dsi_ctrl *dsi_ctrl)')
    if start<0:die('old timeout panic missing')
    brace=s.find('{',start);depth=0;end=None
    for i in range(brace,len(s)):
        if s[i]=='{':depth+=1
        elif s[i]=='}':
            depth-=1
            if depth==0:end=i+1;break
    if end is None:die('old panic close missing')
    repl=r'''static __noreturn void a52_p411_timeout_panic(struct dsi_ctrl *dsi_ctrl)
{
	a52_p439_timeout_snapshot(dsi_ctrl ? &dsi_ctrl->hw : NULL);
	a52_p439_flush_samples(dsi_ctrl ? &dsi_ctrl->hw : NULL);
	if (dsi_ctrl)
		a52_p439_debug_sweep(&dsi_ctrl->hw);
	/* Existing Phase426/P427 calls already made the one-shot SMMU sanity
	 * capture before this helper. Keep it light here and focus on the link. */
	a52_p434_dump_rails("p439");
	a52_p439_sde_dsi_full_dump();
	a52_ackfr_record("P439 PANIC ctrl=%d irq=%u q=%u", dsi_ctrl ? dsi_ctrl->cell_index : -1,
		dsi_ctrl ? (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig) : 0U,
		dsi_ctrl ? dsi_ctrl->dma_wait_queued : 0U);
	a52_ackfr_retain_timeout_snapshot();
	dump_stack();
	panic("A52P439 DSI F0 DMA_DONE timeout");
}'''
    s=s[:start]+repl+s[end:]
    if variant=='C':
        decl='\tbool a52_p421_f0 = false;\n'
        s=one(s,decl,decl+'\tstruct mipi_dsi_msg a52_p439_lpm_msg;\n','C local msg')
        anchor='''\ta52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
\tif (a52_p421_f0) {
'''
        rep='''\ta52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
\tif (a52_p421_f0) {
\t\ta52_p439_lpm_msg = *msg;
\t\ta52_p439_lpm_msg.flags |= MIPI_DSI_MSG_USE_LPM;
\t\tmsg = &a52_p439_lpm_msg;
\t\ta52_ackfr_record("P439 C force_lpm m=%x", (unsigned int)msg->flags);
'''
        s=one(s,anchor,rep,'C semantic LPM')
    return s+'\n/* '+MARK+f': timeout autopsy variant {variant}. */\n'

def patch_disp(s,variant):
    if variant!='B' or MARK in s:return s
    anchor='#if defined(CONFIG_DISPLAY_SAMSUNG)\nextern bool pba_regulator_control_ss;\n#endif\n'
    helper=r'''#if defined(CONFIG_DISPLAY_SAMSUNG)
extern bool pba_regulator_control_ss;
#endif

/* A52_PHASE439_DSI_AUTOPSY_V1 variant B: one early normal-host-path unlock
 * followed by the matching relock. If unlock hangs, the common exact-F0
 * timeout autopsy panics. If it succeeds, boot continues to the normal late F0.
 */
static atomic_t a52_p439_early_once=ATOMIC_INIT(0);
static int a52_p439_early_f0(struct dsi_display *display)
{
	static const u8 unlock[]={0xF0,0x5A,0x5A};
	static const u8 relock[]={0xF0,0xA5,0xA5};
	struct mipi_dsi_msg msg={0}; int rc;
	if(!display || strcmp(display->display_type,"primary") ||
	   atomic_cmpxchg(&a52_p439_early_once,0,1)!=0) return 0;
	msg.channel=0; msg.type=MIPI_DSI_GENERIC_LONG_WRITE;
	msg.flags=MIPI_DSI_MSG_LASTCOMMAND; msg.tx_len=ARRAY_SIZE(unlock); msg.tx_buf=unlock;
	a52_ackfr_record("P439 B early_unlock pre");
	rc=(int)dsi_host_transfer(&display->host,&msg);
	a52_ackfr_record("P439 B early_unlock rc=%d",rc);
	if(rc) return rc;
	msg.tx_len=ARRAY_SIZE(relock); msg.tx_buf=relock;
	a52_ackfr_record("P439 B early_relock pre");
	rc=(int)dsi_host_transfer(&display->host,&msg);
	a52_ackfr_record("P439 B early_relock rc=%d",rc);
	if(rc) panic("A52P439B early F0 relock failed");
	return 0;
}
'''
    s=one(s,anchor,helper,'B helper insertion')
    old='''\tdsi_config_host_engine_state_for_cont_splash(display);
\tmutex_unlock(&display->display_lock);

\t/* Set the current brightness level */
'''
    new='''\tdsi_config_host_engine_state_for_cont_splash(display);
\tmutex_unlock(&display->display_lock);

\t/* Phase439B: use the registered normal DSI host path after splash handoff. */
\trc = a52_p439_early_f0(display);
\tif (rc)
\t\treturn rc;

\t/* Set the current brightness level */
'''
    s=one(s,old,new,'B cont splash hook')
    return s+'\n/* '+MARK+': early-vs-late HS F0 variant B. */\n'

def validate(root,variant):
    files=[CTRL,HWC,REC,SDE]
    if variant=='B':files.append(DISP)
    for p in files:
        if not (root/p).is_file():die('missing '+str(p))
    alltxt='\n'.join((root/p).read_text(errors='replace') for p in files)
    for tok in [MARK,'P439 S i=%u p=%u','P439 D i=%u','P439 P i=%u','P439 B i=%u c=%x v=%x','A52P439 DSI F0 DMA_DONE timeout','a52_p439_sde_dsi_full_dump','0x0ae94000ULL','0x0171']:
        if tok not in alltxt:die('missing '+tok)
    if variant=='B':
        for tok in ['early_unlock pre','early_relock pre','dsi_host_transfer(&display->host,&msg)']:
            if tok not in alltxt:die('B missing '+tok)
    if variant=='C':
        for tok in ['MIPI_DSI_MSG_USE_LPM','P439 C force_lpm']:
            if tok not in alltxt:die('C missing '+tok)
    if MARK in (root/'drivers/devfreq/devfreq.c').read_text(errors='replace'):
        die('unrelated devfreq core modified')
    print('Phase439 DSI autopsy variant '+variant+': PASS')

def apply(root,variant):
    for p,fn in [(REC,patch_rec),(HWC,lambda s:patch_hwc(s,variant)),(SDE,patch_sde),(CTRL,lambda s:patch_ctrl(s,variant)),(DISP,lambda s:patch_disp(s,variant))]:
        q=root/p
        if not q.is_file(): die('missing '+str(p))
        ns=fn(q.read_text(errors='replace'))
        q.write_text(ns)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--variant',choices=['A','B','C'],required=True);ap.add_argument('--check-only',action='store_true');ns=ap.parse_args();root=ns.root.resolve()
    if not ns.check_only:apply(root,ns.variant)
    validate(root,ns.variant)
if __name__=='__main__':main()
