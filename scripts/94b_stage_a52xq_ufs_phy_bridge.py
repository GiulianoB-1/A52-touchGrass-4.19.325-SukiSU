#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path


# Replay the complete Run 34 staging implementation, then bridge Samsung's
# childless qcom,ufs-phy-qmp-v3 node to one generic PHY instance.
RUN34_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "b02b0dcc963b57b294a8c0a6278de31eaf786f25/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"

A52_FLAT_HELPER = r'''/* Samsung's downstream UFS QMP v3 node is a flat #phy-cells=0
 * provider.  Its single register region contains COM, both TX/RX lanes and
 * PCS at the original downstream offsets rather than child reg resources.
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

	a52_persistent_diag_mark("A52PHY copy=1 FLAT_CREATE_BEGIN dev=%s base=%p\n",
		dev_name(dev), base);
	a52_persistent_diag_mark("A52PHY copy=2 FLAT_CREATE_BEGIN dev=%s base=%p\n",
		dev_name(dev), base);
	a52_persistent_diag_mark("A52PHY copy=3 FLAT_CREATE_BEGIN dev=%s base=%p\n",
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

	a52_persistent_diag_mark("A52PHY copy=1 FLAT_LAYOUT tx=%p rx=%p tx2=%p rx2=%p pcs=%p\n",
		qphy->tx, qphy->rx, qphy->tx2, qphy->rx2, qphy->pcs);
	a52_persistent_diag_mark("A52PHY copy=2 FLAT_LAYOUT tx=%p rx=%p tx2=%p rx2=%p pcs=%p\n",
		qphy->tx, qphy->rx, qphy->tx2, qphy->rx2, qphy->pcs);
	a52_persistent_diag_mark("A52PHY copy=3 FLAT_LAYOUT tx=%p rx=%p tx2=%p rx2=%p pcs=%p\n",
		qphy->tx, qphy->rx, qphy->tx2, qphy->rx2, qphy->pcs);

	generic_phy = devm_phy_create(dev, dev->of_node, &qcom_qmp_pcie_ufs_ops);
	if (IS_ERR(generic_phy)) {
		ret = PTR_ERR(generic_phy);
		dev_err(dev, "failed to create flat A52 UFS PHY: %d\n", ret);
		return ret;
	}

	qphy->phy = generic_phy;
	qphy->index = 0;
	qphy->qmp = qmp;
	qmp->phys[0] = qphy;
	phy_set_drvdata(generic_phy, qphy);

	a52_persistent_diag_mark("A52PHY copy=1 FLAT_CREATE_END ret=0 phys=1 phy=%p\n",
		generic_phy);
	a52_persistent_diag_mark("A52PHY copy=2 FLAT_CREATE_END ret=0 phys=1 phy=%p\n",
		generic_phy);
	a52_persistent_diag_mark("A52PHY copy=3 FLAT_CREATE_END ret=0 phys=1 phy=%p\n",
		generic_phy);
	return 0;
}

'''


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run34_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run34-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run34.py"
        request = urllib.request.Request(
            RUN34_STAGE_URL, headers={"User-Agent": "a52-stage94b-run35-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())

        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run(
            [sys.executable, str(previous), *sys.argv[1:]],
            check=True,
            env=env,
        )


def function_span(text: str, anchor: str, label: str) -> tuple[int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"{label}: function anchor missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"{label}: opening brace missing")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"{label}: closing brace missing")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def bridge_flat_legacy_phy(gki: Path, output: Path) -> dict:
    source_path = gki / "drivers/phy/qualcomm/phy-qcom-qmp.c"
    if not source_path.is_file():
        raise SystemExit("Run 34 patched QMP source is missing")

    text = source_path.read_text(encoding="utf-8")
    if "qcom_qmp_phy_create_a52_flat" in text:
        raise SystemExit("flat Samsung UFS PHY bridge already present")
    if 'compatible = "qcom,ufs-phy-qmp-v3"' not in text:
        raise SystemExit("legacy Samsung UFS PHY compatible is missing")
    if "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg" not in text:
        raise SystemExit("Run 34 Lagoon UFS PHY config is missing")

    _, create_end = function_span(
        text,
        "int qcom_qmp_phy_create(struct device *dev, struct device_node *np, int id,",
        "QMP PHY create",
    )
    text = text[:create_end] + "\n\n" + A52_FLAT_HELPER + text[create_end:]

    text = replace_once(
        text,
        "\tif (cfg->no_pcs_sw_reset) {\n",
        "\tif (cfg->no_pcs_sw_reset && cfg != &a52_lagoon_ufsphy_cfg) {\n",
        "skip unavailable UFS reset for flat Lagoon PHY",
    )
    text = replace_once(
        text,
        "\treset_control_assert(qmp->ufs_reset);\n\tif (cfg->has_phy_com_ctrl) {\n",
        "\tif (cfg != &a52_lagoon_ufsphy_cfg)\n"
        "\t\treset_control_assert(qmp->ufs_reset);\n"
        "\tif (cfg->has_phy_com_ctrl) {\n",
        "skip flat Lagoon UFS reset on common exit",
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
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=1 FLAT_RESET_BYPASS stage=power_on\\n\");\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=2 FLAT_RESET_BYPASS stage=power_on\\n\");\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=3 FLAT_RESET_BYPASS stage=power_on\\n\");\n"
        "\t}\n",
        "skip flat Lagoon UFS reset deassert",
    )

    text = replace_once(
        text,
        "\tint num, id, expected_phys;\n\tint ret;\n",
        "\tint num, id, expected_phys;\n\tbool flat_legacy;\n\tint ret;\n",
        "declare flat legacy probe selector",
    )

    child_marker = (
        "\ta52_persistent_diag_mark(\"A52PHY copy=3 STAGE children num=%d expected=%d\\n\", num, expected_phys);\n"
    )
    child_insert = child_marker + (
        "\tflat_legacy = !num && cfg == &a52_lagoon_ufsphy_cfg;\n"
        "\tif (flat_legacy) {\n"
        "\t\tnum = 1;\n"
        "\t\texpected_phys = 1;\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=1 FLAT_DETECTED children=0 phys=1\\n\");\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=2 FLAT_DETECTED children=0 phys=1\\n\");\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=3 FLAT_DETECTED children=0 phys=1\\n\");\n"
        "\t}\n"
    )
    text = replace_once(
        text,
        child_marker,
        child_insert,
        "detect childless Lagoon PHY",
    )

    runtime_anchor = "\tpm_runtime_forbid(dev);\n\n\tid = 0;\n"
    runtime_replacement = (
        "\tpm_runtime_forbid(dev);\n\n"
        "\tif (flat_legacy) {\n"
        "\t\tret = qcom_qmp_phy_create_a52_flat(dev, serdes, cfg);\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=1 STAGE flat_create ret=%d\\n\", ret);\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=2 STAGE flat_create ret=%d\\n\", ret);\n"
        "\t\ta52_persistent_diag_mark(\"A52PHY copy=3 STAGE flat_create ret=%d\\n\", ret);\n"
        "\t\tif (ret) {\n"
        "\t\t\tpm_runtime_disable(dev);\n"
        "\t\t\treturn ret;\n"
        "\t\t}\n"
        "\t\tgoto register_provider;\n"
        "\t}\n\n"
        "\tid = 0;\n"
    )
    text = replace_once(
        text,
        runtime_anchor,
        runtime_replacement,
        "create one flat Lagoon generic PHY",
    )

    provider_anchor = "\tphy_provider = devm_of_phy_provider_register(dev, of_phy_simple_xlate);\n"
    text = replace_once(
        text,
        provider_anchor,
        "register_provider:\n" + provider_anchor,
        "flat provider registration label",
    )

    checks = {
        "flat_helper_present": "qcom_qmp_phy_create_a52_flat" in text,
        "flat_one_phy": (
            "FLAT_DETECTED children=0 phys=1" in text
            and "qmp->phys[0] = qphy;" in text
        ),
        "flat_parent_phy_node": (
            "devm_phy_create(dev, dev->of_node, &qcom_qmp_pcie_ufs_ops)" in text
        ),
        "flat_offsets_exact": all(
            token in text
            for token in (
                "A52_LAGOON_QMP_TX0_OFFSET\t0x400",
                "A52_LAGOON_QMP_RX0_OFFSET\t0x600",
                "A52_LAGOON_QMP_TX1_OFFSET\t0x800",
                "A52_LAGOON_QMP_RX1_OFFSET\t0xa00",
                "A52_LAGOON_QMP_PCS_OFFSET\t0xc00",
            )
        ),
        "legacy_reset_bypass": (
            "cfg != &a52_lagoon_ufsphy_cfg" in text
            and "FLAT_RESET_BYPASS stage=power_on" in text
        ),
        "normal_child_path_retained": (
            "for_each_available_child_of_node(dev->of_node, child)" in text
            and "qcom_qmp_phy_create(dev, child, id, serdes, cfg)" in text
        ),
        "legacy_clock_bridge_retained": (
            '"ref_clk_src", "ref_clk", "ref_aux_clk"' in text
            and "a52_lagoon_ufsphy_cfg" in text
        ),
        "provider_simple_xlate_retained": (
            "devm_of_phy_provider_register(dev, of_phy_simple_xlate)" in text
        ),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit("flat Samsung UFS PHY bridge audit failed: " + ", ".join(failed))

    source_path.write_text(text, encoding="utf-8")
    (output / "patched-phy-qcom-qmp-run35.c").write_text(text, encoding="utf-8")

    return {
        "status": "bridged",
        "reason": (
            "Run 34 bound the QMP platform driver and all three clocks, but the "
            "legacy Samsung PHY node has zero child nodes. The upstream QMP probe "
            "therefore registered a provider with zero generic PHY instances and "
            "the UFS host continued to return -EPROBE_DEFER."
        ),
        "scope": (
            "qcom,ufs-phy-qmp-v3 with zero child nodes only; all native upstream "
            "child-node QMP compatibles retain their existing creation path"
        ),
        "generic_phys": 1,
        "register_offsets": {
            "serdes": "0x000",
            "tx0": "0x400",
            "rx0": "0x600",
            "tx1": "0x800",
            "rx1": "0xa00",
            "pcs": "0xc00",
            "pcs_ready_absolute": "0xd60",
        },
        "checks": checks,
    }


def merge_report(output: Path, flat_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("Run 34 UFS bridge stage report is missing")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = flat_report.get("checks", {})
    report.setdefault("checks", {})["flat_legacy_ufs_phy_created"] = bool(
        checks and all(checks.values())
    )
    report["flat_legacy_ufs_phy_bridge"] = flat_report
    if not report["checks"]["flat_legacy_ufs_phy_created"]:
        raise SystemExit("flat legacy UFS PHY bridge audit failed")

    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run34_stage()
    flat_report = bridge_flat_legacy_phy(gki, output)
    merge_report(output, flat_report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run35-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
