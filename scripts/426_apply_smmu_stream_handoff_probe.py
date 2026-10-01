#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
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


def require_tokens(text: str, tokens: tuple[str, ...], scope: str) -> None:
    missing = [token for token in tokens if token not in text]
    if missing:
        raise SystemExit(
            f"Phase426 {scope} prerequisites missing ({len(missing)}):\n" +
            "\n".join(f" - {token}" for token in missing)
        )


def require_unique_anchors(
    text: str, anchors: tuple[tuple[str, str], ...], scope: str
) -> None:
    bad = []
    for label, anchor in anchors:
        n = text.count(anchor)
        if n != 1:
            bad.append(f"{label}: expected 1, found {n}")
    if bad:
        raise SystemExit(
            f"Phase426 {scope} anchor preflight failed ({len(bad)}):\n" +
            "\n".join(f" - {item}" for item in bad)
        )


def strip_c_comments(text: str) -> str:
    # Validation is interested in executable calls, not words inside comments.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


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

static u16 a52_p426_decode_mask(struct arm_smmu_device *smmu, u32 smr)
{
	u16 mask = (u16)FIELD_GET(ARM_SMMU_SMR_MASK, smr);

	/* In non-EXIDS mode SMR bit31 is VALID, although the generic 5.10
	 * ARM_SMMU_SMR_MASK macro spans bits31:16. With EXIDS, validity moves
	 * to S2CR.EXIDVALID and bit31 is a real mask bit.
	 */
	if (!(smmu->features & ARM_SMMU_FEAT_EXIDS))
		mask &= 0x7fffU;
	return mask;
}

static bool a52_p426_sid_match_raw(struct arm_smmu_device *smmu,
				    u32 smr, u32 s2cr, u16 sid)
{
	u16 id;
	u16 mask;

	if (!a52_p426_hw_valid(smmu, smr, s2cr))
		return false;

	id = (u16)FIELD_GET(ARM_SMMU_SMR_ID, smr);
	mask = a52_p426_decode_mask(smmu, smr);
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
	 * recorder I/O, persistence, delays, votes, or SMMU writes.
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
			(u32)a52_p426_decode_mask(smmu, smr),
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

    require_tokens(text, (
        "A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1",
        "M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
        "M393 G irq=%d g=%x s0=%x s1=%x s2=%x",
        "smmu->skip_init = of_property_read_bool(dev->of_node,",
        "\"qcom,skip-init\"",
        "if (!smmu->skip_init)",
        "static void arm_smmu_test_smr_masks(struct arm_smmu_device *smmu)",
        "static irqreturn_t arm_smmu_global_fault(int irq, void *dev)",
        "err = arm_smmu_device_cfg_probe(smmu);",
    ), "SMMU")

    block_anchor = "static irqreturn_t arm_smmu_context_fault(int irq, void *dev)\n"
    probe_reset = (
        "\tplatform_set_drvdata(pdev, smmu);\n"
        "\tarm_smmu_device_reset(smmu);\n"
        "\tarm_smmu_test_smr_masks(smmu);\n"
    )
    mask_pre = """smr_ok:
	/*
	 * SMR.ID bits may not be preserved if the corresponding MASK
"""
    mask_post = """	smr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
	smmu->smr_mask_mask = FIELD_GET(ARM_SMMU_SMR_MASK, smr);
}
"""
    global_old = """	gfsynr2 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR2);

	if (!gfsr)
"""
    require_unique_anchors(text, (
        ("probe block insertion", block_anchor),
        ("probe-time reset sequence", probe_reset),
        ("mask-test pre-read", mask_pre),
        ("mask-test post-read", mask_post),
        ("global-fault latch", global_old),
    ), "SMMU")

    text = one(text, block_anchor, BLOCK + "\n" + block_anchor,
               "probe block insertion")

    probe_reset_new = (
        "\tplatform_set_drvdata(pdev, smmu);\n"
        "\ta52_p426_cache_boot_routes(smmu);\n"
        "\tarm_smmu_device_reset(smmu);\n"
        "\tarm_smmu_test_smr_masks(smmu);\n"
    )
    text = one(text, probe_reset, probe_reset_new, "pre-write boot route cache")

    mask_pre_new = """smr_ok:
	if (a52_p426_is_target(smmu)) {
		a52_p426_mask_test_idx = i;
		a52_p426_mask_test_before =
			arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
	}
	/*
	 * SMR.ID bits may not be preserved if the corresponding MASK
"""
    text = one(text, mask_pre, mask_pre_new, "mask test pre-read")

    mask_post_new = """	smr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
	smmu->smr_mask_mask = FIELD_GET(ARM_SMMU_SMR_MASK, smr);
	if (a52_p426_is_target(smmu))
		a52_p426_mask_test_after =
			arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
}
"""
    text = one(text, mask_post, mask_post_new, "mask test post-read")

    global_new = """	gfsynr2 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR2);

	a52_p426_latch_global_fault(smmu, gfsr, gfsynr0, gfsynr1, gfsynr2);

	if (!gfsr)
"""
    text = one(text, global_old, global_new, "global fault latch")
    return text


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text

    require_tokens(text, (
        "a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);",
        "extern void a52_p424_dump_snapshot(void);",
        "a52_p424_dump_snapshot();",
    ), "DSI")

    decl = "extern void a52_p424_dump_snapshot(void);\n"
    s00 = """		a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
	}
"""
    post = """	if (a52_p421_target_active())
		a52_p424_dump_snapshot();
"""
    require_unique_anchors(text, (
        ("declarations", decl),
        ("S00 read-only stream snapshot", s00),
        ("post-timeout stream dump", post),
    ), "DSI")

    text = one(
        text, decl,
        decl +
        "extern void a52_p426_capture_f0(void);\n"
        "extern void a52_p426_timeout_dump(void);\n",
        "DSI declarations",
    )

    s00_new = """		a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);
		a52_p426_capture_f0();
	}
"""
    text = one(text, s00, s00_new, "S00 read-only stream snapshot")

    post_new = post + """	if (a52_p421_target_active())
		a52_p426_timeout_dump();
"""
    text = one(text, post, post_new, "post-timeout stream dump")
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
    normal = """if (strncmp(fmt, "P425", 4) &&
    strncmp(fmt, "P424", 4) &&
"""
    clean = """	if (!fmt || (
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    anchors = [
        ("retention admission", retained),
        ("normal admission", normal),
        ("Phase402 admission", clean),
    ]
    if '!strncmp(message, "P426 ", 5)' not in text:
        anchors.append(
            ("critical persistent admission",
             'return !strncmp(message, "P425 ", 5) ||')
        )
    require_unique_anchors(text, tuple(anchors), "recorder")

    retained_new = """	if (unlikely(atomic_read(&a52_r280_retained)) &&
	    strncmp(fmt, "P426", 4) &&
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    text = one(text, retained, retained_new, "retention admission")

    normal_new = """if (strncmp(fmt, "P426", 4) &&
    strncmp(fmt, "P425", 4) &&
    strncmp(fmt, "P424", 4) &&
"""
    text = one(text, normal, normal_new, "normal admission")

    clean_new = """	if (!fmt || (
	    strncmp(fmt, "P426", 4) &&
	    strncmp(fmt, "P425", 4) &&
	    strncmp(fmt, "P424", 4) &&
"""
    text = one(text, clean, clean_new, "Phase402 admission")

    if '!strncmp(message, "P426 ", 5)' not in text:
        critical = 'return !strncmp(message, "P425 ", 5) ||'
        critical_new = ('return !strncmp(message, "P426 ", 5) ||\n'
                        '\t       !strncmp(message, "P425 ", 5) ||')
        text = one(text, critical, critical_new, "critical persistent admission")

    text += "\n/* " + MARK + ": P426 deferred SMMU stream-map evidence admitted. */\n"
    return text


def validate(root: Path) -> None:
    smmu = (root / SMMU).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")
    alltxt = smmu + dsi + rec
    errors: list[str] = []

    required = (
        MARK,
        "a52_p426_decode_mask",
        "if (!(smmu->features & ARM_SMMU_FEAT_EXIDS))",
        "d->pinned = s2cr->pinned ? 1U : 0U;",
        "a52_p426_cache_boot_routes(smmu);",
        "a52_p426_mask_test_before",
        "a52_p426_mask_test_after",
        "a52_p426_latch_global_fault(smmu, gfsr, gfsynr0, gfsynr1, gfsynr2);",
        "void a52_p426_capture_f0(void)",
        "void a52_p426_timeout_dump(void)",
        'P426 B g=%u hv=%u hm=%u sv=%u sm=%u',
        'P426 MT i=%d pre=%x post=%x',
        'P426 F g=%u hv=%u hm=%u sv=%u sm=%u',
        'P426 T g=%u hv=%u hm=%u sv=%u sm=%u',
        'P426 GF n=%u g=%x s0=%x s1=%x s2=%x',
        'P426 TG g=%x s0=%x s1=%x s2=%x',
        'P426 %s i=%u smr=%x s2=%x id=%x m=%x cb=%u ty=%u',
        'a52_p426_dump_hw_matches("BM", &a52_p426_boot, smmu);',
        'a52_p426_dump_hw_matches("FM", &a52_p426_f0, smmu);',
        'a52_p426_dump_hw_matches("TM", &a52_p426_timeout, smmu);',
        'P426 SW i=%u id=%x m=%x cb=%u ty=%u c=%d pin=%u',
        'P426 BT i=%u smr=%x s2=%x',
        "a52_p426_capture_f0();",
        "a52_p426_timeout_dump();",
        'strncmp(fmt, "P426", 4)',
        '!strncmp(message, "P426 ", 5)',
    )
    for token in required:
        if token not in alltxt:
            errors.append("missing token: " + token)

    # F0 observer must remain read-only and recorder-free. Strip comments so
    # documentation such as "no recorder I/O" cannot trip the audit.
    a = smmu.find("void a52_p426_capture_f0(void)")
    b = smmu.find("EXPORT_SYMBOL_GPL(a52_p426_capture_f0);", a if a >= 0 else 0)
    if a < 0 or b < 0 or b <= a:
        errors.append("cannot isolate F0 observer")
    else:
        f0 = strip_c_comments(smmu[a:b])
        forbidden = (
            ("recorder write", r"\ba52_ackfr_record\s*\("),
            ("GR0 write", r"\barm_smmu_gr0_write\s*\("),
            ("CB write", r"\barm_smmu_cb_write\s*\("),
            ("raw writel", r"\bwritel(?:_relaxed)?\s*\("),
            ("msleep", r"\bmsleep\s*\("),
            ("udelay", r"\budelay\s*\("),
        )
        for label, pattern in forbidden:
            if re.search(pattern, f0):
                errors.append("F0 observer contains forbidden " + label)

    # Boot cache must be at the probe-time reset site, before reset and mask test.
    cache = smmu.find("a52_p426_cache_boot_routes(smmu);")
    reset = smmu.find("arm_smmu_device_reset(smmu);", cache + 1 if cache >= 0 else 0)
    test = smmu.find("arm_smmu_test_smr_masks(smmu);", reset + 1 if reset >= 0 else 0)
    if not (0 <= cache < reset < test):
        errors.append("boot-cache ordering is not cache < reset < mask-test")

    # Global fault evidence must be latched before the inherited sGFSR clear.
    handler = smmu.find("static irqreturn_t arm_smmu_global_fault(int irq, void *dev)")
    latch = smmu.find("a52_p426_latch_global_fault(smmu, gfsr", handler if handler >= 0 else 0)
    clear = smmu.find(
        "arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_sGFSR, gfsr);",
        handler if handler >= 0 else 0,
    )
    if not (0 <= handler < latch < clear):
        errors.append("global-fault ordering is not handler < latch < clear")

    # DSI correlation must preserve the intended ordering at S00 and S10.
    publish = dsi.find("a52_p422_publish_m00((u64)dsi_ctrl->cmd_buffer_iova);")
    capture = dsi.find("a52_p426_capture_f0();", publish + 1 if publish >= 0 else 0)
    if not (0 <= publish < capture):
        errors.append("DSI S00 ordering is not M00 publish < P426 capture")

    dump424 = dsi.find("a52_p424_dump_snapshot();")
    dump426 = dsi.find("a52_p426_timeout_dump();", dump424 + 1 if dump424 >= 0 else 0)
    if not (0 <= dump424 < dump426):
        errors.append("DSI S10 ordering is not P424 dump < P426 dump")

    if dsi.count("a52_p426_capture_f0();") != 1:
        errors.append(
            f"expected one DSI P426 capture call, found {dsi.count('a52_p426_capture_f0();')}"
        )
    if dsi.count("a52_p426_timeout_dump();") != 1:
        errors.append(
            f"expected one DSI P426 timeout call, found {dsi.count('a52_p426_timeout_dump();')}"
        )
    if rec.count('!strncmp(message, "P426 ", 5)') != 1:
        errors.append(
            "critical P426 admission count is " +
            str(rec.count('!strncmp(message, "P426 ", 5)'))
        )
    if rec.count('strncmp(fmt, "P426", 4)') < 3:
        errors.append(
            "expected P426 admission in retained/normal/clean paths; found " +
            str(rec.count('strncmp(fmt, "P426", 4)'))
        )

    if errors:
        raise SystemExit(
            f"Phase426 validation failed ({len(errors)} issue(s)):\n" +
            "\n".join(f" - {item}" for item in errors)
        )


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
