#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from a52_diag94_common import declare_helper, replace_first_supported, replace_once, triplet


def instrument_qmp_phy(phy: str) -> str:
    phy = declare_helper(
        phy,
        ("#include <linux/kernel.h>\n", "#include <linux/module.h>\n"),
        "declare persistent helper in phy-qcom-qmp.c",
    )

    compat_anchor = '\t\t.compatible = "qcom,sdm845-qmp-ufs-phy",\n'
    bridge = (
        '\t\t.compatible = "qcom,ufs-phy-qmp-v3",\n'
        "\t\t.data = &sdm845_ufsphy_cfg,\n"
        "\t}, {\n"
        + compat_anchor
    )
    phy = replace_once(
        phy,
        compat_anchor,
        bridge,
        "bridge Samsung downstream QMP-v3 UFS PHY compatible",
    )

    cfg_anchor = "\tqmp->cfg = of_device_get_match_data(dev);\n"
    phy = replace_once(
        phy,
        cfg_anchor,
        cfg_anchor
        + triplet(
            "MATCH dev=%s node=%s compat=%s cfg=%p children=%u",
            'dev_name(dev), dev->of_node ? dev->of_node->full_name : "<none>", '
            'dev->of_node ? of_get_property(dev->of_node, "compatible", NULL) : "<none>", '
            "qmp->cfg, dev->of_node ? of_get_available_child_count(dev->of_node) : 0",
            "\t",
            prefix="A52PHY",
        ),
        "instrument QMP PHY match-data selection",
    )
    return phy


def instrument_device_core(dd: str) -> str:
    dd = declare_helper(
        dd,
        ("#include <linux/device.h>\n", "#include <linux/module.h>\n"),
        "declare persistent helper in drivers/base/dd.c",
    )

    candidates = (
        "static void driver_deferred_probe_add(struct device *dev)\n",
        "void driver_deferred_probe_add(struct device *dev)\n",
    )
    storage_helper = r'''static bool a52_storage_probe_device(const struct device *dev)
{
	const char *name;

	if (!dev)
		return false;
	name = dev_name(dev);
	return name && (strstr(name, "ufs") || strstr(name, "scsi") ||
			strstr(name, "sdhci") || strstr(name, "1d84000") ||
			strstr(name, "1d87000"));
}

'''

    dd = replace_first_supported(
        dd,
        candidates,
        lambda anchor: storage_helper + anchor,
        "add storage probe filter before deferred-probe helper",
    )

    function_anchor = next(candidate for candidate in candidates if candidate in dd)
    body_anchor = function_anchor + "{\n"
    dd = replace_once(
        dd,
        body_anchor,
        body_anchor
        + "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "DEFER dev=%s driver=%s",
            'dev_name(dev), dev->driver ? dev->driver->name : "<none>"',
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n",
        "instrument storage deferred-probe insertion",
    )

    call_anchor = "\tret = call_driver_probe(dev, drv);\n"
    dd = replace_once(
        dd,
        call_anchor,
        "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "CALL dev=%s driver=%s",
            "dev_name(dev), drv->name",
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n"
        + call_anchor
        + "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "RET dev=%s driver=%s ret=%d",
            "dev_name(dev), drv->name, ret",
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n",
        "instrument storage driver callback",
    )

    reason_anchor = (
        "\tdev->p->deferred_probe_reason = kasprintf(GFP_KERNEL, \"%pV\", vaf);\n"
    )
    if reason_anchor in dd:
        dd = replace_once(
            dd,
            reason_anchor,
            reason_anchor
            + "\tif (a52_storage_probe_device(dev)) {\n"
            + triplet(
                "REASON dev=%s reason=%pV",
                "dev_name(dev), vaf",
                "\t\t",
                prefix="A52DEV",
            )
            + "\t}\n",
            "instrument storage deferred-probe reason",
        )
    return dd


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add the A52 downstream QMP-v3 UFS PHY compatibility bridge and "
            "device-core bind/defer tracing to the single-shot storage recorder."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    phy_path = gki / "drivers/phy/qualcomm/phy-qcom-qmp.c"
    dd_path = gki / "drivers/base/dd.c"
    if not phy_path.is_file() or not dd_path.is_file():
        raise SystemExit("pinned QMP PHY or device-core source is missing")

    phy = instrument_qmp_phy(phy_path.read_text(encoding="utf-8"))
    dd = instrument_device_core(dd_path.read_text(encoding="utf-8"))

    checks = {
        "phy_compatible_bridge": phy.count('"qcom,ufs-phy-qmp-v3"') == 1
        and "&sdm845_ufsphy_cfg" in phy,
        "phy_match_triplet": all(
            phy.count(f"A52PHY copy={copy} MATCH") == 1 for copy in (1, 2, 3)
        ),
        "device_call_triplet": all(
            dd.count(f"A52DEV copy={copy} CALL") == 1 for copy in (1, 2, 3)
        ),
        "device_return_triplet": all(
            dd.count(f"A52DEV copy={copy} RET") == 1 for copy in (1, 2, 3)
        ),
        "device_defer_triplet": all(
            dd.count(f"A52DEV copy={copy} DEFER") == 1 for copy in (1, 2, 3)
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("UFS PHY bridge staging audit failed: " + ", ".join(failed))

    phy_path.write_text(phy, encoding="utf-8")
    dd_path.write_text(dd, encoding="utf-8")
    (output / "patched-phy-qcom-qmp.c").write_text(phy, encoding="utf-8")
    (output / "patched-drivers-base-dd.c").write_text(dd, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": (
                    "bridge qcom,ufs-phy-qmp-v3 to upstream QMP-v3 UFS support "
                    "and capture driver calls, returns, deferrals and reasons"
                ),
                "compatibility_bridge": {
                    "from": "qcom,ufs-phy-qmp-v3",
                    "to_configuration": "sdm845_ufsphy_cfg",
                },
                "redundancy": 3,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
