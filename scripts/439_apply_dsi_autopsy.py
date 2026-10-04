#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE439_DSI_AUTOPSY_V2'
CTRL=Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HWC=Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
DISP=Path('drivers/a52_display/msm/dsi/dsi_display.c')
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')


def die(m): raise SystemExit('Phase439: '+m)
def one(s,o,n,l):
    c=s.count(o)
    if c!=1: die(f'{l}: expected 1 anchor, found {c}')
    return s.replace(o,n,1)

COMMON_HWC=r'''
/* A52_PHASE439_DSI_AUTOPSY_V2
 * Exact-F0 DSI autopsy. The hot path stores compact snapshots in RAM only.
 * Persistence and the 256-point Qualcomm DSI debug-bus sweep happen only
 * after the normal ~200 ms timeout, when timing can no longer be perturbed.
 *
 * 0x100/0x104 are the later-Qualcomm DMA scheduling controls. They are read
 * even though this older downstream driver never initializes CTRL2. This is
 * deliberate: continuous splash skips host_setup(), so bootloader state can
 * survive into the first HLOS command.
 */
#define A52_P439_PHY_PHYS          0x0ae94000ULL
#define A52_P439_PHY_SIZE          0x1000U
#define A52_P439_DMA_SCHED_CTRL    0x0100U
#define A52_P439_DMA_SCHED_CTRL2   0x0104U
#define A52_P439_MAX               10U

extern bool a52_p439_f0_active(void);

struct a52_p439_sample {
	u64 ns;
	u32 epoch, point;
	u32 status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status, lane_ctrl;
	u32 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl;
	u32 sched1, sched2, disp_misc;
	u32 ack_err, timeout, phy_err, axi2ahb, dbg171;
	u32 pll_status_one, phy_pll_ctrl, phy_ctrl0, phy_rbuf, phy_clk_cfg1;
	u32 phy_lane0, phy_lane1;
};
static struct a52_p439_sample a52_p439_samples[A52_P439_MAX];
static atomic_t a52_p439_count=ATOMIC_INIT(0);
static atomic_t a52_p439_active=ATOMIC_INIT(0);
static atomic_t a52_p439_post_pending=ATOMIC_INIT(0);
static atomic_t a52_p439_epoch=ATOMIC_INIT(0);
static u32 a52_p439_current_epoch;
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
	s->ns=ktime_get_ns(); s->epoch=READ_ONCE(a52_p439_current_epoch); s->point=point;
	s->status=DSI_R32(ctrl,DSI_STATUS); s->fifo=DSI_R32(ctrl,DSI_FIFO_STATUS);
	s->clk_ctrl=DSI_R32(ctrl,DSI_CLK_CTRL); s->clk_status=DSI_R32(ctrl,DSI_CLK_STATUS);
	s->int_ctrl=DSI_R32(ctrl,DSI_INT_CTRL); s->lane_status=DSI_R32(ctrl,DSI_LANE_STATUS);
	s->lane_ctrl=DSI_R32(ctrl,DSI_LANE_CTRL);
	s->dma_ctrl=DSI_R32(ctrl,DSI_COMMAND_MODE_DMA_CTRL); s->dma_offset=DSI_R32(ctrl,DSI_DMA_CMD_OFFSET);
	s->dma_length=DSI_R32(ctrl,DSI_DMA_CMD_LENGTH); s->sw_trigger=DSI_R32(ctrl,DSI_CMD_MODE_DMA_SW_TRIGGER);
	s->trig_ctrl=DSI_R32(ctrl,DSI_TRIG_CTRL);
	s->sched1=DSI_R32(ctrl,A52_P439_DMA_SCHED_CTRL); s->sched2=DSI_R32(ctrl,A52_P439_DMA_SCHED_CTRL2);
	s->disp_misc=ctrl->disp_cc_base ? DSI_DISP_CC_R32(ctrl,0x0) : ~0U;
	s->ack_err=DSI_R32(ctrl,DSI_ACK_ERR_STATUS); s->timeout=DSI_R32(ctrl,DSI_TIMEOUT_STATUS);
	s->phy_err=DSI_R32(ctrl,DSI_DLN0_PHY_ERR); s->axi2ahb=DSI_R32(ctrl,DSI_AXI2AHB_CTRL);
	s->dbg171=DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS);
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
	if(!a52_p439_f0_active() || !ctrl || !ctrl->base) return;
	if(atomic_cmpxchg(&a52_p439_active,0,1)!=0) return;
	WRITE_ONCE(a52_p439_current_epoch,(u32)atomic_inc_return(&a52_p439_epoch));
	a52_p439_saved_dbg=DSI_R32(ctrl,DSI_DEBUG_BUS_CTL);
	DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,0x0171); wmb();
	atomic_set(&a52_p439_post_pending,1);
	a52_p439_store(ctrl,0U);
}

static void a52_p439_post_trigger(struct dsi_ctrl_hw *ctrl)
{
	u64 start;
	if(atomic_cmpxchg(&a52_p439_post_pending,1,0)!=1 || !ctrl || !ctrl->base) return;
	start=ktime_get_ns();
	a52_p439_wait_until(start,15000ULL); a52_p439_store(ctrl,15U);
	a52_p439_wait_until(start,35000ULL); a52_p439_store(ctrl,35U);
	a52_p439_wait_until(start,50000ULL); a52_p439_store(ctrl,50U);
	DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,a52_p439_saved_dbg); wmb();
}

void a52_p439_allow_next(void)
{
	/* Variant B calls this only after early unlock + relock both returned. */
	atomic_set(&a52_p439_post_pending,0);
	atomic_set(&a52_p439_active,0);
}
EXPORT_SYMBOL_GPL(a52_p439_allow_next);

void a52_p439_timeout_snapshot(struct dsi_ctrl_hw *ctrl)
{
	if(atomic_read(&a52_p439_active)!=1 || !a52_p439_f0_active()) return;
	a52_p439_store(ctrl,200U);
}

void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl)
{
	unsigned int i,n=(unsigned int)atomic_read(&a52_p439_count); u64 base=0;
	if(n>A52_P439_MAX) n=A52_P439_MAX; if(n) base=a52_p439_samples[0].ns;
	for(i=0;i<n;i++) { const struct a52_p439_sample *s=&a52_p439_samples[i];
		a52_ackfr_record("P439 S i=%u e=%u p=%u us=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x db=%x",
			i,s->epoch,s->point,(unsigned long long)((s->ns-base)/1000ULL),s->status,s->fifo,s->clk_ctrl,s->clk_status,s->int_ctrl,s->lane_status,s->dbg171);
		a52_ackfr_record("P439 D i=%u e=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x",
			i,s->epoch,s->dma_ctrl,s->dma_offset,s->dma_length,s->sw_trigger,s->trig_ctrl,s->ack_err,s->timeout,s->phy_err,s->axi2ahb);
		a52_ackfr_record("P439 Q i=%u e=%u s1=%x s2=%x dm=%x lc=%x",i,s->epoch,s->sched1,s->sched2,s->disp_misc,s->lane_ctrl);
		a52_ackfr_record("P439 P i=%u e=%u ps=%x pc=%x c0=%x rb=%x cf=%x l0=%x l1=%x",
			i,s->epoch,s->pll_status_one,s->phy_pll_ctrl,s->phy_ctrl0,s->phy_rbuf,s->phy_clk_cfg1,s->phy_lane0,s->phy_lane1);
	}
	if(ctrl && ctrl->base) a52_ackfr_record("P439 T st=%x in=%x cc=%x ck=%x s1=%x s2=%x tg=%x n=%u",
		DSI_R32(ctrl,DSI_STATUS),DSI_R32(ctrl,DSI_INT_CTRL),DSI_R32(ctrl,DSI_CLK_CTRL),DSI_R32(ctrl,DSI_CLK_STATUS),
		DSI_R32(ctrl,A52_P439_DMA_SCHED_CTRL),DSI_R32(ctrl,A52_P439_DMA_SCHED_CTRL2),DSI_R32(ctrl,DSI_TRIG_CTRL),n);
}

void a52_p439_debug_sweep(struct dsi_ctrl_hw *ctrl)
{
	u32 saved,b,t,ctl,v;
	if(!ctrl || !ctrl->base) return;
	saved=DSI_R32(ctrl,DSI_DEBUG_BUS_CTL);
	for(b=0;b<4;b++) for(t=0;t<64;t++) {
		ctl=((b&0x3U)<<12)|((t&0x3fU)<<4)|BIT(0);
		DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,ctl); wmb();
		v=DSI_R32(ctrl,DSI_DEBUG_BUS_STATUS);
		a52_ackfr_record("P439 B b=%u t=%u c=%x v=%x",b,t,ctl,v);
	}
	DSI_W32(ctrl,DSI_DEBUG_BUS_CTL,saved); wmb();
}

#ifdef CONFIG_DEBUG_FS
#include <linux/debugfs.h>
#include <linux/seq_file.h>
static int a52_p439_dbg_show(struct seq_file *m,void *v)
{
	unsigned int i,n=(unsigned int)atomic_read(&a52_p439_count); if(n>A52_P439_MAX)n=A52_P439_MAX;
	seq_printf(m,"%s count=%u active=%d epoch=%d
", "A52_PHASE439_DSI_AUTOPSY_V2",n,atomic_read(&a52_p439_active),atomic_read(&a52_p439_epoch));
	for(i=0;i<n;i++){const struct a52_p439_sample*s=&a52_p439_samples[i];
		seq_printf(m,"i=%u e=%u p=%u ns=%llu st=%08x in=%08x ck=%08x cc=%08x db=%08x s1=%08x s2=%08x dm=%08x ps=%08x pc=%08x
",
			i,s->epoch,s->point,(unsigned long long)s->ns,s->status,s->int_ctrl,s->clk_status,s->clk_ctrl,s->dbg171,s->sched1,s->sched2,s->disp_misc,s->pll_status_one,s->phy_pll_ctrl);}
	return 0;
}
static int a52_p439_dbg_open(struct inode*i,struct file*f){return single_open(f,a52_p439_dbg_show,NULL);}
static const struct file_operations a52_p439_dbg_fops={.owner=THIS_MODULE,.open=a52_p439_dbg_open,.read=seq_read,.llseek=seq_lseek,.release=single_release};
static int __init a52_p439_dbg_init(void){debugfs_create_file("a52_phase439_dsi",0444,NULL,NULL,&a52_p439_dbg_fops);return 0;} late_initcall(a52_p439_dbg_init);
#endif
'''


def patch_rec(s):
    if MARK in s:return s
    s=s.replace('return !strncmp(message, "P434 ", 5) ||','return !strncmp(message, "P439 ", 5) ||
       !strncmp(message, "P434 ", 5) ||',1)
    s=s.replace('strncmp(fmt, "P434", 4) &&','strncmp(fmt, "P439", 4) &&
	    strncmp(fmt, "P434", 4) &&')
    return s+'
/* '+MARK+': P439 autopsy records admitted to persistent recorder. */
'


def patch_hwc(s,variant):
    if MARK in s:return s
    inc='#include <linux/init.h>
'
    if inc not in s: die('HWC init include anchor missing')
    s=one(s,inc,inc+'#include <linux/module.h>
','HWC module include')
    anchor='/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */
'
    s=one(s,anchor,COMMON_HWC+'
'+anchor,'HWC helper')
    old='''	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		if (a52_p421_target_active()) {
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
			a52_p424_snapshot(ctrl);
			a52_p427_road_snapshot(ctrl, 0U);
			a52_p430_dma_arm(ctrl);
		}
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		if (a52_p421_target_active()) {
			a52_p430_dma_triggered(ctrl);
			a52_p421_survival_record(8U, 0U, 0U, 0, 0U, 0);
		}
'''
    new='''	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		if (a52_p421_target_active()) {
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
			a52_p424_snapshot(ctrl);
			a52_p427_road_snapshot(ctrl, 0U);
			a52_p430_dma_arm(ctrl);
		}
		if (a52_p439_f0_active())
			a52_p439_begin(ctrl);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		if (a52_p439_f0_active())
			a52_p439_post_trigger(ctrl);
		if (a52_p421_target_active()) {
			a52_p430_dma_triggered(ctrl);
			a52_p421_survival_record(8U, 0U, 0U, 0, 0U, 0);
		}
'''
    s=one(s,old,new,'memory trigger')
    old2='''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new2='''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	if (a52_p439_f0_active())
		a52_p439_begin(ctrl);
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
	if (a52_p439_f0_active())
		a52_p439_post_trigger(ctrl);
'''
    s=one(s,old2,new2,'deferred trigger')
    s += f'
static const char a52_p439_variant[] __used = "{MARK}:VARIANT_{variant}";
'
    return s


def patch_ctrl(s,variant):
    if MARK in s:return s
    a='extern void a52_p345_flush(struct dsi_ctrl_hw *ctrl); /* A52_PHASE345_DMA_US_FRONTIER_V1 */
'
    b=a+'''extern void a52_p439_timeout_snapshot(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_debug_sweep(struct dsi_ctrl_hw *ctrl);
extern void a52_p439_allow_next(void);
extern void a52_p434_dump_rails(const char *tag);
'''
    s=one(s,a,b,'CTRL declarations')

    anchor='/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */
'
    state=r'''/* A52_PHASE439_DSI_AUTOPSY_V2 */
static atomic_t a52_p439_f0_inflight=ATOMIC_INIT(0);
bool a52_p439_f0_active(void)
{
	return atomic_read(&a52_p439_f0_inflight)!=0;
}
EXPORT_SYMBOL_GPL(a52_p439_f0_active);

static __maybe_unused void a52_p439d_reset_schedule_state(struct dsi_ctrl *dsi_ctrl)
{
	static const u8 trigger_map[DSI_TRIGGER_MAX]={0x0,0x2,0x1,0x4,0x5,0x6};
	u32 trig,s1,s2;
	if(!dsi_ctrl || !dsi_ctrl->hw.base) return;
	trig=readl_relaxed(dsi_ctrl->hw.base+DSI_TRIG_CTRL);
	s1=readl_relaxed(dsi_ctrl->hw.base+0x100);
	s2=readl_relaxed(dsi_ctrl->hw.base+0x104);
	a52_ackfr_record("P439 R pre tg=%x s1=%x s2=%x dma=%u",trig,s1,s2,dsi_ctrl->host_config.common_config.dma_cmd_trigger);
	/* Qualcomm 2022 fixes: clear stale DMA trigger mux + schedule windows,
	 * and restore the configured software trigger selection. */
	trig &= ~BIT(16);
	trig &= ~0xFU;
	if (dsi_ctrl->host_config.common_config.dma_cmd_trigger < DSI_TRIGGER_MAX)
		trig |= trigger_map[dsi_ctrl->host_config.common_config.dma_cmd_trigger] & 0xFU;
	writel_relaxed(trig,dsi_ctrl->hw.base+DSI_TRIG_CTRL);
	writel_relaxed(0,dsi_ctrl->hw.base+0x100);
	writel_relaxed(0,dsi_ctrl->hw.base+0x104);
	wmb();
	a52_ackfr_record("P439 R post tg=%x s1=%x s2=%x",
		readl_relaxed(dsi_ctrl->hw.base+DSI_TRIG_CTRL),
		readl_relaxed(dsi_ctrl->hw.base+0x100),readl_relaxed(dsi_ctrl->hw.base+0x104));
}

'''
    s=one(s,anchor,state+anchor,'P439 exact-F0 state')

    old='''	u32 hw_flags = 0;
	u32 line_no = 0x1;
	struct dsi_ctrl_hw_ops dsi_hw_ops = dsi_ctrl->hw.ops;
'''
    new='''	u32 hw_flags = 0;
	u32 line_no = 0x1;
	struct dsi_ctrl_hw_ops dsi_hw_ops = dsi_ctrl->hw.ops;
	bool a52_p439_f0 = a52_p411_exact_f0(dsi_ctrl, msg);

	if (a52_p439_f0)
		atomic_set(&a52_p439_f0_inflight, 1);
'''
    s=one(s,old,new,'kickoff exact-F0 local')
    tail='''	}
}

static void dsi_ctrl_validate_msg_flags'''
    tail_new='''	}
	if (a52_p439_f0)
		atomic_set(&a52_p439_f0_inflight, 0);
}

static void dsi_ctrl_validate_msg_flags'''
    s=one(s,tail,tail_new,'kickoff exact-F0 clear')

    if variant=='D':
        old='''		a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
		a52_p426_capture_f0();
'''
        new=old+'		a52_p439d_reset_schedule_state(dsi_ctrl);
'
        s=one(s,old,new,'D scheduling reset')

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
	/* P426/P427 already captured the one-shot SMMU sanity state. The
	 * expensive operation here is deliberately the link's own 256-point
	 * debug bus, after the timed experiment has ended. */
	if (dsi_ctrl)
		a52_p439_debug_sweep(&dsi_ctrl->hw);
	a52_p434_dump_rails("p439");
	a52_ackfr_record("P439 PANIC ctrl=%d irq=%u q=%u", dsi_ctrl ? dsi_ctrl->cell_index : -1,
		dsi_ctrl ? (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig) : 0U,
		dsi_ctrl ? dsi_ctrl->dma_wait_queued : 0U);
	a52_ackfr_retain_timeout_snapshot();
	dump_stack();
	panic("A52P439 DSI F0 DMA_DONE timeout");
}'''
    s=s[:start]+repl+s[end:]

    if variant=='C':
        decl='	bool a52_p421_f0 = false;
'
        s=one(s,decl,decl+'	struct mipi_dsi_msg a52_p439_lpm_msg;
','C local msg')
        anchor2='''	a52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
	if (a52_p421_f0) {
'''
        rep='''	a52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
	if (a52_p421_f0) {
		a52_p439_lpm_msg = *msg;
		a52_p439_lpm_msg.flags |= MIPI_DSI_MSG_USE_LPM;
		msg = &a52_p439_lpm_msg;
		a52_ackfr_record("P439 C force_lpm m=%x", (unsigned int)msg->flags);
'''
        s=one(s,anchor2,rep,'C semantic LPM')
    return s+'
/* '+MARK+f': timeout autopsy variant {variant}. */
'


def patch_disp(s,variant):
    if variant!='B' or MARK in s:return s
    anchor='#if defined(CONFIG_DISPLAY_SAMSUNG)
extern bool pba_regulator_control_ss;
#endif
'
    helper=r'''#if defined(CONFIG_DISPLAY_SAMSUNG)
extern bool pba_regulator_control_ss;
#endif
extern void a52_p439_allow_next(void);

/* A52_PHASE439_DSI_AUTOPSY_V2 variant B: one early normal-host-path unlock
 * followed by matching relock. If unlock hangs the common exact-F0 timeout
 * autopsy panics. If both return, the sampler is re-armed for the normal late
 * F0, proving early-vs-late behavior in the same boot. */
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
	a52_ackfr_record("P439 E unlock pre");
	rc=(int)dsi_host_transfer(&display->host,&msg);
	a52_ackfr_record("P439 E unlock rc=%d",rc);
	if(rc) return rc;
	msg.tx_len=ARRAY_SIZE(relock); msg.tx_buf=relock;
	a52_ackfr_record("P439 E relock pre");
	rc=(int)dsi_host_transfer(&display->host,&msg);
	a52_ackfr_record("P439 E relock rc=%d",rc);
	if(rc) panic("A52P439B early F0 relock failed");
	a52_p439_allow_next();
	a52_ackfr_record("P439 E rearmed late=1");
	return 0;
}
'''
    s=one(s,anchor,helper,'B helper insertion')
    old='''	dsi_config_host_engine_state_for_cont_splash(display);
	mutex_unlock(&display->display_lock);

	/* Set the current brightness level */
'''
    new='''	dsi_config_host_engine_state_for_cont_splash(display);
	mutex_unlock(&display->display_lock);

	/* Phase439B: normal DSI host path after continuous-splash takeover. */
	rc = a52_p439_early_f0(display);
	if (rc)
		return rc;

	/* Set the current brightness level */
'''
    s=one(s,old,new,'B cont splash hook')
    return s+'
/* '+MARK+': early-vs-late HS F0 variant B. */
'


def validate(root,variant):
    files=[CTRL,HWC,REC]
    if variant=='B':files.append(DISP)
    for p in files:
        if not (root/p).is_file():die('missing '+str(p))
    alltxt='
'.join((root/p).read_text(errors='replace') for p in files)
    for tok in [MARK,'P439 S i=%u e=%u p=%u','P439 Q i=%u e=%u','P439 B b=%u t=%u c=%x v=%x','A52P439 DSI F0 DMA_DONE timeout','A52_P439_DMA_SCHED_CTRL2','0x0ae94000ULL','0x0171','a52_p439_f0_active','a52_p430_dma_arm(ctrl);','a52_p430_dma_triggered(ctrl);']:
        if tok not in alltxt:die('missing '+tok)
    if variant=='B':
        for tok in ['P439 E unlock pre','P439 E relock pre','a52_p439_allow_next();','dsi_host_transfer(&display->host,&msg)']:
            if tok not in alltxt:die('B missing '+tok)
    if variant=='C':
        for tok in ['MIPI_DSI_MSG_USE_LPM','P439 C force_lpm']:
            if tok not in alltxt:die('C missing '+tok)
    if variant=='D':
        for tok in ['a52_p439d_reset_schedule_state(dsi_ctrl);','P439 R pre','writel_relaxed(0,dsi_ctrl->hw.base+0x100)','writel_relaxed(0,dsi_ctrl->hw.base+0x104)','trig &= ~BIT(16)']:
            if tok not in alltxt:die('D missing '+tok)
    devfreq=root/'drivers/devfreq/devfreq.c'
    if devfreq.is_file() and MARK in devfreq.read_text(errors='replace'):
        die('unrelated devfreq core modified')
    print('Phase439 DSI autopsy variant '+variant+': PASS')


def apply(root,variant):
    for p,fn in [(REC,patch_rec),(HWC,lambda s:patch_hwc(s,variant)),(CTRL,lambda s:patch_ctrl(s,variant)),(DISP,lambda s:patch_disp(s,variant))]:
        q=root/p
        if not q.is_file():
            if p==DISP and variant!='B': continue
            die('missing '+str(p))
        q.write_text(fn(q.read_text(errors='replace')))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--variant',choices=['A','B','C','D'],required=True);ap.add_argument('--check-only',action='store_true');ns=ap.parse_args();root=ns.root.resolve()
    if not ns.check_only:apply(root,ns.variant)
    validate(root,ns.variant)
if __name__=='__main__':main()
