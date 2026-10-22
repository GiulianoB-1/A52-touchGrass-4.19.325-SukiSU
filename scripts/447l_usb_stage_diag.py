#!/usr/bin/env python3
"""P447L READ-ONLY USB fault stage tracer, separate from 447K display-only boot.

The existing log "udc a600000.dwc3: failed to start g1: -19"
is printed for failures in THREE different phases of udc_bind_to_driver:
composite driver->bind, usb_gadget_udc_start, usb_udc_connect_control.
Do not interpret it as a proven DWC3 role failure without this trace.

This patch changes no return values, role, regulator vote, PM state, console
handover, USB gadget ownership or charging behavior.
"""
from pathlib import Path
import argparse

TAG = "A52_PHASE447L_USB_BIND_STAGE_TRACE_V1"
UDC = Path("drivers/usb/gadget/udc/core.c")
GLUE = Path("drivers/usb/dwc3/dwc3-qcom.c")

def once(s, old, new, why):
    n = s.count(old)
    if n != 1:
        raise RuntimeError("P447L " + why + " anchor count=" + str(n))
    return s.replace(old, new, 1)

def patch_udc(s):
    if TAG in s:
        return s
    start = s.index("static int udc_bind_to_driver(struct usb_udc *udc, struct usb_gadget_driver *driver)")
    end = s.index("\nint usb_gadget_probe_driver(", start)
    before, sub, after = s[:start], s[start:end], s[end:]
    sub = once(sub, "\tret = driver->bind(udc->gadget, driver);",
      r'''	/* A52_PHASE447L_USB_BIND_STAGE_TRACE_V1 */
	dev_info(&udc->dev, "P447L USB BIND stage=COMPOSITE_ENTER gadget=%s udc=%s\n",
		 driver->function, dev_name(&udc->dev));
	ret = driver->bind(udc->gadget, driver);
	dev_info(&udc->dev, "P447L USB BIND stage=COMPOSITE_EXIT gadget=%s rc=%d\n",
		 driver->function, ret);''', "composite bind")
    sub = once(sub, "\tret = usb_gadget_udc_start(udc);",
      r'''	dev_info(&udc->dev, "P447L USB BIND stage=UDC_START_ENTER gadget=%s\n",
		 driver->function);
	ret = usb_gadget_udc_start(udc);
	dev_info(&udc->dev, "P447L USB BIND stage=UDC_START_EXIT gadget=%s rc=%d\n",
		 driver->function, ret);''', "UDC start")
    sub = once(sub, "\tret = usb_udc_connect_control(udc);",
      r'''	dev_info(&udc->dev, "P447L USB BIND stage=CONNECT_ENTER gadget=%s\n",
		 driver->function);
	ret = usb_udc_connect_control(udc);
	dev_info(&udc->dev, "P447L USB BIND stage=CONNECT_EXIT gadget=%s rc=%d\n",
		 driver->function, ret);''', "UDC connect")
    return before + sub + after

def patch_glue(s):
    if TAG in s:
        return s
    start = s.index("static int dwc3_qcom_suspend(struct dwc3_qcom *qcom, bool wakeup)")
    end = s.index("\nstatic irqreturn_t qcom_dwc3_resume_irq(", start)
    before, sub, after = s[:start], s[start:end], s[end:]
    if "static int dwc3_qcom_resume(struct dwc3_qcom *qcom, bool wakeup)" not in sub:
        raise RuntimeError("P447L no expected qcom resume path")
    sub = once(sub,
        "\tif (qcom->is_suspended)\n\t\treturn 0;",
        r'''	/* A52_PHASE447L_USB_BIND_STAGE_TRACE_V1 */
	dev_info(qcom->dev, "P447L USB SUSPEND enter wakeup=%d suspended=%d mode=%d\n",
		 wakeup, qcom->is_suspended, qcom->mode);
	if (qcom->is_suspended)
		return 0;''',"glue suspend entry")
    # Only instrument entry. Existing QUSB regulator/PLL return behavior is
    # preserved; no assumption that the PLL is powered when this runs.
    sub = once(sub,
        "\tif (!qcom->is_suspended)\n\t\treturn 0;",
        r'''	dev_info(qcom->dev, "P447L USB RESUME enter wakeup=%d suspended=%d mode=%d\n",
		 wakeup, qcom->is_suspended, qcom->mode);
	if (!qcom->is_suspended)
		return 0;''',"glue resume entry")
    return before + sub + after

def check(root):
    udc = (root / UDC).read_text()
    glue = (root / GLUE).read_text()
    for t in (TAG, "P447L USB BIND stage=COMPOSITE_ENTER",
              "P447L USB BIND stage=COMPOSITE_EXIT",
              "P447L USB BIND stage=UDC_START_EXIT",
              "P447L USB BIND stage=CONNECT_EXIT",
              "ret = driver->bind(udc->gadget, driver);",
              "ret = usb_gadget_udc_start(udc);",
              "ret = usb_udc_connect_control(udc);"):
        if t not in udc: raise RuntimeError("P447L missing udc stage " + t)
    for t in (TAG, "P447L USB SUSPEND enter",
              "P447L USB RESUME enter"):
        if t not in glue: raise RuntimeError("P447L missing qcom PM stage " + t)
    print("P447L PASS: read-only UDC bind staging and QCOM suspend/resume logging, no USB behavior changes")

def main():
    a = argparse.ArgumentParser()
    a.add_argument("--root", type=Path, required=True)
    a.add_argument("--check-only", action="store_true")
    x = a.parse_args()
    if not x.check_only:
        source = (x.root / UDC).read_text()
        glue = (x.root / GLUE).read_text()
        # Resolve all exact anchors before writing either file.
        source = patch_udc(source)
        glue = patch_glue(glue)
        (x.root / UDC).write_text(source)
        (x.root / GLUE).write_text(glue)
    check(x.root)

if __name__ == "__main__":
    main()
