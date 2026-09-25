#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE382_STICKY_USB_CLIFF_CENSUS_V1"

HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
SYSCALL = Path("arch/arm64/kernel/syscall.c")
BLK = Path("block/blk-mq.c")
LOOP = Path("drivers/block/loop.c")
SERIAL = Path("drivers/usb/gadget/legacy/serial.c")
UDC = Path("drivers/usb/gadget/udc/core.c")
DWC3 = Path("drivers/usb/dwc3/gadget.c")
DWC3CORE = Path("drivers/usb/dwc3/core.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase382 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if "A52_ACKFR_USB_MODE_IN" in text:
        return text
    text = one(text, "#include <linux/compiler.h>\n",
               "#include <linux/compiler.h>\n#include <linux/types.h>\n",
               "header types")
    block = r'''
/* A52_PHASE382_STICKY_USB_CLIFF_CENSUS_V1 */
enum a52_ackfr_usbdiag_field {
	A52_ACKFR_USB_MODE_IN = 0,
	A52_ACKFR_USB_HW_MODE,
	A52_ACKFR_USB_MODE_OUT,
	A52_ACKFR_USB_CORE_MODE,
	A52_ACKFR_USB_GADGET_INIT,
	A52_ACKFR_USB_GADGET_ADD_RC,
	A52_ACKFR_USB_GS_PROBE_RC,
	A52_ACKFR_USB_UDC_BIND_RC,
	A52_ACKFR_USB_PULLUP_RC,
};

struct a52_ackfr_usbdiag_snapshot {
	s32 mode_in;
	s32 hw_mode;
	s32 mode_out;
	s32 core_mode;
	s32 gadget_init;
	s32 gadget_add_rc;
	s32 gs_probe_rc;
	s32 udc_bind_rc;
	s32 pullup_rc;
};

void a52_ackfr_usbdiag_set(unsigned int field, int value);
void a52_ackfr_usbdiag_snapshot(struct a52_ackfr_usbdiag_snapshot *out);
void a52_ackfr_usbdiag_emit(unsigned int sample);

'''
    return one(text, "\n#endif\n", block + "\n#endif\n", "header declarations")


REC_BLOCK = r'''
/* A52_PHASE382_STICKY_USB_CLIFF_CENSUS_V1
 *
 * Phase380/381 diagnostic prefixes were marked critical but were still
 * rejected by the older Phase243 admission gate.  Keep one small sticky USB
 * state vector and re-emit it near the 18.5 s failure frontier.  The state is
 * also copied into the raw triple-copy UFS sideband.
 */
#define A52_R382_USB_UNSEEN (-9999)
#define A52_R382_USB_PULLUP_ON_ENTER (-9998)
#define A52_R382_USB_PULLUP_OFF_ENTER (-9997)

static atomic_t a52_r382_usb_mode_in = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_hw_mode = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_mode_out = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_core_mode = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_gadget_init = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_gadget_add_rc = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_gs_probe_rc = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_udc_bind_rc = ATOMIC_INIT(A52_R382_USB_UNSEEN);
static atomic_t a52_r382_usb_pullup_rc = ATOMIC_INIT(A52_R382_USB_UNSEEN);

void a52_ackfr_usbdiag_set(unsigned int field, int value)
{
	switch (field) {
	case A52_ACKFR_USB_MODE_IN:
		atomic_set(&a52_r382_usb_mode_in, value);
		break;
	case A52_ACKFR_USB_HW_MODE:
		atomic_set(&a52_r382_usb_hw_mode, value);
		break;
	case A52_ACKFR_USB_MODE_OUT:
		atomic_set(&a52_r382_usb_mode_out, value);
		break;
	case A52_ACKFR_USB_CORE_MODE:
		atomic_set(&a52_r382_usb_core_mode, value);
		break;
	case A52_ACKFR_USB_GADGET_INIT:
		atomic_set(&a52_r382_usb_gadget_init, value);
		break;
	case A52_ACKFR_USB_GADGET_ADD_RC:
		atomic_set(&a52_r382_usb_gadget_add_rc, value);
		break;
	case A52_ACKFR_USB_GS_PROBE_RC:
		atomic_set(&a52_r382_usb_gs_probe_rc, value);
		break;
	case A52_ACKFR_USB_UDC_BIND_RC:
		atomic_set(&a52_r382_usb_udc_bind_rc, value);
		break;
	case A52_ACKFR_USB_PULLUP_RC:
		atomic_set(&a52_r382_usb_pullup_rc, value);
		break;
	default:
		break;
	}
}
EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_set);

void a52_ackfr_usbdiag_snapshot(struct a52_ackfr_usbdiag_snapshot *out)
{
	if (!out)
		return;
	out->mode_in = atomic_read(&a52_r382_usb_mode_in);
	out->hw_mode = atomic_read(&a52_r382_usb_hw_mode);
	out->mode_out = atomic_read(&a52_r382_usb_mode_out);
	out->core_mode = atomic_read(&a52_r382_usb_core_mode);
	out->gadget_init = atomic_read(&a52_r382_usb_gadget_init);
	out->gadget_add_rc = atomic_read(&a52_r382_usb_gadget_add_rc);
	out->gs_probe_rc = atomic_read(&a52_r382_usb_gs_probe_rc);
	out->udc_bind_rc = atomic_read(&a52_r382_usb_udc_bind_rc);
	out->pullup_rc = atomic_read(&a52_r382_usb_pullup_rc);
}
EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_snapshot);

void a52_ackfr_usbdiag_emit(unsigned int sample)
{
	struct a52_ackfr_usbdiag_snapshot s;

	a52_ackfr_usbdiag_snapshot(&s);
	a52_ackfr_record("V382 A s=%u mi=%d hw=%d mo=%d ci=%d",
			 sample, s.mode_in, s.hw_mode, s.mode_out, s.core_mode);
	a52_ackfr_record("V382 B s=%u gi=%d ga=%d gp=%d ub=%d pr=%d",
			 sample, s.gadget_init, s.gadget_add_rc,
			 s.gs_probe_rc, s.udc_bind_rc, s.pullup_rc);
}
EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_emit);

static bool a52_r382_diag_format(const char *fmt)
{
	if (!fmt)
		return false;
	return !strncmp(fmt, "U380", 4) ||
	       !strncmp(fmt, "B380", 4) ||
	       !strncmp(fmt, "L380", 4) ||
	       !strncmp(fmt, "USB380", 6) ||
	       !strncmp(fmt, "V380", 4) ||
	       !strncmp(fmt, "V381", 4) ||
	       !strncmp(fmt, "V382", 4);
}

'''


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    anchor = "void a52_ackfr_record(const char *fmt, ...)\n"
    text = one(text, anchor, REC_BLOCK + anchor, "recorder sticky block")

    old = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
"""
    new = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !a52_r382_diag_format(fmt) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
"""
    text = one(text, old, new, "post-retention admission")

    old = """\t    strncmp(fmt, "BOOT rs=ready", 13) &&
\t    strncmp(fmt, "BOOT phase=", 11))
\t\treturn;
"""
    new = """\t    strncmp(fmt, "BOOT rs=ready", 13) &&
\t    strncmp(fmt, "BOOT phase=", 11) &&
\t    strncmp(fmt, "U380", 4) &&
\t    strncmp(fmt, "B380", 4) &&
\t    strncmp(fmt, "L380", 4) &&
\t    strncmp(fmt, "USB380", 6) &&
\t    strncmp(fmt, "V380", 4) &&
\t    strncmp(fmt, "V381", 4) &&
\t    strncmp(fmt, "V382", 4))
\t\treturn;
"""
    text = one(text, old, new, "Phase243 diagnostic admission")

    old = """\t       !strncmp(message, "V380 ", 5) ||
\t       !strncmp(message, "V381 ", 5);
"""
    new = """\t       !strncmp(message, "V380 ", 5) ||
\t       !strncmp(message, "V381 ", 5) ||
\t       !strncmp(message, "V382 ", 5);
"""
    text = one(text, old, new, "critical V382")
    return text


def patch_dwc3_core(text: str) -> str:
    if "A52_ACKFR_USB_MODE_IN" in text:
        return text
    old = """\ta52_ackfr_record("V381 MODE in dr=%u hw=%u host=%u gad=%u dual=%u",
\t\t\t  dwc->dr_mode, hw_mode,
\t\t\t  IS_ENABLED(CONFIG_USB_DWC3_HOST),
\t\t\t  IS_ENABLED(CONFIG_USB_DWC3_GADGET),
\t\t\t  IS_ENABLED(CONFIG_USB_DWC3_DUAL_ROLE));
"""
    new = old + """\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_MODE_IN, dwc->dr_mode);
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_HW_MODE, hw_mode);
"""
    text = one(text, old, new, "DWC3 mode input sticky")

    old = """\ta52_ackfr_record("V381 MODE out dr=%u hw=%u", dwc->dr_mode, hw_mode);

\treturn 0;
"""
    new = """\ta52_ackfr_record("V381 MODE out dr=%u hw=%u", dwc->dr_mode, hw_mode);
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_MODE_OUT, dwc->dr_mode);

\treturn 0;
"""
    text = one(text, old, new, "DWC3 mode output sticky")

    old = """\ta52_ackfr_record("V381 CORE init_mode dr=%u", dwc->dr_mode);
\tswitch (dwc->dr_mode) {
"""
    new = """\ta52_ackfr_record("V381 CORE init_mode dr=%u", dwc->dr_mode);
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_CORE_MODE, dwc->dr_mode);
\tswitch (dwc->dr_mode) {
"""
    return one(text, old, new, "DWC3 core mode sticky")


def patch_dwc3(text: str) -> str:
    if "A52_ACKFR_USB_GADGET_INIT" in text:
        return text

    old = """\ta52_ackfr_record("V381 DWC3 init dev=%s max=%u",
\t\t\t  dev_name(dwc->dev), dwc->maximum_speed);
"""
    new = old + "\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_GADGET_INIT, 1);\n"
    text = one(text, old, new, "gadget init sticky")

    old = """\ta52_ackfr_record("V381 DWC3 add_gadget ret=%d dev=%s",
\t\t\t  ret, dev_name(dwc->dev));
"""
    new = old + "\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_GADGET_ADD_RC, ret);\n"
    text = one(text, old, new, "gadget add sticky")

    old = """\ta52_ackfr_record("V381 DWC3 pullup req=%d state=%u speed=%u",
\t\t\t  !!is_on, g->state, g->speed);
"""
    new = old + """\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_PULLUP_RC,
\t\t\t is_on ? -9998 : -9997);
"""
    text = one(text, old, new, "pullup entry sticky")

    old = """\t\tif (pm_runtime_suspended(dwc->dev))
\t\t\treturn 0;
"""
    new = """\t\tif (pm_runtime_suspended(dwc->dev)) {
\t\t\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_PULLUP_RC, 0);
\t\t\treturn 0;
\t\t}
"""
    text = one(text, old, new, "pullup suspended return")

    old = """\t\tif (ret < 0)
\t\t\tpm_runtime_set_suspended(dwc->dev);
\t\treturn ret;
\t}
"""
    new = """\t\tif (ret < 0)
\t\t\tpm_runtime_set_suspended(dwc->dev);
\t\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_PULLUP_RC, ret);
\t\treturn ret;
\t}
"""
    text = one(text, old, new, "pullup runtime return")

    old = """\tif (dwc->pullups_connected == is_on) {
\t\tpm_runtime_put(dwc->dev);
\t\treturn 0;
\t}
"""
    new = """\tif (dwc->pullups_connected == is_on) {
\t\tpm_runtime_put(dwc->dev);
\t\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_PULLUP_RC, 0);
\t\treturn 0;
\t}
"""
    text = one(text, old, new, "pullup already connected")

    old = """done:
\tpm_runtime_put(dwc->dev);

\treturn ret;
}
"""
    new = """done:
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_PULLUP_RC, ret);
\tpm_runtime_put(dwc->dev);

\treturn ret;
}
"""
    return one(text, old, new, "pullup final result")


def patch_serial(text: str) -> str:
    if "A52_ACKFR_USB_GS_PROBE_RC" in text:
        return text
    old = """\ta52_ackfr_record("V381 GS composite_probe ret=%d", a52_ret);
\treturn a52_ret;
"""
    new = """\ta52_ackfr_record("V381 GS composite_probe ret=%d", a52_ret);
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_GS_PROBE_RC, a52_ret);
\treturn a52_ret;
"""
    return one(text, old, new, "g_serial probe sticky")


def patch_udc(text: str) -> str:
    if "A52_PHASE382_UDC_BIND_STICKY_V1" in text:
        return text
    old = """static int udc_bind_to_driver(struct usb_udc *udc, struct usb_gadget_driver *driver)
{
\tint ret;

\tdev_dbg(&udc->dev, "registering UDC driver [%s]\\n",
"""
    new = """/* A52_PHASE382_UDC_BIND_STICKY_V1 */
static int udc_bind_to_driver(struct usb_udc *udc, struct usb_gadget_driver *driver)
{
\tint ret;

\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, -9998);
\tdev_dbg(&udc->dev, "registering UDC driver [%s]\\n",
"""
    text = one(text, old, new, "UDC bind entry sticky")

    old = """\tkobject_uevent(&udc->dev.kobj, KOBJ_CHANGE);
\treturn 0;

err_connect_control:
"""
    new = """\tkobject_uevent(&udc->dev.kobj, KOBJ_CHANGE);
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, 0);
\ta52_ackfr_record("V382 U bind drv=%s rc=0", driver->function);
\treturn 0;

err_connect_control:
"""
    text = one(text, old, new, "UDC bind success sticky")

    old = """\tudc->driver = NULL;
\tudc->gadget->dev.driver = NULL;
\treturn ret;
}
"""
    new = """\tudc->driver = NULL;
\tudc->gadget->dev.driver = NULL;
\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, ret);
\ta52_ackfr_record("V382 U bind drv=%s rc=%d", driver->function, ret);
\treturn ret;
}
"""
    return one(text, old, new, "UDC bind failure sticky")


def patch_ufs(text: str) -> str:
    if "A52_PHASE382_RAW_USB_SNAPSHOT_V1" in text:
        return text

    text = one(text,
        "#define A52_R380_VERSION             1U\n",
        "#define A52_R380_VERSION             2U\n",
        "raw UFS version")

    old = """static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t18000U, 18100U, 18200U, 18300U, 18400U, 18500U,
\t18600U, 18700U, 18800U, 18900U, 19000U,
};
"""
    new = """/* A52_PHASE382_RAW_USB_SNAPSHOT_V1 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t18300U, 18400U, 18450U, 18475U, 18500U, 18525U,
\t18550U, 18575U, 18600U, 18625U, 18650U,
};
"""
    text = one(text, old, new, "dense 18.5 frontier")

    old = """{
\tmemset(r, 0, sizeof(*r));
"""
    # Scope this replacement to a52_r380_fill_common only.
    start = text.find("static void a52_r380_fill_common(")
    if start < 0:
        raise SystemExit("Phase382 UFS fill_common missing")
    pos = text.find(old, start)
    if pos < 0:
        raise SystemExit("Phase382 UFS fill_common body anchor missing")
    new = """{
\tstruct a52_ackfr_usbdiag_snapshot usbdiag;

\tmemset(r, 0, sizeof(*r));
"""
    text = text[:pos] + new + text[pos + len(old):]

    old = """\tr->nutrs = hba->nutrs;
\tr->active_tags = active_tags;
}
"""
    new = """\tr->nutrs = hba->nutrs;
\tr->active_tags = active_tags;
\tBUILD_BUG_ON(sizeof(usbdiag) != sizeof(r->reserved));
\ta52_ackfr_usbdiag_snapshot(&usbdiag);
\tmemcpy(r->reserved, &usbdiag, sizeof(usbdiag));
}
"""
    text = one(text, old, new, "raw USB sticky copy")

    old = """\t\tif (now_ms >= a52_r380_target_ms[next]) {
\t\t\ta52_r380_take_snapshot(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
"""
    new = """\t\tif (now_ms >= a52_r380_target_ms[next]) {
\t\t\ta52_r380_take_snapshot(next);
\t\t\ta52_ackfr_usbdiag_emit(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
"""
    return one(text, old, new, "late USB re-emit")


def patch_census(text: str) -> str:
    if "A52_PHASE382_CLIFF_CENSUS_V1" in text:
        return text

    replacements = [
        ("#define A52_R377_COPY_BYTES         0x4000U\n",
         "#define A52_R377_COPY_BYTES         0x2400U\n"),
        ("#define A52_R377_SLOTS_PER_COPY     64U\n",
         "#define A52_R377_SLOTS_PER_COPY     36U\n"),
        ("#define A52_R377_SNAPSHOT_COUNT     4U\n",
         "#define A52_R377_SNAPSHOT_COUNT     3U\n"),
        ("#define A52_R377_THREADS_PER_SNAP   16U\n",
         "#define A52_R377_THREADS_PER_SNAP   12U\n"),
        ("#define A52_R377_COMMIT             0x377c0de5U\n",
         "#define A52_R377_COMMIT             0x382c0de5U\n"),
        ("#define A52_R377_VERSION            1U\n",
         "#define A52_R377_VERSION            2U\n"),
        ("\tu8 reserved[4];\n", "\tu32 crc32c;\n"),
    ]
    for old, new in replacements:
        text = one(text, old, new, "census macro/record update")

    old = """static void a52_r377_write_slot(unsigned int slot,
\t\t\t\tconst struct a52_r377_record *r)
{
\tunsigned int pos;
\tvoid *dst0;
\tvoid *dst1;

\tif (!READ_ONCE(a52_r377_sideband) || !r ||
\t    slot >= A52_R377_SLOTS_PER_COPY)
\t\treturn;

\tpos = slot * A52_R377_SLOT_BYTES;
\tdst0 = (u8 *)a52_r377_sideband + pos;
\tdst1 = (u8 *)a52_r377_sideband + A52_R377_COPY_BYTES + pos;
\tmemcpy(dst0, r, sizeof(*r));
\tmemcpy(dst1, r, sizeof(*r));
\twmb();
\t__flush_dcache_area(dst0, sizeof(*r));
\t__flush_dcache_area(dst1, sizeof(*r));
}
"""
    new = """/* A52_PHASE382_CLIFF_CENSUS_V1 */
static u32 a52_r382_crc32c(const void *buffer, size_t len)
{
\tconst u8 *bytes = buffer;
\tu32 crc = ~0U;
\tsize_t index;
\tunsigned int bit;

\tfor (index = 0; index < len; index++) {
\t\tcrc ^= bytes[index];
\t\tfor (bit = 0; bit < 8; bit++)
\t\t\tcrc = (crc >> 1) ^
\t\t\t\t((crc & 1) ? 0x82f63b78U : 0U);
\t}
\treturn ~crc;
}

static void a52_r377_write_slot(unsigned int slot,
\t\t\t\tconst struct a52_r377_record *r)
{
\tunsigned int pos;
\tvoid *dst0;
\tvoid *dst1;
\tvoid *dst2;

\tif (!READ_ONCE(a52_r377_sideband) || !r ||
\t    slot >= A52_R377_SLOTS_PER_COPY)
\t\treturn;

\tpos = slot * A52_R377_SLOT_BYTES;
\tdst0 = (u8 *)a52_r377_sideband + pos;
\tdst1 = (u8 *)a52_r377_sideband + A52_R377_COPY_BYTES + pos;
\tdst2 = (u8 *)a52_r377_sideband + 2U * A52_R377_COPY_BYTES + pos;
\tmemcpy(dst0, r, sizeof(*r));
\tmemcpy(dst1, r, sizeof(*r));
\tmemcpy(dst2, r, sizeof(*r));
\twmb();
\t__flush_dcache_area(dst0, sizeof(*r));
\t__flush_dcache_area(dst1, sizeof(*r));
\t__flush_dcache_area(dst2, sizeof(*r));
}
"""
    text = one(text, old, new, "triple-copy census writer")

    old = """\tr->commit = A52_R377_COMMIT;
\tr->version = A52_R377_VERSION;
}
"""
    new = """\tr->commit = A52_R377_COMMIT;
\tr->version = A52_R377_VERSION;
\tr->crc32c = a52_r382_crc32c(r,
\t\toffsetof(struct a52_r377_record, crc32c));
}
"""
    text = one(text, old, new, "census CRC")

    old = """static int a52_r377_sampler_fn(void *unused)
{
\tstatic const u32 seconds[A52_R377_SNAPSHOT_COUNT] = { 19U, 20U, 25U, 40U };
\tunsigned int next = 0;
\tu64 sec;

\twhile (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
\t\tsec = div_u64(ktime_get_boottime_ns(), NSEC_PER_SEC);
\t\tif (sec >= seconds[next]) {
\t\t\ta52_r377_take_snapshot(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(100) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
}
"""
    new = """static int a52_r377_sampler_fn(void *unused)
{
\tstatic const u32 target_ms[A52_R377_SNAPSHOT_COUNT] = {
\t\t18350U, 18500U, 18550U
\t};
\tunsigned int next = 0;
\tu64 now_ms;

\twhile (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
\t\tnow_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
\t\tif (now_ms >= target_ms[next]) {
\t\t\ta52_r377_take_snapshot(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(10) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
}
"""
    text = one(text, old, new, "cliff census timing")

    old = """\tBUILD_BUG_ON(A52_R377_SNAPSHOT_COUNT * A52_R377_THREADS_PER_SNAP !=
\t\t     A52_R377_SLOTS_PER_COPY);
"""
    new = """\tBUILD_BUG_ON(A52_R377_SNAPSHOT_COUNT * A52_R377_THREADS_PER_SNAP !=
\t\t     A52_R377_SLOTS_PER_COPY);
\tBUILD_BUG_ON(3U * A52_R377_COPY_BYTES > A52_R377_SIDEBAND_BYTES);
"""
    return one(text, old, new, "census triple-copy size audit")


def patch_blk(text: str) -> str:
    if "A52_PHASE382_FREEZE_CLIFF_V1" in text:
        return text
    if "#include <linux/ktime.h>\n" not in text:
        text = one(text, "#include <linux/kernel.h>\n",
                   "#include <linux/kernel.h>\n#include <linux/ktime.h>\n",
                   "blk ktime include")

    anchor = """void blk_mq_freeze_queue_wait(struct request_queue *q)
{
\twait_event(q->mq_freeze_wq, percpu_ref_is_zero(&q->q_usage_counter));
}
"""
    repl = """/* A52_PHASE382_FREEZE_CLIFF_V1 */
static bool a52_r382_block_cliff_window(void)
{
\tu64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);

\treturn ms >= 18000U && ms <= 19000U;
}

void blk_mq_freeze_queue_wait(struct request_queue *q)
{
\tbool trace = a52_r382_block_cliff_window();

\tif (trace)
\t\ta52_ackfr_record("B380 F enter q=%px dep=%d zero=%u",
\t\t\t\t  q, q->mq_freeze_depth,
\t\t\t\t  percpu_ref_is_zero(&q->q_usage_counter));
\twait_event(q->mq_freeze_wq, percpu_ref_is_zero(&q->q_usage_counter));
\tif (trace)
\t\ta52_ackfr_record("B380 F exit q=%px dep=%d", q,
\t\t\t\t  q->mq_freeze_depth);
}
"""
    text = one(text, anchor, repl, "freeze wait cliff trace")

    old = "bool a52_trace = a52_id <= 24;\n"
    new = "bool a52_trace = a52_id <= 24 || a52_r382_block_cliff_window();\n"
    return one(text, old, new, "late nr_requests trace")


def patch_loop(text: str) -> str:
    if "A52_PHASE382_LOOP_CLIFF_V1" in text:
        return text
    if "#include <linux/ktime.h>\n" not in text:
        text = one(text, "#include <linux/module.h>\n",
                   "#include <linux/module.h>\n#include <linux/ktime.h>\n",
                   "loop ktime include")

    old = """/* A52_PHASE380_LOOP_TRACE_V1 */
static atomic_t a52_r380_loop_issue_id = ATOMIC_INIT(0);
static atomic_t a52_r380_loop_done_id = ATOMIC_INIT(0);

"""
    new = """/* A52_PHASE380_LOOP_TRACE_V1 */
/* A52_PHASE382_LOOP_CLIFF_V1 */
static atomic_t a52_r380_loop_issue_id = ATOMIC_INIT(0);
static atomic_t a52_r380_loop_done_id = ATOMIC_INIT(0);
static atomic_t a52_r382_loop_late_issue = ATOMIC_INIT(0);
static atomic_t a52_r382_loop_late_done = ATOMIC_INIT(0);

static bool a52_r382_loop_cliff_window(void)
{
\tu64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);

\treturn ms >= 18000U && ms <= 19000U;
}

"""
    text = one(text, old, new, "loop cliff state")

    old = """\tint a52_id = atomic_inc_return(&a52_r380_loop_done_id);

\tif (a52_id <= 48)
\t\ta52_ackfr_record("L380 D id=%d n=%d q=%px rq=%px aio=%u r=%d",
"""
    new = """\tint a52_id = atomic_inc_return(&a52_r380_loop_done_id);
\tint a52_late = a52_r382_loop_cliff_window() ?
\t\tatomic_inc_return(&a52_r382_loop_late_done) : 0;

\tif (a52_id <= 48 || (a52_late > 0 && a52_late <= 24))
\t\ta52_ackfr_record("L380 D id=%d n=%d q=%px rq=%px aio=%u r=%d",
"""
    text = one(text, old, new, "late loop completion")

    old = """\t{
\t\tint a52_id = atomic_inc_return(&a52_r380_loop_issue_id);
\t\tif (a52_id <= 48)
\t\t\ta52_ackfr_record("L380 I id=%d n=%d q=%px rq=%px op=%u",
"""
    new = """\t{
\t\tint a52_id = atomic_inc_return(&a52_r380_loop_issue_id);
\t\tint a52_late = a52_r382_loop_cliff_window() ?
\t\t\tatomic_inc_return(&a52_r382_loop_late_issue) : 0;
\t\tif (a52_id <= 48 || (a52_late > 0 && a52_late <= 24))
\t\t\ta52_ackfr_record("L380 I id=%d n=%d q=%px rq=%px op=%u",
"""
    return one(text, old, new, "late loop issue")


def validate(root: Path) -> None:
    checks = {
        HDR: ("A52_ACKFR_USB_MODE_IN", "struct a52_ackfr_usbdiag_snapshot"),
        REC: (MARK, "a52_r382_diag_format", "V382 A s=%u", "strncmp(fmt, \"V382\", 4)"),
        UFS: ("A52_PHASE382_RAW_USB_SNAPSHOT_V1", "18525U", "a52_ackfr_usbdiag_emit(next)"),
        SYSCALL: ("A52_PHASE382_CLIFF_CENSUS_V1", "18350U, 18500U, 18550U", "dst2", "crc32c"),
        BLK: ("A52_PHASE382_FREEZE_CLIFF_V1", "B380 F enter"),
        LOOP: ("A52_PHASE382_LOOP_CLIFF_V1", "a52_r382_loop_late_issue"),
        SERIAL: ("A52_ACKFR_USB_GS_PROBE_RC",),
        UDC: ("A52_PHASE382_UDC_BIND_STICKY_V1",),
        DWC3: ("A52_ACKFR_USB_GADGET_INIT", "A52_ACKFR_USB_PULLUP_RC"),
        DWC3CORE: ("A52_ACKFR_USB_MODE_IN", "A52_ACKFR_USB_CORE_MODE"),
    }
    for rel, tokens in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in data:
                raise SystemExit(f"Phase382 validation missing {token} in {rel}")


def run(root: Path) -> None:
    paths = (HDR, REC, UFS, SYSCALL, BLK, LOOP, SERIAL, UDC, DWC3, DWC3CORE)
    for rel in paths:
        if not (root / rel).is_file():
            raise SystemExit(f"Phase382 missing source: {rel}")

    patches = (
        (HDR, patch_header),
        (REC, patch_recorder),
        (UFS, patch_ufs),
        (SYSCALL, patch_census),
        (BLK, patch_blk),
        (LOOP, patch_loop),
        (SERIAL, patch_serial),
        (UDC, patch_udc),
        (DWC3, patch_dwc3),
        (DWC3CORE, patch_dwc3_core),
    )
    for rel, fn in patches:
        p = root / rel
        p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
        print("Phase382 sticky USB + 18.5s cliff census audit: PASS")
        return 0
    run(ns.root)
    print("Phase382 sticky USB + 18.5s cliff census applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
