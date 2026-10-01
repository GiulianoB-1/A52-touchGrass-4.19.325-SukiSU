#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1"
ARM = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
BUS_ARB = Path("drivers/soc/qcom/msm_bus/msm_bus_arb_rpmh.c")
BUS_FAB = Path("drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase427 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase427 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase427 function brace missing: {sig}")
    depth = 0
    i = brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise SystemExit(f"Phase427 function end missing: {sig}")


ARM_BLOCK = r'''
/* A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1
 *
 * Functional A/B treatment, deliberately scoped to the one non-secure
 * display master whose firmware handoff was measured by Phase426:
 *   qcom,smmu_sde_unsec, SID 0x800, translating pinned SME, firmware CB2.
 *
 * TouchGrass reuses that inherited context bank.  The GKI Qualcomm importer
 * reserves/pins it, then normal allocation picks CB4 and arm_smmu_domain_add_master()
 * rewrites the stream.  Phase427 returns the inherited CB only for this exact
 * master.  SID 0x801 and every other firmware stream are untouched.
 *
 * The existing earlymap path remains authoritative: CB2 is programmed with
 * the new TTBR/TCR/MAIR while SCTLR.M is clear, then the existing
 * DOMAIN_ATTR_EARLY_MAP transition enables translation after mappings exist.
 * A context TLBI is issued while translation is disabled after CB2 is fully
 * programmed.  No other SMMU policy is changed.
 */
struct a52_p427_handoff_state {
	struct arm_smmu_device *smmu;
	struct iommu_domain *domain;
	u32 seen;
	u32 sid;
	s32 sme;
	u32 old_cb;
	u32 chosen_cb;
	u32 pinned;
	u32 old_type;
	u32 old_count;
	u32 context_reserved;
	u32 used;
	u32 irptndx;
	s32 irq;
	s32 request_ret;
};

static struct a52_p427_handoff_state a52_p427_handoff;

static bool a52_p427_sid800_dev(struct device *dev,
				struct iommu_fwspec **fw_out,
				struct arm_smmu_master_cfg **cfg_out)
{
	struct iommu_fwspec *fwspec;
	struct arm_smmu_master_cfg *cfg;
	u16 sid;

	if (!dev || !dev->of_node ||
	    !of_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec"))
		return false;

	fwspec = dev_iommu_fwspec_get(dev);
	cfg = dev_iommu_priv_get(dev);
	if (!fwspec || !cfg || fwspec->num_ids != 1)
		return false;

	sid = FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[0]);
	if (sid != 0x800)
		return false;

	if (fw_out)
		*fw_out = fwspec;
	if (cfg_out)
		*cfg_out = cfg;
	return true;
}

static int a52_p427_try_handoff_cb(struct arm_smmu_device *smmu,
				   struct device *dev, unsigned int start)
{
	struct iommu_fwspec *fwspec = NULL;
	struct arm_smmu_master_cfg *cfg = NULL;
	struct arm_smmu_s2cr *s2cr;
	int idx;
	u8 cb;

	if (!a52_p427_sid800_dev(dev, &fwspec, &cfg))
		return -ENOENT;

	idx = cfg->smendx[0];
	memset(&a52_p427_handoff, 0, sizeof(a52_p427_handoff));
	a52_p427_handoff.smmu = smmu;
	a52_p427_handoff.seen = 1;
	a52_p427_handoff.sid = 0x800;
	a52_p427_handoff.sme = idx;

	if (idx == INVALID_SMENDX || idx < 0 ||
	    idx >= smmu->num_mapping_groups)
		return -ENOENT;

	s2cr = &smmu->s2crs[idx];
	cb = s2cr->cbndx;
	a52_p427_handoff.old_cb = cb;
	a52_p427_handoff.chosen_cb = cb;
	a52_p427_handoff.pinned = s2cr->pinned ? 1U : 0U;
	a52_p427_handoff.old_type = (u32)s2cr->type;
	a52_p427_handoff.old_count = (u32)s2cr->count;
	a52_p427_handoff.context_reserved =
		test_bit(cb, smmu->context_map) ? 1U : 0U;

	/* Fail closed to the original allocator unless every measured Phase426
	 * handoff contract is present.  This prevents Phase427 from commandeering
	 * an arbitrary CB on a different boot/DT/SMMU configuration.
	 */
	/* TouchGrass handoff deliberately overrides the normal stage allocator
	 * start index. The firmware-selected CB is the contract being tested.
	 */
	(void)start;
	if (!s2cr->pinned || s2cr->type != S2CR_TYPE_TRANS ||
	    cb != 2U || cb >= smmu->num_context_banks ||
	    !test_bit(cb, smmu->context_map))
		return -ENOENT;

	a52_p427_handoff.used = 1;
	return cb;
}

static bool a52_p427_reused_cb2(struct device *dev, u32 cb)
{
	return cb == 2U && a52_p427_handoff.used &&
	       a52_p427_sid800_dev(dev, NULL, NULL);
}

void a52_p427_smmu_dump(void)
{
	struct a52_p427_handoff_state *h = &a52_p427_handoff;
	struct arm_smmu_device *smmu = READ_ONCE(h->smmu);
	u32 smr = ~0U, s2cr = ~0U, sctlr = ~0U, fsr = ~0U;
	u32 tcr = ~0U, mair0 = ~0U;
	u64 ttbr0 = ~0ULL;
	int ret = -ENODEV;

	if (smmu && h->sme >= 0 && h->sme < smmu->num_mapping_groups &&
	    h->chosen_cb < smmu->num_context_banks) {
		ret = arm_smmu_rpm_get(smmu);
		if (ret >= 0) {
			smr = smmu->smrs ? arm_smmu_gr0_read(smmu,
				ARM_SMMU_GR0_SMR(h->sme)) : 0;
			s2cr = arm_smmu_gr0_read(smmu,
				ARM_SMMU_GR0_S2CR(h->sme));
			sctlr = arm_smmu_cb_read(smmu, h->chosen_cb,
				ARM_SMMU_CB_SCTLR);
			fsr = arm_smmu_cb_read(smmu, h->chosen_cb,
				ARM_SMMU_CB_FSR);
			tcr = arm_smmu_cb_read(smmu, h->chosen_cb,
				ARM_SMMU_CB_TCR);
			ttbr0 = arm_smmu_cb_readq(smmu, h->chosen_cb,
				ARM_SMMU_CB_TTBR0);
			mair0 = arm_smmu_cb_read(smmu, h->chosen_cb,
				ARM_SMMU_CB_S1_MAIR0);
			arm_smmu_rpm_put(smmu);
		}
	}

	a52_ackfr_record("P427 H v=%u sid=%x e=%d old=%u cb=%u p=%u c=%u r=%u",
		h->seen, h->sid, h->sme, h->old_cb, h->chosen_cb,
		h->pinned, h->old_count, h->context_reserved);
	a52_ackfr_record("P427 C u=%u ir=%u irq=%d rr=%d rt=%d",
		h->used, h->irptndx, h->irq, h->request_ret, ret);
	a52_ackfr_record("P427 S smr=%x s2=%x cb=%u ty=%u",
		smr, s2cr, FIELD_GET(ARM_SMMU_S2CR_CBNDX, s2cr),
		FIELD_GET(ARM_SMMU_S2CR_TYPE, s2cr));
	a52_ackfr_record("P427 X sc=%x fs=%x tc=%x ma=%x",
		sctlr, fsr, tcr, mair0);
	a52_ackfr_record("P427 R tt=%llx",
		(unsigned long long)ttbr0);
}
EXPORT_SYMBOL_GPL(a52_p427_smmu_dump);
'''


def patch_arm(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE426_SMMU_STREAM_HANDOFF_PROBE_V1",
        "static int arm_smmu_alloc_context_bank(",
        "a52_p422_irq_cache.cbndx = cfg->cbndx;",
        "arm_smmu_write_context_bank(smmu, cfg->cbndx);",
        "arm_smmu_tlb_inv_context_s1",
    ):
        if token not in text:
            raise SystemExit("Phase427 ARM prerequisite missing: " + token)

    anchor = "static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,\n"
    text = one(text, anchor, ARM_BLOCK + "\n" + anchor, "ARM helper insertion")

    old = '''static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,
				       struct arm_smmu_device *smmu,
				       struct device *dev, unsigned int start)
{
	if (smmu->impl && smmu->impl->alloc_context_bank)
		return smmu->impl->alloc_context_bank(smmu_domain, smmu, dev, start);

	return __arm_smmu_alloc_bitmap(smmu->context_map, start, smmu->num_context_banks);
}
'''
    new = '''static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,
				       struct arm_smmu_device *smmu,
				       struct device *dev, unsigned int start)
{
	int a52_p427_cb;

	a52_p427_cb = a52_p427_try_handoff_cb(smmu, dev, start);
	if (a52_p427_cb >= 0)
		return a52_p427_cb;
	if (a52_p427_cb != -ENOENT)
		return a52_p427_cb;

	if (smmu->impl && smmu->impl->alloc_context_bank)
		return smmu->impl->alloc_context_bank(smmu_domain, smmu, dev, start);

	return __arm_smmu_alloc_bitmap(smmu->context_map, start, smmu->num_context_banks);
}
'''
    text = one(text, old, new, "CB allocator treatment")

    old = '''		arm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);
		arm_smmu_write_context_bank(smmu, cfg->cbndx);

		irq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];
'''
    new = '''		arm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);
		arm_smmu_write_context_bank(smmu, cfg->cbndx);
		/* CB2 is still under earlymap with SCTLR.M clear here.  Drop any
		 * stale firmware translations only after the complete new context
		 * has been written, before HLOS enables stage-1 translation.
		 */
		if (a52_p427_reused_cb2(dev, cfg->cbndx) &&
		    smmu_domain->stage == ARM_SMMU_DOMAIN_S1)
			arm_smmu_tlb_inv_context_s1(smmu_domain);

