#!/usr/bin/env python3
"""Phase 253: restore the KGSL private ARM-SMMU dynamic-domain contract.

Phase252 hardware proves GMU bandwidth/RPMh succeeds and the next deterministic
failure is kgsl_device_platform_probe() -> -ENODEV. The first source-proven
failure is KGSL default pagetable setup calling DOMAIN_ATTR_PROCID on an
unmanaged ARM-SMMU domain. Phase206 added the downstream enum names but left
these provider semantics pending, so ACK returns -ENODEV.

This correction ports the coherent TouchGrass contract required by KGSL:
PROCID, CONTEXT_BANK, TTBR0, CONTEXTIDR and DYNAMIC. Dynamic process domains
reuse the already-programmed GPU context bank, own a distinct ASID, never
rewrite that shared context bank or its stream routing, and free only their own
ASID/page-table resources. Existing Phase208 display/secure-VMID behavior and
Phase250 regulator-before-clock behavior are preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE253_KGSL_SMMU_DYNAMIC_DOMAIN_V1"
PHASE250 = "A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1"
ARM_SMMU_C = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
ARM_SMMU_H = Path("drivers/iommu/arm/arm-smmu/arm-smmu.h")
IOMMU_H = Path("include/linux/iommu.h")


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def locate_root() -> Path:
    for root in (Path.cwd() / "workspace/gki-phase199-src", Path.cwd() / "gki/common"):
        if (root / ARM_SMMU_C).is_file() and (root / ARM_SMMU_H).is_file():
            return root
    raise RuntimeError("Phase253 generated Phase252 ACK source tree not found")


def patch_header(text: str) -> str:
    if MARKER in text:
        validate_header(text)
        return text
    if PHASE250 not in text:
        raise RuntimeError("Phase253 arm-smmu.h requires retained Phase250 power contract")

    if "#include <linux/idr.h>\n" not in text:
        text = one(text, "#include <linux/iommu.h>\n",
                   "#include <linux/iommu.h>\n#include <linux/idr.h>\n",
                   "arm-smmu idr include")

    anchor = "\tDECLARE_BITMAP(context_map, ARM_SMMU_MAX_CBS);\n\tstruct arm_smmu_cb\t\t*cbs;\n"
    repl = (
        "\tDECLARE_BITMAP(context_map, ARM_SMMU_MAX_CBS);\n"
        f"\t/* {MARKER}: ASIDs owned only by KGSL dynamic process domains. */\n"
        "\tstruct ida\t\t\ta52_dynamic_asids;\n"
        "\tstruct arm_smmu_cb\t\t*cbs;\n"
    )
    text = one(text, anchor, repl, "dynamic ASID allocator field")

    anchor = (
        "\tunion {\n"
        "\t\tu16\t\t\tasid;\n"
        "\t\tu16\t\t\tvmid;\n"
        "\t};\n"
        "\tenum arm_smmu_cbar_type\t\tcbar;\n"
    )
    repl = (
        "\tunion {\n"
        "\t\tu16\t\t\tasid;\n"
        "\t\tu16\t\t\tvmid;\n"
        "\t};\n"
        f"\t/* {MARKER}: downstream KGSL CONTEXTIDR/PROCID ABI. */\n"
        "\tu32\t\t\t\tprocid;\n"
        "\tenum arm_smmu_cbar_type\t\tcbar;\n"
    )
    text = one(text, anchor, repl, "cfg procid field")

    anchor = (
        "\tenum arm_smmu_domain_stage\tstage;\n"
        "\tunsigned long\t\t\tattributes;\n"
        "\tu32\t\t\t\tsecure_vmid;\n"
    )
    repl = (
        "\tenum arm_smmu_domain_stage\tstage;\n"
        "\tunsigned long\t\t\tattributes;\n"
        f"\t/* {MARKER}: per-domain TTBR0 must survive shared-CB dynamic use. */\n"
        "\tu64\t\t\t\ta52_ttbr0;\n"
        "\tbool\t\t\t\ta52_dynamic_asid_allocated;\n"
        "\tu32\t\t\t\tsecure_vmid;\n"
    )
    text = one(text, anchor, repl, "domain dynamic state fields")
    validate_header(text)
    return text


def validate_header(text: str) -> None:
    for token in (
        MARKER, PHASE250, "struct ida", "a52_dynamic_asids", "procid",
        "a52_ttbr0", "a52_dynamic_asid_allocated", "#include <linux/idr.h>",
    ):
        if token not in text:
            raise RuntimeError(f"Phase253 arm-smmu.h missing {token}")


HELPERS = f'''\n/* {MARKER}
 * KGSL uses one hardware context bank for the GPU stream and creates software
 * process pagetables which carry distinct ASIDs. Dynamic domains therefore
 * reuse the caller-selected context bank but must not own or reprogram it.
 */
static const char a52_phase253_kgsl_smmu_dynamic_contract[] __used =
\t"{MARKER}";

static bool a52_arm_smmu_is_dynamic(struct arm_smmu_domain *smmu_domain)
{{
\treturn !!(smmu_domain->attributes & BIT(DOMAIN_ATTR_DYNAMIC));
}}

static int a52_arm_smmu_init_asid(struct arm_smmu_domain *smmu_domain)
{{
\tstruct arm_smmu_device *smmu = smmu_domain->smmu;
\tstruct arm_smmu_cfg *cfg = &smmu_domain->cfg;
\tint asid;

\tif (smmu_domain->stage != ARM_SMMU_DOMAIN_S1)
\t\treturn 0;

\tif (!a52_arm_smmu_is_dynamic(smmu_domain)) {{
\t\tcfg->asid = cfg->cbndx;
\t\treturn 0;
\t}}

\t/* Keep dynamic ASIDs disjoint from all fixed context-bank ASIDs. */
\tasid = ida_alloc_range(&smmu->a52_dynamic_asids,
\t\t\t       smmu->num_context_banks + 2, 0xffff,
\t\t\t       GFP_KERNEL);
\tif (asid < 0)
\t\treturn asid;

\tcfg->asid = (u16)asid;
\tsmmu_domain->a52_dynamic_asid_allocated = true;
\treturn 0;
}}

static void a52_arm_smmu_free_asid(struct arm_smmu_domain *smmu_domain)
{{
\tif (!smmu_domain->smmu || !smmu_domain->a52_dynamic_asid_allocated)
\t\treturn;

\tida_free(&smmu_domain->smmu->a52_dynamic_asids,
\t\t smmu_domain->cfg.asid);
\tsmmu_domain->a52_dynamic_asid_allocated = false;
}}

static u64 a52_arm_smmu_build_ttbr0(struct arm_smmu_domain *smmu_domain,
\t\t\t\t    struct io_pgtable_cfg *pgtbl_cfg)
{{
\tstruct arm_smmu_cfg *cfg = &smmu_domain->cfg;

\tif (smmu_domain->stage == ARM_SMMU_DOMAIN_S2)
\t\treturn pgtbl_cfg->arm_lpae_s2_cfg.vttbr;

\tif (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_S)
\t\treturn pgtbl_cfg->arm_v7s_cfg.ttbr;

\treturn FIELD_PREP(ARM_SMMU_TTBRn_ASID, cfg->asid) |
\t\t((pgtbl_cfg->quirks & IO_PGTABLE_QUIRK_ARM_TTBR1) ? 0 :
\t\t pgtbl_cfg->arm_lpae_s1_cfg.ttbr);
}}
\n'''


def patch_source(text: str) -> str:
    if MARKER in text:
        validate_source(text)
        return text
    if PHASE250 not in text:
        raise RuntimeError("Phase253 arm-smmu.c requires retained Phase250 power contract")

    anchor = "static bool using_legacy_binding, using_generic_binding;\n\n"
    text = one(text, anchor, anchor + HELPERS, "Phase253 helper insertion")

    old = '''static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,
\t\t\t\t       struct arm_smmu_device *smmu,
\t\t\t\t       struct device *dev, unsigned int start)
{
\tif (smmu->impl && smmu->impl->alloc_context_bank)
\t\treturn smmu->impl->alloc_context_bank(smmu_domain, smmu, dev, start);

\treturn __arm_smmu_alloc_bitmap(smmu->context_map, start, smmu->num_context_banks);
}
'''
    new = f'''static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,
\t\t\t\t       struct arm_smmu_device *smmu,
\t\t\t\t       struct device *dev, unsigned int start)
{{
\t/* {MARKER}: dynamic KGSL domains reuse the default GPU context bank. */
\tif (a52_arm_smmu_is_dynamic(smmu_domain)) {{
\t\tunsigned int cb = smmu_domain->cfg.cbndx;

\t\tif (smmu_domain->stage != ARM_SMMU_DOMAIN_S1 ||
\t\t    cb >= smmu->num_context_banks)
\t\t\treturn -EINVAL;
\t\treturn cb;
\t}}

\tif (smmu->impl && smmu->impl->alloc_context_bank)
\t\treturn smmu->impl->alloc_context_bank(smmu_domain, smmu, dev, start);

\treturn __arm_smmu_alloc_bitmap(smmu->context_map, start, smmu->num_context_banks);
}}
'''
    text = one(text, old, new, "dynamic context-bank reuse")

    text = one(text,
        "\tirqreturn_t (*context_fault)(int irq, void *dev);\n",
        "\tirqreturn_t (*context_fault)(int irq, void *dev);\n\tbool dynamic;\n",
        "init-domain dynamic declaration")

    text = one(text,
        "\tif (domain->type == IOMMU_DOMAIN_IDENTITY) {\n"
        "\t\tsmmu_domain->stage = ARM_SMMU_DOMAIN_BYPASS;\n"
        "\t\tsmmu_domain->smmu = smmu;\n"
        "\t\tgoto out_unlock;\n"
        "\t}\n\n",
        "\tif (domain->type == IOMMU_DOMAIN_IDENTITY) {\n"
        "\t\tsmmu_domain->stage = ARM_SMMU_DOMAIN_BYPASS;\n"
        "\t\tsmmu_domain->smmu = smmu;\n"
        "\t\tgoto out_unlock;\n"
        "\t}\n\n"
        "\tdynamic = a52_arm_smmu_is_dynamic(smmu_domain);\n\n",
        "init-domain dynamic state")

    old = '''\tif (smmu_domain->stage == ARM_SMMU_DOMAIN_S2)
\t\tcfg->vmid = cfg->cbndx + 1;
\telse
\t\tcfg->asid = cfg->cbndx;
'''
    new = '''\tif (smmu_domain->stage == ARM_SMMU_DOMAIN_S2) {
\t\tcfg->vmid = cfg->cbndx + 1;
\t} else {
\t\tret = a52_arm_smmu_init_asid(smmu_domain);
\t\tif (ret)
\t\t\tgoto out_clear_smmu;
\t}
'''
    text = one(text, old, new, "dynamic ASID allocation")

    old = '''\t/* Initialise the context bank with our page table cfg */
\tarm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);
\tarm_smmu_write_context_bank(smmu, cfg->cbndx);

\t/*
\t * Request context fault interrupt. Do this last to avoid the
\t * handler seeing a half-initialised domain state.
\t */
\tirq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];

\tif (smmu->impl && smmu->impl->context_fault)
\t\tcontext_fault = smmu->impl->context_fault;
\telse
\t\tcontext_fault = arm_smmu_context_fault;

\tret = devm_request_irq(smmu->dev, irq, context_fault,
\t\t\t       IRQF_SHARED, "arm-smmu-context-fault", domain);
\tif (ret < 0) {
\t\tdev_err(smmu->dev, "failed to request context IRQ %d (%u)\\n",
\t\t\tcfg->irptndx, irq);
\t\tcfg->irptndx = ARM_SMMU_INVALID_IRPTNDX;
\t}
'''
    new = f'''\t/* {MARKER}: retain each domain's encoded TTBR0 even when the
\t * hardware context bank is shared with the default GPU domain.
\t */
\tsmmu_domain->a52_ttbr0 = a52_arm_smmu_build_ttbr0(smmu_domain,
\t\t\t\t\t\t     &pgtbl_cfg);

\tif (!dynamic) {{
\t\t/* Initialise and own the hardware context bank normally. */
\t\tarm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);
\t\tarm_smmu_write_context_bank(smmu, cfg->cbndx);
\t\tsmmu_domain->a52_ttbr0 = smmu->cbs[cfg->cbndx].ttbr[0];

\t\t/* Request the context fault IRQ only for the CB-owning domain. */
\t\tirq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];

\t\tif (smmu->impl && smmu->impl->context_fault)
\t\t\tcontext_fault = smmu->impl->context_fault;
\t\telse
\t\t\tcontext_fault = arm_smmu_context_fault;

\t\tret = devm_request_irq(smmu->dev, irq, context_fault,
\t\t\t\t       IRQF_SHARED, "arm-smmu-context-fault", domain);
\t\tif (ret < 0) {{
\t\t\tdev_err(smmu->dev, "failed to request context IRQ %d (%u)\\n",
\t\t\t\tcfg->irptndx, irq);
\t\t\tcfg->irptndx = ARM_SMMU_INVALID_IRPTNDX;
\t\t}}
\t}} else {{
\t\t/* The default GPU domain owns CB programming and its fault IRQ. */
\t\tcfg->irptndx = ARM_SMMU_INVALID_IRPTNDX;
\t}}
'''
    text = one(text, old, new, "dynamic CB programming suppression")

    old = '''out_clear_smmu:
\t__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
\tsmmu_domain->smmu = NULL;
'''
    new = '''out_clear_smmu:
\ta52_arm_smmu_free_asid(smmu_domain);
\tif (!dynamic)
\t\t__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
\tsmmu_domain->smmu = NULL;
'''
    text = one(text, old, new, "dynamic init cleanup ownership")

    old = '''\t/*
\t * Disable the context bank and free the page tables before freeing
\t * it.
\t */
\tsmmu->cbs[cfg->cbndx].cfg = NULL;
\tarm_smmu_write_context_bank(smmu, cfg->cbndx);

\tif (cfg->irptndx != ARM_SMMU_INVALID_IRPTNDX) {
\t\tirq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];
\t\tdevm_free_irq(smmu->dev, irq, domain);
\t}

\tarm_smmu_secure_domain_lock(smmu_domain);
\tfree_io_pgtable_ops(smmu_domain->pgtbl_ops);
\tarm_smmu_secure_pool_destroy(smmu_domain);
\tarm_smmu_unassign_table(smmu_domain);
\tarm_smmu_secure_domain_unlock(smmu_domain);
\t__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
'''
    new = f'''\t/* {MARKER}: a dynamic domain owns only its ASID and pagetable.
\t * The shared GPU context bank, stream routing and IRQ remain owned by the
\t * default KGSL domain.
\t */
\tif (!a52_arm_smmu_is_dynamic(smmu_domain)) {{
\t\tsmmu->cbs[cfg->cbndx].cfg = NULL;
\t\tarm_smmu_write_context_bank(smmu, cfg->cbndx);

\t\tif (cfg->irptndx != ARM_SMMU_INVALID_IRPTNDX) {{
\t\t\tirq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];
\t\t\tdevm_free_irq(smmu->dev, irq, domain);
\t\t}}
\t}}

\tarm_smmu_secure_domain_lock(smmu_domain);
\tfree_io_pgtable_ops(smmu_domain->pgtbl_ops);
\tarm_smmu_secure_pool_destroy(smmu_domain);
\tarm_smmu_unassign_table(smmu_domain);
\tarm_smmu_secure_domain_unlock(smmu_domain);
\ta52_arm_smmu_free_asid(smmu_domain);
\tif (!a52_arm_smmu_is_dynamic(smmu_domain))
\t\t__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
'''
    text = one(text, old, new, "dynamic destroy ownership")

    old = '''\t/* Looks ok, so add the device to the domain. Secure display uses the
\t * same translating stream path after its page tables are assigned to
\t * HLOS and the DT-provided secure VMID.
\t */
\tret = arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);
'''
    new = f'''\t/* {MARKER}: process pagetables share the default GPU stream routing.
\t * Attaching a dynamic domain must not rewrite the S2CR/SMR master state.
\t */
\tif (a52_arm_smmu_is_dynamic(smmu_domain)) {{
\t\tret = 0;
\t\tgoto rpm_put;
\t}}

\t/* Looks ok, so add the device to the domain. Secure display uses the
\t * same translating stream path after its page tables are assigned to
\t * HLOS and the DT-provided secure VMID.
\t */
\tret = arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);
'''
    text = one(text, old, new, "dynamic attach stream preservation")

    get_anchor = '''\tcase DOMAIN_ATTR_SECURE_VMID:
\t\t*(int *)data = smmu_domain->secure_vmid;
\t\treturn 0;
\tdefault:
\t\tbreak;
'''
    get_new = '''\tcase DOMAIN_ATTR_SECURE_VMID:
\t\t*(int *)data = smmu_domain->secure_vmid;
\t\treturn 0;
\tcase DOMAIN_ATTR_PROCID:
\t\t*(u32 *)data = smmu_domain->cfg.procid;
\t\treturn 0;
\tcase DOMAIN_ATTR_DYNAMIC:
\t\t*(int *)data = a52_arm_smmu_is_dynamic(smmu_domain);
\t\treturn 0;
\tcase DOMAIN_ATTR_CONTEXT_BANK:
\t\tif (!smmu_domain->smmu)
\t\t\treturn -ENODEV;
\t\t*(unsigned int *)data = smmu_domain->cfg.cbndx;
\t\treturn 0;
\tcase DOMAIN_ATTR_TTBR0:
\t\tif (!smmu_domain->smmu || !smmu_domain->pgtbl_ops)
\t\t\treturn -ENODEV;
\t\t*(u64 *)data = smmu_domain->a52_ttbr0;
\t\treturn 0;
\tcase DOMAIN_ATTR_CONTEXTIDR:
\t\tif (!smmu_domain->smmu)
\t\t\treturn -ENODEV;
\t\t*(u32 *)data = smmu_domain->cfg.procid;
\t\treturn 0;
\tdefault:
\t\tbreak;
'''
    text = one(text, get_anchor, get_new, "KGSL domain get attributes")

    set_anchor = '''\tcase DOMAIN_ATTR_SECURE_VMID:
\t\tif (smmu_domain->smmu) {
\t\t\tret = -EBUSY;
\t\t} else if (smmu_domain->secure_vmid != VMID_INVAL) {
\t\t\tret = -EEXIST;
\t\t} else {
\t\t\tsmmu_domain->secure_vmid = *(int *)data;
\t\t}
\t\tgoto out_unlock;
\tdefault:
\t\tbreak;
'''
    set_new = '''\tcase DOMAIN_ATTR_SECURE_VMID:
\t\tif (smmu_domain->smmu) {
\t\t\tret = -EBUSY;
\t\t} else if (smmu_domain->secure_vmid != VMID_INVAL) {
\t\t\tret = -EEXIST;
\t\t} else {
\t\t\tsmmu_domain->secure_vmid = *(int *)data;
\t\t}
\t\tgoto out_unlock;
\tcase DOMAIN_ATTR_PROCID:
\t\tif (smmu_domain->smmu)
\t\t\tret = -EBUSY;
\t\telse
\t\t\tsmmu_domain->cfg.procid = *(u32 *)data;
\t\tgoto out_unlock;
\tcase DOMAIN_ATTR_DYNAMIC:
\t\tif (smmu_domain->smmu) {
\t\t\tret = -EBUSY;
\t\t} else if (*(int *)data) {
\t\t\tsmmu_domain->attributes |= BIT(DOMAIN_ATTR_DYNAMIC);
\t\t} else {
\t\t\tsmmu_domain->attributes &= ~BIT(DOMAIN_ATTR_DYNAMIC);
\t\t}
\t\tgoto out_unlock;
\tcase DOMAIN_ATTR_CONTEXT_BANK:
\t\tif (smmu_domain->smmu)
\t\t\tret = -EBUSY;
\t\telse if (!a52_arm_smmu_is_dynamic(smmu_domain))
\t\t\tret = -EINVAL;
\t\telse
\t\t\tsmmu_domain->cfg.cbndx = *(unsigned int *)data;
\t\tgoto out_unlock;
\tdefault:
\t\tbreak;
'''
    text = one(text, set_anchor, set_new, "KGSL domain set attributes")

    text = one(text,
        "\tsmmu->dev = dev;\n\n",
        f"\tsmmu->dev = dev;\n\t/* {MARKER}: no allocations occur until a KGSL dynamic domain exists. */\n"
        "\tida_init(&smmu->a52_dynamic_asids);\n\n",
        "dynamic ASID allocator init")

    remove_anchor = '''\tclk_bulk_unprepare(smmu->num_clks, smmu->clks);
\treturn 0;
}

static void arm_smmu_device_shutdown'''
    remove_new = f'''\tclk_bulk_unprepare(smmu->num_clks, smmu->clks);
\t/* {MARKER}: all domains must be gone before provider removal. */
\tida_destroy(&smmu->a52_dynamic_asids);
\treturn 0;
}}

static void arm_smmu_device_shutdown'''
    text = one(text, remove_anchor, remove_new, "dynamic ASID allocator destroy")

    validate_source(text)
    return text


def validate_source(text: str) -> None:
    tokens = (
        MARKER, PHASE250,
        "DOMAIN_ATTR_PROCID", "DOMAIN_ATTR_DYNAMIC", "DOMAIN_ATTR_CONTEXT_BANK",
        "DOMAIN_ATTR_TTBR0", "DOMAIN_ATTR_CONTEXTIDR",
        "ida_alloc_range", "ida_free", "ida_init", "ida_destroy",
        "a52_arm_smmu_build_ttbr0", "a52_dynamic_asid_allocated",
        "if (a52_arm_smmu_is_dynamic(smmu_domain))",
        "if (!dynamic)", "goto rpm_put;",
    )
    for token in tokens:
        if token not in text:
            raise RuntimeError(f"Phase253 arm-smmu.c missing {token}")
    if "case DOMAIN_ATTR_PROCID:\n\t\treturn 0;" in text:
        raise RuntimeError("Phase253 must store PROCID, not blindly succeed")
    if "case DOMAIN_ATTR_DYNAMIC:\n\t\treturn 0;" in text:
        raise RuntimeError("Phase253 must implement DYNAMIC, not blindly succeed")


def validate_iommu_header(text: str) -> None:
    required = (
        "DOMAIN_ATTR_CONTEXT_BANK", "DOMAIN_ATTR_PROCID", "DOMAIN_ATTR_TTBR0",
        "DOMAIN_ATTR_CONTEXTIDR", "DOMAIN_ATTR_DYNAMIC",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise RuntimeError(f"Phase253 include/linux/iommu.h missing enums: {missing}")


def self_test() -> int:
    h = f'''#include <linux/iommu.h>\n/* {PHASE250} */\nstruct arm_smmu_device {{\n\tDECLARE_BITMAP(context_map, ARM_SMMU_MAX_CBS);\n\tstruct arm_smmu_cb\t\t*cbs;\n}};\nstruct arm_smmu_cfg {{\n\tu8 cbndx;\n\tu8 irptndx;\n\tunion {{\n\t\tu16\t\t\tasid;\n\t\tu16\t\t\tvmid;\n\t}};\n\tenum arm_smmu_cbar_type\t\tcbar;\n}};\nstruct arm_smmu_domain {{\n\tenum arm_smmu_domain_stage\tstage;\n\tunsigned long\t\t\tattributes;\n\tu32\t\t\t\tsecure_vmid;\n}};\n'''
    hp = patch_header(h)
    validate_header(hp)
    assert patch_header(hp) == hp
    for token in ("a52_dynamic_asids", "procid", "a52_ttbr0"):
        assert token in hp

    for token in ("ida_alloc_range", "FIELD_PREP(ARM_SMMU_TTBRn_ASID",
                  "BIT(DOMAIN_ATTR_DYNAMIC)", "a52_dynamic_asid_allocated"):
        assert token in HELPERS
    assert HELPERS.index("ida_alloc_range") < HELPERS.index("a52_dynamic_asid_allocated = true")
    print("Phase 253 self-test: PASS (full KGSL dynamic ARM-SMMU contract, no forced returns)")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    root = locate_root()
    ch = root / ARM_SMMU_C
    hh = root / ARM_SMMU_H
    ih = root / IOMMU_H
    for path in (ch, hh, ih):
        if not path.is_file():
            raise RuntimeError(f"missing Phase253 source: {path}")

    validate_iommu_header(ih.read_text(encoding="utf-8"))
    hh.write_text(patch_header(hh.read_text(encoding="utf-8")), encoding="utf-8")
    ch.write_text(patch_source(ch.read_text(encoding="utf-8")), encoding="utf-8")
    print("Phase 253 KGSL ARM-SMMU dynamic-domain contract applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
