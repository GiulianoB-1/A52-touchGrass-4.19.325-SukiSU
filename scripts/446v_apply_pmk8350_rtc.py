#!/usr/bin/env python3
"""446V PMK8350 RTC support: minimal upstream register-map backport.

v5.10 rtc-pm8xxx keeps IRQ/alarm/time driver unchanged. Only the missing
PMK8350 two-peripheral 0x61xx/0x62xx compatible plus probe/read witnesses
are added. RTC_HCTOSYS and RTC_DRV_PM8XXX built-in are enforced by CI.
Reference: mainline Linux v6.6 drivers/rtc/rtc-pm8xxx.c.
"""
from pathlib import Path
import argparse
P=Path("drivers/rtc/rtc-pm8xxx.c")
TAG="A52_PHASE446V_PMK8350_RTC_V1"

def once(s,a,b,label):
    if s.count(a)!=1:
        raise RuntimeError("P446V RTC "+label+" matched "+str(s.count(a))+" times")
    return s.replace(a,b,1)

def apply(s):
    if TAG in s:return s
    anchor="static const struct of_device_id pm8xxx_id_table[] = {"
    reg=r'''/* A52_PHASE446V_PMK8350_RTC_V1: two distinct RTC and ALARM blocks */
static const struct pm8xxx_rtc_regs pmk8350_regs = {
	.ctrl		= 0x6146,
	.write		= 0x6140,
	.read		= 0x6148,
	.alarm_rw	= 0x6240,
	.alarm_ctrl	= 0x6246,
	.alarm_ctrl2	= 0x6248,
	.alarm_en	= BIT(7),
};

'''
    s=once(s,anchor,reg+anchor,"register layout")
    entry='	{ .compatible = "qcom,pm8941-rtc", .data = &pm8941_regs },'
    s=once(s,entry,entry+'\n\t{ .compatible = "qcom,pmk8350-rtc", .data = &pmk8350_regs },',"OF match")
    # Driver probe logs independently of UART so the missing binding is
    # distinguishable from a failed regmap/IRQ/RTC register.
    needle='rtc_dd->regs = match->data;'
    s=once(s,needle,needle+r'''
	if (of_device_is_compatible(pdev->dev.of_node, "qcom,pmk8350-rtc"))
		dev_info(&pdev->dev,
			 "P446V RTC PROBE pmk8350 rtc=0x6140 alarm=0x6240 irq=%d allow_set=%d\n",
			 rtc_dd->rtc_alarm_irq,rtc_dd->allow_set_time);
''',"probe witness")
    needle='return rtc_register_device(rtc_dd->rtc);'
    repl=r'''rc = rtc_register_device(rtc_dd->rtc);
	if (of_device_is_compatible(pdev->dev.of_node, "qcom,pmk8350-rtc")) {
		struct rtc_time first_tm;
		int first_rc = rc;

		if (!first_rc)
			first_rc = pm8xxx_rtc_read_time(&pdev->dev, &first_tm);
		dev_info(&pdev->dev,
			 "P446V RTC REGISTER registration_rc=%d first_read_rc=%d time=%ptR\n",
			 rc, first_rc, first_rc ? NULL : &first_tm);
	}
	return rc;'''
    s=once(s,needle,repl,"registration and first read")
    return s

def verify(root):
    s=(root/P).read_text()
    for t in (TAG,'"qcom,pmk8350-rtc"','0x6146','0x6140','0x6148','0x6240','0x6246','0x6248',
              'P446V RTC PROBE','P446V RTC REGISTER'):
        if t not in s:raise RuntimeError("RTC contract absent "+t)
    print("P446V RTC MATCH+REGS+FIRST_READ CHECK PASS")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true");a=ap.parse_args()
    if not a.check_only:
        f=a.root/P;f.write_text(apply(f.read_text()))
    verify(a.root)
if __name__=="__main__":main()
