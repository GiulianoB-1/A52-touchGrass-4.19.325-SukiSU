#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY = Path("drivers/phy/qualcomm/phy-qcom-qmp.c")
MARK = "A52_PHASE362_UFS_QMP_FLAT_BRIDGE_V1"

LEGACY_CLOCK_LIST = r'''static const char * const a52_lagoon_ufs_phy_clk_l[] = {
	"ref_clk_src", "ref_clk", "ref_aux_clk",
};

'''

FLAT_HELPER = r'''
/*
 * A52 / Lagoon downstream DT represents the two-lane QMP-v3 UFS PHY as one
 * flat #phy-cells=0 provider.  Upstream 5.10 expects child PHY nodes.
 * These offsets are the downstream QMP-v3 layout already used by this DT.
 */
#define A52_LAGOON_QMP_TX0_OFFSET	0x400
#define A52_LAGOON_QMP_RX0_OFFSET	0x600
#define A52_LAGOON_QMP_TX1_OFFSET	0x800
#define A52_LAGOON_QMP_RX1_OFFSET	0xa00
#define A52_LAGOON_QMP_PCS_OFFSET	0xc00

static int qcom_qmp_phy_create_a52_flat(struct device *dev,
				       void __iomem *base,
				       const struct qmp_phy_cfg *cfg)
{
	struct qcom_qmp *qmp = dev_get_drvdata(dev);
	struct phy *generic_phy;
	struct qmp_phy *qphy;
	int ret;

	a52_persistent_diag_mark(
		"A52GDSC P362 FLAT_CREATE_BEGIN dev=%s base=%p\n",
		dev_name(dev), base);

	qphy = devm_kzalloc(dev, sizeof(*qphy), GFP_KERNEL);
	if (!qphy)
		return -ENOMEM;

	qphy->cfg = cfg;
	qphy->serdes = base;
	qphy->tx = (u8 __iomem *)base + A52_LAGOON_QMP_TX0_OFFSET;
	qphy->rx = (u8 __iomem *)base + A52_LAGOON_QMP_RX0_OFFSET;
	qphy->tx2 = (u8 __iomem *)base + A52_LAGOON_QMP_TX1_OFFSET;
	qphy->rx2 = (u8 __iomem *)base + A52_LAGOON_QMP_RX1_OFFSET;
	qphy->pcs = (u8 __iomem *)base + A52_LAGOON_QMP_PCS_OFFSET;
	qphy->pcs_misc = NULL;
	qphy->pipe_clk = NULL;

	generic_phy = devm_phy_create(dev, dev->of_node, &qcom_qmp_pcie_ufs_ops);
	if (IS_ERR(generic_phy)) {
		ret = PTR_ERR(generic_phy);
		a52_persistent_diag_mark(
			"A52GDSC P362 FLAT_CREATE_END ret=%d\n", ret);
		return ret;
	}

	qphy->phy = generic_phy;
	qphy->index = 0;
	qphy->qmp = qmp;
	qmp->phys[0] = qphy;
	phy_set_drvdata(generic_phy, qphy);

	a52_persistent_diag_mark(
		"A52GDSC P362 FLAT_CREATE_END ret=0 phy=%p\n",
		generic_phy);
	return 0;
}

'''


def function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"Phase362 {label}: function anchor not found: {anchor}")
        brace = text.find("{", start)
        semi = text.find(";", start)
        if brace >= 0 and (semi < 0 or brace < semi):
            break
        search = start + len(anchor)
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase362 {label}: closing brace missing")


def initializer_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    start, close = function_bounds(text, anchor, label)
    semi = text.find(";", close)
    if semi < 0:
        raise SystemExit(f"Phase362 {label}: initializer terminator missing")
    return start, semi + 1


def line_span(text: str, statement: str, label: str,
              start: int = 0, end: int | None = None) -> tuple[int, int]:
    if end is None:
        end = len(text)
    wanted = statement.strip()
    matches: list[tuple[int, int]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() == wanted:
            matches.append((cursor, line_end))
        cursor = line_end
    if len(matches) != 1:
        raise SystemExit(
            f"Phase362 {label}: expected one '{wanted}', found {len(matches)}"
        )
    return matches[0]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Phase362 {label}: expected exactly one match, found {count}"
        )
    return text.replace(old, new, 1)


def ensure_helper_decl(text: str) -> str:
    decl = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if decl in text:
        return text
    for anchor in ("#include <linux/kernel.h>\n", "#include <linux/module.h>\n"):
        if anchor in text:
            return text.replace(anchor, anchor + decl, 1)
    raise SystemExit("Phase362: persistent-helper include anchor missing")


def ensure_a52_config(text: str) -> str:
    # The pinned 5.10 QMP driver expects upstream clock names "ref" and
    # "ref_aux". Samsung's shipped A52 DT exposes the downstream ABI names.
    if "a52_lagoon_ufs_phy_clk_l" not in text:
        anchor = '''static const char * const sdm845_ufs_phy_clk_l[] = {
	"ref", "ref_aux",
};

'''
        if anchor not in text:
            raise SystemExit("Phase362: SDM845 UFS clock-list anchor missing")
        text = text.replace(anchor, anchor + LEGACY_CLOCK_LIST, 1)

    if "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg" not in text:
        start, end = initializer_bounds(
            text,
            "static const struct qmp_phy_cfg sdm845_ufsphy_cfg = {",
            "SDM845 UFS config",
        )
        cfg = text[start:end]
        clone = cfg.replace(
            "static const struct qmp_phy_cfg sdm845_ufsphy_cfg = {",
            "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg = {",
            1,
        )
        clone = clone.replace(
            ".clk_list\t\t= sdm845_ufs_phy_clk_l,",
            ".clk_list\t\t= a52_lagoon_ufs_phy_clk_l,",
            1,
        )
        clone = clone.replace(
            ".num_clks\t\t= ARRAY_SIZE(sdm845_ufs_phy_clk_l),",
            ".num_clks\t\t= ARRAY_SIZE(a52_lagoon_ufs_phy_clk_l),",
            1,
        )
        if clone == cfg or "a52_lagoon_ufs_phy_clk_l" not in clone:
            raise SystemExit("Phase362: failed to clone A52 UFS PHY config")
        text = text[:end] + "\n\n" + clone + text[end:]

    # Add the legacy Samsung compatible if the modern baseline lost it.
    if '"qcom,ufs-phy-qmp-v3"' not in text:
        start, end = line_span(
            text,
            '.compatible = "qcom,sdm845-qmp-ufs-phy",',
            "SDM845 UFS match entry",
        )
        line = text[start:end]
        indent = line[: len(line) - len(line.lstrip())]
        bridge = (
            indent + '.compatible = "qcom,ufs-phy-qmp-v3",\n'
            + indent + ".data = &a52_lagoon_ufsphy_cfg,\n"
            + "\t}, {\n"
        )
        text = text[:start] + bridge + line + text[end:]
    else:
        # Historical Phase 34 initially mapped this compatible directly to the
        # SDM845 config. Upgrade it to the dedicated A52 clock-name config.
        legacy = (
            '\t\t.compatible = "qcom,ufs-phy-qmp-v3",\n'
            '\t\t.data = &sdm845_ufsphy_cfg,'
        )
        if legacy in text:
            text = text.replace(
                legacy,
                '\t\t.compatible = "qcom,ufs-phy-qmp-v3",\n'
                '\t\t.data = &a52_lagoon_ufsphy_cfg,',
                1,
            )

    return text


def ensure_flat_helper(text: str) -> str:
    if "qcom_qmp_phy_create_a52_flat" not in text:
        _, end = function_bounds(
            text,
            "int qcom_qmp_phy_create(struct device *dev, struct device_node *np, int id,",
            "QMP PHY create",
        )
        text = text[:end] + "\n" + FLAT_HELPER + text[end:]

    # The flat Samsung node does not expose the upstream UFS reset resource.
    if "FLAT_RESET_BYPASS stage=power_on" not in text:
        text = replace_once(
            text,
            "\tif (cfg->no_pcs_sw_reset) {\n",
            "\tif (cfg->no_pcs_sw_reset && cfg != &a52_lagoon_ufsphy_cfg) {\n",
            "flat reset prepare bypass",
        )
        text = replace_once(
            text,
            "\treset_control_assert(qmp->ufs_reset);\n\tif (cfg->has_phy_com_ctrl) {\n",
            "\tif (cfg != &a52_lagoon_ufsphy_cfg)\n"
            "\t\treset_control_assert(qmp->ufs_reset);\n"
            "\tif (cfg->has_phy_com_ctrl) {\n",
            "flat reset exit bypass",
        )
        text = replace_once(
            text,
            "\tret = reset_control_deassert(qmp->ufs_reset);\n"
            "\tif (ret)\n"
            "\t\tgoto err_pcs_ready;\n",
            "\tif (cfg != &a52_lagoon_ufsphy_cfg) {\n"
            "\t\tret = reset_control_deassert(qmp->ufs_reset);\n"
            "\t\tif (ret)\n"
            "\t\t\tgoto err_pcs_ready;\n"
            "\t} else {\n"
            "\t\ta52_persistent_diag_mark("
            "\"A52GDSC P362 FLAT_RESET_BYPASS stage=power_on\\n\");\n"
            "\t}\n",
            "flat reset power-on bypass",
        )

    probe_anchor = "static int qcom_qmp_phy_probe(struct platform_device *pdev)"
    pstart, pend = function_bounds(text, probe_anchor, "QMP probe")
    probe = text[pstart:pend]

    if "bool flat_legacy;" not in probe:
        probe = replace_once(
            probe,
            "\tint num, id, expected_phys;\n\tint ret;\n",
            "\tint num, id, expected_phys;\n\tbool flat_legacy;\n\tint ret;\n",
            "flat selector declaration",
        )

    if "P362 FLAT_DETECTED" not in probe:
        old = "\tnum = of_get_available_child_count(dev->of_node);\n"
        new = old + (
            "\tflat_legacy = !num && cfg == &a52_lagoon_ufsphy_cfg;\n"
            "\tif (flat_legacy) {\n"
            "\t\tnum = 1;\n"
            "\t\texpected_phys = 1;\n"
            "\t\ta52_persistent_diag_mark("
            "\"A52GDSC P362 FLAT_DETECTED children=0 phys=1\\n\");\n"
            "\t}\n"
        )
        probe = replace_once(probe, old, new, "flat childless detection")

    if "P362 FLAT_PROVIDER" not in probe:
        old = "\tpm_runtime_forbid(dev);\n\n\tid = 0;\n"
        new = (
            "\tpm_runtime_forbid(dev);\n\n"
            "\tif (flat_legacy) {\n"
            "\t\tret = qcom_qmp_phy_create_a52_flat(dev, serdes, cfg);\n"
            "\t\ta52_persistent_diag_mark("
            "\"A52GDSC P362 FLAT_PROVIDER create_ret=%d\\n\", ret);\n"
            "\t\tif (ret) {\n"
            "\t\t\tpm_runtime_disable(dev);\n"
            "\t\t\treturn ret;\n"
            "\t\t}\n"
            "\t\tgoto register_provider;\n"
            "\t}\n\n"
            "\tid = 0;\n"
        )
        probe = replace_once(probe, old, new, "flat generic-PHY creation")

        provider = (
            "\tphy_provider = devm_of_phy_provider_register(dev, "
            "of_phy_simple_xlate);\n"
        )
        probe = replace_once(
            probe,
            provider,
            "register_provider:\n" + provider,
            "flat provider label",
        )

    text = text[:pstart] + probe + text[pend:]
    return text


def validate(text: str) -> None:
    required = (
        MARK,
        '"ref_clk_src", "ref_clk", "ref_aux_clk"',
        "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg",
        '.compatible = "qcom,ufs-phy-qmp-v3",',
        ".data = &a52_lagoon_ufsphy_cfg,",
        "qcom_qmp_phy_create_a52_flat",
        "A52_LAGOON_QMP_TX0_OFFSET\t0x400",
        "A52_LAGOON_QMP_RX0_OFFSET\t0x600",
        "A52_LAGOON_QMP_TX1_OFFSET\t0x800",
        "A52_LAGOON_QMP_RX1_OFFSET\t0xa00",
        "A52_LAGOON_QMP_PCS_OFFSET\t0xc00",
        "P362 FLAT_DETECTED children=0 phys=1",
        "P362 FLAT_PROVIDER create_ret=%d",
        "FLAT_RESET_BYPASS stage=power_on",
        "devm_phy_create(dev, dev->of_node, &qcom_qmp_pcie_ufs_ops)",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase362 required token missing: " + token)

    if text.count('"qcom,ufs-phy-qmp-v3"') != 1:
        raise SystemExit("Phase362 expected exactly one legacy QMP compatible")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / PHY
    if not path.is_file():
        raise SystemExit("Phase362 QMP source missing")

    text = path.read_text(encoding="utf-8")
    if MARK in text:
        validate(text)
        print("Phase362 UFS QMP flat bridge audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase362 marker missing in check-only mode")

    text = ensure_helper_decl(text)
    text = ensure_a52_config(text)
    text = ensure_flat_helper(text)

    marker_anchor = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    text = text.replace(
        marker_anchor,
        marker_anchor + f'/* {MARK} */\n',
        1,
    )

    validate(text)
    path.write_text(text, encoding="utf-8")
    print("Phase362 UFS QMP flat bridge applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
