#!/usr/bin/env python3
"""446S observational DRM tracing: 300 consecutive commits after 85 s.

Never forces a plane or changes the display power or commit outcome.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE446S_UNSAMPLED_COMMIT_V1"
IO_TAG="A52_PHASE446S_ATOMIC_IOCTL_TRACE_V1"
PATH=Path("drivers/a52_display/msm/msm_atomic.c")
UAPI=Path("drivers/gpu/drm/drm_atomic_uapi.c")
def once(s,a,b,why):
    n=s.count(a)
    if n != 1:raise RuntimeError("446S frames %s anchor=%d" %(why,n))
    return s.replace(a,b,1)
def patch_atomic(s):
    if TAG in s:return s
    s=once(s, "/* A52_PHASE446R_COMMIT_GEM_LIFETIME_V1",
           "#include <linux/sched.h>\n#include <linux/ktime.h>\n\n/* "+TAG+" */\n"
           "static atomic_t p446s_frame_seq = ATOMIC_INIT(0);\n"
           "static atomic_t p446s_bucket_count = ATOMIC_INIT(0);\n"
           "static atomic_t p446s_bucket_zeros = ATOMIC_INIT(0);\n"
           "static atomic_t p446s_bucket = ATOMIC_INIT(-1);\n\n"
           "/* A52_PHASE446R_COMMIT_GEM_LIFETIME_V1",
           "prefix")
    s=once(s,"int total=0, active=0, ncrtcs=0;",
           "int total=0, active=0, ncrtcs=0, n_enabled=0;\n"
           "    u64 now_ns=ktime_get_boottime_ns();\n"
           "    int win=-1, bucket;\n",
           "snapshot locals")
    s=once(s,"if (crtc->state && crtc->state->active)\n            ncrtcs++;",
           "if (crtc->state && crtc->state->active)\n            ncrtcs++;\n"
           "        if (crtc->state && crtc->state->enable)\n            n_enabled++;",
           "crtc enable")
    anchor="    print=(n <= 96) || (n%15 == 0) || result;"
    extra=r'''    /* 85-SECOND START. Log consecutive 300 commits without modulo sampling. */
    if (now_ns >= 85ULL * 1000000000ULL)
        win=atomic_inc_return(&p446s_frame_seq);
    if (win > 0 && win <= 300) {
        pr_info("P446S FRAME seq=%d n=%d t_ns=%llu pid=%d comm=%s rc=%d nonblock=%u crtc_active=%d crtc_enable=%d active_planes=%d p0=%u/fb%u/fmt%08x p1=%u/fb%u/fmt%08x p2=%u/fb%u/fmt%08x p3=%u/fb%u/fmt%08x\n",
            win,n,(unsigned long long)now_ns,task_pid_nr(current),current->comm,
            result,nonblock,ncrtcs,n_enabled,active,
            plane_id[0],fb_id[0],format[0],
            plane_id[1],fb_id[1],format[1],
            plane_id[2],fb_id[2],format[2],
            plane_id[3],fb_id[3],format[3]);
    }
    if (win > 0) {
        int prev;
        bucket = (int)(now_ns / 5000000000ULL);
        atomic_inc(&p446s_bucket_count);
        if (!active) atomic_inc(&p446s_bucket_zeros);
        prev=atomic_read(&p446s_bucket);
        if (prev < 0) {
            atomic_cmpxchg(&p446s_bucket,prev,bucket);
        } else if (prev != bucket &&
                   atomic_cmpxchg(&p446s_bucket,prev,bucket)==prev) {
            int all=atomic_xchg(&p446s_bucket_count,0);
            int empty=atomic_xchg(&p446s_bucket_zeros,0);
            pr_info("P446S FRAME_SUMMARY 5s_bucket=%d commits=%d zero_planes=%d zero_per_second_x100=%d\n",
                prev,all,empty,empty*20);
        }
    }
    print=(n <= 96) || (n%15 == 0) || result;'''
    s=once(s,anchor,extra,"frame window")
    return s
def patch_uapi(s):
    if IO_TAG in s:return s
    a="int drm_mode_atomic_ioctl(struct drm_device *dev,"
    b="struct drm_mode_atomic *arg = data;"
    if a not in s or b not in s:raise RuntimeError("446S missing DRM atomic ioctl")
    s=once(s,b,
           "/* "+IO_TAG+" */\n\t"+b,"ioctl marker")
    # Insert immediately after declarations before first feature check.
    sig="\t/* disallow for drivers not supporting atomic: */"
    inject=r'''	/* A52_PHASE446S_ATOMIC_IOCTL_TRACE_V1
	 * Caller/flags before commit; correlate using pid+timestamp with FRAME.
	 * Do not modify userspace state or ioctl return value.
	 */
	if (ktime_get_boottime_seconds() >= 85) {
		static atomic_t captured = ATOMIC_INIT(0);
		int seq = atomic_inc_return(&captured);
		if (seq <= 350)
			pr_info("P446S IOCTL seq=%d t_ns=%llu pid=%d comm=%s flags=%#x count_objs=%u TEST_ONLY=%u ALLOW_MODESET=%u NONBLOCK=%u\n",
				seq,(unsigned long long)ktime_get_boottime_ns(),
				task_pid_nr(current),current->comm,
				arg->flags,arg->count_objs,
				!!(arg->flags & DRM_MODE_ATOMIC_TEST_ONLY),
				!!(arg->flags & DRM_MODE_ATOMIC_ALLOW_MODESET),
				!!(arg->flags & DRM_MODE_ATOMIC_NONBLOCK));
	}
'''
    s=once(s,sig,inject+sig,"ioctl diagnostics")
    inc="#include <linux/"
    if inc not in s:raise RuntimeError("446S missing linux include")
    s=s.replace(inc, "#include <linux/ktime.h>\n#include <linux/sched.h>\n"+inc,1)
    return s
def check(root):
    a=(root/PATH).read_text()
    u=(root/UAPI).read_text()
    for mark in (TAG,"P446S FRAME seq=","P446S FRAME_SUMMARY","win <= 300"):
        if mark not in a:raise RuntimeError("446S atomic missing "+mark)
    for mark in (IO_TAG,"P446S IOCTL seq=","DRM_MODE_ATOMIC_NONBLOCK"):
        if mark not in u:raise RuntimeError("446S ioctl missing "+mark)
    print("P446S frames PASS: 300 consecutive post-85s commits, caller, ioctl flags")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    args=p.parse_args()
    if not args.check_only:
        a=args.root/PATH;u=args.root/UAPI
        a.write_text(patch_atomic(a.read_text()))
        u.write_text(patch_uapi(u.read_text()))
    check(args.root)
if __name__=="__main__":main()
