#!/usr/bin/env python3
"""P447C compile noisy per-commit diagnostics behind default-OFF verbosity.

Do not modify functional display/GPU paths, error diagnostics, first NullCommit
HWC request ring buffer, or 446AG GSI read/summary logging.
"""
import argparse,re
from pathlib import Path

TARGETS={
 "drivers/gpu/drm/drm_atomic_uapi.c":(
   "P446U PROP seq=", "P446U SET_FB seq="),
 "drivers/a52_display/msm/msm_atomic.c":(
   "P446U PLANE91 seq=", "P446T FRAME_EARLY seq=",
   "P446S FRAME seq=", "P446U FRAME_JOIN seq="),
 "drivers/a52_display/msm/msm_drv.c":("P446U MSM_CHECK seq=",),
 "drivers/a52_display/msm/sde/sde_crtc.c":("P446U SDE_CRTC_CHECK seq=",),
 "drivers/a52_display/msm/sde/sde_plane.c":("P446U PREPARE_FB seq=",),
 "drivers/gpu/drm/drm_mode_object.c":("P446V READ_PROP ts=",),
 "drivers/gpu/drm/drm_property.c":(
   "P446V GETPROPERTY ts=", "P446V GETPROPERTY_VALUE ts=",
   "P446V GETPROPERTY_ENUM ts="),
 "drivers/regulator/core.c":(
   "P446S GPU_CX_CONSUMER phase=", "P446S GPU_CX_VOTES phase="),
}
TAG="A52_PHASE447C_QUIET_PER_COMMIT_V1"
CALL=re.compile(r"\b(pr_info|pr_warn|dev_info|dev_warn)\s*\(")

def patch(root,check_only):
    changed=0
    for rel,markers in TARGETS.items():
        p=root/rel
        if not p.is_file():raise RuntimeError("P447C required trace source missing "+rel)
        src=p.read_text()
        if TAG in src:
            new=src
        elif check_only:
            raise RuntimeError("P447C trace gate not applied: "+rel)
        else:
            lines=src.splitlines(keepends=True)
            seen=set()
            for i,line in enumerate(lines):
                for m in markers:
                    if m not in line:continue
                    match=CALL.search(line)
                    if not match:continue  # this particular log macro may be multiline
                    prefix=line[:match.start()]
                    guard="if (a52_447c_trace_verbose()) "
                    lines[i]=prefix+guard+line[match.start():]
                    seen.add(m)
                    changed+=1
                    break
            if not seen:
                raise RuntimeError("P447C no direct trace log anchors in "+rel)
            new="".join(lines)
            insert=new.find("#include ")
            if insert<0:raise RuntimeError("P447C header anchor missing "+rel)
            new=new[:insert]+"#include <linux/a52_phase447.h> /* "+TAG+" */\n"+new[insert:]
            p.write_text(new)
        if TAG not in new or "if (a52_447c_trace_verbose())" not in new:
            raise RuntimeError("P447C trace guard absent "+rel)
    print("P447C TRACE QUIET PASS: per-commit log macros gated by a52_447c_trace_verbose(); NullCommit ring untouched",changed)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    patch(a.root,a.check_only)
