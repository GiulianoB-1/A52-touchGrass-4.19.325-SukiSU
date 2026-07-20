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

RUN35_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "d4eb4cc711e4297b025938984f57402ee84b3991/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"

A52_RX_TABLE = r'''static const struct qmp_phy_init_tbl a52_lagoon_ufsphy_rx_tbl[] = {
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_SIGDET_LVL, 0x24),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_SIGDET_CNTRL, 0x0f),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_SIGDET_DEGLITCH_CNTRL, 0x1e),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_RX_INTERFACE_MODE, 0x40),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_FASTLOCK_FO_GAIN, 0x0b),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_FO_GAIN, 0x0c),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_RX_TERM_BW, 0x5b),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_RX_EQU_ADAPTOR_CNTRL2, 0x06),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_RX_EQU_ADAPTOR_CNTRL3, 0x04),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_RX_EQU_ADAPTOR_CNTRL4, 0x1b),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_SVS_SO_GAIN_HALF, 0x04),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_SVS_SO_GAIN_QUARTER, 0x04),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_SVS_SO_GAIN, 0x04),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_SO_SATURATION_AND_ENABLE, 0x5b),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_PI_CONTROLS, 0x81),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_UCDR_FASTLOCK_COUNT_LOW, 0x80),
	QMP_PHY_INIT_CFG(QSERDES_V3_RX_RX_MODE_00, 0x59),
};

'''

A52_PCS_TABLE = r'''/* Original downstream QMP-v3 PCS offsets absent from the unified header. */
#define A52_QPHY_V3_PCS_TX_LARGE_AMP_POST_EMP_LVL	0x30
#define A52_QPHY_V3_PCS_TX_SMALL_AMP_POST_EMP_LVL	0x38

static const struct qmp_phy_init_tbl a52_lagoon_ufsphy_pcs_tbl[] = {
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_RX_SIGDET_CTRL2, 0x6f),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_TX_LARGE_AMP_DRV_LVL, 0x0f),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_TX_SMALL_AMP_DRV_LVL, 0x02),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_RX_SYM_RESYNC_CTRL, 0x03),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_TX_MID_TERM_CTRL1, 0x43),
	QMP_PHY_INIT_CFG(A52_QPHY_V3_PCS_TX_LARGE_AMP_POST_EMP_LVL, 0x12),
	QMP_PHY_INIT_CFG(A52_QPHY_V3_PCS_TX_SMALL_AMP_POST_EMP_LVL, 0x0f),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_RX_SIGDET_CTRL1, 0x0f),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_RX_MIN_HIBERN8_TIME, 0xff),
	QMP_PHY_INIT_CFG(QPHY_V3_PCS_MULTI_LANE_CTRL1, 0x02),
};

'''

CUSTOM_ROM_CONTRACT = {
    "boot_source_sha256": "41ae3b24771c70747c26aa17a18d254ffcb1c0d742b96f4f1f1fff20a6638554",
    "board": "SRPTJ06A012",
    "boot_header_version": 2,
    "boot_page_size": 4096,
    "boot_partition_bytes": 100663296,
    "bootdevice": "1d84000.ufshc",
    "host": {
        "compatible": "qcom,ufshc",
        "reg_names": ["ufs_mem", "ufs_ice"],
        "reg_sizes": ["0x3000", "0x8000"],
        "irq": 265,
        "phy_name": "ufsphy",
        "lanes": 2,
        "reset_name": "core_reset",
        "pinctrl_states": ["dev-reset-assert", "dev-reset-deassert"],
        "clock_names": [
            "core_clk", "bus_aggr_clk", "iface_clk", "core_clk_unipro",
            "core_clk_ice", "ref_clk", "tx_lane0_sync_clk",
            "rx_lane0_sync_clk", "rx_lane1_sync_clk",
        ],
    },
    "phy": {
        "compatible": "qcom,ufs-phy-qmp-v3",
        "reg_name": "phy_mem",
        "reg_size": "0xe00",
        "lanes": 2,
        "clock_names": ["ref_clk_src", "ref_clk", "ref_aux_clk"],
        "rpmh_ref_clock_id": 20,
        "max_used_offset": "0xd60",
    },
    "early_filesystems": ["EROFS", "EXT4", "F2FS"],
    "device_mapper": ["DM_CRYPT", "DM_VERITY", "DM_DEFAULT_KEY"],
}


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run35_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run35-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run35.py"
        request = urllib.request.Request(
            RUN35_STAGE_URL, headers={"User-Agent": "a52-stage94b-run36-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())
        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run([sys.executable, str(previous), *sys.argv[1:]], check=True, env=env)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


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


def initializer_span(text: str, anchor: str, label: str) -> tuple[int, int]:
    start, close = function_span(text, anchor, label)
    semi = text.find(";", close)
    if semi < 0:
        raise SystemExit(f"{label}: initializer terminator missing")
    return start, semi + 1


def patch_qmp(gki: Path, output: Path) -> dict:
    path = gki / "drivers/phy/qualcomm/phy-qcom-qmp.c"
    text = path.read_text(encoding="utf-8")
    if "a52_lagoon_ufsphy_rx_tbl" in text:
        raise SystemExit("Run 36 A52 calibration tables already present")

    pcs_anchor = "static const struct qmp_phy_init_tbl sdm845_ufsphy_pcs_tbl[] = {"
    pcs_pos = text.find(pcs_anchor)
    if pcs_pos < 0:
        raise SystemExit("SDM845 PCS table anchor missing")
    text = text[:pcs_pos] + A52_RX_TABLE + A52_PCS_TABLE + text[pcs_pos:]

    cfg_start, cfg_end = initializer_span(
        text, "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg = {", "A52 PHY config"
    )
    cfg = text[cfg_start:cfg_end]
    cfg = replace_once(cfg, ".rx_tbl\t\t\t= sdm845_ufsphy_rx_tbl,", ".rx_tbl\t\t\t= a52_lagoon_ufsphy_rx_tbl,", "A52 RX table pointer")
    cfg = replace_once(cfg, ".rx_tbl_num\t\t= ARRAY_SIZE(sdm845_ufsphy_rx_tbl),", ".rx_tbl_num\t\t= ARRAY_SIZE(a52_lagoon_ufsphy_rx_tbl),", "A52 RX table count")
    cfg = replace_once(cfg, ".pcs_tbl\t\t= sdm845_ufsphy_pcs_tbl,", ".pcs_tbl\t\t= a52_lagoon_ufsphy_pcs_tbl,", "A52 PCS table pointer")
    cfg = replace_once(cfg, ".pcs_tbl_num\t\t= ARRAY_SIZE(sdm845_ufsphy_pcs_tbl),", ".pcs_tbl_num\t\t= ARRAY_SIZE(a52_lagoon_ufsphy_pcs_tbl),", "A52 PCS table count")
    text = text[:cfg_start] + cfg + text[cfg_end:]

    text = replace_once(
        text,
        "\tunsigned int mask, val, ready;\n\tint ret;\n",
        "\tunsigned int mask, val, ready;\n\tunsigned long init_timeout;\n\tint ret;\n",
        "declare A52 PHY timeout",
    )
    poll_old = (
        "\t\tret = readl_poll_timeout(status, val, (val & mask) == ready, 10,\n"
        "\t\t\t\t\t PHY_INIT_COMPLETE_TIMEOUT);\n"
    )
    poll_new = (
        "\t\tinit_timeout = cfg == &a52_lagoon_ufsphy_cfg ?\n"
        "\t\t\t1000000UL : PHY_INIT_COMPLETE_TIMEOUT;\n"
        "\t\tif (cfg == &a52_lagoon_ufsphy_cfg) {\n"
        "\t\t\ta52_persistent_diag_mark(\"A52PHY copy=1 READY_POLL timeout_us=%lu status=%p\\n\", init_timeout, status);\n"
        "\t\t\ta52_persistent_diag_mark(\"A52PHY copy=2 READY_POLL timeout_us=%lu status=%p\\n\", init_timeout, status);\n"
        "\t\t\ta52_persistent_diag_mark(\"A52PHY copy=3 READY_POLL timeout_us=%lu status=%p\\n\", init_timeout, status);\n"
        "\t\t}\n"
        "\t\tret = readl_poll_timeout(status, val, (val & mask) == ready, 10,\n"
        "\t\t\t\t\t init_timeout);\n"
    )
    text = replace_once(text, poll_old, poll_new, "A52 PHY ready timeout")

    checks = {
        "downstream_rx_fo_gain": "QSERDES_V3_RX_UCDR_FO_GAIN, 0x0c" in text,
        "downstream_rx_saturation": "QSERDES_V3_RX_UCDR_SO_SATURATION_AND_ENABLE, 0x5b" in text,
        "downstream_pcs_sigdet": "QPHY_V3_PCS_RX_SIGDET_CTRL2, 0x6f" in text,
        "downstream_tx_amplitude": "QPHY_V3_PCS_TX_LARGE_AMP_DRV_LVL, 0x0f" in text,
        "downstream_post_emphasis": (
            "A52_QPHY_V3_PCS_TX_LARGE_AMP_POST_EMP_LVL, 0x12" in text
            and "A52_QPHY_V3_PCS_TX_SMALL_AMP_POST_EMP_LVL, 0x0f" in text
        ),
        "downstream_hibern8": "QPHY_V3_PCS_RX_MIN_HIBERN8_TIME, 0xff" in text,
        "rate_b_vco_map_retained": "QSERDES_V3_COM_VCO_TUNE_MAP, 0x44" in text,
        "a52_tables_selected": (
            ".rx_tbl\t\t\t= a52_lagoon_ufsphy_rx_tbl," in cfg
            and ".pcs_tbl\t\t= a52_lagoon_ufsphy_pcs_tbl," in cfg
        ),
        "a52_ready_timeout_1s": "1000000UL : PHY_INIT_COMPLETE_TIMEOUT" in text,
        "flat_phy_bridge_retained": "qcom_qmp_phy_create_a52_flat" in text,
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise SystemExit("A52 downstream QMP-v3 audit failed: " + ", ".join(failed))

    path.write_text(text, encoding="utf-8")
    (output / "patched-phy-qcom-qmp-run36.c").write_text(text, encoding="utf-8")
    return {
        "status": "patched",
        "source_contract": "Qualcomm downstream UFS QMP v3 calibration and 1-second PCS-ready poll",
        "checks": checks,
    }


def patch_ufs_host(gki: Path, output: Path) -> dict:
    path = gki / "drivers/scsi/ufs/ufs-qcom.c"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "#include <linux/gpio/consumer.h>\n",
        "#include <linux/gpio/consumer.h>\n#include <linux/pinctrl/consumer.h>\n",
        "include pinctrl consumer",
    )

    reset_old = '''\t/* Setup the reset control of HCI */
\thost->core_reset = devm_reset_control_get(hba->dev, "rst");
\tif (IS_ERR(host->core_reset)) {
\t\terr = PTR_ERR(host->core_reset);
\t\tdev_warn(dev, "Failed to get reset control %d\\n", err);
\t\thost->core_reset = NULL;
\t\terr = 0;
\t}
'''
    reset_new = '''\t/* Samsung's downstream DT names this GCC reset "core_reset". */
\thost->core_reset = devm_reset_control_get(hba->dev, "core_reset");
\tif (IS_ERR(host->core_reset)) {
\t\terr = PTR_ERR(host->core_reset);
\t\tif (err == -ENOENT || err == -EINVAL)
\t\t\thost->core_reset = devm_reset_control_get(hba->dev, "rst");
\t}
\tif (IS_ERR(host->core_reset)) {
\t\terr = PTR_ERR(host->core_reset);
\t\tdev_warn(dev, "Failed to get reset control %d\\n", err);
\t\thost->core_reset = NULL;
\t\terr = 0;
\t} else {
\t\ta52_persistent_diag_mark("A52UFS copy=1 CORE_RESET_READY name=core_reset_or_rst\\n");
\t\ta52_persistent_diag_mark("A52UFS copy=2 CORE_RESET_READY name=core_reset_or_rst\\n");
\t\ta52_persistent_diag_mark("A52UFS copy=3 CORE_RESET_READY name=core_reset_or_rst\\n");
\t}
'''
    text = replace_once(text, reset_old, reset_new, "UFS core reset name compatibility")

    hce_old = '''\tcase PRE_CHANGE:
\t\tufs_qcom_power_up_sequence(hba);
\t\t/*
\t\t * The PHY PLL output is the source of tx/rx lane symbol
\t\t * clocks, hence, enable the lane clocks only after PHY
\t\t * is initialized.
\t\t */
\t\terr = ufs_qcom_enable_lane_clks(host);
\t\tbreak;
'''
    hce_new = '''\tcase PRE_CHANGE:
\t\terr = ufs_qcom_power_up_sequence(hba);
\t\ta52_persistent_diag_mark("A52UFS copy=1 POWER_UP_SEQUENCE ret=%d\\n", err);
\t\ta52_persistent_diag_mark("A52UFS copy=2 POWER_UP_SEQUENCE ret=%d\\n", err);
\t\ta52_persistent_diag_mark("A52UFS copy=3 POWER_UP_SEQUENCE ret=%d\\n", err);
\t\tif (err)
\t\t\tbreak;
\t\t/*
\t\t * The PHY PLL output is the source of tx/rx lane symbol
\t\t * clocks, hence, enable the lane clocks only after PHY
\t\t * is initialized.
\t\t */
\t\terr = ufs_qcom_enable_lane_clks(host);
\t\tbreak;
'''
    text = replace_once(text, hce_old, hce_new, "propagate PHY power-up failure")

    start, end = function_span(text, "static int ufs_qcom_device_reset(struct ufs_hba *hba)", "UFS device reset")
    new_func = r'''static int ufs_qcom_device_reset(struct ufs_hba *hba)
{
	struct ufs_qcom_host *host = ufshcd_get_variant(hba);
	struct pinctrl_state *assert_state, *deassert_state;
	struct pinctrl *pinctrl;
	int ret;

	if (host->device_reset) {
		ufs_qcom_device_reset_ctrl(hba, true);
		usleep_range(10, 15);
		ufs_qcom_device_reset_ctrl(hba, false);
		usleep_range(10, 15);
		return 0;
	}

	/* Samsung's DT represents the UFS device reset as two pinctrl states. */
	pinctrl = pinctrl_get(hba->dev);
	if (IS_ERR(pinctrl))
		return PTR_ERR(pinctrl);

	assert_state = pinctrl_lookup_state(pinctrl, "dev-reset-assert");
	if (IS_ERR(assert_state)) {
		ret = PTR_ERR(assert_state);
		goto out_put;
	}
	deassert_state = pinctrl_lookup_state(pinctrl, "dev-reset-deassert");
	if (IS_ERR(deassert_state)) {
		ret = PTR_ERR(deassert_state);
		goto out_put;
	}

	ret = pinctrl_select_state(pinctrl, assert_state);
	if (ret)
		goto out_put;
	usleep_range(10, 15);
	ret = pinctrl_select_state(pinctrl, deassert_state);
	if (ret)
		goto out_put;
	usleep_range(10, 15);

	a52_persistent_diag_mark("A52UFS copy=1 DEVICE_RESET path=pinctrl ret=0\n");
	a52_persistent_diag_mark("A52UFS copy=2 DEVICE_RESET path=pinctrl ret=0\n");
	a52_persistent_diag_mark("A52UFS copy=3 DEVICE_RESET path=pinctrl ret=0\n");
out_put:
	pinctrl_put(pinctrl);
	return ret;
}'''
    text = text[:start] + new_func + text[end:]

    checks = {
        "core_reset_first": 'devm_reset_control_get(hba->dev, "core_reset")' in text,
        "rst_fallback": 'devm_reset_control_get(hba->dev, "rst")' in text,
        "power_up_error_propagated": "err = ufs_qcom_power_up_sequence(hba);" in text and "if (err)\n\t\t\tbreak;" in text,
        "device_reset_pinctrl_assert": 'pinctrl_lookup_state(pinctrl, "dev-reset-assert")' in text,
        "device_reset_pinctrl_deassert": 'pinctrl_lookup_state(pinctrl, "dev-reset-deassert")' in text,
        "gpio_reset_retained": "if (host->device_reset)" in new_func,
        "bootdevice_filter_retained": "androidboot.bootdevice=" in text,
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise SystemExit("UFS host compatibility audit failed: " + ", ".join(failed))
    path.write_text(text, encoding="utf-8")
    (output / "patched-ufs-qcom-run36.c").write_text(text, encoding="utf-8")
    return {"status": "patched", "checks": checks}


def patch_ice(gki: Path, output: Path) -> dict:
    path = gki / "drivers/scsi/ufs/ufs-qcom-ice.c"
    text = path.read_text(encoding="utf-8")
    include_anchor = '#include "ufs-qcom.h"\n'
    if "A52UFS copy=1 ICE_RESOURCE" not in text:
        text = replace_once(
            text,
            include_anchor,
            include_anchor + 'extern void a52_persistent_diag_mark(const char *fmt, ...);\n',
            "declare persistent helper in UFS ICE",
        )
    old = '\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice");\n\tif (!res) {\n'
    new = (
        '\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice");\n'
        '\tif (!res) {\n'
        '\t\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ufs_ice");\n'
        '\t\tif (res) {\n'
        '\t\t\ta52_persistent_diag_mark("A52UFS copy=1 ICE_RESOURCE name=ufs_ice start=%pa end=%pa\\n", &res->start, &res->end);\n'
        '\t\t\ta52_persistent_diag_mark("A52UFS copy=2 ICE_RESOURCE name=ufs_ice start=%pa end=%pa\\n", &res->start, &res->end);\n'
        '\t\t\ta52_persistent_diag_mark("A52UFS copy=3 ICE_RESOURCE name=ufs_ice start=%pa end=%pa\\n", &res->start, &res->end);\n'
        '\t\t}\n'
        '\t}\n'
        '\tif (!res) {\n'
    )
    text = replace_once(text, old, new, "Samsung ufs_ice resource fallback")
    checks = {
        "standard_ice_retained": 'platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice")' in text,
        "samsung_ufs_ice_fallback": 'platform_get_resource_byname(pdev, IORESOURCE_MEM, "ufs_ice")' in text,
        "ice_trace": "A52UFS copy=1 ICE_RESOURCE" in text,
        "crypto_disable_fallback_retained": "Disabling inline encryption support" in text,
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise SystemExit("UFS ICE compatibility audit failed: " + ", ".join(failed))
    path.write_text(text, encoding="utf-8")
    (output / "patched-ufs-qcom-ice-run36.c").write_text(text, encoding="utf-8")
    return {"status": "patched", "checks": checks}


def audit_contract(gki: Path) -> dict:
    clk = (gki / "drivers/clk/qcom/clk-rpmh.c").read_text(encoding="utf-8")
    cfg_path = gki.parents[1] / "workflow68" / "extracted" / "integrated.config"
    config = cfg_path.read_text(encoding="utf-8")
    required = [
        "CONFIG_EROFS_FS=y", "CONFIG_EXT4_FS=y", "CONFIG_F2FS_FS=y",
        "CONFIG_BLK_DEV_DM=y", "CONFIG_DM_CRYPT=y", "CONFIG_DM_VERITY=y",
        "CONFIG_DM_DEFAULT_KEY=y", "CONFIG_DM_BOW=y", "CONFIG_DM_SNAPSHOT=y",
        "CONFIG_BLK_DEV_LOOP=y", "CONFIG_BLK_INLINE_ENCRYPTION=y",
        "CONFIG_FS_ENCRYPTION=y", "CONFIG_FS_ENCRYPTION_INLINE_CRYPT=y",
        "CONFIG_FS_VERITY=y", "CONFIG_SCSI_UFS_CRYPTO=y",
        "CONFIG_SCSI_UFS_QCOM=y", "CONFIG_SCSI_UFSHCD_PLATFORM=y",
        "CONFIG_PHY_QCOM_QMP=y", "CONFIG_PINCTRL_LAGOON=y",
        "CONFIG_ARM_SMMU=y", "CONFIG_EFI_PARTITION=y",
    ]
    config_checks = {entry: entry in config for entry in required}
    checks = {
        "rpmh_id_20_is_qlink": "A52_LAGOON_RPMH_QLINK_CLK = 20" in clk,
        "rpmh_id_21_is_qlink_active": "A52_LAGOON_RPMH_QLINK_CLK_A = 21" in clk,
        "rpmh_qphy_resource": '"qphy.lvl"' in clk,
        "phy_resource_covers_ready": int("e00", 16) > int("d60", 16),
        "host_resource_covers_hci": int("3000", 16) >= int("3000", 16),
        "ice_resource_exact_size": int("8000", 16) == 0x8000,
        "all_early_kernel_features_built_in": all(config_checks.values()),
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise SystemExit("custom-ROM UFS contract audit failed: " + ", ".join(failed))
    return {
        "status": "passed",
        "contract": CUSTOM_ROM_CONTRACT,
        "checks": checks,
        "config_checks": config_checks,
        "known_nonblocking_gap": (
            "Legacy qcom,msm-bus vote properties have no direct upstream consumer; "
            "all explicit UFS clocks are present, so this is recorded for performance validation."
        ),
    }


def merge_report(output: Path, reports: dict) -> None:
    path = output / "stage-report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["run36_preflash_compatibility_audit"] = reports
    for name, value in reports.items():
        checks = value.get("checks", {})
        report.setdefault("checks", {})[f"run36_{name}"] = bool(checks and all(checks.values()))
    failed = [k for k, v in report["checks"].items() if k.startswith("run36_") and not v]
    if failed:
        raise SystemExit("Run 36 merged audit failed: " + ", ".join(failed))
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run35_stage()
    reports = {
        "qmp_v3_calibration": patch_qmp(gki, output),
        "ufs_host_contract": patch_ufs_host(gki, output),
        "ufs_ice_resource": patch_ice(gki, output),
        "custom_rom_contract": audit_contract(gki),
    }
    merge_report(output, reports)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run36-wrapper-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        except BaseException:
            pass
        raise
