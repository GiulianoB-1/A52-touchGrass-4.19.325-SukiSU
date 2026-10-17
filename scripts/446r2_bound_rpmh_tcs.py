#!/usr/bin/env python3
"""446R2: timeout Phase423 solver drain; log Phase305 flush -EBUSY.

This is an additive final-tree patch AFTER the archived Phase423/305 scripts.
On timeout keep solver mode unchanged and return -ETIMEDOUT.
"""
from pathlib import Path
import argparse

MARK="A52_PHASE446R2_BOUNDED_RPMH_TCS_V1"
P=Path("drivers/soc/qcom/rpmh-rsc.c")

def one(s,a,b,why):
    n=s.count(a)
    if n != 1: raise RuntimeError("R2 "+why+": "+str(n))
    return s.replace(a,b,1)

def patch(s):
    if MARK in s: return s
    for m in ("A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1",
              "A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1"):
        if m not in s: raise RuntimeError("R2 missing inherited "+m)
    begin=s.index("int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)")
    end=s.index("EXPORT_SYMBOL_GPL(a52_rpmh_mode_solver_set_compat);",begin)
    f=s[begin:end]
    f=one(f,"\tunsigned int loops = 0;","\tunsigned int loops = 0;\n"
          "\tunsigned long deadline;\n","solver locals")
    f=one(f,"\tif (enable) {\n\t\tfor (;;) {",
            "\tif (enable) {\n"
            "\t\t/* "+MARK+": bounded 20ms, no busy-wait forever. */\n"
            "\t\tdeadline = jiffies + msecs_to_jiffies(20);\n"
            "\t\tfor (;;) {","solver enter")
    f=one(f,"\t\t\tloops++;\n\t\t\tcpu_relax();",
            "\t\t\tloops++;\n"
            "\t\t\tif (time_after_eq(jiffies, deadline)) {\n"
            "\t\t\t\tpr_warn_ratelimited(\"P446R2 RPMH_TCS_DRAIN_TIMEOUT en=1 loops=%u group=%u tcs=%u\\n\",\n"
            "\t\t\t\t\tloops, tcs->type, tcs->num_tcs);\n"
            "\t\t\t\treturn -ETIMEDOUT;\n"
            "\t\t\t}\n"
            "\t\t\tcpu_relax();","solver busy-loop")
    s=s[:begin]+f+s[end:]
    begin=s.index("int a52_rpmh_flush_compat(const struct device *dev)")
    end=s.index("\n}\n",begin)
    f=s[begin:end]
    f=one(f,'\ta52_ackfr_record("P276 305F x r=%d l=%u b=%u", ret, locked, busy);',
        '\tif (ret == -EBUSY)\n'
        '\t\tpr_warn_ratelimited("P446R2 RPMH_FLUSH_EBUSY lock=%u busy=%u\\n", locked, busy);\n'
        '\ta52_ackfr_record("P276 305F x r=%d l=%u b=%u", ret, locked, busy);',
        "flush busy warning")
    return s[:begin]+f+s[end:]

def audit(s):
    needed=[MARK,"msecs_to_jiffies(20)","time_after_eq(jiffies, deadline)",
            "return -ETIMEDOUT;","P446R2 RPMH_TCS_DRAIN_TIMEOUT",
            "P446R2 RPMH_FLUSH_EBUSY"]
    for x in needed:
        if x not in s:raise RuntimeError("R2 audit missing: "+x)
    print("P446R2 RPMH gate PASS: finite wait, no false solver-enable, flush EBUSY witness")

def main():
    p=argparse.ArgumentParser();p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true");a=p.parse_args()
    path=a.root/P
    if not a.check_only:path.write_text(patch(path.read_text()))
    audit(path.read_text())
if __name__=="__main__":main()
