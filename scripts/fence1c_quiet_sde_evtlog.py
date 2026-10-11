#!/usr/bin/env python3
"""FENCE1C: isolate COM3 stall inside synchronous SDE eventlog dump.

FENCE1B already disabled deliberate SDE panic. Both FENCE1B boots stopped
midway through sde_evtlog_dump_all, which repeatedly pr_info's the event ring
in the CRTC frame-event worker. Suppress ONLY that automatic text flood.
The event ring continues collecting in RAM, and the original register, SDE,
VBIF and DSI debug-bus dumping and ESD/recovery code are untouched.
"""
import argparse
from pathlib import Path

REL = Path("drivers/a52_display/msm/sde_dbg.c")
MARK = "A52_FENCE1C_QUIET_EVTLOG_V1"
OLD = "\tif (dump_all)\n\t\tsde_evtlog_dump_all(sde_dbg_base.evtlog);\n"
NEW = (
    "\tif (dump_all) {\n"
    "\t\t/* A52_FENCE1C_QUIET_EVTLOG_V1: event ring stays in RAM.\n"
    "\t\t * Do not synchronously stream hundreds of events to ttyGS0;\n"
    "\t\t * that hid both FENCE1B boots' post-timeout recovery behavior.\n"
    "\t\t */\n"
    '\t\tpr_info("FENCE1C SDE_EVTLOG retained_in_ram console_dump_skipped=1\\n");\n'
    "\t}\n"
)

def audit(src):
    if src.count(MARK) != 1 or src.count(NEW) != 1:
        raise RuntimeError("FENCE1C marker absent or duplicated")
    if OLD in src:
        raise RuntimeError("unconditional synchronous eventlog flood still present")
    if "A52_FENCE1B_SDE_NO_PANIC_V1" not in src:
        raise RuntimeError("FENCE1B panic-off fix was lost")
    if "sde_dbg_base.panic_on_err = 0;" not in src:
        raise RuntimeError("FENCE1B panic override was lost")
    if "_sde_dump_reg_all(dump_secure);" not in src:
        raise RuntimeError("register diagnostics missing")
    if "_sde_dbg_dump_sde_dbg_bus(&sde_dbg_base.dbgbus_sde);" not in src:
        raise RuntimeError("SDE debug bus dump missing")
    if "_sde_dbg_dump_vbif_dbg_bus(&sde_dbg_base.dbgbus_vbif_rt);" not in src:
        raise RuntimeError("VBIF debug bus dump missing")
    print("FENCE1C PASS: only automatic eventlog console printing bypassed; RAM eventlog and diagnostic dumps retained")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--check-only", action="store_true")
    a = p.parse_args()
    target = a.root / REL
    src = target.read_text()
    if not a.check_only and MARK not in src:
        if src.count(OLD) != 1:
            raise RuntimeError("expected one unconditional evtlog dump, found " + str(src.count(OLD)))
        target.write_text(src.replace(OLD, NEW, 1))
        src = target.read_text()
    audit(src)

if __name__ == "__main__":
    main()
