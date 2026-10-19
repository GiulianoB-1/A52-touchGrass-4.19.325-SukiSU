#!/usr/bin/env python3
"""446W opt-in root-handoff experiment (OFF by default).

At FIRST eligible HWC atomic on a splash-enabled boot, request a proper
modeset instead of manually patching Samsung/DSI state. TG's existing
commit_modeset_enables -> DSI bridge pre_enable -> dsi_display_enable()
then owns all panel init/DSI splash cleanup, while complete_commit releases
SDE splash resources. Guard on CRTC107+connector32 already paired, else
defer the experiment without changing any state. Never modify CRTC enable,
panel_initialized or splash memory ownership directly.
"""
import argparse
from pathlib import Path
P=Path("drivers/a52_display/msm/msm_drv.c")
TAG="A52_PHASE446W_SPLASH_HANDOFF_OPTION_B_V1"
def one(s,old,new,label):
    n=s.count(old)
    if n!=1:raise RuntimeError("P446W handoff "+label+" matches="+str(n))
    return s.replace(old,new,1)
def patch(s):
    if TAG in s:return s
    s='#include <linux/a52_446w_splash.h>\n#include <linux/atomic.h>\n#include <linux/sched.h>\n#include <linux/string.h>\n'+s
    anchor="int msm_atomic_check(struct drm_device *dev,"
    helper=r'''/* A52_PHASE446W_SPLASH_HANDOFF_OPTION_B_V1.
 * This is deliberately disabled on 446W A/B test (cold=1, handoff=0).
 * Do NOT forcibly clear SDE/DSI/encoder flags independently: an atomic
 * modeset is the only safe way to perform existing callbacks in order.
 */
static atomic_t a52w_handoff_requested=ATOMIC_INIT(0);
static void a52w_try_handoff_modeset(struct drm_atomic_state *state)
{
	struct drm_crtc *crtc;
	struct drm_crtc_state *cs;
	struct drm_connector *conn;
	struct drm_connector_state *co;
	int i, j;
	bool joined;

	if (!a52w_handoff_selected() ||
	    atomic_read(&a52w_handoff_requested))
		return;
	if (!strnstr(current->comm, "HwBinder", TASK_COMM_LEN) &&
	    !strnstr(current->comm, "composer", TASK_COMM_LEN))
		return;
	for_each_new_crtc_in_state(state, crtc, cs, i) {
		if (crtc->base.id!=107 || !cs->active || !cs->enable)
			continue;
		joined=false;
		for_each_new_connector_in_state(state,conn,co,j) {
			if (conn->base.id==32 && co->crtc==crtc) {
				joined=true;
				break;
			}
		}
		if (!joined) {
			pr_info_ratelimited("P446W HANDOFF_OPTION_B deferred: CRTC107 not paired with connector32 in incoming atomic\n");
			return;
		}
		if (atomic_cmpxchg(&a52w_handoff_requested,0,1))
			return;
		cs->mode_changed=true;
		cs->connectors_changed=true;
		pr_info("P446W HANDOFF_OPTION_B request full modeset on first joined HWC atomic crtc=%u connector=%u (TG bridge/DSI/SDE cleanup callbacks); no manual flag writes\n",
			crtc->base.id,conn->base.id);
		return;
	}
}
'''
    s=one(s,anchor,helper+anchor,"insertion")
    fnstart=s.index(anchor)
    fnend=s.index("\n}",fnstart)+2
    part=s[fnstart:fnend]
    part=one(part,'\tpriv = dev->dev_private;',
             '\tpriv = dev->dev_private;\n\ta52w_try_handoff_modeset(state);',
             "first HWC pre-check")
    return s[:fnstart]+part+s[fnend:]
def check(root):
    s=(root/P).read_text()
    # 446W-B actual-frame v2 replaces the entire option-B v1 helper.
    # Both share this compatibility check in mandatory strict audit.
    if "A52_PHASE446W_HANDOFF_B_ACTUAL_FRAME_V2" in s:
        for x in ("P446W HANDOFF begin","P446W HANDOFF end",
                  "drm_atomic_get_crtc_state",
                  "drm_atomic_get_connector_state",
                  "a52w_rc=a52w_try_handoff_modeset(state)"):
            if x not in s:
                raise RuntimeError("446W-B v2 handoff missing "+x)
        print("P446W handoff B CHECK PASS: v2 real-HWC atomically acquired states")
        return
    for x in [TAG,"a52w_try_handoff_modeset(state);",
              "P446W HANDOFF_OPTION_B request full modeset",
              "co->crtc==crtc"]:
        if x not in s:raise RuntimeError("446W handoff missing "+x)
    print("P446W HANDOFF B CHECK PASS: default disabled, guarded by HWC/connector join, existing bridge callbacks, no panel flag hacks")
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true");a=p.parse_args()
    if not a.check_only:
        f=a.root/P;f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
