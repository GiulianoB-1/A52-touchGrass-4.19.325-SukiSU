#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE385B_REAL_USB_STICKY_V1"
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
BLK = Path("block/blk-mq.c")
UDC = Path("drivers/usb/gadget/udc/core.c")
SIMPLE = Path("drivers/usb/dwc3/dwc3-of-simple.c")


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase385b {label}: expected 1 anchor, found {count}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if "A52_PHASE385_FIXED_STICKY_V1" in text:
        return text

    block = r'''
/* A52_PHASE385_FIXED_STICKY_V1 */
void a52_ackfr_sticky385_usb_identity(const char *udc, const char *gadget,
                                      const char *parent, const char *driver,
                                      unsigned int pullup_capable);
void a52_ackfr_sticky385_heartbeat(unsigned int sample, u64 elapsed_ms);
void a52_ackfr_sticky385_freeze(unsigned int event, int slot, u64 q,
                                int depth, unsigned int nr,
                                unsigned int zero, u64 age_ms);
void a52_ackfr_sticky385_ufs(unsigned int sample, u32 doorbell,
                             unsigned long outstanding, u64 irq_count);
void a52_ackfr_sticky385_ofsimple(unsigned int stage, int ret);

'''
    return one(text, "\n#endif\n", block + "\n#endif\n",
               "sticky header declarations")


STICKY_BLOCK = r'''
/* A52_PHASE385_FIXED_STICKY_V1
 *
 * The RS48 stream can wrap in roughly 230 ms under this debug load. Keep
 * Phase385 one-shot state in the otherwise-unused final 0x200 bytes of the
 * Phase380 32 KiB sideband:
 *
 *   Phase380 triple copies: 3 * 0x2a00 = 0x7e00 bytes
 *   fixed Phase385 tail:    0x7e00 .. 0x7fff = 0x200 bytes
 *
 * Two identical 256-byte copies are written on every update. Each copy is
 * CRC32C protected and contains the latest USB sticky state, UDC identity,
 * OF-simple stage, freeze observer state, UFS state and frontier heartbeat.
 * This storage is not circular.
 */
#define A52_R385_FIXED_PHYS       0xB1BFFE00ULL
#define A52_R385_FIXED_BYTES      0x200U
#define A52_R385_FIXED_COPY_BYTES 0x100U
#define A52_R385_FIXED_MAGIC      0x353833594b435453ULL
#define A52_R385_FIXED_COMMIT     0x385b0de5U
#define A52_R385_FIXED_VERSION    1U

struct a52_r385_fixed {
	u64 magic;
	u64 seq;
	u64 ns;
	u64 q;
	u64 freeze_age_ms;
	u64 ufs_irq_count;
	u64 ufs_outstanding;
	u32 flags;
	u32 frontier_sample;
	u32 frontier_ms;
	s32 freeze_slot;
	s32 freeze_depth;
	u32 freeze_nr;
	u32 freeze_zero;
	u32 freeze_event;
	u32 ufs_sample;
	u32 ufs_doorbell;
	u32 usb_pullup_capable;
	s32 usb_diag[9];
	char udc[24];
	char gadget[24];
	char parent[24];
	char driver[24];
	s32 ofsimple_ret;
	u32 ofsimple_stage;
	u32 crc32c;
	u32 commit;
	u32 version;
	u8 reserved[4];
};

static void *a52_r385_fixed_base;
static DEFINE_SPINLOCK(a52_r385_fixed_lock);
static struct a52_r385_fixed a52_r385_fixed_state;

static u32 a52_r385_fixed_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				((crc & 1) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

static void a52_r385_fixed_commit_locked(void)
{
	void *dst0;
	void *dst1;

	if (!a52_r385_fixed_base)
		return;

	a52_r385_fixed_state.magic = A52_R385_FIXED_MAGIC;
	a52_r385_fixed_state.seq++;
	a52_r385_fixed_state.ns = ktime_get_boottime_ns();
	a52_r385_fixed_state.crc32c =
		a52_r385_fixed_crc32c(&a52_r385_fixed_state,
			offsetof(struct a52_r385_fixed, crc32c));
	a52_r385_fixed_state.commit = A52_R385_FIXED_COMMIT;
	a52_r385_fixed_state.version = A52_R385_FIXED_VERSION;

	dst0 = a52_r385_fixed_base;
	dst1 = (u8 *)a52_r385_fixed_base + A52_R385_FIXED_COPY_BYTES;
	memcpy(dst0, &a52_r385_fixed_state, sizeof(a52_r385_fixed_state));
	memcpy(dst1, &a52_r385_fixed_state, sizeof(a52_r385_fixed_state));
	wmb();
	__flush_dcache_area(dst0, sizeof(a52_r385_fixed_state));
	__flush_dcache_area(dst1, sizeof(a52_r385_fixed_state));
}

static void a52_r385_fixed_usbdiag(unsigned int field, int value)
{
	unsigned long flags;

	if (field >= ARRAY_SIZE(a52_r385_fixed_state.usb_diag))
		return;
	spin_lock_irqsave(&a52_r385_fixed_lock, flags);
	a52_r385_fixed_state.usb_diag[field] = value;
	a52_r385_fixed_state.flags |= BIT(0);
	a52_r385_fixed_commit_locked();
	spin_unlock_irqrestore(&a52_r385_fixed_lock, flags);
}

void a52_ackfr_sticky385_usb_identity(const char *udc, const char *gadget,
                                      const char *parent, const char *driver,
                                      unsigned int pullup_capable)
{
	unsigned long flags;

	spin_lock_irqsave(&a52_r385_fixed_lock, flags);
	strscpy(a52_r385_fixed_state.udc, udc ? udc : "-",
		sizeof(a52_r385_fixed_state.udc));
	strscpy(a52_r385_fixed_state.gadget, gadget ? gadget : "-",
		sizeof(a52_r385_fixed_state.gadget));
	strscpy(a52_r385_fixed_state.parent, parent ? parent : "-",
		sizeof(a52_r385_fixed_state.parent));
	strscpy(a52_r385_fixed_state.driver, driver ? driver : "-",
		sizeof(a52_r385_fixed_state.driver));
	a52_r385_fixed_state.usb_pullup_capable = pullup_capable;
	a52_r385_fixed_state.flags |= BIT(1);
	a52_r385_fixed_commit_locked();
	spin_unlock_irqrestore(&a52_r385_fixed_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky385_usb_identity);

void a52_ackfr_sticky385_heartbeat(unsigned int sample, u64 elapsed_ms)
{
	unsigned long flags;

	spin_lock_irqsave(&a52_r385_fixed_lock, flags);
	a52_r385_fixed_state.frontier_sample = sample;
	a52_r385_fixed_state.frontier_ms =
		elapsed_ms > U32_MAX ? U32_MAX : (u32)elapsed_ms;
	a52_r385_fixed_state.flags |= BIT(2);
	a52_r385_fixed_commit_locked();
	spin_unlock_irqrestore(&a52_r385_fixed_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky385_heartbeat);

void a52_ackfr_sticky385_freeze(unsigned int event, int slot, u64 q,
                                int depth, unsigned int nr,
                                unsigned int zero, u64 age_ms)
{
	unsigned long flags;

	spin_lock_irqsave(&a52_r385_fixed_lock, flags);
	a52_r385_fixed_state.freeze_event = event;
	a52_r385_fixed_state.freeze_slot = slot;
	a52_r385_fixed_state.q = q;
	a52_r385_fixed_state.freeze_depth = depth;
	a52_r385_fixed_state.freeze_nr = nr;
	a52_r385_fixed_state.freeze_zero = zero;
	a52_r385_fixed_state.freeze_age_ms = age_ms;
	a52_r385_fixed_state.flags |= BIT(3);
	a52_r385_fixed_commit_locked();
	spin_unlock_irqrestore(&a52_r385_fixed_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky385_freeze);

void a52_ackfr_sticky385_ufs(unsigned int sample, u32 doorbell,
                             unsigned long outstanding, u64 irq_count)
{
	unsigned long flags;

	spin_lock_irqsave(&a52_r385_fixed_lock, flags);
	a52_r385_fixed_state.ufs_sample = sample;
	a52_r385_fixed_state.ufs_doorbell = doorbell;
	a52_r385_fixed_state.ufs_outstanding = (u64)outstanding;
	a52_r385_fixed_state.ufs_irq_count = irq_count;
	a52_r385_fixed_state.flags |= BIT(4);
	a52_r385_fixed_commit_locked();
	spin_unlock_irqrestore(&a52_r385_fixed_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky385_ufs);

void a52_ackfr_sticky385_ofsimple(unsigned int stage, int ret)
{
	unsigned long flags;

	spin_lock_irqsave(&a52_r385_fixed_lock, flags);
	a52_r385_fixed_state.ofsimple_stage = stage;
	a52_r385_fixed_state.ofsimple_ret = ret;
	a52_r385_fixed_state.flags |= BIT(5);
	a52_r385_fixed_commit_locked();
	spin_unlock_irqrestore(&a52_r385_fixed_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky385_ofsimple);

static int __init a52_r385_fixed_init(void)
{
	unsigned int i;

	BUILD_BUG_ON(sizeof(struct a52_r385_fixed) != A52_R385_FIXED_COPY_BYTES);
	a52_r385_fixed_base = memremap(A52_R385_FIXED_PHYS,
		A52_R385_FIXED_BYTES, MEMREMAP_WB);
	if (!a52_r385_fixed_base)
		return 0;

	memset(&a52_r385_fixed_state, 0, sizeof(a52_r385_fixed_state));
	for (i = 0; i < ARRAY_SIZE(a52_r385_fixed_state.usb_diag); i++)
		a52_r385_fixed_state.usb_diag[i] = -9999;
	strscpy(a52_r385_fixed_state.udc, "-", sizeof(a52_r385_fixed_state.udc));
	strscpy(a52_r385_fixed_state.gadget, "-", sizeof(a52_r385_fixed_state.gadget));
	strscpy(a52_r385_fixed_state.parent, "-", sizeof(a52_r385_fixed_state.parent));
	strscpy(a52_r385_fixed_state.driver, "-", sizeof(a52_r385_fixed_state.driver));
	memset(a52_r385_fixed_base, 0, A52_R385_FIXED_BYTES);
	wmb();
	__flush_dcache_area(a52_r385_fixed_base, A52_R385_FIXED_BYTES);
	a52_r385_fixed_commit_locked();
	return 0;
}
core_initcall(a52_r385_fixed_init);

'''


def patch_recorder(text: str) -> str:
    if "A52_PHASE385_FIXED_STICKY_V1" in text:
        return text

    includes = (
        ("#include <linux/kernel.h>\n", "#include <linux/io.h>\n"),
        ("#include <linux/kernel.h>\n", "#include <linux/spinlock.h>\n"),
        ("#include <linux/kernel.h>\n", "#include <linux/init.h>\n"),
        ("#include <linux/kernel.h>\n", "#include <linux/string.h>\n"),
        ("#include <linux/kernel.h>\n", "#include <asm/cacheflush.h>\n"),
    )
    for anchor, inc in includes:
        if inc not in text:
            text = one(text, anchor, anchor + inc,
                       "recorder fixed-sticky include")

    text = one(text,
               "void a52_ackfr_record(const char *fmt, ...)\n",
               STICKY_BLOCK + "void a52_ackfr_record(const char *fmt, ...)\n",
               "fixed sticky block")

    old = '''void a52_ackfr_usbdiag_set(unsigned int field, int value)
{
	switch (field) {
'''
    new = '''void a52_ackfr_usbdiag_set(unsigned int field, int value)
{
	a52_r385_fixed_usbdiag(field, value);
	switch (field) {
'''
    return one(text, old, new, "mirror V382 atomics into fixed sticky")


def patch_ufs(text: str) -> str:
    if "A52_PHASE385_FIXED_TAIL_PRESERVE_V1" in text:
        return text

    text = one(
        text,
        "memset(a52_r380_sideband, 0, A52_R380_SIDEBAND_BYTES);\n",
        "/* A52_PHASE385_FIXED_TAIL_PRESERVE_V1 */\n"
        "memset(a52_r380_sideband, 0, 3U * A52_R380_COPY_BYTES);\n",
        "preserve final 0x200 sticky tail",
    )

    old = '''	a52_ackfr_record("U384 C%u tr=%lld se=%lld hk=%lld dn=%lld irq=%lld li=%x lc=%lx",
			 snapshot_id,
			 (long long)atomic64_read(&a52_r378_trc_count),
			 (long long)atomic64_read(&a52_r378_seen_count),
			 (long long)atomic64_read(&a52_r378_hook_done_count),
			 (long long)atomic64_read(&a52_r378_scsi_done_count),
			 (long long)atomic64_read(&a52_r384_irq_count),
			 READ_ONCE(a52_r384_last_irq_status),
			 READ_ONCE(a52_r378_last_completed));
'''
    new = old + '''	a52_ackfr_sticky385_ufs(snapshot_id, doorbell, outstanding,
				     (u64)atomic64_read(&a52_r384_irq_count));
'''
    return one(text, old, new, "fixed sticky UFS update")


def patch_blk(text: str) -> str:
    if "A52_PHASE385_FIXED_FREEZE_MIRROR_V1" in text:
        return text

    old = '''	if (slot >= 0)
		a52_ackfr_record("B385 E s=%d q=%px dep=%d nr=%u zero=%u",
				 slot, q, q->mq_freeze_depth, q->nr_requests,
				 percpu_ref_is_zero(&q->q_usage_counter));
	else
		a52_ackfr_record("B385 E s=-1 q=%px dep=%d nr=%u zero=%u",
				 q, q->mq_freeze_depth, q->nr_requests,
				 percpu_ref_is_zero(&q->q_usage_counter));
'''
    new = old + '''	/* A52_PHASE385_FIXED_FREEZE_MIRROR_V1 */
	a52_ackfr_sticky385_freeze(1U, slot, (u64)(unsigned long)q,
				 q->mq_freeze_depth, q->nr_requests,
				 percpu_ref_is_zero(&q->q_usage_counter), 0);
'''
    text = one(text, old, new, "freeze-enter sticky")

    old = '''	a52_ackfr_record("B385 X s=%d q=%px dep=%d age=%llu zero=%u",
			 slot, q, q->mq_freeze_depth,
			 (unsigned long long)age_ms,
			 percpu_ref_is_zero(&q->q_usage_counter));
'''
    new = old + '''	a52_ackfr_sticky385_freeze(2U, slot, (u64)(unsigned long)q,
				 q->mq_freeze_depth, q->nr_requests,
				 percpu_ref_is_zero(&q->q_usage_counter), age_ms);
'''
    text = one(text, old, new, "freeze-exit sticky")

    old = '''	a52_ackfr_record("P385 H s=%u ms=%llu",
			 sample, (unsigned long long)elapsed_ms);
'''
    new = old + '''	a52_ackfr_sticky385_heartbeat(sample, elapsed_ms);
'''
    text = one(text, old, new, "heartbeat sticky")

    old = '''		if (q)
			a52_ackfr_record("B385 W s=%u q=%px dep=%d nr=%u age=%llu zero=%u",
					 i, q, depth, nr,
					 (unsigned long long)age_ms, zero);
'''
    new = '''		if (q) {
			a52_ackfr_record("B385 W s=%u q=%px dep=%d nr=%u age=%llu zero=%u",
					 i, q, depth, nr,
					 (unsigned long long)age_ms, zero);
			a52_ackfr_sticky385_freeze(3U, (int)i,
					 (u64)(unsigned long)q, depth, nr,
					 zero, age_ms);
		}
'''
    return one(text, old, new, "freeze-observer sticky")


def patch_udc(text: str) -> str:
    if "A52_PHASE385_FIXED_UDC_IDENTITY_V1" in text:
        return text

    old = '''		a52_ackfr_record("V385 U s=%u udc=%s gad=%s par=%s drv=%s pull=%u",
				 sample, dev_name(&udc->dev), gname, parent,
				 driver, pull);
		seen++;
'''
    new = '''		a52_ackfr_record("V385 U s=%u udc=%s gad=%s par=%s drv=%s pull=%u",
				 sample, dev_name(&udc->dev), gname, parent,
				 driver, pull);
		/* A52_PHASE385_FIXED_UDC_IDENTITY_V1 */
		a52_ackfr_sticky385_usb_identity(dev_name(&udc->dev), gname,
					       parent, driver, pull);
		seen++;
'''
    text = one(text, old, new, "UDC fixed identity")

    old = '''	if (!seen)
		a52_ackfr_record("V385 U s=%u none", sample);
'''
    new = '''	if (!seen) {
		a52_ackfr_record("V385 U s=%u none", sample);
		a52_ackfr_sticky385_usb_identity("none", "-", "-", "-", 0U);
	}
'''
    return one(text, old, new, "UDC none sticky")


def patch_simple(text: str) -> str:
    if "A52_PHASE385_OF_SIMPLE_QCOM_BRIDGE_V1" in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        text = one(text, "#include <linux/reset.h>\n",
                   "#include <linux/reset.h>\n" + inc,
                   "OF-simple recorder include")

    old = '''	int			ret;

	simple = devm_kzalloc(dev, sizeof(*simple), GFP_KERNEL);
	if (!simple)
		return -ENOMEM;
'''
    new = '''	int			ret;
	unsigned int		a52_stage = 1U;

	/* A52_PHASE385_OF_SIMPLE_QCOM_BRIDGE_V1 */
	a52_ackfr_sticky385_ofsimple(a52_stage, 0);
	simple = devm_kzalloc(dev, sizeof(*simple), GFP_KERNEL);
	if (!simple) {
		a52_ackfr_sticky385_ofsimple(0x80000000U | a52_stage, -ENOMEM);
		return -ENOMEM;
	}
'''
    text = one(text, old, new, "OF-simple probe entry")

    old = '''	simple->resets = of_reset_control_array_get_optional_exclusive(np);
	if (IS_ERR(simple->resets)) {
		ret = PTR_ERR(simple->resets);
		dev_err(dev, "failed to get device resets, err=%d\n", ret);
		return ret;
	}
'''
    new = '''	a52_stage = 2U;
	simple->resets = of_reset_control_array_get_optional_exclusive(np);
	if (IS_ERR(simple->resets)) {
		ret = PTR_ERR(simple->resets);
		dev_err(dev, "failed to get device resets, err=%d\n", ret);
		a52_ackfr_sticky385_ofsimple(0x80000000U | a52_stage, ret);
		return ret;
	}
'''
    text = one(text, old, new, "OF-simple reset lookup")

    old = '''	ret = reset_control_deassert(simple->resets);
	if (ret)
		goto err_resetc_put;

	ret = clk_bulk_get_all(simple->dev, &simple->clks);
'''
    new = '''	a52_stage = 3U;
	ret = reset_control_deassert(simple->resets);
	if (ret)
		goto err_resetc_put;

	a52_stage = 4U;
	ret = clk_bulk_get_all(simple->dev, &simple->clks);
'''
    text = one(text, old, new, "OF-simple reset/clock stage")

    old = '''	simple->num_clocks = ret;
	ret = clk_bulk_prepare_enable(simple->num_clocks, simple->clks);
	if (ret)
		goto err_resetc_assert;

	ret = of_platform_populate(np, NULL, NULL, dev);
'''
    new = '''	simple->num_clocks = ret;
	a52_stage = 5U;
	ret = clk_bulk_prepare_enable(simple->num_clocks, simple->clks);
	if (ret)
		goto err_resetc_assert;

	a52_stage = 6U;
	ret = of_platform_populate(np, NULL, NULL, dev);
'''
    text = one(text, old, new, "OF-simple clock/populate stage")

    old = '''	pm_runtime_get_sync(dev);

	return 0;
'''
    new = '''	pm_runtime_get_sync(dev);
	a52_ackfr_sticky385_ofsimple(7U, 0);

	return 0;
'''
    text = one(text, old, new, "OF-simple success sticky")

    old = '''err_resetc_put:
	reset_control_put(simple->resets);
	return ret;
}
'''
    new = '''err_resetc_put:
	reset_control_put(simple->resets);
	a52_ackfr_sticky385_ofsimple(0x80000000U | a52_stage, ret);
	return ret;
}
'''
    text = one(text, old, new, "OF-simple error sticky")

    old = '''static const struct of_device_id of_dwc3_simple_match[] = {
	{ .compatible = "rockchip,rk3399-dwc3" },
'''
    new = '''static const struct of_device_id of_dwc3_simple_match[] = {
	{ .compatible = "qcom,dwc-usb3-msm" },
	{ .compatible = "rockchip,rk3399-dwc3" },
'''
    return one(text, old, new, "Qualcomm parent compatible")


def validate(root: Path) -> None:
    checks = {
        HDR: ("A52_PHASE385_FIXED_STICKY_V1", "a52_ackfr_sticky385_ofsimple"),
        REC: ("A52_PHASE385_FIXED_STICKY_V1", "A52_R385_FIXED_PHYS",
              "0xB1BFFE00ULL", "core_initcall(a52_r385_fixed_init)",
              "a52_r385_fixed_usbdiag(field, value)"),
        UFS: ("A52_PHASE385_FIXED_TAIL_PRESERVE_V1",
              "3U * A52_R380_COPY_BYTES",
              "a52_ackfr_sticky385_ufs(snapshot_id"),
        BLK: ("A52_PHASE385_FIXED_FREEZE_MIRROR_V1",
              "a52_ackfr_sticky385_heartbeat",
              "a52_ackfr_sticky385_freeze"),
        UDC: ("A52_PHASE385_FIXED_UDC_IDENTITY_V1",
              "a52_ackfr_sticky385_usb_identity"),
        SIMPLE: ("A52_PHASE385_OF_SIMPLE_QCOM_BRIDGE_V1",
                 'compatible = "qcom,dwc-usb3-msm"',
                 "a52_ackfr_sticky385_ofsimple(7U, 0)"),
    }
    for rel, tokens in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in data:
                raise SystemExit(f"Phase385b validation missing {token!r} in {rel}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()

    if args.check_only:
        validate(root)
        print("Phase385b real USB + fixed sticky validation: PASS")
        return

    patches = (
        (HDR, patch_header),
        (REC, patch_recorder),
        (UFS, patch_ufs),
        (BLK, patch_blk),
        (UDC, patch_udc),
        (SIMPLE, patch_simple),
    )
    for rel, fn in patches:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"Phase385b missing source file: {path}")
        before = path.read_text(encoding="utf-8")
        after = fn(before)
        path.write_text(after, encoding="utf-8")
        print(f"Phase385b patched {rel}: {before != after}")

    validate(root)
    print("Phase385b real USB + fixed sticky applied successfully")


if __name__ == "__main__":
    main()
