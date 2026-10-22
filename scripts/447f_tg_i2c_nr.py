#!/usr/bin/env python3
"""P447F: exact TG I2C adapter IDs for already-enabled SE7/SE8/SE10.

Use i2c_add_numbered_adapter for only these existing legacy QUPv3 engines.
SE0/880000 is an explicit future mapping but remains disabled by P447D.
Do NOT renumber other adapters or enable new camera, NFC, GPIO-I2C buses.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447F_TG_I2C_NR_V1"
SRC=Path("drivers/i2c/busses/i2c-qcom-geni.c")
KCFG=Path("drivers/a52_447/Kconfig")
HELPER=r'''
/* A52_PHASE447F_TG_I2C_NR_V1: TG A526B, observed on real device.
 * Only SE7/8/10 are currently allowed by P447D; SE0 never bypasses it.
 * Downstream alias IDs 34/35/36 are absent from the deployed GKI DT;
 * reserve them deterministically instead of dynamic 31/32/33.
 */
static int a52f_tg_i2c_nr(int se)
{
	switch (se) {
	case 0: return 33; /* future W2 NFC only, currently DENIED */
	case 7: return 34; /* SM5714 MFD, FG, PCA9468 */
	case 8: return 35; /* STM touch, KEEP GSI protocol untouched */
	case 10:return 36; /* SM5714 USB-PD */
	default:return -ENODEV;
	}
}
'''
def one(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447F {name} anchor count {n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for v in ("A52_PHASE447E_DT_ALIAS_AUDIT_V1","P447D I2C SE%d PROBE","a52d_i2c_legacy_allowed"):
        if v not in s:raise RuntimeError("P447F missing "+v)
    s=one(s,"static int geni_i2c_probe(struct platform_device *pdev)",
           HELPER+"\nstatic int geni_i2c_probe(struct platform_device *pdev)",
           "per-controller mapping")
    a="\tret = i2c_add_adapter(&gi2c->adap);"
    b='''\t/* Explicit TG adapter ID, only after legacy SE whitelist passed.
\t * If busy, fail CLOSED: never silently reassign a different ID and
\t * defeat vendor genfscon. Other platform adapters keep normal aliases.
\t */
\tif (IS_ENABLED(CONFIG_A52_447F_TG_I2C_NR) &&
\t    gi2c->se.a52_legacy_ahb &&
\t    a52f_tg_i2c_nr(gi2c->a52d_se)>0) {
\t\tint nr=a52f_tg_i2c_nr(gi2c->a52d_se);
\t\tgi2c->adap.nr=nr;
\t\tret=i2c_add_numbered_adapter(&gi2c->adap);
\t\tdev_info(dev,
\t\t\t "P447F I2C NR ctrl=%pa se=%d nr=%d alias=%d rc=%d policy=TG_A52\\n",
\t\t\t &res->start,gi2c->a52d_se,nr,
\t\t\t of_alias_get_id(dev->of_node,"i2c"),ret);
\t} else {
\t\tret = i2c_add_adapter(&gi2c->adap);
\t}'''
    return one(s,a,b,"numbered register")
def check(root):
    s=(root/SRC).read_text()
    for m in (TAG,"case 7: return 34","case 8: return 35",
              "case 10:return 36","case 0: return 33",
              "P447F I2C NR ctrl=%pa","i2c_add_numbered_adapter(&gi2c->adap)",
              "a52d_i2c_legacy_allowed"):
        if m not in s:raise RuntimeError("P447F I2C missing "+m)
    cfg=(root/KCFG).read_text()
    if "config A52_447F_TG_I2C_NR" not in cfg:
        raise RuntimeError("P447F Kconfig missing")
    print("P447F TG-I2C-NR PASS: SE7=34 SE8=35 SE10=36, SE0 mapping dormant, no other I2C changed")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/SRC;k=a.root/KCFG
    if not a.check_only:
        s=k.read_text()
        if "config A52_447F_TG_I2C_NR" not in s:
            s+="""
config A52_447F_TG_I2C_NR
    bool "A52 exact TG I2C adapter numbers, SE7=34 SE8=35 SE10=36"
    depends on I2C
    default n
    help
      Needed for stock Samsung vendor genfscon paths. Never auto-remap
      other serial engines or i2c-gpio buses.
"""
            k.write_text(s)
        f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
