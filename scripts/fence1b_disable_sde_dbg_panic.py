#!/usr/bin/env python3
"""FENCE1B: keep Samsung's display timeout diagnostics, but never panic on them.

In the Golden TouchGrass production configuration SEC_DEBUG disables the SDE
panic-on-error switch. The GKI port falls back to DEFAULT_PANIC=1 because
CONFIG_SEC_DEBUG is absent. This GKI-only override is applied *after* the
conditional, so the resulting startup evtlog_status always prints panic:0.

Do not modify the SDE timeout, dump, recovery or normal F0 paths here.
"""
import argparse
from pathlib import Path

REL = Path("drivers/a52_display/msm/sde_dbg.c")
MARK = "A52_FENCE1B_SDE_NO_PANIC_V1"
OLD = (
    "#endif\n"
    "\tsde_dbg_base.enable_reg_dump = DEFAULT_REGDUMP;\n"
    "\tmemset(&sde_dbg_base.regbuf, 0, sizeof(sde_dbg_base.regbuf));"
)
NEW = (
    "#endif\n"
    "\t/* A52_FENCE1B_SDE_NO_PANIC_V1: production Golden debug policy.\n"
    "\t * A DSI timeout still captures every SDE diagnostic; it must not\n"
    "\t * deliberately panic before the standard ESD/recovery checks.\n"
    "\t */\n"
    "\tsde_dbg_base.panic_on_err = 0;\n"
    "\tsde_dbg_base.enable_reg_dump = DEFAULT_REGDUMP;\n"
    "\tmemset(&sde_dbg_base.regbuf, 0, sizeof(sde_dbg_base.regbuf));"
)

def audit(source):
    if source.count(MARK) != 1 or source.count(NEW) != 1:
        raise RuntimeError("FENCE1B override absent or duplicated")
    if "#define DEFAULT_PANIC" not in source:
        raise RuntimeError("unexpected SDE diagnostic policy")
    if "if (do_panic && sde_dbg_base.panic_on_err)" not in source:
        raise RuntimeError("panic gate changed unexpectedly")
    if 'pr_info("evtlog_status: enable:%d, panic:%d, dump:%d' not in source:
        raise RuntimeError("SDE startup policy witness missing")
    if source.count("sde_dbg_base.panic_on_err = 0;") < 2:
        raise RuntimeError("SDE no-panic assignment not found")
    print("FENCE1B PASS: post-conditional SDE panic_on_err=0; timeout dumps and recovery unchanged")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    p = a.root / REL
    source = p.read_text()
    if not a.check_only and MARK not in source:
        if source.count(OLD) != 1:
            raise RuntimeError("FENCE1B init anchor count=%d, expected 1" % source.count(OLD))
        source = source.replace(OLD, NEW, 1)
        p.write_text(source)
    audit(source)

if __name__ == "__main__":
    main()
