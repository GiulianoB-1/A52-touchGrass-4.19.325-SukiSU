#!/usr/bin/env python3
"""446V passive before/after AOD SDE/DSI state snapshots and second HWC window.

No changes to atomic ioctl results, panel power, encoder programming or
Display HWC null commits. Automatic EVENT state: 0 boot, 1 first OFF, 2 first
normal (not LP/AOD) DISPLAY_ON. Stage 2 opens exactly 100 more full requests.
"""
import argparse
from pathlib import Path
KMS=Path("drivers/a52_display/msm/sde/sde_kms.c")
ENC=Path("drivers/a52_display/msm/sde/sde_encoder.c")
PHYS=Path("drivers/a52_display/msm/sde/sde_encoder_phys_cmd.c")
MSM=Path("drivers/a52_display/msm/msm_atomic.c")
UAPI=Path("drivers/gpu/drm/drm_atomic_uapi.c")
SS=Path("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c")
HDR=Path("include/linux/a52_446v_display_trace.h")
TAG="A52_PHASE446V_SDE_AOD_STATE_DIFF_V1"

def one(s,a,b,tag):
    n=s.count(a)
    if n!=1:raise RuntimeError("446V state "+tag+" expected one anchor found "+str(n))
    return s.replace(a,b,1)

def patch_kms(s):
    if TAG in s:return s
    pre='#include <linux/atomic.h>\n#include <linux/ktime.h>\n#include <linux/sched.h>\n#include <linux/a52_446v_display_trace.h>\n'
    if '#include "ss_dsi_panel_common.h"' not in s:
        pre+='#if defined(CONFIG_DISPLAY_SAMSUNG)\n#include "ss_dsi_panel_common.h"\n#endif\n'
    s=pre+s
    sig='bool sde_is_custom_client(void)'
    helper=r'''
/* A52_PHASE446V_SDE_AOD_STATE_DIFF_V1 */
static atomic_t a52v_display_phase=ATOMIC_INIT(0);
static atomic_t a52v_first_hwc=ATOMIC_INIT(0);
static atomic_t a52v_first_normal_on_commit=ATOMIC_INIT(0);
static atomic_t a52v_post_req_count=ATOMIC_INIT(0);
int a52v_display_stage(void) { return atomic_read(&a52v_display_phase); }
EXPORT_SYMBOL_GPL(a52v_display_stage);
int a52v_post_window_next(void)
{
	int idx;
	if (atomic_read(&a52v_display_phase)!=2)
		return 0;
	idx=atomic_inc_return(&a52v_post_req_count);
	return idx<=100 ? idx : 0;
}
EXPORT_SYMBOL_GPL(a52v_post_window_next);
void a52v_display_event(bool normal_on)
{
	if (normal_on) {
		if (atomic_cmpxchg(&a52v_display_phase,1,2)==1)
			pr_info("P446V EVENT NORMAL_DISPLAY_ON ts=%llu phase=2 post_window=100\n",
				(unsigned long long)ktime_get_boottime_ns());
	} else if (atomic_cmpxchg(&a52v_display_phase,0,1)==0) {
		pr_info("P446V EVENT FIRST_PANEL_OFF ts=%llu phase=1\n",
			(unsigned long long)ktime_get_boottime_ns());
	}
}
EXPORT_SYMBOL_GPL(a52v_display_event);
extern void a52v_encoder_dump(struct drm_encoder *encoder,const char *phase);

void a52v_sde_snapshot(struct drm_device *dev,const char *phase)
{
	struct sde_kms *kms;
	struct drm_connector *conn;
	struct drm_crtc *crtc;
	struct drm_encoder *encoder;
	struct dsi_display *display;
	struct dsi_panel *panel;
	struct sde_connector *sc;
	struct sde_crtc *sde_crtc;
	struct sde_crtc_state *cs;
	struct samsung_display_driver_data *vdd;

	if (!dev) {
		pr_info("P446V SNAP ts=%llu phase=%s drm_dev=NULL\n",
			(unsigned long long)ktime_get_boottime_ns(),phase);
		return;
	}
	kms=to_sde_kms(ddev_to_msm_kms(dev));
	pr_info("P446V SNAP ts=%llu phase=%s pid=%d comm=%s g_cont_splash=%d kms_first_kickoff=%d event_stage=%d\n",
		(unsigned long long)ktime_get_boottime_ns(),phase,
		task_pid_nr(current),current->comm,
		g_cont_splash,kms?kms->first_kickoff:-1,a52v_display_stage());
	drm_for_each_crtc(crtc,dev) {
		if (crtc->base.id!=107)continue;
		sde_crtc=to_sde_crtc(crtc);
		cs=crtc->state ? to_sde_crtc_state(crtc->state) : NULL;
		pr_info("P446V SNAP_CRTC phase=%s id=%u active=%d enable=%d mode=%s h=%u v=%u clock=%u sde_enabled=%d pending=%d trigger=%d dim_layers=%u num_connectors=%d\n",
			phase,crtc->base.id,crtc->state?crtc->state->active:-1,
			crtc->state?crtc->state->enable:-1,
			crtc->state?crtc->state->mode.name:"(none)",
			crtc->state?crtc->state->mode.hdisplay:0,
			crtc->state?crtc->state->mode.vdisplay:0,
			crtc->state?crtc->state->mode.clock:0,
			sde_crtc->enabled,atomic_read(&sde_crtc->frame_pending),
			sde_crtc->frame_trigger_mode,cs?cs->num_dim_layers:0,
			cs?cs->num_connectors:0);
	}
	drm_for_each_encoder(encoder,dev) {
		pr_info("P446V SNAP_ENCODER phase=%s id=%u crtc=%u cont_splash=%d\n",
			phase,encoder->base.id,encoder->crtc?encoder->crtc->base.id:0,
			sde_encoder_in_cont_splash(encoder));
		a52v_encoder_dump(encoder,phase);
	}
	list_for_each_entry(conn,&dev->mode_config.connector_list,head) {
		if (conn->base.id!=32)continue;
		sc=to_sde_connector(conn);
		pr_info("P446V SNAP_CONN phase=%s id=%u status=%d encoder=%u dpms=%d lp=%d last_power=%d autorefresh=%llu caps_blob=%u info_blob_len=%zu\n",
			phase,conn->base.id,conn->status,
			sc->encoder?sc->encoder->base.id:0,
			sc->dpms_mode,sc->lp_mode,sc->last_panel_power_mode,
			(unsigned long long)(conn->state ?
				sde_connector_get_property(conn->state,CONNECTOR_PROP_AUTOREFRESH):0),
			sc->blob_caps?sc->blob_caps->base.id:0,
			sc->blob_caps?sc->blob_caps->length:0);
		if (sc->connector_type!=DRM_MODE_CONNECTOR_DSI || !sc->display)
			continue;
		display=(struct dsi_display *)sc->display;
		panel=display->panel;
		pr_info("P446V SNAP_DSI phase=%s splash=%d ulps=%d clamp=%d poms=%d cmdref=%u mode_index=%d panel=%s panel_mode=%d power=%d initialized=%d mode=%s refresh=%u\n",
			phase,display->is_cont_splash_enabled,display->ulps_enabled,
			display->clamp_enabled,display->poms_pending,
			display->cmd_engine_refcount,display->cmdline_timing,
			panel&&panel->name?panel->name:"(none)",
			panel?panel->panel_mode:-1,panel?panel->power_mode:-1,
			panel?panel->panel_initialized:-1,
			panel&&panel->cur_mode?"valid":"none",
			panel&&panel->cur_mode?panel->cur_mode->timing.refresh_rate:0);
#if defined(CONFIG_DISPLAY_SAMSUNG)
		vdd=panel ? (struct samsung_display_driver_data *)panel->panel_private:NULL;
		if (vdd)
			pr_info("P446V SNAP_SAMSUNG phase=%s panel_state=%d display_on=%d wait_on=%d first_commit_disp_on=%d finger_mask=%d\n",
				phase,vdd->panel_state,vdd->display_on,
				vdd->display_status_dsi.wait_disp_on,
				vdd->display_status_dsi.first_commit_disp_on,
				vdd->finger_mask);
#else
		(void)vdd;
#endif
	}
}
EXPORT_SYMBOL_GPL(a52v_sde_snapshot);
void a52v_atomic_snapshot(struct drm_device *dev)
{
	bool hwc=(strnstr(current->comm,"HwBinder",TASK_COMM_LEN) ||
		 strnstr(current->comm,"composer",TASK_COMM_LEN));
	if (!hwc || ktime_get_boottime_ns()<40ULL*1000000000ULL)
		return;
	if (atomic_cmpxchg(&a52v_first_hwc,0,1)==0)
		a52v_sde_snapshot(dev,"FIRST_HWC_ATOMIC");
	if (atomic_read(&a52v_display_phase)==2 &&
	    atomic_cmpxchg(&a52v_first_normal_on_commit,0,1)==0)
		a52v_sde_snapshot(dev,"FIRST_POST_NORMAL_ON_ATOMIC");
}
EXPORT_SYMBOL_GPL(a52v_atomic_snapshot);
'''
    s=one(s,sig,helper+sig,"SDE snapshot hook")
    return s

def patch_encoder(s):
    if TAG in s:return s
    key="int sde_encoder_in_cont_splash(struct drm_encoder *drm_enc)"
    func=r'''
/* A52_PHASE446V_SDE_AOD_STATE_DIFF_V1 */
void a52v_encoder_dump(struct drm_encoder *encoder,const char *phase)
{
	struct sde_encoder_virt *sde_enc;
	int i;
	extern void a52v_physcmd_dump(struct sde_encoder_phys *phys,const char *phase);
	if (!encoder)return;
	sde_enc=to_sde_encoder_virt(encoder);
	pr_info("P446V SNAP_ENC_DETAIL phase=%s id=%u idle_pc=%d idle_pc_restore=%d rc_state=%d trigger_mode=%d phys=%u cur_master=%d vblank=%d\n",
		phase,encoder->base.id,sde_enc->idle_pc_enabled,
		sde_enc->idle_pc_restore,sde_enc->rc_state,
		sde_enc->frame_trigger_mode,sde_enc->num_phys_encs,
		!!sde_enc->cur_master,sde_enc->vblank_enabled);
	for (i=0;i<MAX_PHYS_ENCODERS_PER_VIRTUAL;i++)
		if (sde_enc->phys_cmd_encs[i])
			a52v_physcmd_dump(sde_enc->phys_cmd_encs[i],phase);
}
EXPORT_SYMBOL_GPL(a52v_encoder_dump);
'''
    return one(s,key,func+key,"encoder detailed state")

def patch_phys(s):
    if "P446V SNAP_AUTORF" in s:return s
    anchor="static inline int _sde_encoder_phys_cmd_get_idle_timeout("
    helper=r'''
/* A52_PHASE446V_PHYS_CMD_AUTOREFRESH_STATE_V1 */
void a52v_physcmd_dump(struct sde_encoder_phys *phys,const char *phase)
{
	struct sde_encoder_phys_cmd *cmd;
	if (!phys)return;
	cmd=to_sde_encoder_phys_cmd(phys);
	pr_info("P446V SNAP_AUTORF phase=%s enc=%u intf=%d cont_splash=%d phys_enable=%d refresh_enable=%d refresh_frames=%u te=%d split_role=%d\n",
		phase,phys->parent?phys->parent->base.id:0,
		phys->intf_idx,phys->cont_splash_enabled,
		phys->enable_state,cmd->autorefresh.cfg.enable,
		cmd->autorefresh.cfg.frame_count,phys->has_intf_te,phys->split_role);
}
EXPORT_SYMBOL_GPL(a52v_physcmd_dump);

'''
    return one(s,anchor,helper+anchor,"phys cmd autorefresh state")

def patch_msm(s):
    if TAG in s:return s
    s='#include <linux/a52_446v_display_trace.h>\n'+s
    return one(s,'a52u_snapshot_plane91(dev, "before");',
            'a52v_atomic_snapshot(dev);\n\ta52u_snapshot_plane91(dev, "before");',
            "first/post normal-on HWC commit")

def patch_samsung(s):
    if TAG in s:return s
    s='#include <linux/a52_446v_display_trace.h>\n'+s
    key="int ss_panel_off_pre(struct samsung_display_driver_data *vdd)"
    a=s.index(key);end=s.index("\n}",a)+2;fn=s[a:end]
    anchor='LCD_INFO(vdd, "+\\n");'
    fn=one(fn,anchor,anchor+r'''
	/* First pre-off state, BEFORE Samsung changes panel_state. */
	if (a52v_display_stage()==0) {
		a52v_sde_snapshot(GET_DRM_DEV(vdd),"PRE_FIRST_PANEL_OFF");
		a52v_display_event(false);
	}
''',"Samsung first OFF snapshot")
    s=s[:a]+fn+s[end:]
    key="void ss_event_frame_update_post(struct samsung_display_driver_data *vdd)"
    a=s.index(key);end=s.index("\n}",a)+2;fn=s[a:end]
    anchor='LCD_INFO(vdd, "DISPLAY_ON\\n");'
    fn=one(fn,anchor,anchor+r'''
		/* Do not count AOD/LP1/LP2 as normalized DISPLAY_ON. */
		if (!ss_is_panel_lpm(vdd))
			a52v_display_event(true);
''',"Samsung first post-AOD normal ON")
    return s[:a]+fn+s[end:]

def patch_uapi(s):
    if TAG in s:return s
    s='#include <linux/a52_446v_display_trace.h>\n'+s
    anchor='a52u_req = atomic_inc_return(&a52u_ioctl_seq);'
    s=one(s,anchor,'''a52u_req = atomic_inc_return(&a52u_ioctl_seq);
		if (a52u_req > 300) {
			int post = a52v_post_window_next();
			a52u_req = post ? 300 + post : 0;
		}''',"second atomic request window after normal DISPLAY_ON")
    # 446U diagnostic gates keep per-object/per-property dumps bounded:
    # 1..300 (first boot), 301..400 (post-normal-on).
    if s.count('a52u_req <= 300')<2:
        raise RuntimeError("446V no expected original 446U request guards")
    s=s.replace('a52u_req <= 300','a52u_req > 0 && a52u_req <= 400')
    # Print a visible window switch marker at first post-normal commit.
    anchor='''			a52u_req_set(a52u_req);'''
    s=one(s,anchor,'''			if (a52u_req == 301)
				pr_info("P446V POST_WINDOW_BEGIN ts=%llu count=100 trigger=NORMAL_DISPLAY_ON\\n",
					(unsigned long long)ktime_get_boottime_ns());
'''+anchor,"second window marker")
    return s

def check(root):
    expected={
        KMS:(TAG,"P446V SNAP_CRTC","P446V SNAP_DSI","P446V SNAP_CONN","P446V SNAP_SAMSUNG",
             "P446V EVENT NORMAL_DISPLAY_ON","P446V SNAP_ENCODER"),
        ENC:(TAG,"P446V SNAP_ENC_DETAIL"),
        PHYS:("P446V SNAP_AUTORF","A52_PHASE446V_PHYS_CMD_AUTOREFRESH_STATE_V1"),
        MSM:("a52v_atomic_snapshot(dev)",),
        UAPI:("P446V POST_WINDOW_BEGIN","a52v_post_window_next"),
        SS:("PRE_FIRST_PANEL_OFF","a52v_display_event(true)"),
        HDR:("a52v_atomic_snapshot","a52v_display_stage")
    }
    for p,words in expected.items():
        s=(root/p).read_text()
        for word in words:
            if word not in s:raise RuntimeError("446V state missing "+word+" in "+str(p))
    print("P446V STATE DIFF AUDIT PASS: first HWC/first OFF/first NORMAL_ON, SDE/DSI/Samsung, 100 post-cycle requests")

def main():
    a=argparse.ArgumentParser();a.add_argument("--root",type=Path,required=True)
    a.add_argument("--check-only",action="store_true");x=a.parse_args()
    if not x.check_only:
        (x.root/HDR).write_text('''/* A52_PHASE446V_SDE_AOD_STATE_DIFF_V1 */
#ifndef _A52_446V_DISPLAY_TRACE_H
#define _A52_446V_DISPLAY_TRACE_H
#include <linux/types.h>
struct drm_device;
void a52v_sde_snapshot(struct drm_device *dev,const char *phase);
void a52v_atomic_snapshot(struct drm_device *dev);
void a52v_display_event(bool normal_on);
int a52v_display_stage(void);
int a52v_post_window_next(void);
#endif
''')
        for p,fn in ((KMS,patch_kms),(ENC,patch_encoder),(PHYS,patch_phys),(MSM,patch_msm),
                     (SS,patch_samsung),(UAPI,patch_uapi)):
            f=x.root/p;f.write_text(fn(f.read_text()))
    check(x.root)
if __name__=="__main__":main()
