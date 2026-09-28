#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1"
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
MSMSMMU = Path("drivers/a52_display/msm/msm_smmu.c")
ARMSMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase411 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase411 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase411 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase411 closing brace missing: {sig}")


HELPERS = r'''
/* A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1
 *
 * Functional A/B experiment, not another broad observer:
 *  - short DSI commands are routed through the controller FIFO;
 *  - the inherited exact-F0 MMIO observers are deliberately not armed;
 *  - first DMA_DONE timeout prints software/IOMMU state then panics;
 *  - no extra DSI/MDSS MMIO reads are performed by the Phase411 timeout path.
 */
static bool a52_p411_exact_f0(const struct dsi_ctrl *dsi_ctrl,
			      const struct mipi_dsi_msg *msg)
{
	const u8 *p;

	if (!dsi_ctrl || !msg || dsi_ctrl->cell_index != 0 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return false;
	p = msg->tx_buf;
	return p[0] == 0xF0 && p[1] == 0x5A && p[2] == 0x5A;
}

static void a52_p411_note_f0(const struct dsi_ctrl *dsi_ctrl,
			     const struct mipi_dsi_msg *msg, const u32 *flags,
			     const char *where)
{
	if (!a52_p411_exact_f0(dsi_ctrl, msg))
		return;
	pr_emerg("A52P411 F0 %s flags=%x mflags=%x type=%x len=%zu\n",
		 where, flags ? *flags : 0U, msg->flags, msg->type, msg->tx_len);
}

static void a52_p411_dump_domains(struct dsi_ctrl *dsi_ctrl,
				  const char *where)
{
	struct msm_gem_address_space *aspace;
	struct device *aspace_dev = NULL;
	struct iommu_domain *aspace_domain = NULL;
	struct iommu_domain *dsi_domain = NULL;
	struct iommu_domain *drm_domain = NULL;
	u64 gem_iova = 0;
	u64 field_iova = 0;
	phys_addr_t gem_phys = 0;
	phys_addr_t field_phys = 0;

	if (!dsi_ctrl)
		return;

	aspace = dsi_ctrl_get_aspace(dsi_ctrl, MSM_SMMU_DOMAIN_UNSECURE);
	if (aspace)
		aspace_dev = msm_gem_get_aspace_device(aspace);
	if (aspace_dev)
		aspace_domain = iommu_get_domain_for_dev(aspace_dev);
	if (dsi_ctrl->pdev)
		dsi_domain = iommu_get_domain_for_dev(&dsi_ctrl->pdev->dev);
	if (dsi_ctrl->drm_dev && dsi_ctrl->drm_dev->dev)
		drm_domain = iommu_get_domain_for_dev(dsi_ctrl->drm_dev->dev);

	if (dsi_ctrl->tx_cmd_buf && aspace)
		gem_iova = msm_gem_iova(dsi_ctrl->tx_cmd_buf, aspace);
	field_iova = dsi_ctrl->cmd_buffer_iova;

	if (aspace_domain && gem_iova)
		gem_phys = iommu_iova_to_phys(aspace_domain, gem_iova);
	if (aspace_domain && field_iova)
		field_phys = iommu_iova_to_phys(aspace_domain, field_iova);

	pr_emerg("A52P411 SMMU %s asdev=%s adom=%px ddom=%px mdom=%px same_d=%u same_m=%u\n",
		 where, aspace_dev ? dev_name(aspace_dev) : "none",
		 aspace_domain, dsi_domain, drm_domain,
		 aspace_domain && aspace_domain == dsi_domain,
		 aspace_domain && aspace_domain == drm_domain);
	pr_emerg("A52P411 IOVA %s gem=%llx field=%llx gphys=%pa fphys=%pa vaddr=%px\n",
		 where, (unsigned long long)gem_iova,
		 (unsigned long long)field_iova, &gem_phys, &field_phys,
		 dsi_ctrl->vaddr);
}

static __noreturn void a52_p411_timeout_panic(struct dsi_ctrl *dsi_ctrl)
{
	a52_p411_dump_domains(dsi_ctrl, "timeout");
	pr_emerg("A52P411 PANIC first DSI DMA_DONE timeout ctrl=%d irq=%u queued=%u\n",
		 dsi_ctrl ? dsi_ctrl->cell_index : -1,
		 dsi_ctrl ? (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig) : 0U,
		 dsi_ctrl ? dsi_ctrl->dma_wait_queued : 0U);
	panic("A52P411 DSI DMA_DONE timeout");
}
'''


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1" not in text:
        raise SystemExit("Phase411 requires Phase409 lineage")

    inc = "#include <linux/of_irq.h>\n"
    if "#include <linux/iommu.h>\n" not in text:
        text = one(text, inc, inc + "#include <linux/iommu.h>\n", "iommu include")

    anchor = "static void dsi_ctrl_flush_cmd_dma_queue(struct dsi_ctrl *dsi_ctrl)\n"
    text = one(text, anchor, HELPERS + "\n" + anchor, "helper insertion")

    # Stop arming the old exact-F0 observer stack. Keep only panic-visible text.
    text = one(
        text,
        "\ta52_p293_gdm_try_arm(dsi_ctrl, msg, flags);\n\n"
        "\t/* Select the tx mode to transfer the command */\n"
        "\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n",
        "\ta52_p411_note_f0(dsi_ctrl, msg, flags, \"before-mode\");\n\n"
        "\t/* Select the tx mode to transfer the command */\n"
        "\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n"
        "\ta52_p411_note_f0(dsi_ctrl, msg, flags, \"after-mode\");\n",
        "retire exact-F0 observers",
    )

    # Functional treatment: every command that fits the 64-byte controller FIFO
    # bypasses the command-buffer IOVA. Longer commands retain original DMA.
    old = '''	/* Check to see if cmd len plus header is greater than fifo size */
	if ((cmd_len + 4) > DSI_EMBEDDED_MODE_DMA_MAX_SIZE_BYTES) {
'''
    new = '''	/* A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1
	 * A/B treatment: bypass memory DMA for every command that fits the
	 * controller's native FIFO. Longer commands keep the original path.
	 */
	if ((cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE) {
		*flags &= ~(DSI_CTRL_CMD_FETCH_MEMORY |
			    DSI_CTRL_CMD_NON_EMBEDDED_MODE);
		*flags |= DSI_CTRL_CMD_FIFO_STORE;
		return;
	}

	/* Check to see if cmd len plus header is greater than fifo size */
	if ((cmd_len + 4) > DSI_EMBEDDED_MODE_DMA_MAX_SIZE_BYTES) {
'''
    text = one(text, old, new, "FIFO treatment")

    # The current capture has become unreliable. On the first real timeout,
    # preserve the exact moment through panic/pstore instead of continuing into
    # the large inherited MMIO observer branch.
    start = text.find("\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {",
                      text.find("static void dsi_ctrl_dma_cmd_wait_for_done"))
    if start < 0:
        raise SystemExit("Phase411 timeout branch start missing")
    done = text.find("\ndone:\n", start)
    if done < 0:
        raise SystemExit("Phase411 timeout done label missing")
    text = (
        text[:start] +
        "\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig))\n"
        "\t\ta52_p411_timeout_panic(dsi_ctrl);\n" +
        text[done:]
    )

    # Drop old RAM/sideband recorder startup for this experiment.
    text = one(
        text,
        "\ta52_p409_init_buffers();\n\ta52_p346_sideband_init();\n\n",
        "\t/* Phase411: no legacy DSI hot/sideband recorder startup. */\n\n",
        "retire legacy buffer startup",
    )

    # Log the actual GEM mapping at buffer creation without touching DSI MMIO.
    # Scope this to dsi_ctrl_buffer_init() so harmless lineage changes around
    # the alignment check cannot break the injector.
    start, end = function_bounds(text, "int dsi_ctrl_buffer_init(struct dsi_ctrl *dsi_ctrl)")
    fn = text[start:end]
    anchor = "\nerror:\n\treturn rc;\n}"
    if anchor not in fn:
        raise SystemExit("Phase411 buffer-init error label missing")
    insert = '''\n\tpr_emerg("A52P411 BUF local_iova=%llx field_iova=%x size=%x vaddr=%px\\n",
\t\t(unsigned long long)iova, dsi_ctrl->cmd_buffer_iova,
\t\tdsi_ctrl->cmd_buffer_size, dsi_ctrl->vaddr);
\ta52_p411_dump_domains(dsi_ctrl, "buffer-init");
'''
    fn = fn.replace(anchor, insert + anchor, 1)
    text = text[:start] + fn + text[end:]


    return text


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1" not in text:
        raise SystemExit("Phase411 requires Phase409 HW lineage")

    # Remove the inherited microsecond/debug-bus observer burst around the
    # normal memory-DMA trigger. The actual required SW_TRIGGER remains.
    old = '''	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		a52_p307_hw_snapshot(ctrl, 0);
		a52_p319_debugbus_snapshot(ctrl, 0);
		a52_p345_begin(ctrl);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		a52_p345_store(ctrl, 1);
		a52_p345_store(ctrl, 2);
		a52_p345_store(ctrl, 3);
		a52_p345_store(ctrl, 4);
		a52_p345_store(ctrl, 5);
		a52_p345_store(ctrl, 6);
		a52_p345_end(ctrl);
		a52_p409_hot_record(ctrl, A52_P409_TRIGGER_POST, 0, 0);
		a52_p319_debugbus_snapshot(ctrl, 1);
		a52_p307_hw_snapshot(ctrl, 1);
'''
    new = '''	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    text = one(text, old, new, "memory trigger observer retirement")

    old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	a52_p307_hw_snapshot(ctrl, 0);
	a52_p319_debugbus_snapshot(ctrl, 0);
	a52_p345_begin(ctrl);
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
	a52_p345_store(ctrl, 1);
	a52_p345_store(ctrl, 2);
	a52_p345_store(ctrl, 3);
	a52_p345_store(ctrl, 4);
	a52_p345_store(ctrl, 5);
	a52_p345_store(ctrl, 6);
	a52_p345_end(ctrl);
	a52_p409_hot_record(ctrl, A52_P409_TRIGGER_POST, 0, 0);
	a52_p319_debugbus_snapshot(ctrl, 1);
	a52_p307_hw_snapshot(ctrl, 1);
'''
    new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    text = one(text, old, new, "deferred trigger observer retirement")

    text += (
        "\n/* " + MARK + ": functional FIFO/panic experiment; observer burst retired. */\n"
        "static const char a52_p411_hw_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def patch_arm_smmu(text: str) -> str:
    if MARK in text:
        return text
    if 'a52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",' not in text:
        raise SystemExit("Phase411 expected Phase393 SMMU lineage")

    old = '''	cbfrsynra = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBFRSYNRA(idx));

	a52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
'''
    new = '''	cbfrsynra = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBFRSYNRA(idx));

	/* Phase411: values are already required by the real fault handler.
	 * Do not add any extra SMMU/MDSS MMIO reads here.
	 */
	pr_emerg("A52P411 SMMU_CTX irq=%d cb=%d fsr=%x syn=%x iova=%lx cbfr=%x\n",
		irq, idx, fsr, fsynr, iova, cbfrsynra);

	a52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
'''
    text = one(text, old, new, "SMMU fault marker")
    text += (
        "\n/* " + MARK + ": raw context-fault values are panic-visible. */\n"
        "static const char a52_p411_smmu_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def patch_msm_smmu(text: str) -> str:
    if MARK in text:
        return text

    # Downstream trees exist in both client->domain and
    # client->mmu_mapping->domain forms. Instrument the real registration
    # statement instead of assuming one storage layout.
    needle = "\tiommu_set_fault_handler("
    pos = text.find(needle)
    if pos < 0:
        raise SystemExit("Phase411 existing SMMU fault-handler registration missing")
    stmt_end = text.find(";\n", pos)
    if stmt_end < 0:
        raise SystemExit("Phase411 SMMU fault-handler registration terminator missing")
    stmt_end += 2
    stmt = text[pos:stmt_end]
    if "msm_smmu_fault_handler" not in stmt:
        raise SystemExit("Phase411 unexpected iommu_set_fault_handler target")
    text = (
        text[:stmt_end] +
        '\tpr_err("A52P411 SMMU_HANDLER installed dev=%s\\n",\n'
        '\t\tclient->dev ? dev_name(client->dev) : "none");\n' +
        text[stmt_end:]
    )

    # Instrument the already-existing display fault callback without relying
    # on the exact debug-dump sequence used by a particular downstream drop.
    start, end = function_bounds(text, "static int msm_smmu_fault_handler(")
    fn = text[start:end]
    anchor = 'DRM_ERROR("trigger dump, iova=0x%08lx, flags=0x%x\\n", iova, flags);'
    p = fn.find(anchor)
    if p < 0:
        raise SystemExit("Phase411 msm_smmu fault callback anchor missing")
    line = fn.rfind("\n", 0, p) + 1
    inject = (
        '\tpr_emerg("A52P411 MSM_SMMU_FAULT dev=%s domain=%px iova=%lx flags=%x\\n",\n'
        '\t\tclient && client->dev ? dev_name(client->dev) : "none",\n'
        '\t\tdomain, iova, flags);\n'
    )
    fn = fn[:line] + inject + fn[line:]
    text = text[:start] + fn + text[end:]

    text += "\n/* " + MARK + ": downstream display SMMU handler confirmation. */\n"
    return text

def patch_display(text: str) -> str:
    if MARK in text:
        return text

    old = '''	}

	return 0;
}

static int dsi_display_phy_power_on(struct dsi_display *display)
'''
    new = '''	}

	pr_err("A52P411 BOOTDISP p='%s' s='%s' en0=%u en1=%u name0='%s' name1='%s'\n",
		dsi_display_primary, dsi_display_secondary,
		boot_displays[0].boot_disp_en, boot_displays[1].boot_disp_en,
		boot_displays[0].name, boot_displays[1].name);
	return 0;
}

/* A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1 */
static int dsi_display_phy_power_on(struct dsi_display *display)
'''
    return one(text, old, new, "boot-display confirmation")


def validate(root: Path) -> None:
    ctrl = (root / CTRL).read_text(errors="replace")
    hwc = (root / HWC).read_text(errors="replace")
    display = (root / DISPLAY).read_text(errors="replace")
    msmsmmu = (root / MSMSMMU).read_text(errors="replace")
    armsmmu = (root / ARMSMMU).read_text(errors="replace")

    for token in (
        MARK,
        "(cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE",
        "*flags &= ~(DSI_CTRL_CMD_FETCH_MEMORY |",
        "*flags |= DSI_CTRL_CMD_FIFO_STORE",
        'a52_p411_note_f0(dsi_ctrl, msg, flags, "before-mode")',
        'a52_p411_note_f0(dsi_ctrl, msg, flags, "after-mode")',
        "msm_gem_iova(dsi_ctrl->tx_cmd_buf, aspace)",
        "iommu_iova_to_phys(aspace_domain, gem_iova)",
        "iommu_iova_to_phys(aspace_domain, field_iova)",
        "A52P411 PANIC first DSI DMA_DONE timeout",
        'panic("A52P411 DSI DMA_DONE timeout")',
        "Phase411: no legacy DSI hot/sideband recorder startup",
    ):
        if token not in ctrl:
            raise SystemExit("Phase411 CTRL token missing: " + token)

    if "a52_p293_gdm_try_arm(dsi_ctrl, msg, flags);" in ctrl:
        raise SystemExit("Phase411 old exact-F0 arm call still active")
    if "a52_p409_init_buffers();" in ctrl or "a52_p346_sideband_init();" in ctrl:
        raise SystemExit("Phase411 legacy DSI recorder startup remains")

    for token in (
        MARK,
        "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
    ):
        if token not in hwc:
            raise SystemExit("Phase411 HWC token missing: " + token)
    for forbidden in (
        "a52_p345_store(ctrl, 1);",
        "a52_p409_hot_record(ctrl, A52_P409_TRIGGER_POST",
    ):
        if forbidden in hwc:
            raise SystemExit("Phase411 HW observer remains: " + forbidden)

    for token in (
        "A52P411 SMMU_CTX",
        MARK,
    ):
        if token not in armsmmu:
            raise SystemExit("Phase411 arm-smmu token missing: " + token)

    for token in ("A52P411 BOOTDISP", MARK):
        if token not in display:
            raise SystemExit("Phase411 display token missing: " + token)

    for token in ("A52P411 SMMU_HANDLER", "A52P411 MSM_SMMU_FAULT", MARK):
        if token not in msmsmmu:
            raise SystemExit("Phase411 msm-smmu token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (CTRL, HWC, DISPLAY, MSMSMMU, ARMSMMU):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase411 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))
        p = ns.root / DISPLAY
        p.write_text(patch_display(p.read_text(errors="replace")))
        p = ns.root / MSMSMMU
        p.write_text(patch_msm_smmu(p.read_text(errors="replace")))
        p = ns.root / ARMSMMU
        p.write_text(patch_arm_smmu(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase411 FIFO + panic + SMMU experiment: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
