#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
MARK = "A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1"
COMPAT = "qcom,qsmmu-v500"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase335 {label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text

    if COMPAT not in text:
        raise SystemExit("Phase335 requires the staged A52 qcom,qsmmu-v500 compatible")
    if "qcom,skip-init" not in text or "smmu->skip_init" not in text:
        raise SystemExit("Phase335 requires the staged qcom,skip-init handoff")
    if "!smmu->skip_init && smmu->impl && smmu->impl->reset" not in text:
        raise SystemExit("Phase335 requires the inherited skip-init implementation-reset guard")

    anchor = (
        "\tif (!smmu->skip_init && smmu->impl && smmu->impl->reset)\n"
        "\t\tsmmu->impl->reset(smmu);"
    )

    injection = r'''	/* A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1
	 * TouchGrass QSMMUv500 init clears MMU-500 sACR.CACHE_LOCK even
	 * when firmware-owned stream/context state is preserved. Our GKI
	 * qcom,skip-init path suppresses the Qualcomm implementation reset,
	 * so reproduce only that one TouchGrass register semantic here.
	 *
	 * Scope is deliberately narrow:
	 *   - qcom,qsmmu-v500 only
	 *   - qcom,skip-init only
	 *   - sACR offset 0x10 only
	 *   - CACHE_LOCK bit 26 only
	 * No stream-map, context-bank, TLB, ACTLR or reset behavior changes.
	 */
	if (smmu->skip_init &&
	    of_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500")) {
		u32 a52_p335_before;
		u32 a52_p335_wanted;
		u32 a52_p335_after;

		a52_p335_before = readl_relaxed(smmu->base + 0x10);
		a52_p335_wanted = a52_p335_before & ~(1U << 26);
		if (a52_p335_wanted != a52_p335_before)
			writel_relaxed(a52_p335_wanted, smmu->base + 0x10);
		a52_p335_after = readl_relaxed(smmu->base + 0x10);

		dev_info(smmu->dev,
			 "A52 P335 QSMMUV500 sACR before=%08x after=%08x changed=%u lock=%u\n",
			 a52_p335_before, a52_p335_after,
			 a52_p335_before != a52_p335_after,
			 !!(a52_p335_after & (1U << 26)));
	}

'''

    return replace_once(text, anchor, injection + anchor, "sACR parity insertion")


def audit(before: str, after: str) -> None:
    required = (
        MARK,
        'of_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500")',
        "smmu->skip_init &&",
        "readl_relaxed(smmu->base + 0x10)",
        "a52_p335_before & ~(1U << 26)",
        "writel_relaxed(a52_p335_wanted, smmu->base + 0x10)",
        "A52 P335 QSMMUV500 sACR before=%08x after=%08x changed=%u lock=%u",
        "if (!smmu->skip_init && smmu->impl && smmu->impl->reset)",
    )
    for token in required:
        if token not in after:
            raise SystemExit("Phase335 required token missing: " + token)

    expected_deltas = {
        "readl_relaxed(": 2,
        "writel_relaxed(": 1,
        "of_device_is_compatible(": 1,
        "dev_info(": 1,
    }
    for token, delta in expected_deltas.items():
        actual = after.count(token) - before.count(token)
        if actual != delta:
            raise SystemExit(
                f"Phase335 unexpected {token} delta: expected {delta}, got {actual}"
            )

    protected = (
        "arm_smmu_write_sme(",
        "arm_smmu_write_context_bank(",
        "arm_smmu_tlb_inv_context(",
        "arm_smmu_tlb_sync(",
        "arm_smmu_tlb_sync_global(",
        "impl->reset(smmu)",
        "clk_prepare_enable(",
        "clk_disable_unprepare(",
        "regulator_enable(",
        "regulator_disable(",
        "reset_control_assert(",
        "reset_control_deassert(",
    )
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f"Phase335 functional-scope violation {token}: "
                f"{before.count(token)} -> {after.count(token)}"
            )

    block_start = after.index("/* " + MARK)
    block_end = after.index(
        "\tif (!smmu->skip_init && smmu->impl && smmu->impl->reset)",
        block_start,
    )
    block = after[block_start:block_end]

    if block.count("writel_relaxed(") != 1:
        raise SystemExit("Phase335 parity block must contain exactly one possible MMIO write")
    if block.count("readl_relaxed(") != 2:
        raise SystemExit("Phase335 parity block must contain exactly two MMIO reads")

    forbidden = (
        "arm_smmu_write_sme(",
        "arm_smmu_write_context_bank(",
        "TLBI",
        "ACTLR",
        "impl->reset",
        "udelay(",
        "usleep_range(",
        "msleep(",
    )
    for token in forbidden:
        if token in block and token != "ACTLR":
            raise SystemExit("Phase335 parity block contains forbidden token: " + token)

    # ACTLR is allowed only in the explanatory comment above, never as code.
    code_only = "\n".join(
        line for line in block.splitlines()
        if not line.lstrip().startswith("*") and not line.lstrip().startswith("/*")
    )
    if "ACTLR" in code_only:
        raise SystemExit("Phase335 parity block must not access ACTLR")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--before", type=Path)
    ns = ap.parse_args()

    path = ns.root / SMMU
    if not path.is_file():
        raise SystemExit("Phase335 SMMU source missing: " + str(path))

    current = path.read_text(encoding="utf-8")

    if ns.check_only:
        if MARK not in current:
            raise SystemExit("Phase335 marker is not present")
        if ns.before is None or not ns.before.is_file():
            raise SystemExit("Phase335 --check-only requires --before snapshot")
        audit(ns.before.read_text(encoding="utf-8"), current)
        print("Phase335 QSMMUv500 sACR CACHE_LOCK parity audit: PASS")
        return 0

    updated = patch(current)
    if updated == current:
        raise SystemExit("Phase335 patch produced no change")

    audit(current, updated)
    path.write_text(updated, encoding="utf-8")
    print("Phase335 QSMMUv500 sACR CACHE_LOCK parity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
