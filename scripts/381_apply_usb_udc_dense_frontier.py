#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE381_USB_UDC_DENSE_FRONTIER_V1"

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
SERIAL = Path("drivers/usb/gadget/legacy/serial.c")
UDC = Path("drivers/usb/gadget/udc/core.c")
DWC3 = Path("drivers/usb/dwc3/gadget.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase381 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_rec(text: str) -> str:
    if "A52_PHASE381_RETENTION_V1" in text:
        return text
    old = '''\t       !strncmp(message, "V380 ", 5);\n'''
    new = '''\t       !strncmp(message, "V380 ", 5) ||\n\t       !strncmp(message, "V381 ", 5); /* A52_PHASE381_RETENTION_V1 */\n'''
    return one(text, old, new, "recorder retention")


def patch_ufs(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE380_USB_BLOCK_LIVE_DEBUG_V1" not in text:
        raise SystemExit("Phase381 requires Phase380 lineage")
    text = one(text,
        "#define A52_R380_SNAPSHOT_COUNT      6U\n",
        "#define A52_R380_SNAPSHOT_COUNT      11U\n",
        "snapshot count")
    text = one(text,
        "#define A52_R380_TAGS_PER_SNAPSHOT   6U\n",
        "#define A52_R380_TAGS_PER_SNAPSHOT   2U\n",
        "tag cap")
    old = '''static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t15000U, 16000U, 16500U, 17000U, 18000U, 19000U,
};
'''
    new = '''/* A52_PHASE381_USB_UDC_DENSE_FRONTIER_V1
 * Phase380 proved UFS was clean through 18.004 s but the 19 s record never
 * arrived. Resolve that sub-second frontier without using system_wq.
 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t18000U, 18100U, 18200U, 18300U, 18400U, 18500U,
\t18600U, 18700U, 18800U, 18900U, 19000U,
};
'''
    return one(text, old, new, "dense UFS targets")


def add_recorder_include(text: str, anchor: str, label: str) -> str:
    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc in text:
        return text
    return one(text, anchor, anchor + inc, label)


def patch_serial(text: str) -> str:
    if "A52_PHASE381_GSERIAL_BIND_TRACE_V1" in text:
        return text
    text = add_recorder_include(text, "#include <linux/tty_flip.h>\n", "g_serial include")
    old = '''static int __init init(void)
{
\t/* We *could* export two configs; that'd be much cleaner...
'''
    new = '''/* A52_PHASE381_GSERIAL_BIND_TRACE_V1 */
static int __init init(void)
{
\tint a52_ret;

\ta52_ackfr_record("V381 GS init enable=%u acm=%u obex=%u ports=%u",
\t\t\t  enable, use_acm, use_obex, n_ports);
\t/* We *could* export two configs; that'd be much cleaner...
'''
    text = one(text, old, new, "g_serial init entry")
    old = '''\tif (!enable)
\t\treturn 0;

\treturn usb_composite_probe(&gserial_driver);
}
'''
    new = '''\tif (!enable) {
\t\ta52_ackfr_record("V381 GS disabled");
\t\treturn 0;
\t}

\ta52_ret = usb_composite_probe(&gserial_driver);
\ta52_ackfr_record("V381 GS composite_probe ret=%d", a52_ret);
\treturn a52_ret;
}
'''
    return one(text, old, new, "g_serial probe result")


def patch_udc(text: str) -> str:
    if "A52_PHASE381_UDC_CORE_TRACE_V1" in text:
        return text
    text = add_recorder_include(text, "#include <linux/usb/ch9.h>\n", "UDC include")

    old = '''int usb_gadget_probe_driver(struct usb_gadget_driver *driver)
{
\tstruct usb_udc\t\t*udc = NULL;
\tint\t\t\tret = -ENODEV;
'''
    new = '''/* A52_PHASE381_UDC_CORE_TRACE_V1 */
int usb_gadget_probe_driver(struct usb_gadget_driver *driver)
{
\tstruct usb_udc\t\t*udc = NULL;
\tint\t\t\tret = -ENODEV;

\ta52_ackfr_record("V381 UDC probe drv=%s",
\t\t\tdriver && driver->function ? driver->function : "?");
'''
    text = one(text, old, new, "UDC probe entry")

    old = '''\tif (!driver->match_existing_only) {
\t\tlist_add_tail(&driver->pending, &gadget_driver_pending_list);
\t\tpr_info("udc-core: couldn't find an available UDC - added [%s] to list of pending drivers\\n",
\t\t\tdriver->function);
\t\tret = 0;
\t}
'''
    new = '''\tif (!driver->match_existing_only) {
\t\tlist_add_tail(&driver->pending, &gadget_driver_pending_list);
\t\tpr_info("udc-core: couldn't find an available UDC - added [%s] to list of pending drivers\\n",
\t\t\tdriver->function);
\t\ta52_ackfr_record("V381 UDC pending drv=%s", driver->function);
\t\tret = 0;
\t}
'''
    text = one(text, old, new, "UDC pending")

    old = '''found:
\tret = udc_bind_to_driver(udc, driver);
\tmutex_unlock(&udc_lock);
\treturn ret;
}
'''
    new = '''found:
\ta52_ackfr_record("V381 UDC found udc=%s drv=%s busy=%u",
\t\t\t  dev_name(&udc->dev), driver->function, !!udc->driver);
\tret = udc_bind_to_driver(udc, driver);
\ta52_ackfr_record("V381 UDC bind ret=%d udc=%s drv=%s",
\t\t\t  ret, dev_name(&udc->dev), driver->function);
\tmutex_unlock(&udc_lock);
\treturn ret;
}
'''
    text = one(text, old, new, "UDC bind result")

    old = '''\tmutex_lock(&udc_lock);
\tlist_add_tail(&udc->list, &udc_list);

\tret = device_add(&udc->dev);
'''
    new = '''\tmutex_lock(&udc_lock);
\tlist_add_tail(&udc->list, &udc_list);
\ta52_ackfr_record("V381 UDC add gadget=%s parent=%s",
\t\t\t  gadget->name ? gadget->name : "?",
\t\t\t  gadget->dev.parent ? dev_name(gadget->dev.parent) : "?");

\tret = device_add(&udc->dev);
'''
    text = one(text, old, new, "UDC registration")
    return text


def patch_dwc3(text: str) -> str:
    if "A52_PHASE381_DWC3_GADGET_TRACE_V1" in text:
        return text
    text = add_recorder_include(text, "#include <linux/usb/gadget.h>\n", "DWC3 include")

    old = '''int dwc3_gadget_init(struct dwc3 *dwc)
{
\tint ret;
\tint irq;
\tstruct device *dev;

'''
    new = '''/* A52_PHASE381_DWC3_GADGET_TRACE_V1 */
int dwc3_gadget_init(struct dwc3 *dwc)
{
\tint ret;
\tint irq;
\tstruct device *dev;

\ta52_ackfr_record("V381 DWC3 init dev=%s max=%u",
\t\t\t  dev_name(dwc->dev), dwc->maximum_speed);
'''
    text = one(text, old, new, "DWC3 init entry")

    old = '''\tret = usb_add_gadget(dwc->gadget);
\tif (ret) {
\t\tdev_err(dwc->dev, "failed to add gadget\\n");
\t\tgoto err5;
\t}
'''
    new = '''\ta52_ackfr_record("V381 DWC3 add_gadget enter dev=%s",
\t\t\t  dev_name(dwc->dev));
\tret = usb_add_gadget(dwc->gadget);
\ta52_ackfr_record("V381 DWC3 add_gadget ret=%d dev=%s",
\t\t\t  ret, dev_name(dwc->dev));
\tif (ret) {
\t\tdev_err(dwc->dev, "failed to add gadget\\n");
\t\tgoto err5;
\t}
'''
    text = one(text, old, new, "DWC3 add gadget")

    old = '''static int dwc3_gadget_pullup(struct usb_gadget *g, int is_on)
{
\tstruct dwc3\t\t*dwc = gadget_to_dwc(g);
\tstruct dwc3_vendor\t*vdwc = container_of(dwc, struct dwc3_vendor, dwc);
\tint\t\t\tret;

'''
    new = '''static int dwc3_gadget_pullup(struct usb_gadget *g, int is_on)
{
\tstruct dwc3\t\t*dwc = gadget_to_dwc(g);
\tstruct dwc3_vendor\t*vdwc = container_of(dwc, struct dwc3_vendor, dwc);
\tint\t\t\tret;

\ta52_ackfr_record("V381 DWC3 pullup req=%d state=%u speed=%u",
\t\t\t  !!is_on, g->state, g->speed);
'''
    text = one(text, old, new, "DWC3 pullup entry")
    return text


def validate(root: Path) -> None:
    checks = {
        REC: ("A52_PHASE381_RETENTION_V1", 'V381 "'),
        UFS: (MARK, "18100U", "18900U"),
        SERIAL: ("A52_PHASE381_GSERIAL_BIND_TRACE_V1", "V381 GS composite_probe"),
        UDC: ("A52_PHASE381_UDC_CORE_TRACE_V1", "V381 UDC bind ret="),
        DWC3: ("A52_PHASE381_DWC3_GADGET_TRACE_V1", "V381 DWC3 add_gadget ret="),
    }
    for rel, tokens in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in data:
                raise SystemExit(f"Phase381 validation missing {token} in {rel}")


def run(root: Path) -> None:
    paths = (REC, UFS, SERIAL, UDC, DWC3)
    for rel in paths:
        if not (root / rel).is_file():
            raise SystemExit(f"Phase381 missing source: {rel}")

    p = root / REC
    p.write_text(patch_rec(p.read_text()), encoding="utf-8")
    p = root / UFS
    p.write_text(patch_ufs(p.read_text()), encoding="utf-8")
    p = root / SERIAL
    p.write_text(patch_serial(p.read_text()), encoding="utf-8")
    p = root / UDC
    p.write_text(patch_udc(p.read_text()), encoding="utf-8")
    p = root / DWC3
    p.write_text(patch_dwc3(p.read_text()), encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
        print("Phase381 USB/UDC dense-frontier audit: PASS")
        return 0
    run(ns.root)
    print("Phase381 USB/UDC dense-frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
