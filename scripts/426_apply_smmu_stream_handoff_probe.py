#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE426_SMMU_STREAM_HANDOFF_PROBE_V1"
SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase426 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE426_SMMU_STREAM_HANDOFF_PROBE_V1
 *
 * Read-only diagnosis of the qcom,skip-init firmware handoff contract.
 *
 * Phase68 deliberately preserved bootloader SMR/S2CR/context-bank hardware,
 * but did not import those routes into Linux smrs[]/s2crs[]/context_map.
 * TouchGrass does import them in arm_smmu_handoff_cbs().
 *
 * This phase DOES NOT repair that difference. It only:
 *   - snapshots every firmware-valid SMR/S2CR before reset/test writes;
 *   - observes which SMR arm_smmu_test_smr_masks() uses and its before/after;
 *   - snapshots HW + Linux SW routes at exact F0 S00;
 *   - latches (without recorder I/O) every apps-SMMU global fault;
 *   - after S10/timeout, emits the evidence to the existing persistent recorder.
 *
 * No SMR/S2CR/CB/global-fault register is written by Phase426.
 */
#define A52_P426_MAX_SMRS 128U
#define A52_P426_SID      0x0800U

struct a52_p426_hw_route {
	u32 smr;
	u32 s2cr;
};

struct a52_p426_sw_route {
	u16 id;
	u16 mask;
	u8 cbndx;
	u8 type;
	u8 valid;
	u8 pinned;
	s32 count;
};

struct a52_p426_snapshot {
	struct a52_p426_hw_route hw[A52_P426_MAX_SMRS];
	struct a52_p426_sw_route sw[A52_P426_MAX_SMRS];
	u32 groups;
	u32 hw_valid;
	u32 hw_match;
	u32 sw_valid;
	u32 sw_match;
	u32 gfsr;
	u32 gfsynr0;
	u32 gfsynr1;
	u32 gfsynr2;
};

static struct arm_smmu_device *a52_p426_apps_smmu;
static struct a52_p426_snapshot a52_p426_boot;
static struct a52_p426_snapshot a52_p426_f0;
static struct a52_p426_snapshot a52_p426_timeout;
static atomic_t a52_p426_boot_ready = ATOMIC_INIT(0);
static atomic_t a52_p426_f0_taken = ATOMIC_INIT(0);
static atomic_t a52_p426_global_faults = ATOMIC_INIT(0);
static int a52_p426_mask_test_idx = -1;
static u32 a52_p426_mask_test_before;
static u32 a52_p426_mask_test_after;
static u32 a52_p426_last_gfsr;
static u32 a52_p426_last_gfsynr0;
static u32 a52_p426_last_gfsynr1;
static u32 a52_p426_last_gfsynr2;

static bool a52_p426_is_target(struct arm_smmu_device *smmu)
{
	return smmu && smmu->skip_init && smmu->dev && smmu->dev->of_node &&
	       of_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500");
}

static bool a52_p426_hw_valid(struct arm_smmu_device *smmu, u32 smr, u32 s2cr)
{
	if (smmu->features & ARM_SMMU_FEAT_EXIDS)
		return !!(s2cr & ARM_SMMU_S2CR_EXIDVALID);
	return !!(smr & ARM_SMMU_SMR_VALID);
}

static bool a52_p426_sid_match_raw(struct arm_smmu_device *smmu,
				    u32 smr, u32 s2cr, u16 sid)
{
	u16 id;
	u16 mask;

	if (!a52_p426_hw_valid(smmu, smr, s2cr))
		return false;

	id = (u16)FIELD_GET(ARM_SMMU_SMR_ID, smr);
	/* ARM_SMMU_SMR_MASK includes bit31 in this 5.10 header even though
	 * non-EXIDS validity also lives there. Strip that validity bit from
	 * the decoded mask before doing the architectural stream match.
	 */
	mask = (u16)FIELD_GET(ARM_SMMU_SMR_MASK, smr) & 0x7fffU;
	return !((sid ^ id) & (u16)~mask);
}

static bool a52_p426_sid_match_sw(const struct arm_smmu_smr *smr, u16 sid)
{
	if (!smr || !smr->valid)
		return false;
	return !((sid ^ smr->id) & (u16)~smr->mask);
}

static void a52_p426_take_snapshot(struct arm_smmu_device *smmu,
				   struct a52_p426_snapshot *snap,
				   bool include_sw)
{
	u32 i;
	u32 groups;

	if (!a52_p426_is_target(smmu) || !snap)
		return;

	memset(snap, 0, sizeof(*snap));
	groups = min_t(u32, smmu->num_mapping_groups, A52_P426_MAX_SMRS);
	snap->groups = groups;

	for (i = 0; i < groups; i++) {
		u32 raw_smr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
		u32 raw_s2cr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_S2CR(i));

		snap->hw[i].smr = raw_smr;
		snap->hw[i].s2cr = raw_s2cr;
		if (a52_p426_hw_valid(smmu, raw_smr, raw_s2cr))
			snap->hw_valid++;
		if (a52_p426_sid_match_raw(smmu, raw_smr, raw_s2cr,
					    A52_P426_SID))
			snap->hw_match++;

		if (include_sw && smmu->smrs && smmu->s2crs) {
			struct a52_p426_sw_route *d = &snap->sw[i];
			struct arm_smmu_smr *smr = &smmu->smrs[i];
			struct arm_smmu_s2cr *s2cr = &smmu->s2crs[i];

			d->id = smr->id;
			d->mask = smr->mask;
			d->cbndx = s2cr->cbndx;
			d->type = (u8)s2cr->type;
			d->valid = smr->valid ? 1U : 0U;
			d->pinned = s2cr->pinned ? 1U : 0U;
			d->count = s2cr->count;
			if (smr->valid)
				snap->sw_valid++;
			if (a52_p426_sid_match_sw(smr, A52_P426_SID))
				snap->sw_match++;
		}
	}

	snap->gfsr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSR);
	snap->gfsynr0 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR0);
	snap->gfsynr1 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR1);
	snap->gfsynr2 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR2);
}

static void a52_p426_cache_boot_routes(struct arm_smmu_device *smmu)
{
	if (!a52_p426_is_target(smmu))
		return;

	WRITE_ONCE(a52_p426_apps_smmu, smmu);
	/* arm_smmu_device_cfg_probe() has allocated/zeroed Linux's software
	 * tables, while qcom,skip-init hardware still contains firmware state.
	 * This is before arm_smmu_device_reset() and arm_smmu_test_smr_masks().
	 */
	a52_p426_take_snapshot(smmu, &a52_p426_boot, true);
	smp_wmb();
	atomic_set(&a52_p426_boot_ready, 1);
}

void a52_p426_capture_f0(void)
{
	struct arm_smmu_device *smmu = READ_ONCE(a52_p426_apps_smmu);

	if (!smmu || !atomic_read(&a52_p426_boot_ready))
		return;
	if (atomic_cmpxchg(&a52_p426_f0_taken, 0, 1) != 0)
		return;

	/* Exact S00 observer: reads + normal-RAM stores only. No printk,
	 * a52_ackfr_record(), persistence, delays, votes, or SMMU writes.
	 */
	a52_p426_take_snapshot(smmu, &a52_p426_f0, true);
	smp_wmb();
}
EXPORT_SYMBOL_GPL(a52_p426_capture_f0);

static void a52_p426_latch_global_fault(struct arm_smmu_device *smmu,
					u32 gfsr, u32 s0, u32 s1, u32 s2)
{
	if (!a52_p426_is_target(smmu) || !gfsr)
		return;

	WRITE_ONCE(a52_p426_last_gfsr, gfsr);
	WRITE_ONCE(a52_p426_last_gfsynr0, s0);
	WRITE_ONCE(a52_p426_last_gfsynr1, s1);
	WRITE_ONCE(a52_p426_last_gfsynr2, s2);
	smp_wmb();
	atomic_inc(&a52_p426_global_faults);
}

static void a52_p426_dump_hw_matches(const char *tag,
				      const struct a52_p426_snapshot *snap,
				      struct arm_smmu_device *smmu)
{
	u32 i;

	for (i = 0; i < snap->groups; i++) {
		u32 smr = snap->hw[i].smr;
		u32 s2cr = snap->hw[i].s2cr;

		if (!a52_p426_sid_match_raw(smmu, smr, s2cr, A52_P426_SID))
			continue;
		a52_ackfr_record("P426 %s i=%u smr=%x s2=%x id=%x m=%x cb=%u ty=%u",
			tag, i, smr, s2cr,
			(u32)(u16)FIELD_GET(ARM_SMMU_SMR_ID, smr),
			(u32)((u16)FIELD_GET(ARM_SMMU_SMR_MASK, smr) & 0x7fffU),
			(u32)FIELD_GET(ARM_SMMU_S2CR_CBNDX, s2cr),
			(u32)FIELD_GET(ARM_SMMU_S2CR_TYPE, s2cr));
	}
}

static void a52_p426_dump_sw_matches(const struct a52_p426_snapshot *snap)
{
	u32 i;

	for (i = 0; i < snap->groups; i++) {
		const struct a52_p426_sw_route *r = &snap->sw[i];

		if (!r->valid ||
		    ((A52_P426_SID ^ r->id) & (u16)~r->mask))
			continue;
		a52_ackfr_record("P426 SW i=%u id=%x m=%x cb=%u ty=%u c=%d pin=%u",
			i, (u32)r->id, (u32)r->mask, (u32)r->cbndx,
			(u32)r->type, r->count, (u32)r->pinned);
	}
}

void a52_p426_timeout_dump(void)
{
	struct arm_smmu_device *smmu = READ_ONCE(a52_p426_apps_smmu);
	u32 i;
	u32 gf;

	if (!smmu || !atomic_read(&a52_p426_boot_ready))
		return;

	a52_p426_take_snapshot(smmu, &a52_p426_timeout, true);
	gf = (u32)atomic_read(&a52_p426_global_faults);

	a52_ackfr_record("P426 B g=%u hv=%u hm=%u sv=%u sm=%u",
		a52_p426_boot.groups, a52_p426_boot.hw_valid,
		a52_p426_boot.hw_match, a52_p426_boot.sw_valid,
		a52_p426_boot.sw_match);
	a52_ackfr_record("P426 MT i=%d pre=%x post=%x",
		a52_p426_mask_test_idx, a52_p426_mask_test_before,
		a52_p426_mask_test_after);
	a52_ackfr_record("P426 F g=%u hv=%u hm=%u sv=%u sm=%u",
		a52_p426_f0.groups, a52_p426_f0.hw_valid,
		a52_p426_f0.hw_match, a52_p426_f0.sw_valid,
		a52_p426_f0.sw_match);
	a52_ackfr_record("P426 T g=%u hv=%u hm=%u sv=%u sm=%u",
		a52_p426_timeout.groups, a52_p426_timeout.hw_valid,
		a52_p426_timeout.hw_match, a52_p426_timeout.sw_valid,
		a52_p426_timeout.sw_match);
	a52_ackfr_record("P426 GF n=%u g=%x s0=%x s1=%x s2=%x",
		gf, READ_ONCE(a52_p426_last_gfsr),
		READ_ONCE(a52_p426_last_gfsynr0),
		READ_ONCE(a52_p426_last_gfsynr1),
		READ_ONCE(a52_p426_last_gfsynr2));
	a52_ackfr_record("P426 TG g=%x s0=%x s1=%x s2=%x",
		a52_p426_timeout.gfsr, a52_p426_timeout.gfsynr0,
		a52_p426_timeout.gfsynr1, a52_p426_timeout.gfsynr2);

	a52_p426_dump_hw_matches("BM", &a52_p426_boot, smmu);
	a52_p426_dump_hw_matches("FM", &a52_p426_f0, smmu);
	a52_p426_dump_hw_matches("TM", &a52_p426_timeout, smmu);
	a52_p426_dump_sw_matches(&a52_p426_f0);

	/* Full bootloader-valid table is emitted only after the failed transfer,
	 * never on the F0/pre-trigger path.
	 */
	for (i = 0; i < a52_p426_boot.groups; i++) {
		u32 smr = a52_p426_boot.hw[i].smr;
		u32 s2cr = a52_p426_boot.hw[i].s2cr;

		if (!a52_p426_hw_valid(smmu, smr, s2cr))
			continue;
		a52_ackfr_record("P426 BT i=%u smr=%x s2=%x", i, smr, s2cr);
	}
}
EXPORT_SYMBOL_GPL(a52_p426_timeout_dump);
'''


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text

    for token in (
        "A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1",
        "M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
        "M393 G irq=%d g=%x s0=%x s1=%x s2=%x",
        "smmu->skip_init = of_property_read_bool(dev->of_node,",
        "\"qcom,skip-init\"",
        "if (!smmu->skip_init)",
        "static void arm_smmu_test_smr_masks(struct arm_smmu_device *smmu)",
        "static irqreturn_t arm_smmu_global_fault(int irq, void *dev)",
        "err = arm_smmu_device_cfg_probe(smmu);",
    ):
        if token not in text:
            raise SystemExit("Phase426 SMMU prerequisite missing: " + token)

    anchor = "static irqreturn_t arm_smmu_context_fault(int irq, void *dev)\n"
    text = one(text, anchor, BLOCK + "\n" + anchor, "probe block insertion")

    # Cache firmware routes after cfg_probe has allocated Linux SW tables, but
    # before reset/mask-test code is allowed to touch the retained HW table.
    old = """	err = arm_smmu_device_cfg_probe(smmu);
	if (err)
		return err;

"""
    if old not in text:
        old = """	err = arm_smmu_device_cfg_probe(smmu);
	if (err)
		goto out_power_off;

"""
        new = """	err = arm_smmu_device_cfg_probe(smmu);
	if (err)
		goto out_power_off;

	a52_p426_cache_boot_routes(smmu);

"""
    else:
        new = """	err = arm_smmu_device_cfg_probe(smmu);
	if (err)
		return err;

	a52_p426_cache_boot_routes(smmu);

"""
    text = one(text, old, new, "pre-write boot route cache")

    # Observe the exact physically chosen mask-test entry before the existing
    # writes. This intentionally does NOT alter/restore the legacy behavior.
    old = """smr_ok:
	/*
	 * SMR.ID bits may not be preserved if the corresponding MASK
"""
    new = """smr_ok:
	if (a52_p426_is_target(smmu)) {
		a52_p426_mask_test_idx = i;
		a52_p426_mask_test_before =
			arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
	}
	/*
	 * SMR.ID bits may not be preserved if the corresponding MASK
"""
    text = one(text, old, new, "mask test pre-read")

    old = """	smr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
	smmu->smr_mask_mask = FIELD_GET(ARM_SMMU_SMR_MASK, smr);
}
"""
    new = """	smr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
	smmu->smr_mask_mask = FIELD_GET(ARM_SMMU_SMR_MASK, smr);
	if (a52_p426_is_target(smmu))
		a52_p426_mask_test_after =
			arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
}
"""
    text = one(text, old, new, "mask test post-read")

    # Latch global-fault evidence before the existing handler clears sGFSR.
    old = """	gfsynr2 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR2);

	if (!gfsr)
"""
    new = """	gfsynr2 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR2);

	a52_p426_latch_global_fault(smmu, gfsr, gfsynr0, gfsynr1, gfsynr2);

	if (!gfsr)
"""
    text = one(text, old, new, "global fault latch")

    return text


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE424_PAIRED_HW_SNAPSHOT_V1",
        "a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);",
        "a52_p424_dump_snapshot();",
    ):
        if token not in text:
            raise SystemExit("Phase426 DSI prerequisite missing: " + token)

    decl = "extern void a52_p424_dump_snapshot(void);\n"
    text = one(
        text, decl,
        decl +
        "extern void a52_p426_capture_f0(void);\n"
        "extern void a52_p426_timeout_dump(void);\n",
        "DSI declarations",
    )

    old = """		a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
	}
"""
    new = """		a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
		a52_p426_capture_f0();
	}
"""
    text = one(text, old, new, "S00 read-only stream snapshot")

    old = """	if (a52_p421_target_active())
		a52_p424_dump_snapshot();
"""
    new = old + """	if (a52_p421_target_active())
		a52_p426_timeout_dump();
"""
    text = one(text, old, new, "post-timeout stream dump")
    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE425_REBOOT_ORIGIN_RECORDER_V1" not in text:
        raise SystemExit("Phase426 recorder requires Phase425 lineage")

    retained = """	if (unlikely(atomic_read(&a52_r280_retained)) &&
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    retained_new = """	if (unlikely(atomic_read(&a52_r280_retained)) &&
	    strncmp(fmt, "P426", 4) &&
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    text = one(text, retained, retained_new, "retention admission")

    normal = """if (strncmp(fmt, "P425", 4) &&
    strncmp(fmt, "P424", 4) &&
"""
    normal_new = """if (strncmp(fmt, "P426", 4) &&
    strncmp(fmt, "P425", 4) &&
    strncmp(fmt, "P424", 4) &&
"""
    text = one(text, normal, normal_new, "normal admission")

    clean = """	if (!fmt || (
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    clean_new = """	if (!fmt || (
	    strncmp(fmt, "P426", 4) &&
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    text = one(text, clean, clean_new, "Phase402 admission")

    critical = """	return !strncmp(message, "P425 ", 5) ||
	       !strncmp(message, "P414 ", 5) ||
"""
    critical_new = """	return !strncmp(message, "P426 ", 5) ||
	       !strncmp(message, "P425 ", 5) ||
	       !strncmp(message, "P414 ", 5) ||
"""
    text = one(text, critical, critical_new, "critical persistent admission")

    text += "\n/* " + MARK + ": P426 deferred SMMU stream-map evidence admitted. */\n"
    return text


def validate(root: Path) -> None:
    smmu = (root / SMMU).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")
    alltxt = smmu + dsi + rec

    required = (
        MARK,
        "a52_p426_cache_boot_routes(smmu);",
        "a52_p426_mask_test_before",
        "a52_p426_mask_test_after",
        "a52_p426_latch_global_fault(smmu, gfsr, gfsynr0, gfsynr1, gfsynr2);",
        "void a52_p426_capture_f0(void)",
        "void a52_p426_timeout_dump(void)",
        'P426 GF n=%u g=%x s0=%x s1=%x s2=%x',
        'P426 BM i=%u smr=%x s2=%x',
        'P426 SW i=%u id=%x m=%x cb=%u ty=%u c=%d pin=%u',
        'P426 BT i=%u smr=%x s2=%x',
        "a52_p426_capture_f0();",
        "a52_p426_timeout_dump();",
        'strncmp(fmt, "P426", 4)',
        '!strncmp(message, "P426 ", 5)',
    )
    for token in required:
        if token not in alltxt:
            raise SystemExit("Phase426 validation missing: " + token)

    # F0 observer must remain read-only and recorder-free.
    a = smmu.index("void a52_p426_capture_f0(void)")
    b = smmu.index("EXPORT_SYMBOL_GPL(a52_p426_capture_f0);", a)
    f0 = smmu[a:b]
    for forbidden in (
        "a52_ackfr_record(",
        "arm_smmu_gr0_write(",
        "arm_smmu_cb_write(",
        "writel",
        "msleep",
        "udelay",
    ):
        if forbidden in f0:
            raise SystemExit("Phase426 F0 observer contains forbidden operation: " + forbidden)

    # Boot cache must precede the inherited reset/mask test at probe time.
    cache = smmu.index("a52_p426_cache_boot_routes(smmu);")
    reset = smmu.index("arm_smmu_device_reset(smmu);", cache)
    test = smmu.index("arm_smmu_test_smr_masks(smmu);", reset)
    if not cache < reset < test:
        raise SystemExit("Phase426 boot-cache ordering must be cache < reset < mask-test")

    # Global fault latch must precede existing sGFSR clear.
    handler = smmu.index("static irqreturn_t arm_smmu_global_fault(int irq, void *dev)")
    latch = smmu.index("a52_p426_latch_global_fault(smmu, gfsr", handler)
    clear = smmu.index("arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_sGFSR, gfsr);", handler)
    if latch > clear:
        raise SystemExit("Phase426 global fault latch occurs after clear")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (SMMU, DSI, REC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase426 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / SMMU
        p.write_text(patch_smmu(p.read_text(errors="replace")))
        p = ns.root / DSI
        p.write_text(patch_dsi(p.read_text(errors="replace")))
        p = ns.root / REC
        p.write_text(patch_rec(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase426 SMMU stream-handoff read-only probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
