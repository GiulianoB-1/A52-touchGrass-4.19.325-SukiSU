#!/usr/bin/env python3
"""Phase 253: restore the downstream KGSL ARM-SMMU domain contract.

Phase252 hardware proves the GPU SMMU provider, GMU context banks, legacy
MSM-bus/RPMh clients and GMU probe all succeed.  The next permanent failure is
kgsl_device_platform_probe() -> -ENODEV.  The pinned TouchGrass KGSL IOMMU
backend is active in the Phase252 Image and creates the default pagetable in
per-process mode.  Its first fatal domain operation is DOMAIN_ATTR_PROCID.
The ACK arm-smmu port has the enum values but no Qualcomm semantics and returns
-ENODEV for that unmanaged-domain attribute.

This overlay ports only the KGSL-required contract onto the already adapted
ACK 5.10 arm-smmu driver:
  * PROCID set/get and CONTEXTIDR readback
  * CONTEXT_BANK readback and dynamic-domain selection
  * TTBR0 readback from the real io-pgtable configuration
  * DYNAMIC per-process domains sharing the already programmed KGSL context
    bank without rewriting its stream mappings
  * unique 8-bit ASIDs for dynamic KGSL pagetables, matching TouchGrass

It does not fabricate IOMMU groups, force attach success, change stream IDs,
replace the ACK arm-smmu implementation, or alter the already working display
and generic SMMU domains.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CFILE = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
HFILE = Path("drivers/iommu/arm/arm-smmu/arm-smmu.h")
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MARKER = "A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old[:100]!r}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str, label: str) -> tuple[int, int]:
    pat = re.compile(r"(?m)^[^\n]*\b" + re.escape(name) + r"\s*\(")
    matches = list(pat.finditer(text))
    for m in matches:
        start = m.start()
        brace = text.find("{", m.end())
        semi = text.find(";", m.end())
        if brace < 0 or (semi >= 0 and semi < brace):
            continue
        depth = 0
        i = brace
        state = "code"
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ""
            if state == "code":
                if c == '"':
                    state = "str"
                elif c == "'":
                    state = "char"
                elif c == "/" and n == "/":
                    state = "line"; i += 1
                elif c == "/" and n == "*":
                    state = "block"; i += 1
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return start, i + 1
            elif state == "str":
                if c == "\\":
                    i += 1
                elif c == '"':
                    state = "code"
            elif state == "char":
                if c == "\\":
                    i += 1
                elif c == "'":
                    state = "code"
            elif state == "line":
                if c == "\n":
                    state = "code"
            elif state == "block":
                if c == "*" and n == "/":
                    state = "code"; i += 1
            i += 1
    raise RuntimeError(f"{label}: function {name} not found")


def one_in_function(text: str, name: str, old: str, new: str, label: str) -> str:
    start, end = function_bounds(text, name, label)
    body = text[start:end]
    if new in body:
        return text
    count = body.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one scoped anchor, found {count}: {old[:100]!r}")
    body = body.replace(old, new, 1)
    return text[:start] + body + text[end:]


def replace_function(text: str, name: str, replacement: str, label: str) -> str:
    start, end = function_bounds(text, name, label)
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


def patch_header(text: str, label: str) -> str:
    if MARKER in text:
        validate_header(text, label)
        return text

    text = one(text, "#include <linux/iommu.h>\n",
               "#include <linux/iommu.h>\n#include <linux/idr.h>\n",
               f"{label}: idr include")

    dev_anchor = "\tstruct mutex\t\t\tstream_map_mutex;\n"
    dev_new = dev_anchor + (
        "\n\t/* " + MARKER + ": dynamic KGSL pagetable ASIDs. */\n"
        "\tstruct mutex\t\t\tkgsl_asid_lock;\n"
        "\tstruct idr\t\t\tkgsl_asid_idr;\n"
    )
    text = one(text, dev_anchor, dev_new, f"{label}: device ASID state")

    cfg_anchor = "\tenum arm_smmu_cbar_type\t\tcbar;\n\tenum arm_smmu_context_fmt\tfmt;\n"
    cfg_new = (
        "\tenum arm_smmu_cbar_type\t\tcbar;\n"
        "\tu32\t\t\t\tprocid; /* " + MARKER + " */\n"
        "\tenum arm_smmu_context_fmt\tfmt;\n"
    )
    text = one(text, cfg_anchor, cfg_new, f"{label}: procid")

    domain_anchor = "\tconst struct iommu_flush_ops\t*flush_ops;\n\tstruct arm_smmu_cfg\t\tcfg;\n"
    domain_new = (
        "\tconst struct iommu_flush_ops\t*flush_ops;\n"
        "\tstruct io_pgtable_cfg\t\tpgtbl_cfg; /* " + MARKER + " */\n"
        "\tstruct arm_smmu_cfg\t\tcfg;\n"
    )
    text = one(text, domain_anchor, domain_new, f"{label}: stored pgtable cfg")

    marker_anchor = "#define ARM_SMMU_INVALID_IRPTNDX\t0xff\n"
    marker_new = marker_anchor + (
        "#define A52_KGSL_INVALID_CBNDX\t0xff /* " + MARKER + " */\n"
        "#define A52_KGSL_INVALID_ASID\t0xffff\n"
        "#define A52_KGSL_MAX_ASID\t\t0xff\n"
    )
    text = one(text, marker_anchor, marker_new, f"{label}: invalid ids")
    validate_header(text, label)
    return text


def validate_header(text: str, label: str) -> None:
    for token in (
        MARKER, "#include <linux/idr.h>", "kgsl_asid_lock", "kgsl_asid_idr",
        "procid;", "struct io_pgtable_cfg\t\tpgtbl_cfg",
        "A52_KGSL_INVALID_CBNDX", "A52_KGSL_INVALID_ASID", "A52_KGSL_MAX_ASID",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


HELPERS = r'''/* A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1
 * Qualcomm KGSL creates one real/global domain and then dynamic per-process
 * page-table domains which share that context bank.  The dynamic domains own
 * page tables and ASIDs only; they must never rewrite the stream-to-CB map.
 */
static bool a52_kgsl_dynamic_domain(struct iommu_domain *domain)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);

	return !!(smmu_domain->attributes & BIT(DOMAIN_ATTR_DYNAMIC));
}

static bool a52_kgsl_procid_domain(struct arm_smmu_domain *smmu_domain)
{
	return !!(smmu_domain->attributes & BIT(DOMAIN_ATTR_PROCID));
}

static int a52_kgsl_init_asid(struct iommu_domain *domain,
			      struct arm_smmu_device *smmu)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;
	int ret;

	if (smmu_domain->stage == ARM_SMMU_DOMAIN_S2) {
		cfg->vmid = cfg->cbndx + 1;
		return 0;
	}

	if (!a52_kgsl_dynamic_domain(domain)) {
		/* Preserve ACK behavior for unrelated domains. KGSL's downstream
		 * contract reserves ASID zero and uses cbndx+1 for real KGSL CBs.
		 */
		cfg->asid = a52_kgsl_procid_domain(smmu_domain) ?
			cfg->cbndx + 1 : cfg->cbndx;
		return 0;
	}

	mutex_lock(&smmu->kgsl_asid_lock);
	ret = idr_alloc_cyclic(&smmu->kgsl_asid_idr, domain,
			       smmu->num_context_banks + 2,
			       A52_KGSL_MAX_ASID + 1, GFP_KERNEL);
	mutex_unlock(&smmu->kgsl_asid_lock);
	if (ret < 0)
		return ret;

	cfg->asid = ret;
	return 0;
}

static void a52_kgsl_free_asid(struct iommu_domain *domain)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_device *smmu = smmu_domain->smmu;
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;

	if (!smmu || !a52_kgsl_dynamic_domain(domain) ||
	    cfg->asid == A52_KGSL_INVALID_ASID)
		return;

	mutex_lock(&smmu->kgsl_asid_lock);
	idr_remove(&smmu->kgsl_asid_idr, cfg->asid);
	mutex_unlock(&smmu->kgsl_asid_lock);
	cfg->asid = A52_KGSL_INVALID_ASID;
}

'''

INIT_DOMAIN = r'''static int arm_smmu_init_domain_context(struct iommu_domain *domain,
					struct arm_smmu_device *smmu,
					struct device *dev)
{
	int irq, start, ret = 0;
	unsigned long ias, oas;
	struct io_pgtable_ops *pgtbl_ops;
	struct io_pgtable_cfg pgtbl_cfg;
	enum io_pgtable_fmt fmt;
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;
	irqreturn_t (*context_fault)(int irq, void *dev);
	bool dynamic = a52_kgsl_dynamic_domain(domain);

	mutex_lock(&smmu_domain->init_mutex);
	if (smmu_domain->smmu)
		goto out_unlock;

	if (domain->type == IOMMU_DOMAIN_IDENTITY) {
		smmu_domain->stage = ARM_SMMU_DOMAIN_BYPASS;
		smmu_domain->smmu = smmu;
		goto out_unlock;
	}

	if (!(smmu->features & ARM_SMMU_FEAT_TRANS_S1))
		smmu_domain->stage = ARM_SMMU_DOMAIN_S2;
	if (!(smmu->features & ARM_SMMU_FEAT_TRANS_S2))
		smmu_domain->stage = ARM_SMMU_DOMAIN_S1;

	if (smmu->features & ARM_SMMU_FEAT_FMT_AARCH32_L)
		cfg->fmt = ARM_SMMU_CTX_FMT_AARCH32_L;
	if (IS_ENABLED(CONFIG_IOMMU_IO_PGTABLE_ARMV7S) &&
	    !IS_ENABLED(CONFIG_64BIT) && !IS_ENABLED(CONFIG_ARM_LPAE) &&
	    (smmu->features & ARM_SMMU_FEAT_FMT_AARCH32_S) &&
	    (smmu_domain->stage == ARM_SMMU_DOMAIN_S1))
		cfg->fmt = ARM_SMMU_CTX_FMT_AARCH32_S;
	if ((IS_ENABLED(CONFIG_64BIT) || cfg->fmt == ARM_SMMU_CTX_FMT_NONE) &&
	    (smmu->features & (ARM_SMMU_FEAT_FMT_AARCH64_64K |
			       ARM_SMMU_FEAT_FMT_AARCH64_16K |
			       ARM_SMMU_FEAT_FMT_AARCH64_4K)))
		cfg->fmt = ARM_SMMU_CTX_FMT_AARCH64;

	if (cfg->fmt == ARM_SMMU_CTX_FMT_NONE) {
		ret = -EINVAL;
		goto out_unlock;
	}

	switch (smmu_domain->stage) {
	case ARM_SMMU_DOMAIN_S1:
		cfg->cbar = CBAR_TYPE_S1_TRANS_S2_BYPASS;
		start = smmu->num_s2_context_banks;
		ias = smmu->va_size;
		oas = smmu->ipa_size;
		if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH64) {
			fmt = ARM_64_LPAE_S1;
			if (smmu->use_3lvl_tables)
				ias = min(ias, 39UL);
		} else if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_L) {
			fmt = ARM_32_LPAE_S1;
			ias = min(ias, 32UL);
			oas = min(oas, 40UL);
		} else {
			fmt = ARM_V7S;
			ias = min(ias, 32UL);
			oas = min(oas, 32UL);
		}
		smmu_domain->flush_ops = &arm_smmu_s1_tlb_ops;
		break;
	case ARM_SMMU_DOMAIN_NESTED:
	case ARM_SMMU_DOMAIN_S2:
		cfg->cbar = CBAR_TYPE_S2_TRANS;
		start = 0;
		ias = smmu->ipa_size;
		oas = smmu->pa_size;
		if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH64) {
			fmt = ARM_64_LPAE_S2;
		} else {
			fmt = ARM_32_LPAE_S2;
			ias = min(ias, 40UL);
			oas = min(oas, 40UL);
		}
		if (smmu->version == ARM_SMMU_V2)
			smmu_domain->flush_ops = &arm_smmu_s2_tlb_ops_v2;
		else
			smmu_domain->flush_ops = &arm_smmu_s2_tlb_ops_v1;
		break;
	default:
		ret = -EINVAL;
		goto out_unlock;
	}

	if (dynamic) {
		if (cfg->cbndx == A52_KGSL_INVALID_CBNDX ||
		    cfg->cbndx >= smmu->num_context_banks) {
			ret = -EINVAL;
			goto out_unlock;
		}
		ret = cfg->cbndx;
	} else {
		ret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);
		if (ret < 0)
			goto out_unlock;
	}

	smmu_domain->smmu = smmu;
	cfg->cbndx = ret;

	ret = a52_kgsl_init_asid(domain, smmu);
	if (ret)
		goto out_clear_smmu;

	if (dynamic) {
		cfg->irptndx = ARM_SMMU_INVALID_IRPTNDX;
	} else if (smmu->version < ARM_SMMU_V2) {
		cfg->irptndx = atomic_inc_return(&smmu->irptndx);
		cfg->irptndx %= smmu->num_context_irqs;
	} else {
		cfg->irptndx = cfg->cbndx;
	}

	pgtbl_cfg = (struct io_pgtable_cfg) {
		.pgsize_bitmap	= smmu->pgsize_bitmap,
		.ias		= ias,
		.oas		= oas,
		.coherent_walk	= smmu->features & ARM_SMMU_FEAT_COHERENT_WALK,
		.tlb		= smmu_domain->flush_ops,
		.iommu_dev	= smmu->dev,
	};
	if (arm_smmu_has_secure_vmid(smmu_domain)) {
		pgtbl_cfg.alloc_pages_exact = arm_smmu_alloc_pages_exact;
		pgtbl_cfg.free_pages_exact = arm_smmu_free_pages_exact;
	}

	if (smmu->impl && smmu->impl->init_context) {
		ret = smmu->impl->init_context(smmu_domain, &pgtbl_cfg, dev);
		if (ret)
			goto out_free_asid;
	}

	if (smmu_domain->non_strict)
		pgtbl_cfg.quirks |= IO_PGTABLE_QUIRK_NON_STRICT;

	pgtbl_ops = alloc_io_pgtable_ops(fmt, &pgtbl_cfg, smmu_domain);
	if (!pgtbl_ops) {
		ret = -ENOMEM;
		arm_smmu_secure_domain_lock(smmu_domain);
		arm_smmu_secure_pool_destroy(smmu_domain);
		arm_smmu_unassign_table(smmu_domain);
		arm_smmu_secure_domain_unlock(smmu_domain);
		goto out_free_asid;
	}

	arm_smmu_secure_domain_lock(smmu_domain);
	ret = arm_smmu_assign_table(smmu_domain);
	if (ret) {
		free_io_pgtable_ops(pgtbl_ops);
		arm_smmu_secure_pool_destroy(smmu_domain);
		arm_smmu_unassign_table(smmu_domain);
	}
	arm_smmu_secure_domain_unlock(smmu_domain);
	if (ret)
		goto out_free_asid;

	/* alloc_io_pgtable_ops() fills TTBR/TCR/MAIR in this structure. */
	smmu_domain->pgtbl_cfg = pgtbl_cfg;

	domain->pgsize_bitmap = pgtbl_cfg.pgsize_bitmap;
	if (pgtbl_cfg.quirks & IO_PGTABLE_QUIRK_ARM_TTBR1) {
		domain->geometry.aperture_start = ~0UL << ias;
		domain->geometry.aperture_end = ~0UL;
	} else {
		domain->geometry.aperture_end = (1UL << ias) - 1;
	}
	domain->geometry.force_aperture = true;

	if (!dynamic) {
		arm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);
		arm_smmu_write_context_bank(smmu, cfg->cbndx);

		irq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];
		if (smmu->impl && smmu->impl->context_fault)
			context_fault = smmu->impl->context_fault;
		else
			context_fault = arm_smmu_context_fault;

		ret = devm_request_irq(smmu->dev, irq, context_fault,
			       IRQF_SHARED, "arm-smmu-context-fault", domain);
		if (ret < 0) {
			dev_err(smmu->dev, "failed to request context IRQ %d (%u)\n",
				cfg->irptndx, irq);
			cfg->irptndx = ARM_SMMU_INVALID_IRPTNDX;
		}
	}

	mutex_unlock(&smmu_domain->init_mutex);
	smmu_domain->pgtbl_ops = pgtbl_ops;

	if (a52_apps_smmu(smmu->dev) || a52_kgsl_procid_domain(smmu_domain))
		a52_ackfr_record("K253 D init dyn=%d cb=%u asid=%u proc=%u",
			dynamic, cfg->cbndx, cfg->asid, cfg->procid);
	return 0;

out_free_asid:
	if (dynamic)
		a52_kgsl_free_asid(domain);
out_clear_smmu:
	if (!dynamic)
		__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
	smmu_domain->smmu = NULL;
out_unlock:
	mutex_unlock(&smmu_domain->init_mutex);
	return ret;
}'''

DESTROY_DOMAIN = r'''static void arm_smmu_destroy_domain_context(struct iommu_domain *domain)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_device *smmu = smmu_domain->smmu;
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;
	bool dynamic = a52_kgsl_dynamic_domain(domain);
	int ret, irq;

	if (!smmu || domain->type == IOMMU_DOMAIN_IDENTITY)
		return;

	ret = arm_smmu_rpm_get(smmu);
	if (ret < 0)
		return;

	if (dynamic) {
		a52_kgsl_free_asid(domain);
		arm_smmu_secure_domain_lock(smmu_domain);
		free_io_pgtable_ops(smmu_domain->pgtbl_ops);
		arm_smmu_secure_pool_destroy(smmu_domain);
		arm_smmu_unassign_table(smmu_domain);
		arm_smmu_secure_domain_unlock(smmu_domain);
		smmu_domain->pgtbl_ops = NULL;
		smmu_domain->smmu = NULL;
		arm_smmu_rpm_put(smmu);
		return;
	}

	smmu->cbs[cfg->cbndx].cfg = NULL;
	arm_smmu_write_context_bank(smmu, cfg->cbndx);

	if (cfg->irptndx != ARM_SMMU_INVALID_IRPTNDX) {
		irq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];
		devm_free_irq(smmu->dev, irq, domain);
	}

	arm_smmu_secure_domain_lock(smmu_domain);
	free_io_pgtable_ops(smmu_domain->pgtbl_ops);
	arm_smmu_secure_pool_destroy(smmu_domain);
	arm_smmu_unassign_table(smmu_domain);
	arm_smmu_secure_domain_unlock(smmu_domain);
	__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);

	arm_smmu_rpm_put(smmu);
}'''

GET_ATTR = r'''static int arm_smmu_domain_get_attr(struct iommu_domain *domain,
				    enum iommu_attr attr, void *data)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;

	switch (attr) {
	case DOMAIN_ATTR_EARLY_MAP:
	case DOMAIN_ATTR_NON_FATAL_FAULTS:
		*(int *)data = !!(smmu_domain->attributes & BIT(attr));
		return 0;
	case DOMAIN_ATTR_SECURE_VMID:
		*(int *)data = smmu_domain->secure_vmid;
		return 0;
	case DOMAIN_ATTR_PROCID:
		*(u32 *)data = cfg->procid;
		return 0;
	case DOMAIN_ATTR_DYNAMIC:
		*(int *)data = a52_kgsl_dynamic_domain(domain);
		return 0;
	case DOMAIN_ATTR_CONTEXT_BANK:
		if (!smmu_domain->smmu)
			return -ENODEV;
		*(unsigned int *)data = cfg->cbndx;
		return 0;
	case DOMAIN_ATTR_TTBR0: {
		u64 val;

		if (!smmu_domain->smmu || !smmu_domain->pgtbl_ops)
			return -ENODEV;
		if (smmu_domain->stage != ARM_SMMU_DOMAIN_S1)
			return -EINVAL;

		val = smmu_domain->pgtbl_cfg.arm_lpae_s1_cfg.ttbr;
		val |= FIELD_PREP(ARM_SMMU_TTBRn_ASID, cfg->asid);
		*(u64 *)data = val;
		return 0;
	}
	case DOMAIN_ATTR_CONTEXTIDR:
		if (!smmu_domain->smmu)
			return -ENODEV;
		*(u32 *)data = cfg->procid;
		return 0;
	default:
		break;
	}

	switch (domain->type) {
	case IOMMU_DOMAIN_UNMANAGED:
		switch (attr) {
		case DOMAIN_ATTR_NESTING:
			*(int *)data = (smmu_domain->stage == ARM_SMMU_DOMAIN_NESTED);
			return 0;
		default:
			return -ENODEV;
		}
	case IOMMU_DOMAIN_DMA:
		switch (attr) {
		case DOMAIN_ATTR_DMA_USE_FLUSH_QUEUE:
			*(int *)data = smmu_domain->non_strict;
			return 0;
		default:
			return -ENODEV;
		}
	default:
		return -EINVAL;
	}
}'''

SET_ATTR = r'''static int arm_smmu_domain_set_attr(struct iommu_domain *domain,
				    enum iommu_attr attr, void *data)
{
	int ret = 0;
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;

	mutex_lock(&smmu_domain->init_mutex);

	switch (attr) {
	case DOMAIN_ATTR_EARLY_MAP:
		if (*(int *)data) {
			smmu_domain->attributes |= BIT(DOMAIN_ATTR_EARLY_MAP);
		} else {
			ret = arm_smmu_enable_s1_translations(smmu_domain);
			if (!ret)
				smmu_domain->attributes &= ~BIT(DOMAIN_ATTR_EARLY_MAP);
		}
		goto out_unlock;
	case DOMAIN_ATTR_NON_FATAL_FAULTS:
		if (*(int *)data)
			smmu_domain->attributes |= BIT(DOMAIN_ATTR_NON_FATAL_FAULTS);
		else
			smmu_domain->attributes &= ~BIT(DOMAIN_ATTR_NON_FATAL_FAULTS);
		goto out_unlock;
	case DOMAIN_ATTR_SECURE_VMID:
		if (smmu_domain->smmu)
			ret = -EBUSY;
		else if (smmu_domain->secure_vmid != VMID_INVAL)
			ret = -EEXIST;
		else
			smmu_domain->secure_vmid = *(int *)data;
		goto out_unlock;
	case DOMAIN_ATTR_PROCID:
		if (smmu_domain->smmu) {
			ret = -EBUSY;
		} else {
			cfg->procid = *(u32 *)data;
			smmu_domain->attributes |= BIT(DOMAIN_ATTR_PROCID);
		}
		goto out_unlock;
	case DOMAIN_ATTR_DYNAMIC:
		if (smmu_domain->smmu) {
			ret = -EBUSY;
		} else if (*(int *)data) {
			smmu_domain->attributes |= BIT(DOMAIN_ATTR_DYNAMIC);
		} else {
			smmu_domain->attributes &= ~BIT(DOMAIN_ATTR_DYNAMIC);
		}
		goto out_unlock;
	case DOMAIN_ATTR_CONTEXT_BANK:
		if (smmu_domain->smmu) {
			ret = -EBUSY;
		} else if (!a52_kgsl_dynamic_domain(domain)) {
			ret = -EINVAL;
		} else {
			cfg->cbndx = *(unsigned int *)data;
		}
		goto out_unlock;
	default:
		break;
	}

	switch (domain->type) {
	case IOMMU_DOMAIN_UNMANAGED:
		switch (attr) {
		case DOMAIN_ATTR_NESTING:
			if (smmu_domain->smmu) {
				ret = -EPERM;
				goto out_unlock;
			}
			if (*(int *)data)
				smmu_domain->stage = ARM_SMMU_DOMAIN_NESTED;
			else
				smmu_domain->stage = ARM_SMMU_DOMAIN_S1;
			break;
		default:
			ret = -ENODEV;
		}
		break;
	case IOMMU_DOMAIN_DMA:
		switch (attr) {
		case DOMAIN_ATTR_DMA_USE_FLUSH_QUEUE:
			smmu_domain->non_strict = *(int *)data;
			break;
		default:
			ret = -ENODEV;
		}
		break;
	default:
		ret = -EINVAL;
	}

out_unlock:
	mutex_unlock(&smmu_domain->init_mutex);
	return ret;
}'''


def patch_c(text: str, label: str) -> str:
    if MARKER in text:
        validate_c(text, label)
        return text

    anchor = "static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"{label}: context-bank helper anchor mismatch")
    text = text.replace(anchor, HELPERS + anchor, 1)

    text = replace_function(text, "arm_smmu_init_domain_context", INIT_DOMAIN,
                            f"{label}: init domain")
    text = replace_function(text, "arm_smmu_destroy_domain_context", DESTROY_DOMAIN,
                            f"{label}: destroy domain")
    text = replace_function(text, "arm_smmu_domain_get_attr", GET_ATTR,
                            f"{label}: get attr")
    text = replace_function(text, "arm_smmu_domain_set_attr", SET_ATTR,
                            f"{label}: set attr")

    alloc_anchor = "\tsmmu_domain->secure_vmid = VMID_INVAL;\n"
    alloc_new = alloc_anchor + (
        "\tsmmu_domain->cfg.cbndx = A52_KGSL_INVALID_CBNDX;\n"
        "\tsmmu_domain->cfg.irptndx = ARM_SMMU_INVALID_IRPTNDX;\n"
        "\tsmmu_domain->cfg.asid = A52_KGSL_INVALID_ASID;\n"
    )
    text = one_in_function(text, "arm_smmu_domain_alloc", alloc_anchor, alloc_new,
                           f"{label}: domain invalid ids")

    attach_anchor = "\t/*\n\t * Sanity check the domain. We don't support domains across\n"
    attach_new = (
        "\tif (a52_kgsl_dynamic_domain(domain)) {\n"
        "\t\tret = 0;\n"
        "\t\tgoto rpm_put;\n"
        "\t}\n\n" + attach_anchor
    )
    text = one_in_function(text, "arm_smmu_attach_dev", attach_anchor, attach_new,
                           f"{label}: dynamic attach")

    probe_anchor = "\tsmmu->dev = dev;\n"
    probe_new = probe_anchor + (
        "\tidr_init(&smmu->kgsl_asid_idr); /* " + MARKER + " */\n"
        "\tmutex_init(&smmu->kgsl_asid_lock);\n"
    )
    text = one_in_function(text, "arm_smmu_device_probe", probe_anchor, probe_new,
                           f"{label}: provider idr init")

    remove_anchor = "\tif (!bitmap_empty(smmu->context_map, ARM_SMMU_MAX_CBS))\n"
    remove_new = (
        "\tidr_destroy(&smmu->kgsl_asid_idr); /* " + MARKER + " */\n\n" + remove_anchor
    )
    text = one_in_function(text, "arm_smmu_device_remove", remove_anchor, remove_new,
                           f"{label}: provider idr destroy")

    validate_c(text, label)
    return text


def validate_c(text: str, label: str) -> None:
    required = (
        MARKER,
        "a52_kgsl_dynamic_domain", "a52_kgsl_init_asid", "a52_kgsl_free_asid",
        "DOMAIN_ATTR_PROCID", "DOMAIN_ATTR_DYNAMIC", "DOMAIN_ATTR_CONTEXT_BANK",
        "DOMAIN_ATTR_TTBR0", "DOMAIN_ATTR_CONTEXTIDR",
        "FIELD_PREP(ARM_SMMU_TTBRn_ASID, cfg->asid)",
        "idr_alloc_cyclic(&smmu->kgsl_asid_idr",
        "if (a52_kgsl_dynamic_domain(domain))",
        "idr_init(&smmu->kgsl_asid_idr)", "idr_destroy(&smmu->kgsl_asid_idr)",
        'K253 D init dyn=%d cb=%u asid=%u proc=%u',
        "a52_arm_smmu_apply_dt_domain_attrs(smmu_domain, dev);",
        "arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);",
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_recorder(text: str, label: str) -> str:
    if 'strncmp(fmt, "K253", 4)' in text and '!strncmp(message, "K253 ", 5)' in text:
        return text
    old_filter = 'if (strncmp(fmt, "K251", 4) &&\n'
    new_filter = 'if (strncmp(fmt, "K253", 4) &&\n    strncmp(fmt, "K251", 4) &&\n'
    text = one(text, old_filter, new_filter, f"{label}: K253 format filter")
    old_crit = 'return !strncmp(message, "K251 ", 5) ||\n'
    new_crit = ('return !strncmp(message, "K253 ", 5) ||\n'
                '       !strncmp(message, "K251 ", 5) ||\n')
    text = one(text, old_crit, new_crit, f"{label}: K253 critical filter")
    return text


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key); out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        c, h, r = root / CFILE, root / HFILE, root / RECORDER
        if not (c.is_file() and h.is_file() and r.is_file()):
            continue
        ct = c.read_text(encoding="utf-8")
        ht = h.read_text(encoding="utf-8")
        if "a52_arm_smmu_apply_dt_domain_attrs" not in ct:
            continue
        if "skip_init" not in ht or "use_3lvl_tables" not in ht:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key); hits.append(root)
    if len(hits) != 1:
        raise RuntimeError("expected one generated Phase252 root, found " +
                           (", ".join(map(str, hits)) or "none"))
    return hits[0]


def self_test() -> None:
    h = '''#include <linux/iommu.h>\nstruct arm_smmu_device {\n\tstruct mutex\t\t\tstream_map_mutex;\n};\nstruct arm_smmu_cfg {\n\tu8 cbndx;\n\tu8 irptndx;\n\tunion { u16 asid; u16 vmid; };\n\tenum arm_smmu_cbar_type\t\tcbar;\n\tenum arm_smmu_context_fmt\tfmt;\n};\n#define ARM_SMMU_INVALID_IRPTNDX\t0xff\nstruct arm_smmu_domain {\n\tstruct arm_smmu_device *smmu;\n\tstruct io_pgtable_ops *pgtbl_ops;\n\tconst struct iommu_flush_ops\t*flush_ops;\n\tstruct arm_smmu_cfg\t\tcfg;\n};\n'''
    h2 = patch_header(h, "fixture/header")
    assert patch_header(h2, "fixture/header2") == h2
    print("Phase 253 KGSL/SMMU domain-contract overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    hp = root / HFILE
    cp = root / CFILE
    rp = root / RECORDER
    hp.write_text(patch_header(hp.read_text(encoding="utf-8"), str(hp)), encoding="utf-8")
    cp.write_text(patch_c(cp.read_text(encoding="utf-8"), str(cp)), encoding="utf-8")
    rp.write_text(patch_recorder(rp.read_text(encoding="utf-8"), str(rp)), encoding="utf-8")
    print("Phase 253 KGSL ARM-SMMU domain contract applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
