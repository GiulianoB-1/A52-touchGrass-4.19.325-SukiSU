#!/usr/bin/env python3
"""447N passive Android boot touch/USB bind fault capture, no behavior changes."""
from pathlib import Path
import argparse

TAG="A52_PHASE447N_TOUCH_USB_LATE_TRACE_V1"
TOUCH=Path("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c")
USB=Path("drivers/usb/gadget/configfs.c")

def once(s,a,b,label):
    n=s.count(a)
    if n!=1:
        raise RuntimeError("P447N "+label+" anchor count="+str(n))
    return s.replace(a,b,1)

def patch_touch(s):
    if TAG in s:return s
    if "A52_PHASE447I_TOUCH_LIFECYCLE_DIAG_V1" not in s:
        raise RuntimeError("P447N first apply 447I touch port")
    s=once(s,"#include <linux/input/mt.h>\n",
           "#include <linux/input/mt.h>\n#include <linux/timekeeping.h>\n","timekeeping")
    token="static atomic_t a52i_touch_life_samples = ATOMIC_INIT(0);\n"
    s=once(s,token,token+r'''
/* A52_PHASE447N_TOUCH_USB_LATE_TRACE_V1 */
static atomic_t a52n_touch_window_count = ATOMIC_INIT(0);

static bool a52n_touch_window(void)
{
	time64_t sec = ktime_get_boottime_seconds();

	return sec >= 60 && sec <= 140;
}
''',"window state")
    token="info->finger[TouchID].z = p_event_coord->z & 0x3F;\n"
    s=once(s,token,r'''/* Capture next 200 actions in boot window; never change reports. */
			if (a52n_touch_window() &&
			    atomic_read(&a52n_touch_window_count) < 200) {
				int seq = atomic_inc_return(&a52n_touch_window_count);

				if (seq <= 200)
					pr_info("P447N TOUCH WIN sec=%lld seq=%d id=%u action=%u prev=%u count=%d x=%u y=%u state=%d\n",
						(long long)ktime_get_boottime_seconds(),
						seq, TouchID, info->finger[TouchID].action,
						prev_action, info->touch_count,
						info->finger[TouchID].x,
						info->finger[TouchID].y,
						info->fts_power_state);
			}
			'''+token,"finger events")
    token="void fts_release_all_finger(struct fts_ts_info *info)\n{\n\tint i;\n"
    s=once(s,token,token+r'''
	if (a52n_touch_window())
		pr_info("P447N TOUCH RELEASE_ALL sec=%lld count=%d power=%d flip=%d\n",
			(long long)ktime_get_boottime_seconds(),
			info->touch_count, info->fts_power_state, info->flip_status_current);
''',"release all")
    token="if (info->flip_status_prev != info->flip_status_current || info->prox_power_off) {\n\t\tinput_report_key(info->input_dev, KEY_INT_CANCEL, 1);"
    s=once(s,token,r'''if (info->flip_status_prev != info->flip_status_current || info->prox_power_off) {
		if (a52n_touch_window())
			pr_info("P447N TOUCH CANCEL sec=%lld flip_prev=%d flip_now=%d prox=%d count=%d\n",
				(long long)ktime_get_boottime_seconds(), info->flip_status_prev,
				info->flip_status_current, info->prox_power_off,
				info->touch_count);
		input_report_key(info->input_dev, KEY_INT_CANCEL, 1);''',"cancel key")
    token="static int fts_input_open(struct input_dev *dev)\n{\n\tstruct fts_ts_info *info = input_get_drvdata(dev);\n\tint retval;\n"
    s=once(s,token,token+r'''
	if (a52n_touch_window())
		pr_info("P447N TOUCH CALLBACK open sec=%lld done=%d power=%d count=%d\n",
			(long long)ktime_get_boottime_seconds(),
			info->info_work_done, info->fts_power_state, info->touch_count);
''',"open")
    token="static void fts_input_close(struct input_dev *dev)\n{\n\tstruct fts_ts_info *info = input_get_drvdata(dev);\n"
    s=once(s,token,token+r'''
	if (a52n_touch_window())
		pr_info("P447N TOUCH CALLBACK close sec=%lld done=%d power=%d count=%d\n",
			(long long)ktime_get_boottime_seconds(),
			info->info_work_done, info->fts_power_state, info->touch_count);
''',"close")
    for name in ("stop","start"):
        token="static int fts_"+name+"_device(struct fts_ts_info *info)\n{\n"
        s=once(s,token,token+r'''
	if (a52n_touch_window())
		pr_info("P447N TOUCH CALLBACK %s sec=%lld power=%d count=%d\n",
			__func__, (long long)ktime_get_boottime_seconds(),
			info->fts_power_state, info->touch_count);
''',name)
    return s

def patch_usb(s):
    if TAG in s:return s
    if "static int configfs_composite_bind(struct usb_gadget *gadget," not in s:
        raise RuntimeError("P447N expected ConfigFS composite bind")
    token="\tret = composite_dev_prepare(composite, cdev);\n\tif (ret)\n\t\treturn ret;"
    s=once(s,token,r'''	/* A52_PHASE447N_TOUCH_USB_LATE_TRACE_V1 */
	ret = composite_dev_prepare(composite, cdev);
	pr_info("P447N USB CFG stage=PREPARE rc=%d\n", ret);
	if (ret)
		return ret;''',"composite prepare")
    token="\t\t\tret = usb_add_function(c, f);\n\t\t\tif (ret) {"
    s=once(s,token,r'''			pr_info("P447N USB FUNC stage=ENTER name=%s config=%s\n",
				f->name, c->label);
			ret = usb_add_function(c, f);
			pr_info("P447N USB FUNC stage=EXIT name=%s config=%s rc=%d\n",
				f->name, c->label, ret);
			if (ret) {''',"per-function bind")
    token="\t\t\t\tret = PTR_ERR(s);\n\t\t\t\tgoto err_comp_cleanup;"
    s=once(s,token,r'''				ret = PTR_ERR(s);
				pr_info("P447N USB CFG stage=STRINGS rc=%d\n", ret);
				goto err_comp_cleanup;''',"string descriptor failure")
    token="\t\tret = usb_gadget_check_config(cdev->gadget);\n\t\tif (ret)"
    s=once(s,token,r'''		ret = usb_gadget_check_config(cdev->gadget);
		pr_info("P447N USB CFG stage=CHECK_CONFIG rc=%d\n", ret);
		if (ret)''',"configuration validity")
    return s

def check(root):
    a=(root/TOUCH).read_text()
    b=(root/USB).read_text()
    for x in (TAG,"P447N TOUCH WIN sec=","P447N TOUCH RELEASE_ALL",
              "P447N TOUCH CANCEL","P447N TOUCH CALLBACK open",
              "P447N TOUCH CALLBACK close","a52n_touch_window_count",
              "A52_PHASE447I_TOUCH_LIFECYCLE_DIAG_V1"):
        if x not in a:raise RuntimeError("P447N missing touch "+x)
    for x in (TAG,"P447N USB FUNC stage=ENTER",
              "P447N USB FUNC stage=EXIT","P447N USB CFG stage=PREPARE",
              "P447N USB CFG stage=STRINGS","P447N USB CFG stage=CHECK_CONFIG"):
        if x not in b:raise RuntimeError("P447N missing USB "+x)
    if a.count("P447N TOUCH WIN sec=")!=1 or b.count("P447N USB FUNC stage=EXIT")!=1:
        raise RuntimeError("P447N instrumentation must be unique")
    print("P447N PASS: 60..140s touch transitions and 200 input events; ConfigFS function bind; diagnostic only")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    x=p.parse_args()
    if not x.check_only:
        ta=x.root/TOUCH
        ub=x.root/USB
        a=patch_touch(ta.read_text())
        b=patch_usb(ub.read_text())
        # Atomic w.r.t. all source-anchor resolution; no partial patch.
        ta.write_text(a)
        ub.write_text(b)
    check(x.root)

if __name__=="__main__":
    main()
