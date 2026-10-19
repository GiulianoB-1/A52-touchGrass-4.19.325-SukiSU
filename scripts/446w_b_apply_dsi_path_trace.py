#!/usr/bin/env python3
"""446W-B passive DSI prepare/enable/disable/unprepare and panel ON log gates."""
from pathlib import Path
import argparse
D=Path("drivers/a52_display/msm/dsi/dsi_display.c")
P=Path("drivers/a52_display/msm/dsi/dsi_panel.c")
TAG="A52_PHASE446W_HANDOFF_B_DSI_LIFECYCLE_V1"
def one(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError("446W-B DSI "+label+" matches="+str(n))
    return s.replace(a,b,1)

def trace_func(s,signature,kind):
    start=s.index(signature)
    begin=s.index("{",start)+1
    end=s.index("\n}",begin)
    frag=s[begin:end]
    # C89-safe: inject at the existing body after all locals but before any
    # hardware operation. The first !display check is a safe insertion point.
    check=frag.index("\n\tif (!display")
    entry=f'''
	if (a52w_handoff_selected())
		pr_info("P446W HANDOFF DSI {kind} enter ts=%llu\\n",
			(unsigned long long)ktime_get_boottime_ns());
'''
    frag=frag[:check]+entry+frag[check:]
    last=max(frag.rfind("\treturn rc;"),frag.rfind("\treturn 0;"))
    if last<0:raise RuntimeError("446W-B DSI no rc return in "+kind)
    ext=f'''if (a52w_handoff_selected())
		pr_info("P446W HANDOFF DSI {kind} exit rc=%d ts=%llu\\n",
			rc,(unsigned long long)ktime_get_boottime_ns());
	'''
    frag=frag[:last]+ext+frag[last:]
    return s[:begin]+frag+s[end:]

def patch_d(s):
    if TAG in s:return s
    s='#include <linux/ktime.h>\n#include <linux/a52_446w_splash.h>\n'+s
    for name in ("prepare","enable","disable","unprepare"):
        s=trace_func(s,"int dsi_display_"+name+"(struct dsi_display *display)","dsi_display_"+name)
    return s+"\n/* "+TAG+" */\n"

def patch_panel(s):
    if TAG in s:return s
    s='#include <linux/ktime.h>\n#include <linux/a52_446w_splash.h>\n'+s
    begin=s.index("int dsi_panel_enable(struct dsi_panel *panel)")
    end=s.index("\n}",begin)+2
    frag=s[begin:end]
    anchor='''	/* sleep out command is included in DSI_CMD_SET_ON */'''
    frag=one(frag,anchor,r'''	if (a52w_handoff_selected())
		pr_info("P446W HANDOFF panel ON_CMDS begin initialized=%d splash=%d skip_on_splash=%d\n",
			panel->panel_initialized,
#if defined(CONFIG_DISPLAY_SAMSUNG)
			vdd->samsung_splash_enabled,vdd->skip_cmd_set_on_splash_enabled
#else
			0,0
#endif
			);
'''+anchor,"panel on commands start")
    anchor='''	else if (!a52_p445_samsung_blocked())
		panel->panel_initialized = true;'''
    frag=one(frag,anchor,anchor+r'''
	if (a52w_handoff_selected())
		pr_info("P446W HANDOFF panel ON_CMDS end rc=%d initialized=%d\n",
			rc,panel->panel_initialized);
''',"panel on command result")
    s=s[:begin]+frag+s[end:]
    return s+"\n/* "+TAG+" */\n"

def check(root):
    s=(root/D).read_text()
    for t in (TAG,"P446W HANDOFF DSI dsi_display_prepare enter",
              "P446W HANDOFF DSI dsi_display_enable exit",
              "P446W HANDOFF DSI dsi_display_disable",
              "P446W HANDOFF DSI dsi_display_unprepare"):
        if t not in s:raise RuntimeError("446W-B missing "+t)
    s=(root/P).read_text()
    for t in (TAG,"P446W HANDOFF panel ON_CMDS begin","P446W HANDOFF panel ON_CMDS end"):
        if t not in s:raise RuntimeError("446W-B missing "+t)
    print("446W-B DSI+ON_CMDS lifecycle source PASS")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true");a=ap.parse_args()
    if not a.check_only:
        for p,fn in ((D,patch_d),(P,patch_panel)):
            f=a.root/p;f.write_text(fn(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
