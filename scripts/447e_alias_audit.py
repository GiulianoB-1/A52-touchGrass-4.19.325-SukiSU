#!/usr/bin/env python3
"""447E: log the I2C alias semantics used by unmodified Linux 5.10.

In 5.10 drivers/i2c/i2c-core-base.c i2c_add_adapter() ALREADY calls
of_alias_get_id(np, "i2c") and uses __i2c_add_numbered_adapter when found.
No forced remap is safe until the *deployed* A52 DT has proper aliases.
Existing SE8 GSI touch bus numbering must be preserved.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447E_DT_ALIAS_AUDIT_V1"
SRC=Path("drivers/i2c/busses/i2c-qcom-geni.c")
def one(s,a,b):
    if s.count(a)!=1:raise RuntimeError("P447E I2C alias anchor count "+str(s.count(a)))
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    if "P447D I2C SE%d PROBE" not in s:
        raise RuntimeError("P447E requires verified 447D transport")
    # 447D keeps the upstream i2c_add_adapter() call. That function already
    # implements exact DT alias selection. Log the result and fail no probes.
    anchor="\tret = i2c_add_adapter(&gi2c->adap);"
    if s.count(anchor)!=1:
        raise RuntimeError("P447E unexpected I2C adapter registration")
    msg='''\t/* A52_PHASE447E_DT_ALIAS_AUDIT_V1 -- audit, do not renumber SE8. */
\tif (gi2c->se.a52_legacy_ahb)
\t\tdev_info(dev,"P447E I2C SE%d DT_ALIAS=%d rule=upstream_auto_num before_register\\n",
\t\t\tgi2c->a52d_se,of_alias_get_id(dev->of_node,"i2c"));
'''
    return one(s,anchor,msg+anchor)
def check(root):
    s=(root/SRC).read_text()
    for marker in (TAG,"i2c_add_adapter(&gi2c->adap)",
                   "P447E I2C SE%d DT_ALIAS=%d"):
        if marker not in s:raise RuntimeError("P447E alias audit missing "+marker)
    # Phase447F has captured TG proof of exact A52 SE7/SE8/SE10 IDs.
    # Only that fail-closed mapping is exempt from the 447E ban.
    if "i2c_add_numbered_adapter(&gi2c->adap)" in s:
        required=("A52_PHASE447F_TG_I2C_NR_V1",
                  "case 7: return 34", "case 8: return 35",
                  "case 10:return 36",
                  "gi2c->se.a52_legacy_ahb",
                  "a52d_i2c_legacy_allowed")
        if not all(x in s for x in required):
            raise RuntimeError("P447E forbids unverified forced adapter number")
        print("P447E/P447F I2C whitelist PASS: TG-proven SE7/SE8/SE10 only")
    print("P447E I2C ALIAS PASS: uses existing 5.10 of_alias_get_id auto-number and reports each resolved alias; touch untouched")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
