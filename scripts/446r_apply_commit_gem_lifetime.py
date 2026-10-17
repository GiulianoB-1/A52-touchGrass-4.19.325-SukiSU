#!/usr/bin/env python3
"""Phase446R: bounded per-commit plane metadata and 5.10 GEM-free contract.

The global DRM core invokes .gem_free_object_unlocked without holding
dev->struct_mutex (Android 5.10). Qualcomm 4.19 still WARNs that the caller
must hold it. Remove ONLY that obsolete WARN; keep is_active() validation.
Never add a recursive struct_mutex lock around GEM destruction.

Does not alter packet encoding, ION flags, the atomic commit behavior,
hardware power transitions, or Android service startup.
"""
from pathlib import Path
import argparse

TAG="A52_PHASE446R_COMMIT_GEM_LIFETIME_V1"
MSM="drivers/a52_display/msm/"

def replace_once(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"446R {name}: expected one anchor, found {n}")
    return s.replace(a,b,1)

TRACE=r'''
/* A52_PHASE446R_COMMIT_GEM_LIFETIME_V1
 * One line per selected submitted DRM commit. Plane states below are the
 * current *post-swap* composition, not only planes changed in this commit.
 * Black pixel content cannot be inferred from these metadata fields.
 */
static atomic_t p446r_commit_seq = ATOMIC_INIT(0);
static void p446r_commit_snapshot(struct drm_device *dev,
                int result, bool nonblock, const char *stage)
{
    struct drm_plane *plane;
    struct drm_crtc *crtc;
    struct drm_framebuffer *fb;
    u32 fb_id[4]={0}, format[4]={0}, plane_id[4]={0};
    int n=atomic_inc_return(&p446r_commit_seq);
    int total=0, active=0, ncrtcs=0;
    bool print;
    drm_for_each_crtc(crtc,dev) {
        if (crtc->state && crtc->state->active)
            ncrtcs++;
    }
    drm_for_each_plane(plane,dev) {
        total++;
        if (!plane->state || !plane->state->crtc ||
            !plane->state->fb)
            continue;
        fb=plane->state->fb;
        if (active<4) {
            plane_id[active]=plane->base.id;
            fb_id[active]=fb->base.id;
            format[active]=fb->format ? fb->format->format : 0;
        }
        active++;
    }
    print=(n <= 96) || (n%15 == 0) || result;
    if (print)
        pr_info("P446R COMMIT n=%d step=%s rc=%d nb=%u crtcs=%d active_planes=%d planes_total=%d p0=%u/fb%u/fmt%08x p1=%u/fb%u/fmt%08x p2=%u/fb%u/fmt%08x p3=%u/fb%u/fmt%08x\n",
            n,stage,result,nonblock,ncrtcs,active,total,
            plane_id[0],fb_id[0],format[0],
            plane_id[1],fb_id[1],format[1],
            plane_id[2],fb_id[2],format[2],
            plane_id[3],fb_id[3],format[3]);
}
'''
def patch_atomic(s):
    if TAG in s:return s
    s=replace_once(s,"int msm_atomic_commit(struct drm_device *dev,",
                   TRACE+"\nint msm_atomic_commit(struct drm_device *dev,",
                   "commit helper")
    # Deliberately log the new composition after drm_atomic_helper_swap_state.
    s=replace_once(s,
        "\tA52_R210_REC(c, \"swap_state exit\");",
        "\tA52_R210_REC(c, \"swap_state exit\");\n"+
        '\tp446r_commit_snapshot(dev, 0, nonblock, "swapped");',
        "post-swap state")
    s=replace_once(s,
        "\tA52_R210_REC_ID(a52_trace, a52_trace_index, \"commit error rc=%d\", ret);",
        "\tA52_R210_REC_ID(a52_trace, a52_trace_index, \"commit error rc=%d\", ret);\n"+
        '\tp446r_commit_snapshot(dev, ret, nonblock, "error");',
        "commit failure")
    # The only earlier return is the plane preparation failure.
    s=replace_once(s,
        "\t\ta52_ackfr_record(\"P276 296C x r=%d q=1\", ret);",
        "\t\ta52_ackfr_record(\"P276 296C x r=%d q=1\", ret);\n"+
        '\t\tp446r_commit_snapshot(dev, ret, nonblock, "prepare");',
        "prepare failure")
    return s

def patch_gem(s):
    if TAG in s:return s
    if "void msm_gem_free_object(struct drm_gem_object *obj)" not in s:
        raise RuntimeError("446R missing Qualcomm GEM destructor")
    return replace_once(s,
        "\tWARN_ON(!mutex_is_locked(&dev->struct_mutex));\n\n"
        "\t/* object should not be on active list: */",
        '''    /* A52_PHASE446R_COMMIT_GEM_LIFETIME_V1
     * The Android 5.10 DRM callback is gem_free_object_unlocked.
     * Unlike Qualcomm 4.19 it does not promise struct_mutex is held.
     * Do not WARN on that obsolete lock precondition. The active-list
     * warning immediately below remains a real lifetime invariant.
     */
    {
        static atomic_t seen = ATOMIC_INIT(0);
        int n = atomic_inc_return(&seen);
        if (n <= 16)
            pr_info("P446R GEM_FREE n=%d legacy_mutex=%u active=%u imported=%u\\n",
                n, mutex_is_locked(&dev->struct_mutex),
                is_active(msm_obj), !!obj->import_attach);
    }

    /* object should not be on active list: */''',
        "obsolete mutex-only WARN")

def patch(root):
    main=(root/(MSM+"msm_atomic.c"))
    gem=(root/(MSM+"msm_gem.c"))
    drv=(root/(MSM+"msm_drv.c"))
    if ".gem_free_object_unlocked = msm_gem_free_object" not in drv.read_text():
        raise RuntimeError("446R requires 5.10 unlocked GEM callback")
    patches=[(main,patch_atomic(main.read_text())),(gem,patch_gem(gem.read_text()))]
    for path,new in patches:path.write_text(new)

def audit(root):
    atomic=(root/(MSM+"msm_atomic.c")).read_text()
    gem=(root/(MSM+"msm_gem.c")).read_text()
    drv=(root/(MSM+"msm_drv.c")).read_text()
    for x in (TAG,"P446R COMMIT","p446r_commit_snapshot(dev, 0"):
        if x not in atomic:raise RuntimeError("446R atomic audit missing "+x)
    if TAG not in gem or "P446R GEM_FREE" not in gem:
        raise RuntimeError("446R GEM free audit missing")
    start=gem.index("void msm_gem_free_object(struct drm_gem_object *obj)")
    end=gem.index("\n}\n",start)
    free_body=gem[start:end]
    if "WARN_ON(!mutex_is_locked(&dev->struct_mutex));" in free_body:
        raise RuntimeError("446R stale 4.19 GEM-free lock assertion remains")
    if "WARN_ON(is_active(msm_obj));" not in gem:
        raise RuntimeError("446R active list validation lost")
    if ".gem_free_object_unlocked = msm_gem_free_object" not in drv:
        raise RuntimeError("446R 5.10 GEM destructor callback changed")
    print("P446R AUDIT PASS: post-swap layer/FB trace, 5.10 unlocked GEM free contract, active-list WARN retained")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:patch(a.root)
    audit(a.root)

if __name__=="__main__":main()
