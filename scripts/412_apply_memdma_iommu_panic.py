#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE412_MEMDMA_IOMMU_PANIC_V1"
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
MSMSMMU = Path("drivers/a52_display/msm/msm_smmu.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase412 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1" not in text:
        raise SystemExit("Phase412 requires Phase409 DSI lineage")
    if "(cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE" in text:
        raise SystemExit("Phase412 control must retain original memory-DMA routing")
    if "A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1" not in text:
        raise SystemExit("Phase412 requires Phase279 SMMU/IOVA helper")

    # msm_drv.h only forward-declares msm_gem_address_space. We need the
    # concrete aspace->mmu->dev chain used to obtain the actual IOMMU domain.
    inc = '#include "msm_mmu.h"\n'
    if '#include "msm_gem.h"\n' not in text:
        text = one(text, inc, inc + '#include "msm_gem.h"\n', "msm_gem include")

    anchor = '''static inline struct msm_gem_address_space*
dsi_ctrl_get_aspace(struct dsi_ctrl *dsi_ctrl,
		int domain)
{
	if (!dsi_ctrl || !dsi_ctrl->drm_dev)
		return NULL;

	return msm_gem_smmu_address_space_get(dsi_ctrl->drm_dev, domain);
}

'''
    helper = anchor + r'''/* A52_PHASE412_MEMDMA_IOMMU_PANIC_V1
 * One-shot control-path check at the already-failed DMA_DONE boundary.
 * No mapping, unmapping, TLB operation or fault-handler replacement.
 */
static void a52_p412_iommu_timeout_check(struct dsi_ctrl *dsi_ctrl)
{
	struct msm_gem_address_space *aspace;
	struct device *aspace_dev = NULL;
	struct iommu_domain *aspace_domain = NULL;
	struct iommu_domain *dsi_domain = NULL;
	struct iommu_domain *drm_domain = NULL;
	u64 gem_iova = 0;
	u64 field_iova = 0;
	phys_addr_t gem_pa = 0;
	phys_addr_t field_pa = 0;

	if (!dsi_ctrl || !dsi_ctrl->pdev)
		return;

	aspace = dsi_ctrl_get_aspace(dsi_ctrl, MSM_SMMU_DOMAIN_UNSECURE);
	if (aspace)
		aspace_dev = msm_gem_get_aspace_device(aspace);
	if (aspace_dev)
		aspace_domain = iommu_get_domain_for_dev(aspace_dev);
	dsi_domain = iommu_get_domain_for_dev(&dsi_ctrl->pdev->dev);
	if (dsi_ctrl->drm_dev && dsi_ctrl->drm_dev->dev)
		drm_domain = iommu_get_domain_for_dev(dsi_ctrl->drm_dev->dev);

	if (aspace && dsi_ctrl->tx_cmd_buf)
		gem_iova = msm_gem_iova(dsi_ctrl->tx_cmd_buf, aspace);
	field_iova = dsi_ctrl->cmd_buffer_iova;

	if (aspace_domain && gem_iova)
		gem_pa = iommu_iova_to_phys(aspace_domain, gem_iova);
	if (aspace_domain && field_iova)
		field_pa = iommu_iova_to_phys(aspace_domain, field_iova);

	pr_emerg("A52P412 DOM ctrl=%u asdev=%s adom=%px ddom=%px mdom=%px same_d=%u same_m=%u attached=%u\\n",
		dsi_ctrl->cell_index,
		aspace_dev ? dev_name(aspace_dev) : "none",
		aspace_domain, dsi_domain, drm_domain,
		aspace_domain && aspace_domain == dsi_domain,
		aspace_domain && aspace_domain == drm_domain,
		aspace ? aspace->domain_attached : 0U);
	pr_emerg("A52P412 IOVA gem=%llx field=%llx gem_pa=%pa field_pa=%pa vaddr=%px\\n",
		(unsigned long long)gem_iova,
		(unsigned long long)field_iova,
		&gem_pa, &field_pa, dsi_ctrl->vaddr);
}

'''
    text = one(text, anchor, helper, "IOMMU helper")

    # Run only after the real DMA wait has timed out and while the original
    # transfer's clocks/power are still active. Scope the insertion to the
    # existing Phase279 timeout snapshot so the separate polling helper cannot
    # match accidentally.
    timeout = '''\t\tif (a52_p276r_deep_active())
\t\t\ta52_p279_display_fault_snapshot(2);
\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);
'''
    timeout_new = '''\t\tif (a52_p276r_deep_active())
\t\t\ta52_p279_display_fault_snapshot(2);
\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);
\t\ta52_p412_iommu_timeout_check(dsi_ctrl);
'''
    text = one(text, timeout, timeout_new, "timeout IOMMU check")

    anchor2 = '''		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_p345_flush(&dsi_ctrl->hw);
			a52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
				(unsigned int)a52_p276r_deep_active(), status, mask);
			a52_p338_emit_atomic_latch();
			a52_ackfr_record("P276 280Z q=2");
			a52_ackfr_retain_timeout_snapshot();
			a52_ackfr_record("P276 332B q=2 retained=1");
		}
'''
    panic_block = anchor2 + '''
		pr_emerg("A52P412 DMA_DONE TIMEOUT ctrl=%u status=%x irq=%d iova=%llx size=%u pwr=%u host=%u cmd=%u\\n",
			dsi_ctrl->cell_index, status,
			atomic_read(&dsi_ctrl->dma_irq_trig),
			(unsigned long long)dsi_ctrl->cmd_buffer_iova,
			dsi_ctrl->cmd_buffer_size,
			dsi_ctrl->current_state.power_state,
			dsi_ctrl->current_state.host_initialized,
			dsi_ctrl->current_state.cmd_engine_state);
		panic("A52P412 DSI MEMDMA timeout ctrl=%u status=%x iova=%llx",
			dsi_ctrl->cell_index, status,
			(unsigned long long)dsi_ctrl->cmd_buffer_iova);
'''
    text = one(text, anchor2, panic_block, "timeout panic")
    text += (
        "\n/* " + MARK + " */\n"
        "static const char a52_p412_dsi_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text
    if ('a52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx"' not in text or
        'a52_ackfr_record("M393 G irq=%d g=%x s0=%x s1=%x s2=%x"' not in text):
        raise SystemExit("Phase412 requires existing M393 ARM-SMMU fault hooks")

    old = '''	fsynr = arm_smmu_cb_read(smmu, idx, ARM_SMMU_CB_FSYNR0);
	iova = arm_smmu_cb_readq(smmu, idx, ARM_SMMU_CB_FAR);
	cbfrsynra = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBFRSYNRA(idx));

	a52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
'''
    new = '''	fsynr = arm_smmu_cb_read(smmu, idx, ARM_SMMU_CB_FSYNR0);
	iova = arm_smmu_cb_readq(smmu, idx, ARM_SMMU_CB_FAR);
	cbfrsynra = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBFRSYNRA(idx));

	/* A52_PHASE412_MEMDMA_IOMMU_PANIC_V1: emit before FSR is cleared. */
	pr_emerg("A52P412 SMMU_CONTEXT_FAULT irq=%d cb=%d fsr=%x fsynr=%x far=%lx cbfr=%x\\n",
		irq, idx, fsr, fsynr, iova, cbfrsynra);

	a52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
'''
    text = one(text, old, new, "context fault emergency log")

    oldg = '''	if (!gfsr)
		return IRQ_NONE;

	a52_ackfr_record("M393 G irq=%d g=%x s0=%x s1=%x s2=%x",
'''
    newg = '''	if (!gfsr)
		return IRQ_NONE;

	pr_emerg("A52P412 SMMU_GLOBAL_FAULT irq=%d gfsr=%x syn0=%x syn1=%x syn2=%x\\n",
		irq, gfsr, gfsynr0, gfsynr1, gfsynr2);

	a52_ackfr_record("M393 G irq=%d g=%x s0=%x s1=%x s2=%x",
'''
    text = one(text, oldg, newg, "global fault emergency log")
    text += (
        "\n/* " + MARK + " */\n"
        "static const char a52_p412_smmu_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def patch_msm_smmu(text: str) -> str:
    if MARK in text:
        return text

    old = '''\tiommu_set_fault_handler(client->domain,
\t\t\tmsm_smmu_fault_handler, (void *)client);

\tDRM_INFO("Created domain %s, secure=%d\\n",
'''
    new = '''\tiommu_set_fault_handler(client->domain,
\t\t\tmsm_smmu_fault_handler, (void *)client);
\tpr_emerg("A52P412 HANDLER installed dev=%s domain=%px\\n",
\t\tdev_name(client->dev), client->domain);

\tDRM_INFO("Created domain %s, secure=%d\\n",
'''
    text = one(text, old, new, "existing display fault-handler confirmation")

    old = '''\tDRM_ERROR("trigger dump, iova=0x%08lx, flags=0x%x\\n", iova, flags);
\tDRM_ERROR("SMMU device:%s", client->dev ? client->dev->kobj.name : "");
'''
    new = '''\tDRM_ERROR("trigger dump, iova=0x%08lx, flags=0x%x\\n", iova, flags);
\tDRM_ERROR("SMMU device:%s", client->dev ? client->dev->kobj.name : "");
\tpr_emerg("A52P412 DOMAIN_FAULT dev=%s domain=%px iova=%lx flags=%x\\n",
\t\tclient->dev ? dev_name(client->dev) : "none",
\t\tdomain, iova, flags);
'''
    return one(text, old, new, "display domain fault marker")


def validate(root: Path) -> None:
    d = (root / DSI).read_text(errors="replace")
    s = (root / SMMU).read_text(errors="replace")
    ms = (root / MSMSMMU).read_text(errors="replace")
    for token in (
        MARK,
        '#include "msm_gem.h"',
        "msm_gem_get_aspace_device(aspace)",
        "iommu_get_domain_for_dev(aspace_dev)",
        "iommu_get_domain_for_dev(&dsi_ctrl->pdev->dev)",
        "iommu_get_domain_for_dev(dsi_ctrl->drm_dev->dev)",
        "msm_gem_iova(dsi_ctrl->tx_cmd_buf, aspace)",
        "iommu_iova_to_phys(aspace_domain, gem_iova)",
        "iommu_iova_to_phys(aspace_domain, field_iova)",
        'pr_emerg("A52P412 DOM',
        'pr_emerg("A52P412 IOVA',
        "a52_p279_display_iova_snapshot(2, dsi_ctrl->cmd_buffer_iova, 8U)",
        'panic("A52P412 DSI MEMDMA timeout',
    ):
        if token not in d:
            raise SystemExit("Phase412 DSI missing: " + token)
    for token in (
        MARK,
        'pr_emerg("A52P412 SMMU_CONTEXT_FAULT',
        'pr_emerg("A52P412 SMMU_GLOBAL_FAULT',
    ):
        if token not in s:
            raise SystemExit("Phase412 SMMU missing: " + token)
    for token in (
        "iommu_set_fault_handler(client->domain",
        'pr_emerg("A52P412 HANDLER installed',
        'pr_emerg("A52P412 DOMAIN_FAULT',
    ):
        if token not in ms:
            raise SystemExit("Phase412 MSM SMMU missing: " + token)
    if "(cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE" in d:
        raise SystemExit("Phase412 unexpectedly contains Phase411 FIFO bypass")
    if "iommu_set_fault_handler" in d:
        raise SystemExit("Phase412 must not replace the domain fault handler")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    for rel in (DSI, SMMU, MSMSMMU):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase412 source missing: " + str(rel))
    if not ns.check_only:
        p = ns.root / DSI
        p.write_text(patch_dsi(p.read_text(errors="replace")))
        p = ns.root / SMMU
        p.write_text(patch_smmu(p.read_text(errors="replace")))
        p = ns.root / MSMSMMU
        p.write_text(patch_msm_smmu(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase412 memory-DMA IOMMU one-shot + panic: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
