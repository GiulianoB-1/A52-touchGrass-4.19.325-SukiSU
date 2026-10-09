#!/usr/bin/env python3
"""Phase446l2G: exact early hardware-FIFO experiment twin on TouchGrass 4.19.

Reuses the GKI Phase446l2 helper body verbatim except for the recorder
backend and the unavailable GKI-only a52_ackfr_record() API.
"""
import argparse
import importlib.util
from pathlib import Path

MARK="A52_PHASE446L2_FIFO_LIVE_SPLASH_V1"
SIG="int dsi_display_cont_splash_config(void *dsi_display)"
END="clks_disabled:"

def one(s,a,b,name):
    if s.count(a)!=1:
        raise RuntimeError("%s expected one anchor, got %d"%(name,s.count(a)))
    return s.replace(a,b,1)

def shared_helper():
    p=Path(__file__).with_name("446l2_apply_live_fifo.py")
    sp=importlib.util.spec_from_file_location("a52_l2_shared_fifo",p)
    mod=importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    helper=mod.HELPER
    if helper.count("a52_p445_store_section") != 3:
        raise RuntimeError("GKI helper store hook unexpected count")
    helper=helper.replace("a52_p445_store_section","a52_p444_store_section")
    # a52_ackfr_record is a GKI-only exported function; Golden uses pr_err.
    begin=helper.index('    a52_ackfr_record("P446L2 st=')
    end=helper.index(";\n",begin)+2
    helper=helper[:begin]+helper[end:]
    if "a52_ackfr_record" in helper: raise RuntimeError("GKI-only symbol retained")
    return helper

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    p=a.root/"techpack/display/msm/dsi/dsi_display.c"
    s=p.read_text()
    if not a.check_only and MARK not in s:
        s=one(s,SIG,shared_helper()+SIG,"helper")
        lo=s.index(SIG); hi=s.index(END,lo)
        fn=s[lo:hi]
        anchor="\treturn rc;\n\n"
        if fn.count(anchor)!=1:
            raise RuntimeError("Golden continuous-splash return unexpected")
        fn=fn.replace(anchor,'''
    /* One per boot, approximately seven seconds after cont-splash adoption. */
    if (!rc && display->is_cont_splash_enabled &&
        !atomic_read(&p446l2_once)) {
        WRITE_ONCE(p446l2_display, display);
        schedule_delayed_work(&p446l2_work,msecs_to_jiffies(7000));
    }
\treturn rc;\n\n''',1)
        s=s[:lo]+fn+s[hi:]
        p.write_text(s)
    t=p.read_text()
    helper=t[t.index(MARK):t.index(SIG,t.index(MARK))]
    for part in (MARK, "a52_p444_store_section", "P446L2_REPLICAS 16U",
                 "DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER","DSI_CMD_MODE_DMA_SW_TRIGGER",
                 "P446L2 stage=", "crc32_le("):
        if part not in helper: raise RuntimeError("missing shared feature: "+part)
    if "a52_p445_store_section" in helper or "a52_ackfr_record" in helper:
        raise RuntimeError("GKI-only symbols in Golden helper")
    for bad in ("dsi_display_cmd_engine_enable(",
                "dsi_display_cmd_engine_disable(",
                "dsi_display_clk_ctrl(", "dsi_ctrl_cmd_transfer("):
        if bad in helper: raise RuntimeError("forbidden lifecycle call: "+bad)
    if t.count(MARK)!=1 or t.count("schedule_delayed_work(&p446l2_work")!=1:
        raise RuntimeError("not exactly one one-shot Golden FIFO probe")
    print("Phase446l2G Golden same FIFO packet/trigger/timing, 16 replicas: PASS")
if __name__=="__main__":
    main()
