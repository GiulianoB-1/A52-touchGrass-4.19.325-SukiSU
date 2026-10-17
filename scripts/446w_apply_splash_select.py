#!/usr/bin/env python3
"""Phase446W: safe opt-out of bootloader SDE continuous splash.

Switch A: a52.446w_cold=1 (default), cold first KMS enable. Do NOT free the
reserved continuous splash buffer: bootloader may still be scanning it when
this code runs. The memory remains reserved for the entire boot (safe leak).
Rollback: a52.446w_cold=0 on the boot image cmdline.

Switch B: a52.446w_handoff=0 (default), experimental full modeset in first
HWC atomic with splash kept. Intended to exercise EXISTING TG safe cleanup
paths, not blind mutation of panel, crtc, or encoder flags.
"""
import argparse
from pathlib import Path
P=Path("drivers/a52_display/msm/sde/sde_kms.c")
SS=Path("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c")
H=Path("include/linux/a52_446w_splash.h")
TAG="A52_PHASE446W_SPLASH_SELECT_V1"

def one(s,a,b,label):
    if s.count(a)!=1:raise RuntimeError("446W SPLASH "+label+" anchor count="+str(s.count(a)))
    return s.replace(a,b,1)
def patch_kms(s):
    if TAG in s:return s
    s='#include <linux/a52_446w_splash.h>\n'+s
    anchor="static bool g_cont_splash = true;"
    code=r'''/* A52_PHASE446W_SPLASH_SELECT_V1 */
static bool a52w_cold = true;
static bool a52w_handoff;
static int __init a52w_cold_arg(char *s)
{
	if (!s) return -EINVAL;
	if (!strcmp(s,"0")) a52w_cold=false;
	else if (!strcmp(s,"1")) a52w_cold=true;
	else return -EINVAL;
	return 0;
}
early_param("a52.446w_cold",a52w_cold_arg);
static int __init a52w_handoff_arg(char *s)
{
	if (!s) return -EINVAL;
	if (!strcmp(s,"0")) a52w_handoff=false;
	else if (!strcmp(s,"1")) a52w_handoff=true;
	else return -EINVAL;
	return 0;
}
early_param("a52.446w_handoff",a52w_handoff_arg);
bool a52w_cold_selected(void) { return a52w_cold; }
bool a52w_handoff_selected(void) { return !a52w_cold && a52w_handoff; }
EXPORT_SYMBOL_GPL(a52w_cold_selected);
EXPORT_SYMBOL_GPL(a52w_handoff_selected);
'''
    s=one(s,anchor,anchor+"\n"+code,"cmdline switches")
    # The verified Phase446S source has P446 recorder markers BETWEEN
    # the splash-fetch call and if(rc). Replace only the single call.
    anchor="rc = _sde_kms_get_splash_data(&sde_kms->splash_data);"
    repl=r'''if (a52w_cold_selected()) {
		memset(&sde_kms->splash_data, 0,sizeof(sde_kms->splash_data));
		rc = 0;
		pr_info("P446W SPLASH COLD_SELECTED=1 handoff=0: take-over bypassed; bootloader framebuffer remains RESERVED for boot, normal first DRM modeset must prepare DSI\n");
	} else {
		rc = _sde_kms_get_splash_data(&sde_kms->splash_data);
		pr_info("P446W SPLASH COLD_SELECTED=0 handoff_requested=%d regions=%d displays=%d\n",
			a52w_handoff_selected(),sde_kms->splash_data.num_splash_regions,
			sde_kms->splash_data.num_splash_displays);
	}'''
    return one(s,anchor,repl,"single splash fetch, keep P446 recorders")

def patch_samsung(s):
    if "P446W_FULL_POST_NORMAL_ON" in s:return s
    if "a52v_display_event(true);" not in s:
        raise RuntimeError("446W requires Phase446V Samsung normal-on event")
    s='#include <linux/a52_446w_splash.h>\n'+s
    hook=r'''			a52v_display_event(true);'''
    replacement=r'''			a52v_display_event(true);
		/* Always print the COMPLETE state after the first NORMAL DISPLAY_ON.
		 * 446V's first post HWC snapshot may be drowned by GETBLOB dumps.
		 * Keep a separate immediate snapshot at the transition.
		 */
		if (a52v_display_stage()==2) {
			static bool a52w_post_snapshot_done;
			if (!a52w_post_snapshot_done) {
				a52w_post_snapshot_done=true;
				pr_info("P446W FULL_POST_NORMAL_ON first transition stage=2\n");
				a52v_sde_snapshot(GET_DRM_DEV(vdd),
						"P446W_FULL_POST_NORMAL_ON");
			}
		}'''
    return one(s,hook,replacement,"post cold-cycle full snapshot")

def check(root):
    s=(root/P).read_text()
    if "P446W_FULL_POST_NORMAL_ON" not in (root/SS).read_text():
        raise RuntimeError("446W post-cycle complete snapshot missing")
    for a in (TAG,'early_param("a52.446w_cold"','early_param("a52.446w_handoff"',
              "P446W SPLASH COLD_SELECTED=1","sde_kms->splash_data.num_splash_regions",
              "a52w_handoff_selected"):
        if a not in s:raise RuntimeError("446W missing "+a)
    print("P446W SPLASH CHECK PASS: default cold, explicit rollback, disabled B, reserved memory not freed")
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true");args=p.parse_args()
    if not args.check_only:
        f=args.root/P;f.write_text(patch_kms(f.read_text()))
        h=args.root/H;h.write_text('''/* Phase446W runtime selection */
#ifndef _A52_446W_SPLASH_H
#define _A52_446W_SPLASH_H
#include <linux/types.h>
bool a52w_cold_selected(void);
bool a52w_handoff_selected(void);
#endif
''')
        sf=args.root/SS;sf.write_text(patch_samsung(sf.read_text()))
    check(args.root)
if __name__=="__main__":main()
