#!/usr/bin/env python3
"""Phase446X A52 TG STM FTS5CU56A -> 5.10, entirely firmware-write locked.

Copies only SHA-pinned TG sources from workspace/touchgrass-a52xq and refuses
unexpected source drift. fts_sec.c stays TEXTUALLY included by fts_ts.c (never
compiled independently). All paths that could flash firmware return -EPERM,
including boot, hidden-menu/sysfs, kernel/UMS and low-level flash functions.
This script changes no display, KGSL, PMIC, RTC, or USB file.
"""
from pathlib import Path
import argparse,hashlib,re
TAG="A52_PHASE446X_STM_FTS_READ_ONLY_V1"
TG="drivers/input/touchscreen/stm/fts5cu56a"
DST="drivers/input/touchscreen/stm/fts5cu56a"
COMMON="drivers/input/common"
INPUT="include/linux/input"
REQUIRED=[f"{TG}/{p}" for p in ("fts_ts.c","fts_fwu.c","fts_sec.c","fts_ts.h","Kconfig","Makefile")]
REQUIRED += [f"{COMMON}/{p}" for p in ("sec_cmd.c","sec_tclm_v2.c","sec_secure_touch.c","Kconfig","Makefile")]
REQUIRED += [f"{INPUT}/{p}" for p in ("sec_cmd.h","sec_tclm_v2.h","sec_secure_touch.h","sec_tsp_log.h")]

def one(s,a,z,why):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P446X {why}: anchor occurs {n} times")
    return s.replace(a,z,1)

def function_body(s,signature,newbody):
    # Strict exact signature, lexical brace match. Refuse any unexpected layout.
    pos=s.find(signature)
    if pos<0 or s.find(signature,pos+1)>=0:raise RuntimeError("P446X function signature "+signature)
    at=s.index("{",pos);depth=0;quote=None;line=False;block=False;escaped=False
    for i in range(at,len(s)):
        ch=s[i];nxt=s[i+1] if i+1<len(s) else ''
        if line:
            if ch=='\n':line=False
            continue
        if block:
            if ch=='*' and nxt=='/':block=False
            continue
        if quote:
            if escaped:escaped=False
            elif ch=='\\':escaped=True
            elif ch==quote:quote=None
            continue
        if ch=='/' and nxt=='/':line=True;continue
        if ch=='/' and nxt=='*':block=True;continue
        if ch in "'\"":quote=ch;continue
        if ch=='{':depth+=1
        elif ch=='}':
            depth-=1
            if depth==0:return s[:at]+"{\n"+newbody+"\n}"+s[i+1:]
    raise RuntimeError("unbalanced C braces "+signature)

def fwu(s):
    if TAG in s:return s
    # Strip *all* ways a factory/ROM request could program controller flash.
    # Keep fts_fw_wait_for_event/echo_event and calibration helpers.
    blocked=[
      "int fts_fw_fillFlash(struct fts_ts_info *info, u32 address, u8 *data, int size)",
      "static int fts_fw_burn(struct fts_ts_info *info, u8 *fw_data)",
      "static const int fts_fw_updater(struct fts_ts_info *info, u8 *fw_data)",
      "static int fts_load_fw_from_kernel(struct fts_ts_info *info,",
      "static int fts_load_fw_from_ums(struct fts_ts_info *info, int type)",
      "int fts_fw_update_on_hidden_menu(struct fts_ts_info *info, int update_type)"]
    for sig in blocked:
        # Kernel-load helper signature can wrap onto second line.
        s=function_body(s,sig,'''	pr_warn_ratelimited("P446X TOUCH FW_WRITE_BLOCKED function=%s (no IC flash writes on GKI)\\n",__func__);
	return -EPERM;''')
    # Preserve the full TG parser for firmware header and IC-vs-bin version
    # reading, but make UPDATE->BURN unreachable (no auto flash or calibration).
    needle="retval = fts_fw_updater(info, fw_data);"
    s=one(s,needle,r'''pr_info("P446X TOUCH FW_VERSIONS ic=%04x bin=%04x ic_main=%04x bin_main=%04x fw_write=BLOCKED\n",
		info->fw_version_of_ic,info->fw_version_of_bin,
		info->fw_main_version_of_ic,info->fw_main_version_of_bin);
	retval=FTS_NOT_ERROR;
	goto done; /* Firmware always read-only on GKI. */''',"probe updater call")
    if "set_fs(" in s or "get_fs(" in s or "vfs_read(" in s:
        raise RuntimeError("unsafe 4.19 filesystem API survived firmware lockout")
    if "fts_fw_burn(info, fw_data)" in s:
        raise RuntimeError("low-level firmware burner remains callable")
    s="/* "+TAG+" */\n"+s
    return s

def ts(s):
    if TAG in s:return s
    s=s.replace('#include <linux/wakelock.h>','#include <linux/pm_wakeup.h>')
    s='#include <linux/atomic.h>\n#include <linux/ktime.h>\n#include <linux/sched.h>\n'+s
    s=one(s,"wake_lock_timeout(&info->wakelock, msecs_to_jiffies(500));",
        "__pm_wakeup_event(info->wakelock, 500);","wake timeout")
    s=one(s,'wake_lock_init(&info->wakelock, WAKE_LOCK_SUSPEND, "tsp_wakelock");',
        '''info->wakelock=wakeup_source_register(&info->client->dev,"tsp_wakelock");
	if (!info->wakelock) {
		retval=-ENOMEM;
		goto err_fts_init;
	}''',"wake init")
    s=s.replace('wake_lock_destroy(&info->wakelock);','wakeup_source_unregister(info->wakelock);')
    s=one(s,'wake_lock(&info->wakelock);','__pm_stay_awake(info->wakelock);',"wake stay")
    s=one(s,'wake_unlock(&info->wakelock);','__pm_relax(info->wakelock);',"wake relax")
    s=one(s,'static const struct file_operations tsp_cmoffset_all_file_ops = {\n\t.owner = THIS_MODULE,\n\t.read = fts_tsp_cmoffset_read,\n\t.llseek = generic_file_llseek,',
        'static const struct proc_ops tsp_cmoffset_all_file_ops = {\n\t.proc_read = fts_tsp_cmoffset_read,\n\t.proc_lseek = generic_file_llseek,',"proc_ops")
    s=one(s,'struct timeval current_time;','struct timespec64 current_time;',"timespec")
    s=one(s,'do_gettimeofday(&current_time);','ktime_get_real_ts64(&current_time);',"ktime")
    s=one(s,'-sys_tz.tz_minuteswest * 60','0',"UTC")
    s=one(s,'static int fts_probe(struct i2c_client *client, const struct i2c_device_id *idp)\n{',
        '''static int fts_probe(struct i2c_client *client, const struct i2c_device_id *idp)
{
	pr_info("P446X TOUCH probe-enter bus=%d addr=%#x irq=%d pid=%d comm=%s\\n",
		client->adapter?i2c_adapter_id(client->adapter):-1,
		client->addr,client->irq,task_pid_nr(current),current->comm);''',"probe enter")
    s=one(s,'\tinfo->probe_done = true;',
        '''	pr_info("P446X TOUCH INPUT name=%s irq=%d i2c_errors=%d fw_ic=%04x fw_bin=%04x\\n",
		info->input_dev->name,info->irq,atomic_read(&a52x_i2c_errors),
		info->fw_version_of_ic,info->fw_version_of_bin);
	pr_info("P446X TOUCH probe-rc=0 i2c=0x49 irq=%d input=%s fw_ic=%04x fw_bin=%04x\\n",
		info->irq,info->input_dev->name,
		info->fw_version_of_ic,info->fw_version_of_bin);
	info->probe_done = true;''',"registered input witness")
    s=one(s,'\tinput_err(true, &client->dev, "%s: failed(%d)\\n", __func__, retval);',
        '''	pr_info("P446X TOUCH probe-rc=%d adapter=%d addr=0x49?%u actual=%#x irq=%d i2c_errors=%d\\n",
		retval,client->adapter?i2c_adapter_id(client->adapter):-1,
		client->addr==0x49,client->addr,client->irq,atomic_read(&a52x_i2c_errors));
	input_err(true, &client->dev, "%s: failed(%d)\\n", __func__, retval);''',"probe error")
    # Device-tree supply/GPIO -EPROBE_DEFER must propagate, never swallowed.
    for prop in ("tsp_icid","tsp_id","device_id","irq_gpio"):
        line=f'\tpdata->{prop} = of_get_named_gpio(np, "stm,'
        pos=s.find(line)
        if pos<0:raise RuntimeError("missing DT GPIO "+prop)
        end=s.index(";",pos)+1
        s=s[:end]+f"\n\tif (pdata->{prop} == -EPROBE_DEFER)\n\t\treturn -EPROBE_DEFER;"+s[end:]
    s=one(s,'client->irq = gpio_to_irq(pdata->irq_gpio);',
        '''client->irq = gpio_to_irq(pdata->irq_gpio);
	if (client->irq < 0)
		return client->irq;''',"IRQ err")
    # Set a per-source error counter using the already-known I2C transfer error branches.
    pos=s.index("int fts_write_reg(")
    s=s[:pos]+'''/* P446X TOUCH I2C diagnostics */
static atomic_t a52x_i2c_errors=ATOMIC_INIT(0);
static atomic_t a52x_touch_events=ATOMIC_INIT(0);
'''+s[pos:]
    # Use check near the end of the I2C transfer call; don't rewrite internal retries.
    s=one(s,'\t\tret = i2c_transfer(info->client->adapter, xfer_msg, 1);',
        '''		ret = i2c_transfer(info->client->adapter, xfer_msg, 1);
		if (ret<0) {
			atomic_inc(&a52x_i2c_errors);
			pr_info_ratelimited("P446X TOUCH I2C_WRITE_ERR rc=%d count=%d\\n",
				ret,atomic_read(&a52x_i2c_errors));
		}''',"I2C write count")
    s=one(s,'\t\tret = i2c_transfer(info->client->adapter, xfer_msg, 2);',
        '''		ret = i2c_transfer(info->client->adapter, xfer_msg, 2);
		if (ret<0) {
			atomic_inc(&a52x_i2c_errors);
			pr_info_ratelimited("P446X TOUCH I2C_READ_ERR rc=%d count=%d\\n",
				ret,atomic_read(&a52x_i2c_errors));
		}''',"I2C read count")
    needle='info->finger[TouchID].y = (p_event_coord->y_11_4 << 4) | (p_event_coord->y_3_0);'
    s=one(s,needle,needle+'''
			if (info->finger[TouchID].action==FTS_COORDINATE_ACTION_PRESS &&
			    atomic_add_unless(&a52x_touch_events,1,5))
				pr_info("P446X TOUCH EVENT event=%d id=%d x=%d y=%d\\n",
					atomic_read(&a52x_touch_events),TouchID,
					info->finger[TouchID].x,info->finger[TouchID].y);
''',"first 5 touches")
    # 5.10 input_mt_init_slots returns int: MUST propagate failures before
    # registration. At both call sites, probe has allocated input devices and
    # initialized wakelock; err_register_input is the correct unwind.
    s=one(s,
        "static void fts_set_input_prop(struct fts_ts_info *info, struct input_dev *dev, u8 propbit)\n{",
        "static int fts_set_input_prop(struct fts_ts_info *info, struct input_dev *dev, u8 propbit)\n{\n\tint rc;",
        "change input props to checked int")
    s=one(s,
        '''if (propbit == INPUT_PROP_POINTER)
		input_mt_init_slots(dev, FINGER_MAX, INPUT_MT_POINTER);
	else
		input_mt_init_slots(dev, FINGER_MAX, INPUT_MT_DIRECT);

	input_set_drvdata(dev, info);''',
        r'''if (propbit == INPUT_PROP_POINTER)
		rc=input_mt_init_slots(dev,FINGER_MAX,INPUT_MT_POINTER);
	else
		rc=input_mt_init_slots(dev,FINGER_MAX,INPUT_MT_DIRECT);
	if (rc) {
		pr_err("P446X TOUCH input_mt_init_slots FAILED rc=%d name=%s\n",
		       rc,dev->name?dev->name:"(unnamed)");
		return rc;
	}
	input_set_drvdata(dev, info);
	return 0;''',
        "return mt slot init")
    s=one(s,
        'fts_set_input_prop(info, info->input_dev_pad, INPUT_PROP_POINTER);',
        '''retval=fts_set_input_prop(info,info->input_dev_pad,INPUT_PROP_POINTER);
		if (retval)goto err_register_input;''',
        "pad init rc")
    s=one(s,
        'fts_set_input_prop(info, info->input_dev, INPUT_PROP_DIRECT);',
        '''retval=fts_set_input_prop(info,info->input_dev,INPUT_PROP_DIRECT);
	if (retval)goto err_register_input;''',
        "touch init rc")
    # Diagnostic and defer-safe regulator handling. TG original silently
    # returns success when a supplier is missing; don't continue with ERR_PTR.
    for rail in ("dvdd","avdd"):
        key=f'if (IS_ERR_OR_NULL(info->regulator_{rail})) {{'
        replace=f'''if (IS_ERR(info->regulator_{rail})) {{
		retval=PTR_ERR(info->regulator_{rail});
		info->regulator_{rail}=NULL;
		pr_info("P446X TOUCH REGULATOR_GET {rail} rc=%d\\n",retval);
		goto out;
	}}'''
        s=one(s,key,replace,"regulator "+rail)
    s=one(s,'if (info->board->power)\n\t\tinfo->board->power(info, true);',
        '''if (info->board->power) {
		retval=info->board->power(info,true);
		pr_info("P446X TOUCH REGULATOR_ENABLE avdd/dvdd rc=%d\\n",retval);
		if (retval) goto err_input_allocate_device;
	}''',"probe enable defer")
    # Early return avoids NULL deref if probe fails prior to allocating info.
    s=one(s,'\tinput_info(true, &client->dev, "%s: FTS Driver [70%s]\\n", __func__,',
        '''	pr_info("P446X TOUCH I2C_ID addr=%#x expected=0x49 irq=%d\\n",client->addr,client->irq);
	input_info(true, &client->dev, "%s: FTS Driver [70%s]\\n", __func__,''',"I2C identifier")
    # Kconfig gated driver registration before touching I2C device.
    s=one(s,'static int __init fts_driver_init(void)\n{',
        '''static bool a52x_touch_enabled=true;
static int __init a52x_touch_param(char *s)
{
	if (!s)return -EINVAL;
	if (!strcmp(s,"0"))a52x_touch_enabled=false;
	else if (!strcmp(s,"1"))a52x_touch_enabled=true;
	else return -EINVAL;
	return 0;
}
early_param("a52.touch",a52x_touch_param);
static int __init fts_driver_init(void)
{
	if (!a52x_touch_enabled) {
		pr_info("P446X TOUCH disabled a52.touch=0: no driver registration\\n");
		return 0;
	}
	pr_info("P446X TOUCH enabled a52.touch=1, registering STM I2C driver\\n");''',"kill switch")
    s="/* "+TAG+" */\n"+s
    return s

def header(s):
    s=one(s,'#include <linux/wakelock.h>','#include <linux/pm_wakeup.h>',"header wake lock")
    s=one(s,'struct wake_lock wakelock;','struct wakeup_source *wakelock;',"header wakelock pointer")
    return s

def linkage(root):
    paths=[
      ("drivers/input/Makefile",
       'obj-$(CONFIG_INPUT_TOUCHSCREEN) += common/'),
      ("drivers/input/touchscreen/Makefile",
       'obj-$(CONFIG_TOUCHSCREEN_STM_FTS5CU56A) += stm/fts5cu56a/'),
      ("drivers/input/touchscreen/Kconfig",
       'source "drivers/input/touchscreen/stm/fts5cu56a/Kconfig"'),
      ("drivers/input/Kconfig",
       'source "drivers/input/common/Kconfig"')]
    for p,add in paths:
        f=root/p;s=f.read_text()
        if add not in s:f.write_text(s.rstrip()+"\n"+add+"\n")
    mk=root/COMMON/"Makefile"
    s=mk.read_text()
    if "sec_cmd.o" not in s or "sec_tclm_v2.o" not in s:
        raise RuntimeError("TG sec common build missing")
    # The secure touch class is copied but not enabled until sec sysfs is
    # demonstrated. It must not silently drag in foldable notifier symbols.
    (root/TG/"Makefile").write_text("obj-$(CONFIG_TOUCHSCREEN_STM_FTS5CU56A) += fts_ts.o fts_fwu.o\n")
    k=root/TG/"Kconfig";s=k.read_text()
    s=s.replace('tristate "STMicroelectronics','bool "STMicroelectronics')
    if "depends on I2C" not in s:
        raise RuntimeError("TG FTS Kconfig missing required I2C dependency")
    k.write_text(s)

def check(root):
    c={DST+"/fts_ts.c":(TAG,'early_param("a52.touch"',"P446X TOUCH EVENT",
            "P446X TOUCH I2C_ID","P446X TOUCH INPUT","P446X TOUCH REGULATOR_ENABLE",
            "struct proc_ops","ktime_get_real_ts64","wakeup_source_register",
            "P446X TOUCH input_mt_init_slots FAILED"),
       DST+"/fts_fwu.c":(TAG,"FW_WRITE_BLOCKED","P446X TOUCH FW_VERSIONS"),
       DST+"/fts_ts.h":("struct wakeup_source *wakelock;",),
       COMMON+"/sec_cmd.c":("sec_cmd_init(",),
       INPUT+"/sec_cmd.h":("struct sec_cmd_data",)}
    for p,terms in c.items():
        s=(root/p).read_text()
        for term in terms:
            if term not in s:raise RuntimeError("P446X missing "+term+" in "+p)
    mk=(root/DST/"Makefile").read_text()
    if "fts_sec.o" in mk or "fts_sec.c" not in (root/DST/"fts_ts.c").read_text():
        raise RuntimeError("fts_sec.c MUST be included only, not a separate object")
    src=(root/DST/"fts_fwu.c").read_text()
    if "set_fs(" in src or "vfs_read(" in src or "fts_fw_burn(info, fw_data)" in src:
        raise RuntimeError("Firmware programming or set_fs compiled into GKI")
    print("P446X TOUCH SOURCE CONTRACT PASS: built-in FTS+sec_cmd/tclm, read-only firmware, a52.touch kill switch, first5, GPIO regulator defer")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--tg",type=Path)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:
        if not a.tg:raise RuntimeError("--tg pinned TG checkout required")
        for f in REQUIRED:
            src=a.tg/f
            if not src.is_file():raise RuntimeError("missing TG pinned "+str(src))
            dest=a.root/f;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                raise RuntimeError("refusing to overwrite preexisting GKI source "+str(dest))
            dest.write_bytes(src.read_bytes())
        for p,fn in ((DST+"/fts_ts.c",ts),(DST+"/fts_fwu.c",fwu),(DST+"/fts_ts.h",header)):
            f=a.root/p;f.write_text(fn(f.read_text()))
        linkage(a.root)
    check(a.root)
if __name__=="__main__":main()
