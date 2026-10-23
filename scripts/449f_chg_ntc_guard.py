#!/usr/bin/env python3
"""Phase449F — temporary battery+charger NTC emergency inhibit only.

Never enables charging or changes current/voltage, PD/OTP/NV. This code
has ONE hardware write path: existing CHG_CNTL1 ENQ4FET bit3 clear.
The thermal cutoffs below are taken from TG's live battery DT.
Recovery thresholds are logged but cannot release the inhibit latch
without a validated full charger policy (Phase447I/H).
"""
import argparse
from pathlib import Path

SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE449F_CHG_MIX_GUARD_V1"
DECL="int die,st,temp,fgtemp,ntc,ctrl,rc,readback,vbus;"
NEW_DECL="""int die,st,temp,fgtemp,ntc,chgtemp,ctrl,rc,readback,vbus;
\tbool batt_trip,chg_trip,mix_trip,missing_chg,unsafe;
\tconst char *rule;"""
OLD="""unsafe=(temp>=A52G_GUARD_TEMP_DECI_C ||
\t\ttemp< -400);"""
NEW=r"""/* A52_PHASE449F_CHG_MIX_GUARD_V1:
\t * TG live DT 0.1C: chg_high_temp=550 recovery=480,
\t * mix_high_temp=420 / mix_high_chg_temp=500,
\t * mix_high_temp_recovery=390.
\t * Until sec_battery exists this is a ONE-WAY INHIBIT, not throttle;
\t * do not automatically resume when the temperature falls.
\t * An unreadable charger thermistor with VBUS present also fails
\t * closed (guard first executes before the IIO attach retry).
\t */
\tchgtemp=a52h_chg_ntc_temp(fg);
\tbatt_trip=(temp>=A52G_GUARD_TEMP_DECI_C || temp< -400);
\tchg_trip=(chgtemp>=550);
\tmix_trip=(temp>=420 && chgtemp>=500);
\tmissing_chg=(chgtemp< -400 || chgtemp>900);
\tunsafe=batt_trip || chg_trip || mix_trip ||
\t\t(missing_chg && vbus!=0);
\trule=batt_trip ? "batt" :
\t\tchg_trip ? "chg" :
\t\tmix_trip ? "mix" :
\t\t(missing_chg && vbus!=0) ? "chg_sensor_missing" : "none";
\tif (first || unsafe || fg->a52g_guard_cycle % 15 == 0)
\t\tdev_info(fg->dev,
\t\t\t"P449 CHG_GUARD rule=%s temp_batt=%d temp_chg=%d VBUS=%d action=%s latch=%d chg_limit=550 chg_recovery=480 mix_batt=420 mix_chg=500 mix_batt_recovery=390 never_reenable=1\n",
\t\t\trule,temp,chgtemp,vbus,unsafe && vbus!=0 ? "INHIBIT" : "MONITOR",
\t\t\tfg->a52g_inhibit_latched);"""
def patch(src):
    if TAG in src:
        if src.count(TAG)!=1:raise RuntimeError("P449 duplicate F marker")
        return src
    for v in ("A52_PHASE447H_TCHG_GPIO3_IIO_V1",
              "a52h_chg_ntc_temp(struct a52d_fg *fg)",
              "A52_PHASE447G_CHARGER_GUARD_V1"):
        if v not in src:raise RuntimeError("P449 F prerequisites missing "+v)
    if src.count(DECL)!=1 or src.count(OLD)!=1:
        raise RuntimeError("P449 F guard structural anchor drift")
    src=src.replace(DECL,NEW_DECL,1)
    src=src.replace(OLD,NEW.replace(chr(92)+"t",chr(9)),1)
    return src
def check(root):
    s=(root/SRC).read_text()
    for x in (TAG,'P449 CHG_GUARD rule=%s',
              "chgtemp=a52h_chg_ntc_temp(fg)",
              "chg_trip=(chgtemp>=550)",
              "mix_trip=(temp>=420 && chgtemp>=500)",
              "(missing_chg && vbus!=0)",
              "never_reenable=1",
              "fg->a52g_inhibit_latched=true;"):
        if x not in s:raise RuntimeError("P449 F contract missing "+x)
    if s.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P449 F must retain only the existing inhibit-only charger write")
    if "A52G_CHG_ENQ4FET" not in s or "ctrl & ~A52G_CHG_ENQ4FET" not in s:
        raise RuntimeError("P449 F charger inhibit contract drift")
    print("P449 F GUARD PASS: batt 45C / chg 55C / mix 42+50C; fail closed on missing Tchg under VBUS; one-way inhibit, zero enable writes")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();path=a.root/SRC
    if not a.check_only:path.write_text(patch(path.read_text()))
    check(a.root)
if __name__=="__main__":main()
