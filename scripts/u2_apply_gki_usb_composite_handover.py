#!/usr/bin/env python3
"""A52 U2: hand early g_serial UDC ownership to Android ConfigFS ADB+ACM.

Only the A52 UDC a600000.dwc3 is affected. Android's existing gadget,
descriptors, ACM and ADB functions are NOT replaced; when Android writes
its UDC attribute, release the early g_serial first. Linux 5.10 ConfigFS
ACM may allocate ttyGS1 while the early ttyGS0 is still allocated, so
enable the ACM console for its actual port number (using u_serial) and
request console=ttyGS1 in the boot cmdline as an additional console.

This is an experimental instrumented boot. Do not remove the U1 fallback.
"""
from pathlib import Path
import argparse

MARK = "A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1"
SERIAL = Path("drivers/usb/gadget/legacy/serial.c")
CONFIGFS = Path("drivers/usb/gadget/configfs.c")
ACM = Path("drivers/usb/gadget/function/f_acm.c")

def one(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"U2 {label}: required one exact anchor; got {n}")
    return text.replace(old, new, 1)

def patch_serial(text):
    if MARK in text:
        return text
    needle = "static int __init init(void)\n{\n"
    code = """/* A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1
 * Called from Android's ConfigFS UDC write (process context, before the
 * configfs gadget probes the A52 UDC). No udc_lock is held at this point.
 * Do not affect other UDCs; caller checks a600000.dwc3 explicitly.
 */
int a52_u2_release_early_serial(void)
{
    int ret;

    if (!enable) {
        pr_info("A52U2 serial already released\\n");
        return 0;
    }

    pr_warn("A52U2 UDC handover: releasing early ttyGS g_serial\\n");
    ret = switch_gserial_enable(false);
    if (ret) {
        pr_err("A52U2 UDC handover: g_serial release failed %d\\n", ret);
        return ret;
    }
    enable = false;
    pr_warn("A52U2 UDC handover: g_serial released, ConfigFS may bind\\n");
    return 0;
}
EXPORT_SYMBOL_GPL(a52_u2_release_early_serial);

"""
    return one(text, needle, code + needle, "g_serial release symbol")

def patch_configfs(text):
    if MARK in text:
        return text
    needle = "static ssize_t gadget_dev_desc_UDC_store(struct config_item *item,\n"
    helper = """/* A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1
 * The A52 GKI U1 boot binds g_serial to a600000.dwc3 at ~1.2 s.
 * Android subsequently gets -EBUSY writing g1/UDC at ~13 s.
 * Release only this early serial gadget immediately before the write.
 */
#if IS_BUILTIN(CONFIG_USB_G_SERIAL)
extern int a52_u2_release_early_serial(void);
#endif

"""
    text = one(text, needle, helper + needle, "ConfigFS handover declaration")
    old = """		gi->composite.gadget_driver.udc_name = name;
		ret = usb_gadget_probe_driver(&gi->composite.gadget_driver);
"""
    new = """		/* A52 U2: one-shot release before Android's composite bind. */
#if IS_BUILTIN(CONFIG_USB_G_SERIAL)
		if (!strcmp(name, "a600000.dwc3")) {
			pr_warn("A52U2 ConfigFS requests A52 UDC: begin handover\\n");
			ret = a52_u2_release_early_serial();
			if (ret)
				goto err;
		}
#endif
		gi->composite.gadget_driver.udc_name = name;
		ret = usb_gadget_probe_driver(&gi->composite.gadget_driver);
		pr_warn("A52U2 ConfigFS UDC bind result=%d\\n", ret);
"""
    return one(text, old, new, "ConfigFS exact probe")

def patch_acm(text):
    if MARK in text:
        return text
    needle = """	ret = gserial_alloc_line(&opts->port_num);
	if (ret) {
		kfree(opts);
		return ERR_PTR(ret);
	}
"""
    new = needle + """	/* A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1
	 * ACM may allocate ttyGS1 while legacy g_serial still owns ttyGS0.
	 * Enable the actual ACM port console; the boot cmdline requests both.
	 * Keep ACM functional if optional console registration fails.
	 */
#if IS_ENABLED(CONFIG_U_SERIAL_CONSOLE)
	{
		ssize_t c_ret = gserial_set_console(opts->port_num, "1", 1);
		pr_warn("A52U2 ConfigFS ACM port=ttyGS%u console_rc=%zd\\n",
			opts->port_num, c_ret);
	}
#endif
"""
    return one(text, needle, new, "ACM port console")

def validate(root):
    for rel, expected in [
        (SERIAL, ["A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1",
                  "a52_u2_release_early_serial", "switch_gserial_enable(false)"]),
        (CONFIGFS, ["A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1",
                    '"a600000.dwc3"', "a52_u2_release_early_serial()",
                    "usb_gadget_probe_driver(&gi->composite.gadget_driver)"]),
        (ACM, ["A52_PHASE_U2_GSERIAL_CONFIGFS_HANDOVER_V1",
               "gserial_set_console(opts->port_num", "gserial_alloc_line"]),
    ]:
        t = (root / rel).read_text()
        for item in expected:
            if item not in t:
                raise RuntimeError(f"U2 check failed: {rel}: {item}")
    print("A52U2 source audit PASS: A52-only UDC handover and actual ACM port console")
    print("A52U2 runtime NOT PROVEN: check ADB enumeration and PuTTY after handover")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if not args.check_only:
        for rel, fn in ((SERIAL, patch_serial),
                        (CONFIGFS, patch_configfs),
                        (ACM, patch_acm)):
            path = args.root / rel
            if not path.is_file():
                raise RuntimeError("missing kernel source " + str(path))
            path.write_text(fn(path.read_text()))
    validate(args.root)

if __name__ == "__main__":
    main()
