	if (dsi_ctrl->vaddr)
		memcpy(&first8, dsi_ctrl->vaddr, sizeof(first8));

	memset(&a52_p427_cmdmap, 0, sizeof(a52_p427_cmdmap));
	a52_p427_cmdmap.gem_iova = iova;
	a52_p427_cmdmap.field_iova = dsi_ctrl->cmd_buffer_iova;
	if (domain && dsi_ctrl->cmd_buffer_iova)
		a52_p427_cmdmap.iova_phys =
			iommu_iova_to_phys(domain, dsi_ctrl->cmd_buffer_iova);
	if (bo && bo->pages && bo->pages[0])
		a52_p427_cmdmap.page_phys = page_to_phys(bo->pages[0]);
	a52_p427_cmdmap.first8 = first8;
	smp_wmb();
	WRITE_ONCE(a52_p427_cmdmap.valid, 1U);
}

static void a52_p427_cmdmap_dump(void)
{
	struct a52_p427_cmdmap *m = &a52_p427_cmdmap;
	a52_ackfr_record("P427 I v=%u gi=%llx fi=%llx",
		READ_ONCE(m->valid), (unsigned long long)m->gem_iova,
		(unsigned long long)m->field_iova);
	a52_ackfr_record("P427 P ip=%llx pp=%llx b=%llx",
		(unsigned long long)m->iova_phys,
		(unsigned long long)m->page_phys,
		(unsigned long long)m->first8);
}

static void a52_p427_post_wait(struct dsi_ctrl *dsi_ctrl)
{
	if (!dsi_ctrl || atomic_cmpxchg(&a52_p427_posted, 0, 1) != 0)
		return;
	a52_p427_road_snapshot(&dsi_ctrl->hw, 1U);
	a52_p427_smmu_dump();
	a52_p427_cmdmap_dump();
	a52_p427_road_dump();
}
'''


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "a52_p426_capture_f0();",
        "a52_p426_timeout_dump();",
        "static atomic_t a52_p421_target = ATOMIC_INIT(0);",
        "dsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);",
        "ret = wait_for_completion_timeout(",
    ):
        if token not in text:
            raise SystemExit("Phase427 DSI prerequisite missing: " + token)

    if '#include "msm_gem.h"\n' not in text:
        text = one(text, '#include "msm_mmu.h"\n',
                   '#include "msm_mmu.h"\n#include "msm_gem.h"\n',
                   "MSM GEM include")
    if "#include <linux/mm.h>\n" not in text:
        text = one(text, "#include <linux/iommu.h>\n",
                   "#include <linux/iommu.h>\n#include <linux/mm.h>\n",
                   "page_to_phys include")

    anchor = "static atomic_t a52_p421_target = ATOMIC_INIT(0);\n"
    text = one(text, anchor, anchor + "\n" + DSI_BLOCK + "\n",
               "DSI cmdmap helpers")

    old = '''kickoff:
	if ((a52_p276r_deep_active() && !a52_p421_target_active())) a52_ackfr_record("P276 D M k=0");
	dsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);
'''
    new = '''kickoff:
	if (a52_p421_f0)
		a52_p427_capture_cmdmap(dsi_ctrl);
	if ((a52_p276r_deep_active() && !a52_p421_target_active())) a52_ackfr_record("P276 D M k=0");
	dsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);
'''
    text = one(text, old, new, "pre-kickoff command map")

    a, b = function_bounds(text, "static void dsi_ctrl_dma_cmd_wait_for_done(")
    fn = text[a:b]
    old = '''	if (atomic_read(&dsi_ctrl->dma_irq_trig)) {
		if (a52_p293_gdm_armed(dsi_ctrl))
			a52_ackfr_record("P276 387D short=1");
		goto done;
	}
'''
    new = '''	if (atomic_read(&dsi_ctrl->dma_irq_trig)) {
		if (a52_p421_target_active())
			a52_p427_post_wait(dsi_ctrl);
		if (a52_p293_gdm_armed(dsi_ctrl))
			a52_ackfr_record("P276 387D short=1");
		goto done;
	}
'''
    if old not in fn:
        raise SystemExit("Phase427 DSI early-DMA_DONE anchor missing")
    fn = fn.replace(old, new, 1)

    wait_anchor = '''	if (a52_p421_target_active())
		a52_p421_survival_record(10U, A52_P421_V_IRQ | A52_P421_V_RET,
			0U, 0, (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig),
			ret);
'''
    if wait_anchor not in fn:
        raise SystemExit("Phase427 DSI post-wait anchor missing")
    fn = fn.replace(wait_anchor, wait_anchor +
                    "\tif (a52_p421_target_active())\n\t\ta52_p427_post_wait(dsi_ctrl);\n", 1)
    text = text[:a] + fn + text[b:]
    text += "\n/* " + MARK + ": command backing + post-wait road dump. */\n"
    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE426_SMMU_STREAM_HANDOFF_PROBE_V1" not in text:
        raise SystemExit("Phase427 recorder requires Phase426")

    retained = '''	if (unlikely(atomic_read(&a52_r280_retained)) &&
	    strncmp(fmt, "P426", 4) &&
'''
    text = one(text, retained,
               '''	if (unlikely(atomic_read(&a52_r280_retained)) &&
	    strncmp(fmt, "P427", 4) &&
	    strncmp(fmt, "P426", 4) &&
''', "retained admission")

    normal = '''if (strncmp(fmt, "P426", 4) &&
'''
    text = one(text, normal,
               '''if (strncmp(fmt, "P427", 4) &&
    strncmp(fmt, "P426", 4) &&
''', "normal admission")

    clean = '''	if (!fmt || (
	    strncmp(fmt, "P426", 4) &&
'''
    text = one(text, clean,
               '''	if (!fmt || (
	    strncmp(fmt, "P427", 4) &&
	    strncmp(fmt, "P426", 4) &&
''', "clean admission")

    critical = 'return !strncmp(message, "P426 ", 5) ||'
    text = one(text, critical,
               'return !strncmp(message, "P427 ", 5) ||\n\t       !strncmp(message, "P426 ", 5) ||',
               "critical admission")
    text += "\n/* " + MARK + ": P427 evidence admitted. */\n"
    return text


def validate(root: Path) -> None:
    arm = (root / ARM).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")
    hwc = (root / HWC).read_text(errors="replace")
    arb = (root / BUS_ARB).read_text(errors="replace")
    fab = (root / BUS_FAB).read_text(errors="replace")

    checks = {
        "ARM": (arm, (
            MARK,
            'of_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec")',
            "sid != 0x800",
            "s2cr->pinned",
            "cb != 2U",
            "a52_p427_try_handoff_cb(smmu, dev, start)",
            "arm_smmu_tlb_inv_context_s1(smmu_domain);",
            "a52_p427_handoff.irptndx = cfg->irptndx;",
            "void a52_p427_smmu_dump(void)",
        )),
        "REC": (rec, (
            'strncmp(fmt, "P427", 4)',
            '!strncmp(message, "P427 ", 5)',
        )),
        "DSI": (dsi, (
            '#include "msm_gem.h"',
            "a52_p427_capture_cmdmap(dsi_ctrl);",
            "iommu_iova_to_phys(domain, dsi_ctrl->cmd_buffer_iova)",
            "page_to_phys(bo->pages[0])",
            "a52_p427_post_wait(dsi_ctrl);",
        )),
        "HWC": (hwc, (
            "ioremap(A52_P424_RSC_DRV_PHYS, 0x2400)",
            "A52_P427_HF0_GCC_PHYS",
            "A52_P427_TCS_BASE",
            "a52_p427_road_snapshot(ctrl, 0U);",
            "void a52_p427_road_dump(void)",
            "get_sde_rsc_current_state(0)",
        )),
        "BUS_ARB": (arb, (
            "void a52_p427_bus_get(",
            '"mdss_sde"',
            '"disp_rsc_mnoc_llcc"',
            '"disp_rsc_ebi"',
            "a52_p427_bus_shadow_update(cl, client);",
        )),
        "BUS_FAB": (fab, (
            "void a52_p427_bcm_get(",
            '"MM0_DISPLAY"',
            '"MM1_DISPLAY"',
            "a52_p427_bcm_shadow_update(cur_bcm);",
        )),
    }
    for scope, (text, tokens) in checks.items():
        missing = [t for t in tokens if t not in text]
        if missing:
            raise SystemExit(f"Phase427 {scope} validation missing: {missing}")

    # Guard the single functional scope: only the exact SID800 allocator hook
    # may select CB2.  The road probes must not contain MMIO writes/RPMh votes.
    h = hwc[hwc.index(MARK):]
    forbidden = ("writel_relaxed(", "rpmh_write(", "rpmh_write_batch(",
                 "msm_bus_scale_client_update_request(", "regulator_enable(")
    bad = [x for x in forbidden if x in h]
    if bad:
        raise SystemExit("Phase427 road probe contains functional writes: " + ", ".join(bad))



def apply(root: Path) -> None:
    paths = {
        ARM: patch_arm,
        REC: patch_rec,
        DSI: patch_dsi,
        HWC: patch_hwc,
        BUS_ARB: patch_bus_arb,
        BUS_FAB: patch_bus_fab,
    }
    for rel, fn in paths.items():
        p = root / rel
        if not p.is_file():
            raise SystemExit("Phase427 source missing: " + str(rel))
        p.write_text(fn(p.read_text(errors="replace")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if not ns.check_only:
        apply(ns.root)
    validate(ns.root)
    print("Phase427 CB2 handoff + road-to-memory probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
