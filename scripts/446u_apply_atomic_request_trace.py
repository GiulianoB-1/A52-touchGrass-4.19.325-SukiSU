#!/usr/bin/env python3
"""446U Part A: observational atomic request/plane-91 and brightness trace.

No DRM or SDE state transition is modified. Data is joined by the 446U ioctl
request id (req), also emitted next to the original 446S frame sequence.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE446U_ATOMIC_REQUEST_PLANE91_V1"
HDR=Path("include/linux/a52_446u_trace.h")
UAPI=Path("drivers/gpu/drm/drm_atomic_uapi.c")
ATOMIC=Path("drivers/a52_display/msm/msm_atomic.c")
SDEPLANE=Path("drivers/a52_display/msm/sde/sde_plane.c")
SDECRTC=Path("drivers/a52_display/msm/sde/sde_crtc.c")
DSIPANEL=Path("drivers/a52_display/msm/dsi/dsi_panel.c")

def once(src,old,new,label):
    n=src.count(old)
    if n!=1: raise RuntimeError(f"446U {label}: expected 1 anchor, found {n}")
    return src.replace(old,new,1)

def patch_uapi(s):
    if TAG in s:return s
    marker="int drm_mode_atomic_ioctl(struct drm_device *dev,"
    # One 446U request id per ioctl; 256 task-index slots for synchronous
    # msm_atomic_commit callbacks, never retained after ioctl completion.
    defs=r'''
/* A52_PHASE446U_ATOMIC_REQUEST_PLANE91_V1
 * The ioctl thread owns its slot until the call returns. No changing
 * DRM objects, properties or commit outcomes. Slot collision is detected.
 */
static atomic_t a52u_ioctl_seq = ATOMIC_INIT(0);
struct a52u_slot { pid_t pid; int req; };
static struct a52u_slot a52u_slots[1024];
static DEFINE_SPINLOCK(a52u_slot_lock);
int a52_446u_request_id(void)
{
	unsigned long flags;
	unsigned int idx = (unsigned int)task_pid_nr(current) & 1023;
	int req = 0;
	spin_lock_irqsave(&a52u_slot_lock, flags);
	if (a52u_slots[idx].pid == task_pid_nr(current))
		req = a52u_slots[idx].req;
	spin_unlock_irqrestore(&a52u_slot_lock, flags);
	return req;
}
EXPORT_SYMBOL_GPL(a52_446u_request_id);
static void a52u_req_set(int req)
{
	unsigned long flags;
	unsigned int idx = (unsigned int)task_pid_nr(current) & 1023;
	spin_lock_irqsave(&a52u_slot_lock, flags);
	a52u_slots[idx].pid = req ? task_pid_nr(current) : 0;
	a52u_slots[idx].req = req;
	spin_unlock_irqrestore(&a52u_slot_lock, flags);
}
'''
    s=once(s,marker,defs+"\n"+marker,"ioctl defs")
    s=once(s,"int ret = 0;\n\tunsigned int i, j, num_fences;",
           "int ret = 0;\n\tunsigned int i, j, num_fences;\n\tint a52u_req = 0;",
           "ioctl local")
    # Assign after initial ioctl validation/state allocation, before retry.
    anchor="retry:\n\tcopied_objs = 0;"
    s=once(s,anchor,'''if (ktime_get_boottime_ns() >= 85ULL * 1000000000ULL) {
		a52u_req = atomic_inc_return(&a52u_ioctl_seq);
		if (a52u_req <= 300) {
			a52u_req_set(a52u_req);
			pr_info("P446U REQUEST seq=%d pid=%d comm=%s ts=%llu objs=%u flags=%#x\n",
				a52u_req, task_pid_nr(current), current->comm,
				(unsigned long long)ktime_get_boottime_ns(),
				arg->count_objs, arg->flags);
		}
	}

'''+anchor,"request begin")
    needle="ret = drm_atomic_set_property(state, file_priv,"
    # Only log properties after successful get_user/copy_from_user.
    injected='''if (a52u_req && a52u_req <= 300)
				pr_info("P446U PROP seq=%d pid=%d obj=%u type=%#x prop=%s val=%llu hex=%#llx\n",
					a52u_req, task_pid_nr(current), obj_id, obj->type,
					prop->name, (unsigned long long)prop_value,
					(unsigned long long)prop_value);

			'''+needle
    s=once(s,needle,injected,"all atomic property values")
    needle="drm_atomic_state_put(state);"
    s=once(s,needle,"if (a52u_req && a52u_req <= 300)\n\t\ta52u_req_set(0);\n\n\t"+needle,"clear request id")
    for func,needle,inject in (
        ("FB","void\ndrm_atomic_set_fb_for_plane(struct drm_plane_state *plane_state,\n\t\t\t    struct drm_framebuffer *fb)\n{",
         '''
	if (ktime_get_boottime_ns() >= 85ULL * 1000000000ULL &&
	    plane_state && plane_state->plane &&
	    plane_state->plane->base.id == 91)
		pr_info("P446U SET_FB seq=%d plane=91 old_fb=%u new_fb=%u crtc=%u caller=%pS\n",
			a52_446u_request_id(),
			plane_state->fb ? plane_state->fb->base.id : 0,
			fb ? fb->base.id : 0,
			plane_state->crtc ? plane_state->crtc->base.id : 0,
			__builtin_return_address(0));
'''),
        ("CRTC","int\ndrm_atomic_set_crtc_for_plane(struct drm_plane_state *plane_state,\n\t\t\t      struct drm_crtc *crtc)\n{",
         '''
	if (ktime_get_boottime_ns() >= 85ULL * 1000000000ULL &&
	    plane_state && plane_state->plane &&
	    plane_state->plane->base.id == 91)
		pr_info("P446U SET_CRTC seq=%d plane=91 fb=%u old_crtc=%u new_crtc=%u caller=%pS\n",
			a52_446u_request_id(),
			plane_state->fb ? plane_state->fb->base.id : 0,
			plane_state->crtc ? plane_state->crtc->base.id : 0,
			crtc ? crtc->base.id : 0,
			__builtin_return_address(0));
''')):
        if s.count(needle)!=1:
            # Some 5.10 ACKs have a different indentation. Same semantic
            # anchor, replacing only the opening brace after exact signature.
            import re
            pattern=r'(?m)^(?:void|int)\s*\n'+re.escape("drm_atomic_set_"+("fb" if func=="FB" else "crtc")+"_for_plane")+r'\([^;]*?\)\s*\{'
            hits=list(re.finditer(pattern,s,re.S))
            if len(hits)!=1:raise RuntimeError("446U could not locate DRM "+func+" setter")
            pos=hits[0].end()
            s=s[:pos]+inject+s[pos:]
        else:s=once(s,needle,needle+inject,"set "+func)
    if '#include <linux/a52_446u_trace.h>' not in s:
        s='#include <linux/a52_446u_trace.h>\n#include <linux/ktime.h>\n#include <linux/sched.h>\n#include <linux/spinlock.h>\n'+s
    return s

def patch_msm(s):
    if TAG in s:return s
    # 446S already introduced win and p446r_commit_snapshot at swap.
    anchor='    if (win > 0 && win <= 300) {'
    s=once(s,anchor,'''
    if (win > 0 && win <= 300)
        pr_info("P446U FRAME_JOIN seq=%d req=%d pid=%d\n",
            win, a52_446u_request_id(), task_pid_nr(current));
'''+anchor,"P446S FRAME join")
    begin='\tA52_R210_REC(c, "swap_state exit");'
    # Phase446R inserts p446r_commit_snapshot directly after this mark.
    assert begin in s
    swap=s.find('drm_atomic_helper_swap_state(state',s.find('int msm_atomic_commit('))
    if swap<0:raise RuntimeError("446U swap anchor missing")
    # Capture immediately before and after the REAL state swap, not just
    # changed planes in state->planes[].
    helper=r'''
/* A52_PHASE446U_ATOMIC_REQUEST_PLANE91_V1 */
static void a52u_snapshot_plane91(struct drm_device *dev, const char *when)
{
	struct drm_plane *plane;
	if (ktime_get_boottime_ns() < 85ULL * 1000000000ULL)
		return;
	drm_for_each_plane(plane, dev) {
		struct drm_plane_state *s;
		if (plane->base.id != 91)
			continue;
		s = plane->state;
		pr_info("P446U PLANE91 seq=%d phase=%s ts=%llu fb=%u crtc=%u visible=%u\n",
			a52_446u_request_id(), when,
			(unsigned long long)ktime_get_boottime_ns(),
			s && s->fb ? s->fb->base.id : 0,
			s && s->crtc ? s->crtc->base.id : 0,
			s ? s->visible : 0);
	}
}
'''
    s=once(s,"int msm_atomic_commit(struct drm_device *dev,",helper+"\nint msm_atomic_commit(struct drm_device *dev,","msm helper")
    # Require the exact matched around swap from Phase210 lineage.
    anchor='A52_R210_REC(c, "swap_state exit");'
    s=once(s,anchor,anchor+'\n\ta52u_snapshot_plane91(dev, "after");',"post swap")
    needle='WARN_ON(drm_atomic_helper_swap_state(state, false) < 0);'
    if needle not in s:
        raise RuntimeError("446U swap expression changed; inspect MSM atomic path")
    s=once(s,needle,'a52u_snapshot_plane91(dev, "before");\n\t'+needle,"pre swap")
    s='#include <linux/a52_446u_trace.h>\n'+s
    return s

def patch_sde_plane(s):
    if TAG in s:return s
    # Observational checks: capture arguments plus return code at exit.
    chk='static int sde_plane_atomic_check(struct drm_plane *plane,'
    prep='static int sde_plane_prepare_fb(struct drm_plane *plane,'
    if chk not in s or prep not in s:raise RuntimeError("446U SDE plane hooks absent")
    start=s.index(chk);end=s.index('\n}',start)+2
    sub=s[start:end]
    sub=once(sub,"\treturn ret;\n}",
        '''	if (plane && plane->base.id == 91 && ktime_get_boottime_ns() >= 85ULL * 1000000000ULL)
		pr_info("P446U SDE_PLANE_CHECK seq=%d p=91 rc=%d fb=%u crtc=%u visible=%u\n",
			a52_446u_request_id(), ret,
			state && state->fb ? state->fb->base.id : 0,
			state && state->crtc ? state->crtc->base.id : 0,
			state ? state->visible : 0);
	return ret;
}''',"sde_plane check exit")
    s=s[:start]+sub+s[end:]
    start=s.index(prep);end=s.index('\n}',start)+2;sub=s[start:end]
    anchor='struct drm_framebuffer *fb = new_state->fb;'
    sub=once(sub,anchor,anchor+'''
	if (plane && plane->base.id == 91 && ktime_get_boottime_ns() >= 85ULL * 1000000000ULL)
		pr_info("P446U PREPARE_FB seq=%d plane=91 fb=%u crtc=%u\n",
			a52_446u_request_id(), fb ? fb->base.id : 0,
			new_state->crtc ? new_state->crtc->base.id : 0);
''',"prepare_fb")
    s=s[:start]+sub+s[end:]
    return '#include <linux/a52_446u_trace.h>\n#include <linux/ktime.h>\n'+s

def patch_sde_crtc(s):
    if TAG in s:return s
    sig='static int sde_crtc_atomic_check(struct drm_crtc *crtc,'
    if sig not in s:raise RuntimeError("446U SDE CRTC atomic check absent")
    p=s.index(sig);end=s.index('\n}',p)+2;part=s[p:end]
    part=once(part,"end:\n\tkfree(pstates);",
        '''end:
	if (ktime_get_boottime_ns() >= 85ULL * 1000000000ULL)
		pr_info("P446U SDE_CRTC_CHECK seq=%d crtc=%u rc=%d active=%u enable=%u\n",
			a52_446u_request_id(), crtc ? crtc->base.id : 0,
			rc, state ? state->active : 0, state ? state->enable : 0);
	kfree(pstates);''',"sde_crtc check")
    s=s[:p]+part+s[end:]
    return '#include <linux/a52_446u_trace.h>\n#include <linux/ktime.h>\n'+s

def patch_panel(s):
    if "A52_PHASE446U_BRIGHTNESS_TRACE_V1" in s:return s
    sig="int dsi_panel_set_backlight(struct dsi_panel *panel, u32 bl_lvl)"
    if sig not in s:raise RuntimeError("446U Samsung panel brightness entry missing")
    anchor="struct dsi_backlight_config *bl = &panel->bl_config;"
    snippet='''	/* A52_PHASE446U_BRIGHTNESS_TRACE_V1: no brightness override */
	if (ktime_get_boottime_ns() >= 85ULL * 1000000000ULL)
		pr_info("P446U BRIGHTNESS ts=%llu pid=%d comm=%s level=%u\n",
			(unsigned long long)ktime_get_boottime_ns(),
			task_pid_nr(current), current->comm, bl_lvl);
'''
    x=s.index(sig)
    y=s.find("\n}",x)
    if y<0:raise RuntimeError("446U brightness function incomplete")
    body=s[x:y]
    if body.count(anchor)!=1:raise RuntimeError("446U brightness declaration anchor count")
    body=body.replace(anchor,anchor+"\n"+snippet,1)
    s=s[:x]+body+s[y:]
    return '#include <linux/ktime.h>\n#include <linux/sched.h>\n'+s

def check(root):
    checks={
        HDR:("a52_446u_request_id",),
        UAPI:(TAG,"P446U PROP seq=","P446U REQUEST seq=","P446U SET_FB","P446U SET_CRTC"),
        ATOMIC:(TAG,"P446U PLANE91 seq=","P446U FRAME_JOIN seq="),
        SDEPLANE:("P446U SDE_PLANE_CHECK","P446U PREPARE_FB"),
        SDECRTC:("P446U SDE_CRTC_CHECK",),
        DSIPANEL:("P446U BRIGHTNESS",)
    }
    for path,words in checks.items():
        txt=(root/path).read_text()
        for w in words:
            if w not in txt:raise RuntimeError(f"446U missing {w} in {path}")
    print("P446U A AUDIT PASS: property names and all values, atomic join, plane91 swap, setters, SDE checks and brightness")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    root=a.root
    if not a.check_only:
        (root/HDR).write_text('''/* A52_PHASE446U_ATOMIC_REQUEST_PLANE91_V1 */
#ifndef _A52_446U_TRACE_H
#define _A52_446U_TRACE_H
int a52_446u_request_id(void);
#endif
''')
        for p,fn in ((UAPI,patch_uapi),(ATOMIC,patch_msm),
                     (SDEPLANE,patch_sde_plane),(SDECRTC,patch_sde_crtc),
                     (DSIPANEL,patch_panel)):
            f=root/p
            f.write_text(fn(f.read_text()))
    check(root)
if __name__=="__main__":
    main()
