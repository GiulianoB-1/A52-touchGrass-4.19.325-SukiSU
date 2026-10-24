#!/usr/bin/env python3
"""P453 DAILY: VBUS/UDC pending-configuration wake lock.

FG reports VBUS_POK (not guess from USB gadget state). UDC core tracks
CONFIGURED, holding one wakeup source while VBUS is present and gadget
remains unconfigured, including FunctionFS/adbd handover/retry.
"""
from pathlib import Path
import argparse
FG=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
UDC=Path("drivers/usb/gadget/udc/core.c")
TAG="A52_PHASE453_USB_VBUS_PENDING_WAKE_V1"
def once(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P453 WAKE {name}: anchors {n}")
    return s.replace(a,b,1)
UDC_HELPER=r'''
/* A52_PHASE453_USB_VBUS_PENDING_WAKE_V1
 * The A52 5.10 ROM may not write ConfigFS g1/UDC until ~134s; an
 * s2idle before that loses the legacy USB2 PHY tuning and PC attach.
 * Never keep the phone awake without a real STATUS1 VBUS_POK vote.
 */
static struct wakeup_source *a52p453_g1_ws;
static DEFINE_MUTEX(a52p453_g1_ws_lock);
static bool a52p453_vbus;
static bool a52p453_configured;
static void a52p453_refresh_g1_ws(void)
{
	struct wakeup_source *ws=READ_ONCE(a52p453_g1_ws);
	if(!ws)return;
	if(READ_ONCE(a52p453_vbus) && !READ_ONCE(a52p453_configured))
		__pm_stay_awake(ws);
	else
		__pm_relax(ws);
}
void a52p453_usb_vbus_present(bool present)
{
	mutex_lock(&a52p453_g1_ws_lock);
	if(!a52p453_g1_ws)
		a52p453_g1_ws=wakeup_source_register(NULL,
			"a52-vbus-unconfigured-g1");
	if(!a52p453_g1_ws) {
		pr_err_ratelimited("P453 USB WAKE source register failed\n");
		mutex_unlock(&a52p453_g1_ws_lock);
		return;
	}
	if(a52p453_vbus!=present)
		pr_info("P453 USB WAKE VBUS=%d configured=%d\n",
			present,a52p453_configured);
	WRITE_ONCE(a52p453_vbus,present);
	a52p453_refresh_g1_ws();
	mutex_unlock(&a52p453_g1_ws_lock);
}
EXPORT_SYMBOL_GPL(a52p453_usb_vbus_present);
'''
def patch_udc(s):
    if TAG in s:return s
    s=once(s,"#include <linux/workqueue.h>",
       "#include <linux/workqueue.h>\n#include <linux/pm_wakeup.h>",
       "wakeup include")
    s=once(s,"static DEFINE_MUTEX(udc_lock);",
       "static DEFINE_MUTEX(udc_lock);"+UDC_HELPER,
       "state/vbus wake source")
    s=once(s,'''	gadget->state = state;
	schedule_work(&gadget->work);''',
       '''	gadget->state = state;
	/* Limit this strictly to the A52 DWC3 UDC, not arbitrary gadgets. */
	if(gadget->name && !strcmp(gadget->name,"a600000.dwc3")) {
		WRITE_ONCE(a52p453_configured,state==USB_STATE_CONFIGURED);
		a52p453_refresh_g1_ws();
	}
	schedule_work(&gadget->work);''',"configure-state release")
    return s
def patch_fg(s):
    if TAG in s:return s
    if "A52_PHASE451_DAILY_READONLY_V1" not in s:
        raise RuntimeError("P453 WAKE FG daily prerequisite absent")
    s=once(s,"static int a52g_sample(struct a52d_fg *fg, bool first)",
       '''/* P453 VBUS vote lives in the UDC core; no duplicate wake lock here. */
extern void a52p453_usb_vbus_present(bool present);
static int a52g_sample(struct a52d_fg *fg, bool first)''',"VBUS helper declaration")
    s=once(s,"fg->a52p451_last_vbus=vbus;",
       '''fg->a52p451_last_vbus=vbus;
    /* Only an actual charger STATUS1 read controls wake ownership.
     * No guessed VBUS state, no charger writes.
     */
    if(vbus>=0)a52p453_usb_vbus_present(vbus>0);''',"poll VBUS update")
    return s
def check(root):
    s=(root/UDC).read_text(); fg=(root/FG).read_text()
    for key in (TAG,"a52-vbus-unconfigured-g1","USB_STATE_CONFIGURED",
                "__pm_stay_awake(ws)","__pm_relax(ws)",
                "EXPORT_SYMBOL_GPL(a52p453_usb_vbus_present)"):
        if key not in s:raise RuntimeError("P453 UDC wake missing "+key)
    for key in ("a52p453_usb_vbus_present(vbus>0)",
                "a52p453_usb_vbus_present(bool present)"):
        if key not in fg:raise RuntimeError("P453 FG wake missing "+key)
    if fg.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P453 WAKE changed guarded charger writes")
    print("P453 USB WAKE PASS: VBUS_POK AND gadget not CONFIGURED holds wake, releases when configured/unplugged")
def main():
    a=argparse.ArgumentParser()
    a.add_argument("--root",type=Path,required=True)
    a.add_argument("--check-only",action="store_true")
    p=a.parse_args()
    if not p.check_only:
        f=p.root/UDC;f.write_text(patch_udc(f.read_text()))
        f=p.root/FG;f.write_text(patch_fg(f.read_text()))
    check(p.root)
if __name__=="__main__":main()
