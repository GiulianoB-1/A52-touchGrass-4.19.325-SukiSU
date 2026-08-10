#!/usr/bin/env python3
"""Phase 250: restore the downstream GPU SMMU regulator-before-clock contract.

TouchGrass hardware reference proves the GPU SMMU powers in this order:
optional bus vote (no-op on a52xq GPU SMMU), DT-declared vdd/CX regulator,
clock prepare, then clock enable. Phase249 proves ACK 5.10 skips the regulator
step and fails enabling gcc_gpu_memnoc_gfx_clk with -EBUSY.

This overlay teaches the ACK arm-smmu driver to consume qcom,regulator-names
using the existing 5.10 clock/runtime lifetime. It does not alter DT, stream
IDs, clock topology, GDSC implementation, IOMMU grouping, probe ordering, or
return-value semantics.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1"
PHASE249 = "A52_PHASE249_GPU_SMMU_ENODEV_ROOT_V1"
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
ARM_SMMU_C = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
ARM_SMMU_H = Path("drivers/iommu/arm/arm-smmu/arm-smmu.h")


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {n}: {old!r}")
    return text.replace(old, new, 1)


def locate_root() -> Path:
    for root in (Path.cwd() / "workspace/gki-phase199-src", Path.cwd() / "gki/common"):
        if (root / ARM_SMMU_C).is_file():
            return root
    raise RuntimeError("Phase250 generated ACK source tree not found")


def patch_recorder(text: str) -> str:
    if MARKER in text:
        return text
    if PHASE249 not in text:
        raise RuntimeError("Phase250 recorder requires Phase249")
    text = one(
        text,
        'if (strncmp(fmt, "K249", 4) &&\n',
        f'/* {MARKER} */\nif (strncmp(fmt, "K250", 4) &&\n    strncmp(fmt, "K249", 4) &&\n',
        "recorder format admission",
    )
    text = one(
        text,
        'return !strncmp(message, "K249 ", 5) ||\n',
        'return !strncmp(message, "K250 ", 5) ||\n       !strncmp(message, "K249 ", 5) ||\n',
        "recorder critical admission",
    )
    return text


def patch_header(text: str) -> str:
    if MARKER in text:
        return text
    anchor = '\tstruct clk_bulk_data\t\t*clks;\n\tint\t\t\t\tnum_clks;\n'
    repl = anchor + (
        f'\n\t/* {MARKER}: downstream qcom,regulator-names supply contract */\n'
        '\tstruct regulator_bulk_data\t*a52_gdscs;\n'
        '\tint\t\t\t\ta52_num_gdscs;\n'
    )
    return one(text, anchor, repl, "arm-smmu header power fields")


HELPERS = f'''\n/* {MARKER}
 * Qualcomm downstream SMMUv2 nodes may describe GDSCs as regulators via
 * qcom,regulator-names. Keep their lifetime paired with ACK's existing clock
 * lifetime: regulator on before clk_prepare/enable; regulator off after clocks.
 */
static const char a52_phase250_gpu_smmu_power_contract[] __used =
\t"{MARKER}";

static int a52_arm_smmu_init_gdscs(struct arm_smmu_device *smmu)
{{
\tstruct device *dev = smmu->dev;
\tint i, ret, count;

\tcount = of_property_count_strings(dev->of_node, "qcom,regulator-names");
\tif (count <= 0) {{
\t\tsmmu->a52_num_gdscs = 0;
\t\treturn 0;
\t}}

\tsmmu->a52_gdscs = devm_kcalloc(dev, count, sizeof(*smmu->a52_gdscs),
\t\t\t\t       GFP_KERNEL);
\tif (!smmu->a52_gdscs)
\t\treturn -ENOMEM;

\tfor (i = 0; i < count; i++) {{
\t\tconst char *name;

\t\tret = of_property_read_string_index(dev->of_node,
\t\t\t\t"qcom,regulator-names", i, &name);
\t\tif (ret)
\t\t\treturn ret;
\t\tsmmu->a52_gdscs[i].supply = name;
\t}}

\tret = devm_regulator_bulk_get(dev, count, smmu->a52_gdscs);
\tif (ret)
\t\treturn ret;

\tsmmu->a52_num_gdscs = count;
\treturn 0;
}}

static int a52_arm_smmu_enable_gdscs(struct arm_smmu_device *smmu)
{{
\tif (!smmu->a52_num_gdscs)
\t\treturn 0;
\treturn regulator_bulk_enable(smmu->a52_num_gdscs, smmu->a52_gdscs);
}}

static void a52_arm_smmu_disable_gdscs(struct arm_smmu_device *smmu)
{{
\tint ret;

\tif (!smmu->a52_num_gdscs)
\t\treturn;
\tret = regulator_bulk_disable(smmu->a52_num_gdscs, smmu->a52_gdscs);
\tif (ret)
\t\tdev_warn(smmu->dev, "failed to disable SMMU regulators: %d\\n", ret);
}}
\n'''


def patch_source(text: str) -> str:
    if MARKER in text:
        validate_source(text)
        return text
    if PHASE249 not in text or "K249 S clkon rc=%d" not in text:
        raise RuntimeError("Phase250 arm-smmu requires Phase249 generated source")

    if '#include <linux/regulator/consumer.h>\n' not in text:
        text = one(text, '#include <linux/ratelimit.h>\n',
                   '#include <linux/ratelimit.h>\n#include <linux/regulator/consumer.h>\n',
                   "arm-smmu regulator include")

    anchor = 'static bool using_legacy_binding, using_generic_binding;\n\n'
    text = one(text, anchor, anchor + HELPERS, "arm-smmu helper insertion")

    old = (
        '\terr = devm_clk_bulk_get_all(dev, &smmu->clks);\n'
        '\tif (k249)\n'
        '\t\ta52_ackfr_record("K249 S clkget rc=%d", err);\n'
        '\tif (err < 0) {\n'
        '\t\tdev_err(dev, "failed to get clocks %d\\n", err);\n'
        '\t\treturn err;\n'
        '\t}\n'
        '\tsmmu->num_clks = err;\n\n'
        '\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n'
        '\tif (k249)\n'
        '\t\ta52_ackfr_record("K249 S clkon rc=%d", err);\n'
        '\tif (err)\n'
        '\t\treturn err;\n'
    )
    new = (
        '\terr = devm_clk_bulk_get_all(dev, &smmu->clks);\n'
        '\tif (k249)\n'
        '\t\ta52_ackfr_record("K249 S clkget rc=%d", err);\n'
        '\tif (err < 0) {\n'
        '\t\tdev_err(dev, "failed to get clocks %d\\n", err);\n'
        '\t\treturn err;\n'
        '\t}\n'
        '\tsmmu->num_clks = err;\n\n'
        '\terr = a52_arm_smmu_init_gdscs(smmu);\n'
        '\tif (k249)\n'
        '\t\ta52_ackfr_record("K250 S gdscget rc=%d n=%d", err,\n'
        '\t\t\terr ? 0 : smmu->a52_num_gdscs);\n'
        '\tif (err)\n'
        '\t\treturn err;\n\n'
        '\terr = a52_arm_smmu_enable_gdscs(smmu);\n'
        '\tif (k249)\n'
        '\t\ta52_ackfr_record("K250 S regon rc=%d n=%d", err,\n'
        '\t\t\tsmmu->a52_num_gdscs);\n'
        '\tif (err)\n'
        '\t\treturn err;\n\n'
        '\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n'
        '\tif (k249) {\n'
        '\t\ta52_ackfr_record("K249 S clkon rc=%d", err);\n'
        '\t\ta52_ackfr_record("K250 S clkon rc=%d", err);\n'
        '\t}\n'
        '\tif (err) {\n'
        '\t\ta52_arm_smmu_disable_gdscs(smmu);\n'
        '\t\treturn err;\n'
        '\t}\n'
    )
    text = one(text, old, new, "arm-smmu probe power ordering")

    old = (
        '\tif (pm_runtime_enabled(smmu->dev))\n'
        '\t\tpm_runtime_force_suspend(smmu->dev);\n'
        '\telse\n'
        '\t\tclk_bulk_disable(smmu->num_clks, smmu->clks);\n\n'
        '\tclk_bulk_unprepare(smmu->num_clks, smmu->clks);\n'
    )
    new = (
        '\tif (pm_runtime_enabled(smmu->dev))\n'
        '\t\tpm_runtime_force_suspend(smmu->dev);\n'
        '\telse {\n'
        '\t\tclk_bulk_disable(smmu->num_clks, smmu->clks);\n'
        '\t\ta52_arm_smmu_disable_gdscs(smmu);\n'
        '\t}\n\n'
        '\tclk_bulk_unprepare(smmu->num_clks, smmu->clks);\n'
    )
    text = one(text, old, new, "arm-smmu remove regulator symmetry")

    old = (
        '\tret = clk_bulk_enable(smmu->num_clks, smmu->clks);\n'
        '\tif (ret)\n'
        '\t\treturn ret;\n\n'
        '\tarm_smmu_device_reset(smmu);\n'
    )
    new = (
        '\tret = a52_arm_smmu_enable_gdscs(smmu);\n'
        '\tif (ret)\n'
        '\t\treturn ret;\n\n'
        '\tret = clk_bulk_enable(smmu->num_clks, smmu->clks);\n'
        '\tif (ret) {\n'
        '\t\ta52_arm_smmu_disable_gdscs(smmu);\n'
        '\t\treturn ret;\n'
        '\t}\n\n'
        '\tarm_smmu_device_reset(smmu);\n'
    )
    text = one(text, old, new, "arm-smmu runtime resume ordering")

    old = (
        '\tclk_bulk_disable(smmu->num_clks, smmu->clks);\n\n'
        '\treturn 0;\n'
    )
    new = (
        '\tclk_bulk_disable(smmu->num_clks, smmu->clks);\n'
        '\ta52_arm_smmu_disable_gdscs(smmu);\n\n'
        '\treturn 0;\n'
    )
    text = one(text, old, new, "arm-smmu runtime suspend ordering")

    validate_source(text)
    return text


def validate_source(text: str) -> None:
    for token in (
        MARKER, PHASE249, 'qcom,regulator-names', 'devm_regulator_bulk_get',
        'regulator_bulk_enable', 'regulator_bulk_disable',
        'K250 S gdscget rc=%d n=%d', 'K250 S regon rc=%d n=%d',
        'K250 S clkon rc=%d', 'K249 S clkon rc=%d',
    ):
        if token not in text:
            raise RuntimeError(f"Phase250 arm-smmu missing {token}")
    regon = text.index('K250 S regon rc=%d n=%d')
    clkon = text.index('K250 S clkon rc=%d')
    if regon >= clkon:
        raise RuntimeError("Phase250 regulator marker must precede clock marker")


def self_test() -> int:
    sample_h = '\tstruct clk_bulk_data\t\t*clks;\n\tint\t\t\t\tnum_clks;\n'
    h = patch_header(sample_h)
    assert 'a52_gdscs' in h and MARKER in h
    assert 'qcom,regulator-names' in HELPERS
    assert MARKER in HELPERS
    assert HELPERS.index('regulator_bulk_enable') < HELPERS.index('regulator_bulk_disable')
    print("Phase 250 self-test: PASS (DT regulator -> clock contract; no DT/SID rewrite)")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    root = locate_root()
    paths = (root / RECORDER, root / ARM_SMMU_H, root / ARM_SMMU_C)
    for p in paths:
        if not p.is_file():
            raise RuntimeError(f"missing Phase250 source: {p}")
    rec = patch_recorder(paths[0].read_text(encoding="utf-8"))
    hdr = patch_header(paths[1].read_text(encoding="utf-8"))
    src = patch_source(paths[2].read_text(encoding="utf-8"))
    paths[0].write_text(rec, encoding="utf-8")
    paths[1].write_text(hdr, encoding="utf-8")
    paths[2].write_text(src, encoding="utf-8")
    print("Phase 250 GPU SMMU downstream power contract applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
