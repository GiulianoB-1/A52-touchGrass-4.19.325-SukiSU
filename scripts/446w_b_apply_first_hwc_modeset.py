#!/usr/bin/env python3
"""446W-B: first eligible live HWC atomic modeset, with observable lifecycle.

Keeps continuous splash resource ownership from 446V. Unlike the original
446W-B helper, a plane-only HWC commit is eligible: obtain checked CRTC and
connector atomic states through normal DRM locking/retry semantics, only
when current connector is already paired and a nonzero FB is being committed.
Never directly flip Samsung state, panel_initialized, encoder splash bits,
DSI rails, or SDE allocation. No forced activation without real mode/encoder.
"""
import argparse
from pathlib import Path
DRV=Path("drivers/a52_display/msm/msm_drv.c")
ATOM=Path("drivers/a52_display/msm/msm_atomic.c")
HDR=Path("include/linux/a52_446w_handoff_b.h")
TAG="A52_PHASE446W_HANDOFF_B_ACTUAL_FRAME_V2"

def one(s,old,new,name):
    n=s.count(old)
    if n!=1:raise RuntimeError("446W-B "+name+" anchor count="+str(n))
    return s.replace(old,new,1)

def patch_drv(s):
    if TAG in s:return s
    start=s.index("/* A52_PHASE446W_SPLASH_HANDOFF_OPTION_B_V1.")
    end=s.index("\nint msm_atomic_check(",start)
    helper=r'''/* A52_PHASE446W_HANDOFF_B_ACTUAL_FRAME_V2
 * 446W-B ONLY: atomic modeset request through the normal DRM helpers.
 * The earlier version required userspace to modify the connector itself;
 * most frame-only HWC commits do not contain it. Safely acquire both
 * object states with the atomic acquire context to avoid that dead end.
 */
static atomic_t a52w_handoff_completed = ATOMIC_INIT(0);
bool a52w_handoff_done(void)
{
	return atomic_read(&a52w_handoff_completed) != 0;
}
EXPORT_SYMBOL_GPL(a52w_handoff_done);
void a52w_handoff_finish(struct drm_device *dev)
{
	if (atomic_cmpxchg(&a52w_handoff_completed,0,1))
		return;
	pr_info("P446W HANDOFF end result=commit_tail_complete begin_eligible=1\n");
	a52_ackfr_record("P446W HANDOFF end result=commit_tail_complete");
	a52v_sde_snapshot(dev,"P446W_HANDOFF_POST_COMPLETE");
}
EXPORT_SYMBOL_GPL(a52w_handoff_finish);

static int a52w_try_handoff_modeset(struct drm_atomic_state *state)
{
	struct drm_device *dev=state->dev;
	struct drm_crtc *crtc=NULL, *candidate;
	struct drm_connector *conn=NULL, *candidate_conn;
	struct drm_plane *plane;
	struct drm_plane_state *ps;
	struct drm_crtc_state *cs;
	struct drm_connector_state *co;
	int i;
	bool active_fb=false;

	if (!a52w_handoff_selected() || a52w_handoff_done())
		return 0;
	if (!strnstr(current->comm,"HwBinder",TASK_COMM_LEN) &&
	    !strnstr(current->comm,"composer",TASK_COMM_LEN))
		return 0;

	list_for_each_entry(candidate,&dev->mode_config.crtc_list,head) {
		if (candidate->base.id==107) {
			crtc=candidate;
			break;
		}
	}
	list_for_each_entry(candidate_conn,&dev->mode_config.connector_list,head) {
		if (candidate_conn->base.id==32) {
			conn=candidate_conn;
			break;
		}
	}
	if (!crtc || !conn || !crtc->state || !conn->state)
		return 0;

	/* Never initiate a splash handoff from an HWC NULL/flush commit. */
	for_each_new_plane_in_state(state,plane,ps,i) {
		if (ps && ps->fb && ps->crtc==crtc) {
			active_fb=true;
			break;
		}
	}
	if (!active_fb)
		return 0;

	if (!crtc->state->active || !crtc->state->enable ||
	    conn->state->crtc!=crtc || !conn->state->best_encoder) {
		pr_info_ratelimited("P446W HANDOFF defer phase=live_pair active=%d enable=%d conn_crtc=%u best_enc=%u\n",
			crtc->state->active,crtc->state->enable,
			conn->state->crtc?conn->state->crtc->base.id:0,
			conn->state->best_encoder?conn->state->best_encoder->base.id:0);
		return 0;
	}

	cs=drm_atomic_get_crtc_state(state,crtc);
	if (IS_ERR(cs))
		return PTR_ERR(cs); /* let DRM ioctl perform -EDEADLK retry */
	co=drm_atomic_get_connector_state(state,conn);
	if (IS_ERR(co))
		return PTR_ERR(co);
	if (!cs->active || !cs->enable || !cs->mode.clock ||
	    co->crtc!=crtc || !co->best_encoder) {
		pr_info_ratelimited("P446W HANDOFF defer phase=atomic_pair active=%d enable=%d clock=%d co_crtc=%u best_enc=%u\n",
			cs->active,cs->enable,cs->mode.clock,
			co->crtc?co->crtc->base.id:0,
			co->best_encoder?co->best_encoder->base.id:0);
		return 0;
	}

	/* This is BEFORE drm_atomic_helper_check_modeset, which must see
	 * our flags and expand the connector/encoder validation paths.
	 */
	cs->mode_changed=true;
	cs->connectors_changed=true;
	pr_info("P446W HANDOFF begin pid=%d comm=%s crtc=%u connector=%u encoder=%u fb_plane=%u active=%d clock=%d HWC_frame=1\n",
		task_pid_nr(current),current->comm,crtc->base.id,conn->base.id,
		co->best_encoder->base.id,plane->base.id,cs->active,cs->mode.clock);
	a52_ackfr_record("P446W HANDOFF begin crtc=%u conn=%u enc=%u",
		crtc->base.id,conn->base.id,co->best_encoder->base.id);
	return 0;
}
'''
    s=s[:start]+helper+s[end:]
    s='#include <linux/a52_ack_secure_flight_recorder.h>\n#include <linux/a52_446v_display_trace.h>\n#include <linux/a52_446w_handoff_b.h>\n'+s
    # Pinned 446T already instruments this function with trace_id, trace,
    # and rc declarations, plus a52_ackfr_record statements before priv.
    # Change only the original 446W helper call inside msm_atomic_check,
    # keeping C89 declarations BEFORE those existing trace statements.
    at=s.index("int msm_atomic_check(struct drm_device *dev,")
    en=s.index("\\n}",at)+2
    sub=s[at:en]
    sub=one(sub,"\\tint rc;","\\tint rc;\\n\\tint a52w_rc;","C89 declaration after rc")
    sub=one(sub,"\\ta52w_try_handoff_modeset(state);",
      "\\ta52w_rc=a52w_try_handoff_modeset(state);\\n\\tif (a52w_rc)\\n\\t\\treturn a52w_rc;",
      "replace original option-B helper invocation")
    return s[:at]+sub+s[en:]

def patch_atomic(s):
    if TAG in s:return s
    s='/* '+TAG+' */\n#include <linux/a52_446w_handoff_b.h>\n#include <linux/a52_446v_display_trace.h>\n'+s
    begin=s.index("static void complete_commit(struct msm_commit *c)")
    end=s.index("\n}",begin)+2
    sub=s[begin:end]
    needle='''	drm_atomic_state_put(state);'''
    rep=r'''	/* SDE complete_commit has now released splash resources, if eligible.
	 * Only a committed full DRM modeset may close the once-only gate.
	 */
	if (a52w_handoff_selected() && !a52w_handoff_done()) {
		struct drm_crtc *a52w_crtc;
		struct drm_crtc_state *a52w_cs;
		int a52w_i;
		for_each_new_crtc_in_state(state,a52w_crtc,a52w_cs,a52w_i) {
			if (a52w_crtc->base.id==107 &&
			    a52w_cs->active && a52w_cs->mode_changed &&
			    a52w_cs->connectors_changed) {
				a52w_handoff_finish(dev);
				break;
			}
		}
	}
	drm_atomic_state_put(state);'''
    sub=one(sub,needle,rep,"after complete_commit")
    s=s[:begin]+sub+s[end:]
    # Print high-level lifecycle in MSM commit, without altering bridge calls.
    sub_start=s.index("static void complete_commit(struct msm_commit *c)")
    sub_end=s.index("\n}",sub_start)+2
    sub=s[sub_start:sub_end]
    for needle,label in [("drm_atomic_helper_commit_modeset_disables(dev, state);","disable"),
                         ("msm_atomic_helper_commit_modeset_enables(dev, state);","enable"),
                         ("kms->funcs->complete_commit(kms, state);","sde_complete")]:
        replacement=f'''if (a52w_handoff_selected() && !a52w_handoff_done())
		pr_info("P446W HANDOFF phase={label}-enter\\n");
	{needle}
	if (a52w_handoff_selected() && !a52w_handoff_done())
		pr_info("P446W HANDOFF phase={label}-exit\\n");'''
        sub=one(sub,needle,replacement,"commit phase "+label)
    s=s[:sub_start]+sub+s[sub_end:]
    # Tag the unused generic atomic commit tail for completeness.
    return s

def check(root):
    checks={DRV:(TAG,"P446W HANDOFF begin","P446W HANDOFF end",
                 "drm_atomic_get_crtc_state","drm_atomic_get_connector_state",
                 "P446W HANDOFF defer","P446W_HANDOFF_POST_COMPLETE"),
            ATOM:(TAG,"P446W HANDOFF phase=disable-enter",
                  "P446W HANDOFF phase=enable-enter","a52w_handoff_finish"),
            HDR:("a52w_handoff_done","a52w_handoff_finish")}
    for p,words in checks.items():
        s=(root/p).read_text()
        for word in words:
            if word not in s:raise RuntimeError("446W-B missing "+word+" in "+str(p))
    print("446W-B actual-frame first HWC full modeset and end/snapshot diagnostics check PASS")
def main():
    a=argparse.ArgumentParser();a.add_argument("--root",type=Path,required=True)
    a.add_argument("--check-only",action="store_true");x=a.parse_args()
    if not x.check_only:
        f=x.root/DRV;f.write_text(patch_drv(f.read_text()))
        f=x.root/ATOM;f.write_text(patch_atomic(f.read_text()))
        (x.root/HDR).write_text('''/* 446W-B, first HWC full modeset transaction, diagnostics */
#ifndef _A52_446W_HANDOFF_B_H
#define _A52_446W_HANDOFF_B_H
struct drm_device;
bool a52w_handoff_selected(void);
bool a52w_handoff_done(void);
void a52w_handoff_finish(struct drm_device *dev);
#endif
''')
    check(x.root)
if __name__=="__main__":main()
