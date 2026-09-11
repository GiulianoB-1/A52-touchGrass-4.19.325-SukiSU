#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
MARK = "A52_PHASE336_QSMMUV500_HF0_TBU_STATE_RECORDER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase336 {label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1" not in text:
        raise SystemExit("Phase336 requires Phase335 sACR parity source")

    fn_anchor = "static void arm_smmu_device_reset(struct arm_smmu_device *smmu)\n"
    globals_block = r'''/* A52_PHASE336_QSMMUV500_HF0_TBU_STATE_RECORDER_V1
 * Preserve the early apps-SMMU Phase335 result and expose a read-only
 * late snapshot for the exact F0 DSI recorder.
 *
 * The physical addresses are not guessed:
 *   apps_smmu       = 0x15000000
 *   mnoc_hf_0 TBU   = 0x1518d000
 *   HF0 status-reg  = 0x15182210
 * from the pinned lito msm-arm-smmu DT. SID 0x800 belongs to HF0.
 *
 * No SMMU/TBU register is written by Phase336.
 */
static u32 a52_p336_sacr_before;
static u32 a52_p336_sacr_after;
static bool a52_p336_sacr_seen;
static void __iomem *a52_p336_apps_smmu_base;
static void __iomem *a52_p336_hf0_status;

void a52_p336_qsmmuv500_snapshot(u32 *seen, u32 *sacr_before,
					 u32 *sacr_after, u32 *sacr_current,
					 u32 *tbu_valid, u32 *tbu_status)
{
	void __iomem *smmu_base = READ_ONCE(a52_p336_apps_smmu_base);
	void __iomem *hf0_status = READ_ONCE(a52_p336_hf0_status);

	if (seen)
		*seen = READ_ONCE(a52_p336_sacr_seen);
	if (sacr_before)
		*sacr_before = READ_ONCE(a52_p336_sacr_before);
	if (sacr_after)
		*sacr_after = READ_ONCE(a52_p336_sacr_after);
	if (sacr_current)
		*sacr_current = smmu_base ?
			readl_relaxed(smmu_base + 0x10) : 0xffffffff;
	if (tbu_valid)
		*tbu_valid = !!hf0_status;
	if (tbu_status)
		*tbu_status = hf0_status ? readl_relaxed(hf0_status) : 0xffffffff;
}
EXPORT_SYMBOL_GPL(a52_p336_qsmmuv500_snapshot);

'''
    text = one(text, fn_anchor, globals_block + fn_anchor, "SMMU state block")

    readback_anchor = (
        "\t\ta52_p335_after = readl_relaxed(smmu->base + 0x10);\n\n"
        "\t\tdev_info(smmu->dev,\n"
    )
    readback_new = r'''		a52_p335_after = readl_relaxed(smmu->base + 0x10);

		/*
		 * Phase336: retain the Phase335 result only for apps_smmu and
		 * map only the HF0 TBU status register used by SID 0x800.
		 * devm_ioremap() creates a read-only-observer mapping here; no
		 * write, power vote, reset, delay or transaction is issued.
		 */
		{
			struct platform_device *a52_p336_pdev =
				to_platform_device(smmu->dev);
			struct resource *a52_p336_res =
				platform_get_resource(a52_p336_pdev, IORESOURCE_MEM, 0);

			if (a52_p336_res &&
			    a52_p336_res->start == (resource_size_t)0x15000000) {
				void __iomem *a52_p336_status;

				WRITE_ONCE(a52_p336_sacr_before, a52_p335_before);
				WRITE_ONCE(a52_p336_sacr_after, a52_p335_after);
				WRITE_ONCE(a52_p336_apps_smmu_base, smmu->base);
				WRITE_ONCE(a52_p336_sacr_seen, true);

				a52_p336_status = READ_ONCE(a52_p336_hf0_status);
				if (!a52_p336_status) {
					a52_p336_status = devm_ioremap(smmu->dev,
						(resource_size_t)0x15182210, 0x8);
					if (a52_p336_status)
						WRITE_ONCE(a52_p336_hf0_status,
							   a52_p336_status);
				}
			}
		}

		dev_info(smmu->dev,
'''
    text = one(text, readback_anchor, readback_new, "apps SMMU retention/map")
    return text


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1" not in text:
        raise SystemExit("Phase336 requires inherited Phase316 exact-F0 snapshot")

    helper_anchor = (
        "/* A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1\n"
    )
    decl = r'''/* A52_PHASE336_QSMMUV500_HF0_TBU_STATE_RECORDER_V1 */
extern void a52_p336_qsmmuv500_snapshot(u32 *seen, u32 *sacr_before,
						u32 *sacr_after, u32 *sacr_current,
						u32 *tbu_valid, u32 *tbu_status);

'''
    text = one(text, helper_anchor, decl + helper_anchor, "Phase336 extern")

    tail = r'''	a52_ackfr_record("P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u", point,
		ck, (ck >> 7) & 1, (ck >> 10) & 1, (ck >> 12) & 1,
		(ck >> 16) & 1, (ck >> 23) & 1);
}
'''
    tail_new = r'''	a52_ackfr_record("P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u", point,
		ck, (ck >> 7) & 1, (ck >> 10) & 1, (ck >> 12) & 1,
		(ck >> 16) & 1, (ck >> 23) & 1);

	{
		u32 a52_p336_seen = 0;
		u32 a52_p336_before = 0xffffffff;
		u32 a52_p336_after = 0xffffffff;
		u32 a52_p336_current = 0xffffffff;
		u32 a52_p336_tbu_valid = 0;
		u32 a52_p336_tbu_status = 0xffffffff;

		a52_p336_qsmmuv500_snapshot(&a52_p336_seen,
			&a52_p336_before, &a52_p336_after, &a52_p336_current,
			&a52_p336_tbu_valid, &a52_p336_tbu_status);
		a52_ackfr_record("P276 336A q=%u v=%u sb=%x sa=%x sc=%x tv=%u ts=%x",
			point, a52_p336_seen, a52_p336_before, a52_p336_after,
			a52_p336_current, a52_p336_tbu_valid, a52_p336_tbu_status);
	}
}
'''
    text = one(text, tail, tail_new, "q0/q1/q2 probe record")
    return text


def validate(before_smmu: str, after_smmu: str,
             before_hwc: str, after_hwc: str) -> None:
    required = (
        MARK,
        "a52_p336_qsmmuv500_snapshot",
        "a52_p336_res->start == (resource_size_t)0x15000000",
        "(resource_size_t)0x15182210",
        "P276 336A q=%u v=%u sb=%x sa=%x sc=%x tv=%u ts=%x",
    )
    combined = after_smmu + after_hwc
    for token in required:
        if token not in combined:
            raise SystemExit("Phase336 required token missing: " + token)

    # Phase336 is a recorder only. It may add MMIO reads and one devm_ioremap,
    # but must not add a register write or any DSI/clock/power/reset action.
    write_tokens = (
        "writel_relaxed(", "writel(", "writeq_relaxed(", "regmap_write(",
        "regmap_update_bits(", "DSI_W32(", "clk_set_rate(", "clk_set_parent(",
        "clk_prepare_enable(", "clk_disable_unprepare(", "regulator_enable(",
        "regulator_disable(", "reset_control_assert(", "reset_control_deassert(",
    )
    for token in write_tokens:
        before = before_smmu.count(token) + before_hwc.count(token)
        after = after_smmu.count(token) + after_hwc.count(token)
        if before != after:
            raise SystemExit(
                f"Phase336 functional-scope violation {token}: {before} -> {after}"
            )

    if after_smmu.count("devm_ioremap(") != before_smmu.count("devm_ioremap(") + 1:
        raise SystemExit("Phase336 must add exactly one HF0 status observer mapping")

    if after_smmu.count("readl_relaxed(") != before_smmu.count("readl_relaxed(") + 2:
        raise SystemExit("Phase336 must add exactly two live MMIO reads")

    if after_hwc.count("a52_ackfr_record(") != before_hwc.count("a52_ackfr_record(") + 1:
        raise SystemExit("Phase336 must add exactly one q-point recorder record")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--before-smmu", type=Path)
    ap.add_argument("--before-hwc", type=Path)
    ns = ap.parse_args()

    smmu_path = ns.root / SMMU
    hwc_path = ns.root / HWC
    if not smmu_path.is_file() or not hwc_path.is_file():
        raise SystemExit("Phase336 required source missing")

    smmu = smmu_path.read_text(encoding="utf-8")
    hwc = hwc_path.read_text(encoding="utf-8")

    if ns.check_only:
        if not ns.before_smmu or not ns.before_smmu.is_file():
            raise SystemExit("Phase336 --check-only requires --before-smmu")
        if not ns.before_hwc or not ns.before_hwc.is_file():
            raise SystemExit("Phase336 --check-only requires --before-hwc")
        validate(
            ns.before_smmu.read_text(encoding="utf-8"), smmu,
            ns.before_hwc.read_text(encoding="utf-8"), hwc,
        )
        print("Phase336 QSMMUv500 HF0 TBU state recorder audit: PASS")
        return 0

    updated_smmu = patch_smmu(smmu)
    updated_hwc = patch_hwc(hwc)
    if updated_smmu == smmu or updated_hwc == hwc:
        raise SystemExit("Phase336 patch produced an incomplete/no-op change")

    validate(smmu, updated_smmu, hwc, updated_hwc)
    smmu_path.write_text(updated_smmu, encoding="utf-8")
    hwc_path.write_text(updated_hwc, encoding="utf-8")
    print("Phase336 QSMMUv500 HF0 TBU state recorder applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
