#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE383_POST_APEX_DUAL_LIVENESS_V1"

HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
SYSCALL = Path("arch/arm64/kernel/syscall.c")
BLK = Path("block/blk-mq.c")
LOOP = Path("drivers/block/loop.c")
UDC = Path("drivers/usb/gadget/udc/core.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase383 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if "a52_ackfr_usbdiag_identity_set" in text:
        return text
    old = """void a52_ackfr_usbdiag_emit(unsigned int sample);\n"""
    new = """#define A52_ACKFR_USB_ID_BYTES 32\nvoid a52_ackfr_usbdiag_emit(unsigned int sample);\nvoid a52_ackfr_usbdiag_identity_set(const char *udc, const char *gadget,\n\t\t\t\t    const char *parent);\nvoid a52_ackfr_usbdiag_identity_snapshot(char *udc, char *gadget,\n\t\t\t\t\t char *parent);\n"""
    return one(text, old, new, "USB identity declarations")


def patch_recorder(text: str) -> str:
    if "A52_PHASE383_USB_IDENTITY_STICKY_V1" in text:
        return text

    old = """static atomic_t a52_r382_usb_pullup_rc = ATOMIC_INIT(A52_R382_USB_UNSEEN);\n\nvoid a52_ackfr_usbdiag_set(unsigned int field, int value)\n"""
    new = """static atomic_t a52_r382_usb_pullup_rc = ATOMIC_INIT(A52_R382_USB_UNSEEN);\n\n/* A52_PHASE383_USB_IDENTITY_STICKY_V1 */\nstatic DEFINE_SPINLOCK(a52_r383_usb_id_lock);\nstatic char a52_r383_usb_udc[A52_ACKFR_USB_ID_BYTES];\nstatic char a52_r383_usb_gadget[A52_ACKFR_USB_ID_BYTES];\nstatic char a52_r383_usb_parent[A52_ACKFR_USB_ID_BYTES];\n\nvoid a52_ackfr_usbdiag_identity_set(const char *udc, const char *gadget,\n\t\t\t\t    const char *parent)\n{\n\tunsigned long flags;\n\n\tspin_lock_irqsave(&a52_r383_usb_id_lock, flags);\n\tstrscpy(a52_r383_usb_udc, udc ? udc : \"?\", sizeof(a52_r383_usb_udc));\n\tstrscpy(a52_r383_usb_gadget, gadget ? gadget : \"?\",\n\t\tsizeof(a52_r383_usb_gadget));\n\tstrscpy(a52_r383_usb_parent, parent ? parent : \"?\",\n\t\tsizeof(a52_r383_usb_parent));\n\tspin_unlock_irqrestore(&a52_r383_usb_id_lock, flags);\n}\nEXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_identity_set);\n\nvoid a52_ackfr_usbdiag_identity_snapshot(char *udc, char *gadget, char *parent)\n{\n\tunsigned long flags;\n\n\tspin_lock_irqsave(&a52_r383_usb_id_lock, flags);\n\tif (udc)\n\t\tstrscpy(udc, a52_r383_usb_udc, A52_ACKFR_USB_ID_BYTES);\n\tif (gadget)\n\t\tstrscpy(gadget, a52_r383_usb_gadget, A52_ACKFR_USB_ID_BYTES);\n\tif (parent)\n\t\tstrscpy(parent, a52_r383_usb_parent, A52_ACKFR_USB_ID_BYTES);\n\tspin_unlock_irqrestore(&a52_r383_usb_id_lock, flags);\n}\nEXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_identity_snapshot);\n\nvoid a52_ackfr_usbdiag_set(unsigned int field, int value)\n"""
    text = one(text, old, new, "sticky USB identity state")

    old = """void a52_ackfr_usbdiag_emit(unsigned int sample)\n{\n\tstruct a52_ackfr_usbdiag_snapshot s;\n\n\ta52_ackfr_usbdiag_snapshot(&s);\n\ta52_ackfr_record("V382 A s=%u mi=%d hw=%d mo=%d ci=%d",\n\t\t\t sample, s.mode_in, s.hw_mode, s.mode_out, s.core_mode);\n\ta52_ackfr_record("V382 B s=%u gi=%d ga=%d gp=%d ub=%d pr=%d",\n\t\t\t sample, s.gadget_init, s.gadget_add_rc,\n\t\t\t s.gs_probe_rc, s.udc_bind_rc, s.pullup_rc);\n}\n"""
    new = """void a52_ackfr_usbdiag_emit(unsigned int sample)\n{\n\tstruct a52_ackfr_usbdiag_snapshot s;\n\tchar udc[A52_ACKFR_USB_ID_BYTES];\n\tchar gadget[A52_ACKFR_USB_ID_BYTES];\n\tchar parent[A52_ACKFR_USB_ID_BYTES];\n\n\ta52_ackfr_usbdiag_snapshot(&s);\n\ta52_ackfr_usbdiag_identity_snapshot(udc, gadget, parent);\n\ta52_ackfr_record("V382 A s=%u mi=%d hw=%d mo=%d ci=%d",\n\t\t\t sample, s.mode_in, s.hw_mode, s.mode_out, s.core_mode);\n\ta52_ackfr_record("V382 B s=%u gi=%d ga=%d gp=%d ub=%d pr=%d",\n\t\t\t sample, s.gadget_init, s.gadget_add_rc,\n\t\t\t s.gs_probe_rc, s.udc_bind_rc, s.pullup_rc);\n\ta52_ackfr_record("V382 C s=%u u=%.20s g=%.16s p=%.20s",\n\t\t\t sample, udc, gadget, parent);\n}\n"""
    return one(text, old, new, "late USB identity emission")


def patch_udc(text: str) -> str:
    if "A52_PHASE383_UDC_IDENTITY_V1" in text:
        return text
    old = """\tkobject_uevent(&udc->dev.kobj, KOBJ_CHANGE);\n\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, 0);\n\ta52_ackfr_record("V382 U bind drv=%s rc=0", driver->function);\n\treturn 0;\n"""
    new = """\tkobject_uevent(&udc->dev.kobj, KOBJ_CHANGE);\n\t/* A52_PHASE383_UDC_IDENTITY_V1 */\n\ta52_ackfr_usbdiag_identity_set(dev_name(&udc->dev),\n\t\t\t\t       udc->gadget && udc->gadget->name ?\n\t\t\t\t       udc->gadget->name : "?",\n\t\t\t\t       udc->gadget && udc->gadget->dev.parent ?\n\t\t\t\t       dev_name(udc->gadget->dev.parent) : "?");\n\ta52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, 0);\n\ta52_ackfr_record("V382 U bind drv=%s rc=0", driver->function);\n\treturn 0;\n"""
    return one(text, old, new, "UDC identity capture")


def patch_ufs(text: str) -> str:
    if "A52_PHASE383_EARLY_FRONTIER_V1" in text:
        return text

    old = """/* A52_PHASE382_RAW_USB_SNAPSHOT_V1 */\nstatic const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {\n\t18300U, 18400U, 18450U, 18475U, 18500U, 18525U,\n\t18550U, 18575U, 18600U, 18625U, 18650U,\n};\n"""
    new = """/* A52_PHASE382_RAW_USB_SNAPSHOT_V1 */\n/* A52_PHASE383_EARLY_FRONTIER_V1 */\nstatic const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {\n\t18150U, 18200U, 18240U, 18260U, 18280U, 18300U,\n\t18320U, 18340U, 18360U, 18400U, 18500U,\n};\n"""
    text = one(text, old, new, "earlier UFS frontier")

    text = one(text,
        "#define A52_R380_VERSION             2U\n",
        "#define A52_R380_VERSION             3U\n",
        "raw sideband version")

    old = """\tstruct a52_r380_record r;\n\tunsigned long outstanding;\n"""
    new = """\tstruct a52_r380_record r;\n\tchar usb_gadget[A52_ACKFR_USB_ID_BYTES];\n\tchar usb_parent[A52_ACKFR_USB_ID_BYTES];\n\tunsigned long outstanding;\n"""
    text = one(text, old, new, "UFS USB identity locals")

    old = """\tr.type = A52_R380_TYPE_SNAPSHOT;\n\tr.tag = ~0U;\n\tstrscpy(r.label, "UFS_SNAPSHOT", sizeof(r.label));\n\ta52_r380_write_slot(&r);\n"""
    new = """\tr.type = A52_R380_TYPE_SNAPSHOT;\n\tr.tag = ~0U;\n\ta52_ackfr_usbdiag_identity_snapshot(r.label, usb_gadget, usb_parent);\n\tif (!r.label[0])\n\t\tstrscpy(r.label, "UFS_SNAPSHOT", sizeof(r.label));\n\ta52_r380_write_slot(&r);\n"""
    return one(text, old, new, "raw UDC identity in snapshot label")


HB_BLOCK = r'''
/* A52_PHASE383_DUAL_LIVENESS_HRTIMER_V1
 *
 * Phase382 proved UFS/loop/block progress through ~18.0 s and then its
 * dedicated UFS sampler survived to 18.312 s.  Use the unused tail of the
 * Phase377/382 32 KiB census reservation as an independent hard-IRQ liveness
 * channel. Two pinned hrtimers (CPU0 and CPU5) write only between 17.7 and
 * 18.85 seconds. No R48/printk work happens in the callback.
 *
 * Layout inside A52_R377_SIDEBAND_PHYS:
 *   0x0000..0x6bff : Phase382 census, three x 0x2400 copies
 *   0x6c00..0x7dff : Phase383 heartbeat, three x 0x600 copies
 *   0x7e00..0x7fff : unused
 */
#define A52_R383_HB_OFFSET          0x6c00U
#define A52_R383_HB_BYTES           0x1200U
#define A52_R383_HB_COPY_BYTES      0x600U
#define A52_R383_HB_SLOT_BYTES      32U
#define A52_R383_HB_SLOTS_PER_COPY  48U
#define A52_R383_HB_MAGIC           0x38334b4349545248ULL
#define A52_R383_HB_COMMIT          0x383c0de5U
#define A52_R383_HB_INTERVAL_MS     50U
#define A52_R383_HB_START_MS        17700U
#define A52_R383_HB_STOP_MS         18850U

struct a52_r383_hb_slot {
	u64 magic;
	u64 ns;
	u32 index;
	u16 cpu;
	u16 tick;
	u32 crc32c;
	u32 commit;
};

struct a52_r383_hb_timer {
	struct hrtimer timer;
	u16 tick;
};

static atomic_t a52_r383_hb_index = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_r383_hb_timer, a52_r383_hb_timers);

static void a52_r383_hb_write(u16 cpu, u16 tick, u64 ns)
{
	struct a52_r383_hb_slot slot;
	unsigned int index;
	unsigned int pos;
	void *base;
	void *dst0;
	void *dst1;
	void *dst2;

	if (!READ_ONCE(a52_r377_sideband))
		return;

	index = (unsigned int)atomic_inc_return(&a52_r383_hb_index) - 1U;
	if (index >= A52_R383_HB_SLOTS_PER_COPY)
		return;

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_R383_HB_MAGIC;
	slot.ns = ns;
	slot.index = index;
	slot.cpu = cpu;
	slot.tick = tick;
	slot.crc32c = a52_r382_crc32c(&slot,
		offsetof(struct a52_r383_hb_slot, crc32c));
	slot.commit = A52_R383_HB_COMMIT;

	pos = index * A52_R383_HB_SLOT_BYTES;
	base = (u8 *)a52_r377_sideband + A52_R383_HB_OFFSET;
	dst0 = (u8 *)base + pos;
	dst1 = (u8 *)base + A52_R383_HB_COPY_BYTES + pos;
	dst2 = (u8 *)base + 2U * A52_R383_HB_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	memcpy(dst2, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
	__flush_dcache_area(dst2, sizeof(slot));
}

static enum hrtimer_restart a52_r383_hb_fn(struct hrtimer *timer)
{
	struct a52_r383_hb_timer *hb =
		container_of(timer, struct a52_r383_hb_timer, timer);
	u64 ns = ktime_get_boottime_ns();
	u64 ms = div_u64(ns, NSEC_PER_MSEC);

	hb->tick++;
	if (ms >= A52_R383_HB_START_MS && ms <= A52_R383_HB_STOP_MS)
		a52_r383_hb_write((u16)smp_processor_id(), hb->tick, ns);

	if (ms > A52_R383_HB_STOP_MS ||
	    atomic_read(&a52_r383_hb_index) >= A52_R383_HB_SLOTS_PER_COPY)
		return HRTIMER_NORESTART;

	hrtimer_forward_now(timer, ms_to_ktime(A52_R383_HB_INTERVAL_MS));
	return HRTIMER_RESTART;
}

static void a52_r383_hb_start_cpu(void *unused)
{
	struct a52_r383_hb_timer *hb = this_cpu_ptr(&a52_r383_hb_timers);

	memset(hb, 0, sizeof(*hb));
	hrtimer_init(&hb->timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED);
	hb->timer.function = a52_r383_hb_fn;
	hrtimer_start(&hb->timer, ms_to_ktime(A52_R383_HB_INTERVAL_MS),
		      HRTIMER_MODE_REL_PINNED);
}

static void a52_r383_hb_start(void)
{
	static const unsigned int cpus[] = { 0U, 5U };
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(cpus); i++) {
		unsigned int cpu = cpus[i];

		if (cpu >= nr_cpu_ids || !cpu_online(cpu))
			continue;
		smp_call_function_single(cpu, a52_r383_hb_start_cpu, NULL, 1);
	}
}

'''


def patch_census(text: str) -> str:
    if "A52_PHASE383_DUAL_LIVENESS_HRTIMER_V1" in text:
        return text

    for inc in (
        "#include <linux/hrtimer.h>\n",
        "#include <linux/percpu.h>\n",
        "#include <linux/smp.h>\n",
    ):
        if inc not in text:
            text = inc + text

    old = """\tstatic const u32 target_ms[A52_R377_SNAPSHOT_COUNT] = {\n\t\t18350U, 18500U, 18550U\n\t};\n"""
    new = """\tstatic const u32 target_ms[A52_R377_SNAPSHOT_COUNT] = {\n\t\t18200U, 18280U, 18320U\n\t};\n"""
    text = one(text, old, new, "earlier apexd census")

    anchor = "static int __init a52_r377_init(void)\n"
    text = one(text, anchor, HB_BLOCK + anchor, "hardirq heartbeat insertion")

    old = """\tBUILD_BUG_ON(3U * A52_R377_COPY_BYTES > A52_R377_SIDEBAND_BYTES);\n\n\ta52_r377_sideband = memremap(A52_R377_SIDEBAND_PHYS,\n"""
    new = """\tBUILD_BUG_ON(3U * A52_R377_COPY_BYTES > A52_R377_SIDEBAND_BYTES);\n\tBUILD_BUG_ON(sizeof(struct a52_r383_hb_slot) != A52_R383_HB_SLOT_BYTES);\n\tBUILD_BUG_ON(3U * A52_R383_HB_COPY_BYTES > A52_R383_HB_BYTES);\n\tBUILD_BUG_ON(3U * A52_R377_COPY_BYTES + A52_R383_HB_BYTES >\n\t\t     A52_R377_SIDEBAND_BYTES);\n\n\ta52_r377_sideband = memremap(A52_R377_SIDEBAND_PHYS,\n"""
    text = one(text, old, new, "heartbeat layout audits")

    old = """\t__flush_dcache_area(a52_r377_sideband, A52_R377_SIDEBAND_BYTES);\n\n\ta52_r377_sampler_task =\n"""
    new = """\t__flush_dcache_area(a52_r377_sideband, A52_R377_SIDEBAND_BYTES);\n\n\ta52_r383_hb_start();\n\ta52_r377_sampler_task =\n"""
    return one(text, old, new, "heartbeat start")


def patch_blk(text: str) -> str:
    if "A52_PHASE383_LOW_PERTURBATION_BLOCK_V1" in text:
        return text
    old = """static bool a52_r382_block_cliff_window(void)\n{\n\tu64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);\n\n\treturn ms >= 18000U && ms <= 19000U;\n}\n"""
    new = """/* A52_PHASE383_LOW_PERTURBATION_BLOCK_V1 */\nstatic bool a52_r382_block_cliff_window(void)\n{\n\t/* Phase382 proved BC -> SQ -> SR delivery; retire high-volume R48 here. */\n\treturn false;\n}\n"""
    return one(text, old, new, "retire high-volume block tracing")


def patch_loop(text: str) -> str:
    if "A52_PHASE383_LOW_PERTURBATION_LOOP_V1" in text:
        return text
    old = """static bool a52_r382_loop_cliff_window(void)\n{\n\tu64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);\n\n\treturn ms >= 18000U && ms <= 19000U;\n}\n"""
    new = """/* A52_PHASE383_LOW_PERTURBATION_LOOP_V1 */\nstatic bool a52_r382_loop_cliff_window(void)\n{\n\t/* Phase382 proved late loop completions; remove timing perturbation. */\n\treturn false;\n}\n"""
    return one(text, old, new, "retire high-volume loop tracing")


def validate(root: Path) -> None:
    checks = {
        HDR: ("a52_ackfr_usbdiag_identity_set", "A52_ACKFR_USB_ID_BYTES 32"),
        REC: ("A52_PHASE383_USB_IDENTITY_STICKY_V1",
              'V382 C s=%u u=%.20s g=%.16s p=%.20s'),
        UDC: ("A52_PHASE383_UDC_IDENTITY_V1",
              "a52_ackfr_usbdiag_identity_set(dev_name(&udc->dev)"),
        UFS: ("A52_PHASE383_EARLY_FRONTIER_V1", "18150U", "18320U",
              "#define A52_R380_VERSION             3U"),
        SYSCALL: ("A52_PHASE383_DUAL_LIVENESS_HRTIMER_V1",
                  "18200U, 18280U, 18320U",
                  "A52_R383_HB_OFFSET          0x6c00U",
                  "A52_R383_HB_INTERVAL_MS     50U",
                  "a52_r383_hb_start();"),
        BLK: ("A52_PHASE383_LOW_PERTURBATION_BLOCK_V1", "return false;"),
        LOOP: ("A52_PHASE383_LOW_PERTURBATION_LOOP_V1", "return false;"),
    }
    for rel, tokens in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in data:
                raise SystemExit(f"Phase383 validation missing {token} in {rel}")


def run(root: Path) -> None:
    patches = (
        (HDR, patch_header),
        (REC, patch_recorder),
        (UDC, patch_udc),
        (UFS, patch_ufs),
        (SYSCALL, patch_census),
        (BLK, patch_blk),
        (LOOP, patch_loop),
    )
    for rel, fn in patches:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"Phase383 missing source: {rel}")
        path.write_text(fn(path.read_text(encoding="utf-8")), encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
        print("Phase383 post-apex dual-liveness audit: PASS")
        return 0
    run(ns.root)
    print("Phase383 post-apex dual-liveness applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
