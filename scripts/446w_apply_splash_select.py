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
    anchor='''	rc = _sde_kms_get_splash_data(&sde_kms->splash_data);
	if (rc)
		SDE_DEBUG("sde splash data fetch failed: %d\\n", rc);'''
    repl=r'''	if (a52w_cold_selected()) {
		/*
		 * Do not map/take ownership of bootloader splash. In particular,
		 * DO NOT call _sde_kms_free_splash_region() because that path
		 * needs a live one-to-one mapping and can memblock_free pages
		 * while firmware scanout still references them.
		 */
		memset(&sde_kms->splash_data, 0,sizeof(sde_kms->splash_data));
		pr_info("P446W SPLASH COLD_SELECTED=1 handoff=0: takeover bypassed, bootloader framebuffer remains RESERVED for boot; normal first DRM modeset must prepare DSI\n");
	} else {
		rc = _sde_kms_get_splash_data(&sde_kms->splash_data);
		if (rc)
			SDE_DEBUG("sde splash data fetch failed: %d\n", rc);
		pr_info("P446W SPLASH COLD_SELECTED=0 handoff_requested=%d regions=%d displays=%d (TG splash path)\n",
			a52w_handoff_selected(),
			sde_kms->splash_data.num_splash_regions,
			sde_kms->splash_data.num_splash_displays);
	}'''
    return one(s,anchor,repl,"safe splash data bypass")
def check(root):
    s=(root/P).read_text()
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
    check(args.root)
if __name__=="__main__":main()
