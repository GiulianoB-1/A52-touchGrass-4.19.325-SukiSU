#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PHASE = "A52_PHASE11_DOWNSTREAM_CLK_SET_FLAGS"


def read(path: Path) -> str:
    return path.read_text(errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text)


def insert_once(text: str, marker: str, anchor: str, block: str) -> tuple[str, int]:
    if marker in text:
        return text, 0
    if text.count(anchor) != 1:
        raise SystemExit(
            f"expected one insertion anchor for {marker}, found {text.count(anchor)}"
        )
    return text.replace(anchor, block.rstrip() + "\n" + anchor, 1), 1


def insert_after_once(
    text: str, marker: str, anchor: str, block: str
) -> tuple[str, int]:
    if marker in text:
        return text, 0
    if text.count(anchor) != 1:
        raise SystemExit(
            f"expected one insertion anchor for {marker}, found {text.count(anchor)}"
        )
    return text.replace(anchor, anchor + "\n\n" + block.rstrip(), 1), 1


def patch_provider_header(gki: Path) -> dict[str, int]:
    path = gki / "include/linux/clk-provider.h"
    text = read(path)
    result = {"documentation": 0, "callback": 0}

    doc_marker = f"{PHASE}_PROVIDER_DOC"
    doc_block = f""" * {doc_marker}
 * @set_flags: Set custom hardware-specific flags for this clock. Returns 0
 *\t\ton success, a negative errno otherwise.
 *
"""
    text, result["documentation"] = insert_once(
        text,
        doc_marker,
        " * @pre_rate_change:",
        doc_block,
    )

    callback_marker = f"/* {PHASE}_PROVIDER_CALLBACK */"
    callback_block = f"""\t{callback_marker}
\tint\t\t(*set_flags)(struct clk_hw *hw, unsigned int flags);
"""
    text, result["callback"] = insert_once(
        text,
        callback_marker,
        "\tint\t\t(*pre_rate_change)(struct clk_hw *hw,",
        callback_block,
    )

    write(path, text)
    return result


def patch_public_header(gki: Path) -> dict[str, int]:
    path = gki / "include/linux/clk.h"
    text = read(path)
    result = {"declaration": 0, "fallback_stub": 0}

    decl_marker = f"/* {PHASE}_PUBLIC_DECLARATION */"
    declaration = f"""{decl_marker}
/**
 * clk_set_flags - set custom hardware-specific flags for a clock
 * @clk: clock source
 * @flags: provider-specific flag selector
 *
 * Returns 0 on success or a negative errno.
 */
int clk_set_flags(struct clk *clk, unsigned long flags);

"""
    text, result["declaration"] = insert_once(
        text,
        decl_marker,
        "/**\n * clk_save_context - save clock context for poweroff",
        declaration,
    )

    stub_marker = f"/* {PHASE}_PUBLIC_STUB */"
    stub = f"""{stub_marker}
static inline int clk_set_flags(struct clk *clk, unsigned long flags)
{{
\treturn 0;
}}

"""
    text, result["fallback_stub"] = insert_once(
        text,
        stub_marker,
        "static inline int clk_save_context(void)",
        stub,
    )

    write(path, text)
    return result


def patch_qcom_flag_header(gki: Path) -> int:
    path = gki / "include/linux/clk/qcom.h"
    marker = f"/* {PHASE}_QCOM_FLAG_HEADER */"
    if path.is_file():
        text = read(path)
        if marker in text:
            return 0
        raise SystemExit(
            "native include/linux/clk/qcom.h already exists without the Workflow 113 marker"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "/* SPDX-License-Identifier: GPL-2.0-only */\n"
        "/*\n"
        " * Copyright (c) 2016, 2020 The Linux Foundation. All rights reserved.\n"
        " */\n\n"
        "#ifndef __LINUX_CLK_QCOM_H_\n"
        "#define __LINUX_CLK_QCOM_H_\n\n"
        f"{marker}\n"
        "#include <linux/clk.h>\n\n"
        "enum branch_mem_flags {\n"
        "\tCLKFLAG_RETAIN_PERIPH,\n"
        "\tCLKFLAG_NORETAIN_PERIPH,\n"
        "\tCLKFLAG_RETAIN_MEM,\n"
        "\tCLKFLAG_NORETAIN_MEM,\n"
        "\tCLKFLAG_PERIPH_OFF_SET,\n"
        "\tCLKFLAG_PERIPH_OFF_CLEAR,\n"
        "};\n\n"
        "void qcom_clk_dump(struct clk *clk, bool calltrace);\n"
        "void qcom_clk_bulk_dump(int num_clks, struct clk_bulk_data *clks,\n"
        "\t\t\tbool calltrace);\n\n"
        "#endif  /* __LINUX_CLK_QCOM_H_ */\n"
    )
    return 1


def patch_clock_core(gki: Path) -> int:
    path = gki / "drivers/clk/clk.c"
    text = read(path)
    marker = f"/* {PHASE}_CORE */"
    block = f"""{marker}
/**
 * clk_set_flags - dispatch a provider-specific clock flag request
 * @clk: clock source
 * @flags: provider-specific flag selector
 *
 * This preserves the downstream Qualcomm clock API used by the A52 display
 * and KGSL drivers. The provider remains responsible for interpreting flags.
 */
int clk_set_flags(struct clk *clk, unsigned long flags)
{{
\tif (!clk)
\t\treturn 0;

\tif (!clk->core->ops->set_flags)
\t\treturn -EINVAL;

\treturn clk->core->ops->set_flags(clk->core->hw, flags);
}}
EXPORT_SYMBOL_GPL(clk_set_flags);

"""
    text, count = insert_after_once(
        text,
        marker,
        "EXPORT_SYMBOL_GPL(clk_is_match);",
        block,
    )
    write(path, text)
    return count


def find_ops_block(text: str, name: str) -> tuple[int, int]:
    start_token = f"const struct clk_ops {name} = {{"
    start = text.find(start_token)
    if start < 0:
        raise SystemExit(f"clock operation table not found: {name}")
    end = text.find("\n};", start)
    if end < 0:
        raise SystemExit(f"clock operation table has no closing brace: {name}")
    return start, end


def wire_ops_table(text: str, name: str) -> tuple[str, int]:
    marker = f"{PHASE}_{name.upper()}"
    if marker in text:
        return text, 0
    start, end = find_ops_block(text, name)
    block = text[start:end]
    if ".set_flags" in block:
        raise SystemExit(f"{name} already has an unmarked set_flags callback")
    insertion = f"\t/* {marker} */\n\t.set_flags = clk_branch_set_flags,\n"
    return text[:end] + "\n" + insertion.rstrip("\n") + text[end:], 1


def patch_qcom_branch(gki: Path) -> dict[str, object]:
    path = gki / "drivers/clk/qcom/clk-branch.c"
    text = read(path)
    result: dict[str, object] = {
        "qcom_flag_include": 0,
        "cbcr_helper": 0,
        "wired_tables": [],
    }

    include_marker = f"/* {PHASE}_QCOM_HEADER */"
    include_block = f"""{include_marker}
#include <linux/clk/qcom.h>
"""
    text, result["qcom_flag_include"] = insert_once(
        text,
        include_marker,
        "#include <linux/regmap.h>",
        include_block,
    )

    helper_marker = f"/* {PHASE}_QCOM_CBCR */"
    helper_block = f"""{helper_marker}
static int clk_cbcr_set_flags(struct regmap *regmap, unsigned int reg,
\t\t\t      unsigned long flags)
{{
\tu32 cbcr_val = 0;
\tu32 cbcr_mask;
\tint ret;

\tswitch (flags) {{
\tcase CLKFLAG_PERIPH_OFF_SET:
\t\tcbcr_val = cbcr_mask = BIT(12);
\t\tbreak;
\tcase CLKFLAG_PERIPH_OFF_CLEAR:
\t\tcbcr_mask = BIT(12);
\t\tbreak;
\tcase CLKFLAG_RETAIN_PERIPH:
\t\tcbcr_val = cbcr_mask = BIT(13);
\t\tbreak;
\tcase CLKFLAG_NORETAIN_PERIPH:
\t\tcbcr_mask = BIT(13);
\t\tbreak;
\tcase CLKFLAG_RETAIN_MEM:
\t\tcbcr_val = cbcr_mask = BIT(14);
\t\tbreak;
\tcase CLKFLAG_NORETAIN_MEM:
\t\tcbcr_mask = BIT(14);
\t\tbreak;
\tdefault:
\t\treturn -EINVAL;
\t}}

\tret = regmap_update_bits(regmap, reg, cbcr_mask, cbcr_val);
\tif (ret)
\t\treturn ret;

\t/* Ensure the retention request reaches the CBCR before returning. */
\tmb();
\tudelay(1);

\treturn 0;
}}

static int clk_branch_set_flags(struct clk_hw *hw, unsigned int flags)
{{
\tstruct clk_branch *br = to_clk_branch(hw);

\treturn clk_cbcr_set_flags(br->clkr.regmap, br->halt_reg, flags);
}}

"""
    text, result["cbcr_helper"] = insert_once(
        text,
        helper_marker,
        "static void clk_branch_disable(struct clk_hw *hw)",
        helper_block,
    )

    table_names = ["clk_branch_ops", "clk_branch2_ops"]
    if "const struct clk_ops clk_branch2_hw_ctl_ops = {" in text:
        table_names.append("clk_branch2_hw_ctl_ops")

    wired: list[str] = []
    for name in table_names:
        text, count = wire_ops_table(text, name)
        if count:
            wired.append(name)
    result["wired_tables"] = wired
    result["expected_tables"] = table_names

    write(path, text)
    return result


def validate(gki: Path) -> dict[str, bool]:
    provider = read(gki / "include/linux/clk-provider.h")
    public = read(gki / "include/linux/clk.h")
    qcom_header_path = gki / "include/linux/clk/qcom.h"
    qcom_header = read(qcom_header_path) if qcom_header_path.is_file() else ""
    core = read(gki / "drivers/clk/clk.c")
    branch = read(gki / "drivers/clk/qcom/clk-branch.c")

    table_names = ["clk_branch_ops", "clk_branch2_ops"]
    if "const struct clk_ops clk_branch2_hw_ctl_ops = {" in branch:
        table_names.append("clk_branch2_hw_ctl_ops")

    table_checks = {}
    for name in table_names:
        start, end = find_ops_block(branch, name)
        table_checks[name] = ".set_flags = clk_branch_set_flags" in branch[start:end]

    expected_enum = (
        "enum branch_mem_flags {\n"
        "\tCLKFLAG_RETAIN_PERIPH,\n"
        "\tCLKFLAG_NORETAIN_PERIPH,\n"
        "\tCLKFLAG_RETAIN_MEM,\n"
        "\tCLKFLAG_NORETAIN_MEM,\n"
        "\tCLKFLAG_PERIPH_OFF_SET,\n"
        "\tCLKFLAG_PERIPH_OFF_CLEAR,\n"
        "};"
    )

    return {
        "provider_callback": (
            "(*set_flags)(struct clk_hw *hw, unsigned int flags);" in provider
        ),
        "public_declaration": (
            "int clk_set_flags(struct clk *clk, unsigned long flags);" in public
        ),
        "public_fallback_stub": (
            "static inline int clk_set_flags(struct clk *clk, unsigned long flags)"
            in public
        ),
        "native_qcom_flag_header": qcom_header_path.is_file(),
        "exact_downstream_flag_order": expected_enum in qcom_header,
        "core_dispatch": (
            "return clk->core->ops->set_flags(clk->core->hw, flags);" in core
        ),
        "core_export": "EXPORT_SYMBOL_GPL(clk_set_flags);" in core,
        "qcom_flag_include": "#include <linux/clk/qcom.h>" in branch,
        "cbcr_bit12": branch.count("BIT(12)") >= 2,
        "cbcr_bit13": branch.count("BIT(13)") >= 2,
        "cbcr_bit14": branch.count("BIT(14)") >= 2,
        "all_six_downstream_flags": all(
            flag in branch
            for flag in (
                "CLKFLAG_PERIPH_OFF_SET",
                "CLKFLAG_PERIPH_OFF_CLEAR",
                "CLKFLAG_RETAIN_PERIPH",
                "CLKFLAG_NORETAIN_PERIPH",
                "CLKFLAG_RETAIN_MEM",
                "CLKFLAG_NORETAIN_MEM",
            )
        ),
        "regmap_update": (
            "regmap_update_bits(regmap, reg, cbcr_mask, cbcr_val)" in branch
        ),
        "ordered_write": "mb();" in branch and "udelay(1);" in branch,
        **{f"ops_{name}": passed for name, passed in table_checks.items()},
        "aon_not_broadened": not bool(
            re.search(
                r"const struct clk_ops clk_branch2_aon_ops = \{.*?\.set_flags",
                branch,
                re.S,
            )
        ),
        "simple_not_broadened": not bool(
            re.search(
                r"const struct clk_ops clk_branch_simple_ops = \{.*?\.set_flags",
                branch,
                re.S,
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "phase11-clk-set-flags-abi-staged",
        "flashable": False,
        "hardware_validated": False,
        "public_header": patch_public_header(gki),
        "provider_header": patch_provider_header(gki),
        "qcom_flag_header": {"created": patch_qcom_flag_header(gki)},
        "clock_core": {"dispatcher": patch_clock_core(gki)},
        "qcom_branch": patch_qcom_branch(gki),
        "semantic_source": "exact-touchgrass-common-clock-and-qcom-cbcr-behaviour",
        "cbcr_semantics": {
            "peripheral_off": "bit 12",
            "retain_peripheral": "bit 13",
            "retain_memory": "bit 14",
            "write_ordering": "regmap_update_bits then mb then udelay(1)",
        },
        "scope": [
            "public clk_set_flags consumer ABI",
            "exact downstream Qualcomm branch flag enum",
            "clk_ops provider callback",
            "common clock dispatcher and GPL export",
            "Qualcomm CBCR flag decoding",
            "clk_branch and clk_branch2 provider wiring",
        ],
        "explicitly_deferred": [
            "DRM struct and mode ABI",
            "IOMMU domain-attribute ABI",
            "QSEECom ION ownership barrier",
            "hardware validation and flashable packaging",
        ],
    }
    report["validation"] = validate(gki)

    failures = [
        name for name, passed in report["validation"].items() if not passed
    ]
    expected_first_application = {
        "public_header.declaration": report["public_header"]["declaration"] == 1,
        "public_header.fallback_stub": report["public_header"]["fallback_stub"] == 1,
        "provider_header.documentation": (
            report["provider_header"]["documentation"] == 1
        ),
        "provider_header.callback": report["provider_header"]["callback"] == 1,
        "qcom_flag_header.created": report["qcom_flag_header"]["created"] == 1,
        "clock_core.dispatcher": report["clock_core"]["dispatcher"] == 1,
        "qcom_branch.qcom_flag_include": (
            report["qcom_branch"]["qcom_flag_include"] == 1
        ),
        "qcom_branch.cbcr_helper": report["qcom_branch"]["cbcr_helper"] == 1,
        "qcom_branch.wired_tables": (
            report["qcom_branch"]["wired_tables"]
            == report["qcom_branch"]["expected_tables"]
        ),
    }
    failures.extend(
        name for name, passed in expected_first_application.items() if not passed
    )

    (output / "phase11-clk-set-flags-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    if failures:
        raise SystemExit(
            "Workflow 113 staging validation failed: " + ", ".join(failures)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
