#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase422 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


P422_REC = r'''
/* A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1
 *
 * Four corruption-tolerant 64-byte records, each written three times:
 *   copy0: B1900000 + 0x200
 *   copy1: B1900000 + 0x300
 *   copy2: B1A00000 + 0xD80 (1 MiB-separated surviving page)
 *
 * M00: cached SID 0x800 context-IRQ registration + exact cmd_buffer_iova
 * M01: first SID 0x800 context fault during the exact Phase421 F0 window
 * M02: post-timeout SID 0x800 FSR/FAR/SCTLR snapshot
 * M03: timestamp captured at Phase421 S08 immediately after SW_TRIGGER returns
 *
 * This lane never changes SMMU or DSI behavior. CRC is convenience evidence;
 * the three physical copies remain independently decodable after bit decay.
 */
#define A52_P422_OFF0              0x00000200U
#define A52_P422_OFF1              0x00000300U
#define A52_P422_OFF2              0x00000D80U
#define A52_P422_SLOT_BYTES        64U
#define A52_P422_SLOTS             4U
#define A52_P422_MAGIC             0xA52F4220A52F4220ULL
#define A52_P422_COMMIT            0x422c0de5U

struct a52_p422_slot {
	u64 magic;
	u64 ts_ns;
	u64 arg0;
	u64 arg1;
	u64 arg2;
	u64 arg3;
	u32 stage;
	u32 seq;
	u32 crc32c;
	u32 commit;
} __packed;

static atomic_t a52_p422_written[A52_P422_SLOTS] = {
	ATOMIC_INIT(0), ATOMIC_INIT(0), ATOMIC_INIT(0), ATOMIC_INIT(0)
};

static void a52_p422_copy_persist(void __iomem *dst,
				   const struct a52_p422_slot *slot)
{
	if (!dst || !slot)
		return;

	memcpy_toio(dst, slot, sizeof(*slot));
	wmb();
	__flush_dcache_area((void __force *)dst, sizeof(*slot));
	dsb(sy);
}

void a52_p422_survival_record_at(u32 stage, u32 seq, u64 ts_ns,
				 u64 arg0, u64 arg1, u64 arg2, u64 arg3)
{
	struct a52_p422_slot slot;
	void __iomem *dst;

	BUILD_BUG_ON(sizeof(struct a52_p422_slot) != A52_P422_SLOT_BYTES);
	BUILD_BUG_ON(A52_P422_OFF1 +
		     A52_P422_SLOTS * A52_P422_SLOT_BYTES >
		     A52_P421_OFF0);
	BUILD_BUG_ON(A52_P422_OFF2 +
		     A52_P422_SLOTS * A52_P422_SLOT_BYTES >
		     A52_P414_HEADER_BYTES);

	if (stage >= A52_P422_SLOTS)
		return;
	if (atomic_cmpxchg(&a52_p422_written[stage], 0, 1) != 0)
		return;

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P422_MAGIC;
	slot.ts_ns = ts_ns;
	slot.arg0 = arg0;
	slot.arg1 = arg1;
	slot.arg2 = arg2;
	slot.arg3 = arg3;
	slot.stage = stage;
	slot.seq = seq;
	slot.crc32c = a52_p392_crc32c(&slot,
				      offsetof(struct a52_p422_slot, crc32c));
	slot.commit = A52_P422_COMMIT;

	if (READ_ONCE(a52_p392_base)) {
		dst = (u8 __iomem *)a52_p392_base + A52_P422_OFF0 +
		      stage * A52_P422_SLOT_BYTES;
		a52_p422_copy_persist(dst, &slot);
		dst = (u8 __iomem *)a52_p392_base + A52_P422_OFF1 +
		      stage * A52_P422_SLOT_BYTES;
		a52_p422_copy_persist(dst, &slot);
	}

	if (READ_ONCE(a52_p414_ram)) {
		dst = (u8 __iomem *)a52_p414_ram + A52_P422_OFF2 +
		      stage * A52_P422_SLOT_BYTES;
		a52_p422_copy_persist(dst, &slot);
	}
}
EXPORT_SYMBOL_GPL(a52_p422_survival_record_at);
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1",
        "#define A52_P421_OFF0              0x00000500U",
        "static void a52_p421_copy_persist",
        "void a52_p421_survival_record(u8 stage",
        "a52_p392_crc32c",
        "a52_p414_ram",
    ):
        if token not in text:
            raise SystemExit("Phase422 recorder prerequisite missing: " + token)

    anchor = "void a52_p421_survival_record(u8 stage, u8 valid, u32 flags,\n"
    text = one(text, anchor, P422_REC + "\n" + anchor,
               "recorder insertion")

    decl = "\tu32 nonce;\n\tint previous;\n"
    text = one(text, decl,
               "\tu32 nonce;\n\tu64 a52_p422_s08_ns = 0;\n\tint previous;\n",
               "S08 timestamp declaration")

    won = '''\t\tif (atomic_cmpxchg(&a52_p421_highest, previous, stage) == previous)
\t\t\tbreak;
\t}

\tnonce = a52_p421_get_nonce();
'''
    won_new = '''\t\tif (atomic_cmpxchg(&a52_p421_highest, previous, stage) == previous)
\t\t\tbreak;
\t}

\t/* Timestamp the accepted S08 call before any survival-copy latency. */
\tif (stage == 8U)
\t\ta52_p422_s08_ns = ktime_get_boottime_ns();

\tnonce = a52_p421_get_nonce();
'''
    text = one(text, won, won_new, "S08 timestamp capture")

    tail = '''\tif (READ_ONCE(a52_p414_ram)) {
\t\tdst = (u8 __iomem *)a52_p414_ram + A52_P421_OFF2 +
\t\t      (unsigned int)stage * A52_P421_SLOT_BYTES;
\t\ta52_p421_copy_persist(dst, &slot);
\t}
}
EXPORT_SYMBOL_GPL(a52_p421_survival_record);
'''
    tail_new = '''\tif (READ_ONCE(a52_p414_ram)) {
\t\tdst = (u8 __iomem *)a52_p414_ram + A52_P421_OFF2 +
\t\t      (unsigned int)stage * A52_P421_SLOT_BYTES;
\t\ta52_p421_copy_persist(dst, &slot);
\t}

\tif (stage == 8U)
\t\ta52_p422_survival_record_at(3U, 1U, a52_p422_s08_ns,
\t\t\t\t       0, 0, 0, 0);
}
EXPORT_SYMBOL_GPL(a52_p421_survival_record);
'''
    text = one(text, tail, tail_new, "M03 S08 persistence")
    return text


P422_SMMU_HELPERS = r'''
/* A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1
 * Cache SID 0x800's exact domain/context IRQ registration during SMMU setup,
 * but defer persistent writes until the Phase421 target is active and B19/B1A
 * survival RAM is known to be initialized.
 */
extern bool a52_p421_target_active(void);
extern void a52_p422_survival_record_at(u32 stage, u32 seq, u64 ts_ns,
					 u64 arg0, u64 arg1,
					 u64 arg2, u64 arg3);

struct a52_p422_irq_cache {
	struct iommu_domain *domain;
	struct arm_smmu_device *smmu;
	u16 sid;
	u8 cbndx;
	u8 irptndx;
	int irq;
	int request_ret;
	bool valid;
};

static struct a52_p422_irq_cache a52_p422_irq_cache;
static atomic_t a52_p422_fault_count = ATOMIC_INIT(0);

static bool a52_p422_dev_has_sid800(struct device *dev)
{
	struct iommu_fwspec *fwspec = dev_iommu_fwspec_get(dev);
	int i;

	if (!fwspec)
		return false;
	for (i = 0; i < fwspec->num_ids; i++) {
		u16 sid = FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[i]);

		if (sid == 0x800)
			return true;
	}
	return false;
}

static bool a52_p422_target_domain(struct iommu_domain *domain, int cbndx)
{
	return a52_p421_target_active() &&
	       READ_ONCE(a52_p422_irq_cache.valid) &&
	       a52_p422_irq_cache.sid == 0x800 &&
	       a52_p422_irq_cache.domain == domain &&
	       a52_p422_irq_cache.cbndx == cbndx;
}

void a52_p422_publish_m00(u64 cmd_buffer_iova)
{
	struct a52_p422_irq_cache *c = &a52_p422_irq_cache;
	u64 packed;

	if (!READ_ONCE(c->valid) || c->sid != 0x800)
		return;

	packed = ((u64)c->sid << 48) |
		 ((u64)c->cbndx << 40) |
		 ((u64)c->irptndx << 32) |
		 (u32)c->irq;
	a52_p422_survival_record_at(0U, 1U, ktime_get_boottime_ns(),
		cmd_buffer_iova, packed, (u64)(s64)c->request_ret, 0);
}
EXPORT_SYMBOL_GPL(a52_p422_publish_m00);

void a52_p422_display_timeout_snapshot(u64 cmd_buffer_iova)
{
	struct a52_p422_irq_cache *c = &a52_p422_irq_cache;
	u32 fsr = ~0U, sctlr = ~0U;
	u64 far = ~0ULL;
	u32 count;
	int ret;

	if (!a52_p422_target_domain(c->domain, c->cbndx) || !c->smmu)
		return;

	ret = arm_smmu_rpm_get(c->smmu);
	if (ret >= 0) {
		fsr = arm_smmu_cb_read(c->smmu, c->cbndx, ARM_SMMU_CB_FSR);
		far = arm_smmu_cb_readq(c->smmu, c->cbndx, ARM_SMMU_CB_FAR);
		sctlr = arm_smmu_cb_read(c->smmu, c->cbndx, ARM_SMMU_CB_SCTLR);
		arm_smmu_rpm_put(c->smmu);
	}
	count = (u32)atomic_read(&a52_p422_fault_count);
	a52_p422_survival_record_at(2U, count, ktime_get_boottime_ns(),
		cmd_buffer_iova, far, ((u64)sctlr << 32) | fsr,
		((u64)(u32)ret << 32) | count);
}
EXPORT_SYMBOL_GPL(a52_p422_display_timeout_snapshot);
'''


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1",
        "A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1",
        "static irqreturn_t arm_smmu_context_fault(int irq, void *dev)",
        "ret = devm_request_irq(smmu->dev, irq, context_fault,",
        "ARM_SMMU_CB_FSR",
        "ARM_SMMU_CB_FAR",
        "ARM_SMMU_CB_SCTLR",
    ):
        if token not in text:
            raise SystemExit("Phase422 SMMU prerequisite missing: " + token)

    if "#include <linux/ktime.h>\n" not in text:
        text = one(text, "#include <linux/io.h>\n",
                   "#include <linux/io.h>\n#include <linux/ktime.h>\n",
                   "ktime include")

    anchor = "static irqreturn_t arm_smmu_context_fault(int irq, void *dev)\n"
    text = one(text, anchor, P422_SMMU_HELPERS + "\n" + anchor,
               "SMMU helpers insertion")

    decl = "\tu32 fsr, fsynr, cbfrsynra;\n\tunsigned long iova;\n"
    text = one(text, decl,
               "\tu32 fsr, fsynr, cbfrsynra;\n\tu32 a52_p422_sctlr;\n\tu64 a52_p422_ts;\n\tu32 a52_p422_count;\n\tunsigned long iova;\n",
               "fault locals")

    fault_anchor = '''\tcbfrsynra = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBFRSYNRA(idx));

\t/* Phase411: values are already required by the real fault handler.
'''
    fault_new = '''\tcbfrsynra = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBFRSYNRA(idx));

\t/* Phase422: one extra context-bank read only when the exact SID 0x800
\t * Phase421 target is active. Record before the existing FSR clear.
\t */
\tif (a52_p422_target_domain(domain, idx)) {
\t\ta52_p422_ts = ktime_get_boottime_ns();
\t\ta52_p422_sctlr = arm_smmu_cb_read(smmu, idx, ARM_SMMU_CB_SCTLR);
\t\ta52_p422_count = (u32)atomic_inc_return(&a52_p422_fault_count);
\t\ta52_p422_survival_record_at(1U, a52_p422_count, a52_p422_ts,
\t\t\t(u64)iova, ((u64)fsynr << 32) | fsr,
\t\t\t((u64)(u32)idx << 32) | a52_p422_sctlr,
\t\t\t((u64)(u32)irq << 32) | cbfrsynra);
\t}

\t/* Phase411: values are already required by the real fault handler.
'''
    text = one(text, fault_anchor, fault_new, "M01 before FSR clear")

    req = '''\t\tret = devm_request_irq(smmu->dev, irq, context_fault,
\t\t\t       IRQF_SHARED, "arm-smmu-context-fault", domain);
\t\tif (ret < 0) {
'''
    req_new = '''\t\tret = devm_request_irq(smmu->dev, irq, context_fault,
\t\t\t       IRQF_SHARED, "arm-smmu-context-fault", domain);

\t\t/* Cache first, publish later from Phase421 S00 after survival RAM is live. */
\t\tif (a52_p422_dev_has_sid800(dev)) {
\t\t\ta52_p422_irq_cache.domain = domain;
\t\t\ta52_p422_irq_cache.smmu = smmu;
\t\t\ta52_p422_irq_cache.sid = 0x800;
\t\t\ta52_p422_irq_cache.cbndx = cfg->cbndx;
\t\t\ta52_p422_irq_cache.irptndx = cfg->irptndx;
\t\t\ta52_p422_irq_cache.irq = irq;
\t\t\ta52_p422_irq_cache.request_ret = ret;
\t\t\tWRITE_ONCE(a52_p422_irq_cache.valid, true);
\t\t}
\t\tif (ret < 0) {
'''
    text = one(text, req, req_new, "M00 IRQ cache")
    return text


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1",
        "bool a52_p421_target_active(void)",
        "a52_p421_survival_record(0U, A52_P421_V_FLAGS",
        "a52_p421_survival_record(10U, A52_P421_V_IRQ | A52_P421_V_RET",
        "dsi_ctrl->cmd_buffer_iova",
    ):
        if token not in text:
            raise SystemExit("Phase422 DSI prerequisite missing: " + token)

    decl = '''extern void a52_p421_survival_record(u8 stage, u8 valid, u32 flags,
				     int rc, u32 irq, int ret);
'''
    decl_new = decl + '''/* A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1 */
extern void a52_p422_publish_m00(u64 cmd_buffer_iova);
extern void a52_p422_display_timeout_snapshot(u64 cmd_buffer_iova);
'''
    text = one(text, decl, decl_new, "DSI declarations")

    s00 = '''\tif (a52_p421_f0) {
\t\tatomic_set(&a52_p421_target, 1);
\t\ta52_p421_survival_record(0U, A52_P421_V_FLAGS,
\t\t\tflags ? *flags : 0U, 0, 0U, 0);
\t}
'''
    s00_new = '''\tif (a52_p421_f0) {
\t\tatomic_set(&a52_p421_target, 1);
\t\ta52_p421_survival_record(0U, A52_P421_V_FLAGS,
\t\t\tflags ? *flags : 0U, 0, 0U, 0);
\t\ta52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
\t}
'''
    text = one(text, s00, s00_new, "M00 publish at S00")

    s10 = '''\tif (a52_p421_target_active())
\t\ta52_p421_survival_record(10U, A52_P421_V_IRQ | A52_P421_V_RET,
\t\t\t0U, 0, (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\tret);
'''
    s10_new = s10 + '''\tif (a52_p421_target_active())
\t\ta52_p422_display_timeout_snapshot((u64)dsi_ctrl->cmd_buffer_iova);
'''
    text = one(text, s10, s10_new, "M02 timeout snapshot")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    smmu = (root / SMMU).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")

    for token in (
        MARK,
        "#define A52_P422_OFF0              0x00000200U",
        "#define A52_P422_OFF1              0x00000300U",
        "#define A52_P422_OFF2              0x00000D80U",
        "struct a52_p422_slot",
        "a52_p422_survival_record_at(3U, 1U, a52_p422_s08_ns",
        "ktime_get_boottime_ns()",
        "EXPORT_SYMBOL_GPL(a52_p422_survival_record_at);",
    ):
        if token not in rec:
            raise SystemExit("Phase422 recorder validation missing: " + token)

    for token in (
        MARK,
        "struct a52_p422_irq_cache",
        "a52_p422_dev_has_sid800",
        "a52_p422_target_domain",
        "a52_p422_publish_m00",
        "a52_p422_display_timeout_snapshot",
        "a52_p422_irq_cache.sid = 0x800;",
        "a52_p422_sctlr = arm_smmu_cb_read(smmu, idx, ARM_SMMU_CB_SCTLR);",
        "a52_p422_survival_record_at(1U, a52_p422_count",
        "arm_smmu_cb_write(smmu, idx, ARM_SMMU_CB_FSR, fsr);",
    ):
        if token not in smmu:
            raise SystemExit("Phase422 SMMU validation missing: " + token)

    m01 = smmu.index("a52_p422_survival_record_at(1U, a52_p422_count")
    clear = smmu.index("arm_smmu_cb_write(smmu, idx, ARM_SMMU_CB_FSR, fsr);")
    if m01 > clear:
        raise SystemExit("Phase422 M01 is after FSR clear")
    if "ARM_SMMU_CB_RESUME" in smmu[smmu.find(MARK):smmu.find("static irqreturn_t arm_smmu_global_fault")]:
        raise SystemExit("Phase422 must not add CB_RESUME behavior")

    for token in (
        MARK,
        "a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);",
        "a52_p422_display_timeout_snapshot((u64)dsi_ctrl->cmd_buffer_iova);",
    ):
        if token not in dsi:
            raise SystemExit("Phase422 DSI validation missing: " + token)


def apply(root: Path) -> None:
    paths = {REC: patch_rec, SMMU: patch_smmu, DSI: patch_dsi}
    for rel, patcher in paths.items():
        path = root / rel
        if not path.is_file():
            raise SystemExit("Phase422 source missing: " + str(path))
        path.write_text(patcher(path.read_text()))
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if args.check_only:
        validate(args.root)
    else:
        apply(args.root)
    print("Phase422 display SMMU fault probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
