#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def triplet(fmt: str, args: str, indent: str, prefix: str = "A52UFS") -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("{prefix} copy={copy} {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record the live UFS device-tree state and Qualcomm UFS probe result "
            "after Android init reported that all required partitions were missing."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    qcom_path = gki / "drivers/scsi/ufs/ufs-qcom.c"
    makefile_path = gki / "drivers/scsi/ufs/Makefile"
    live_path = gki / "drivers/scsi/ufs/a52-ufs-live-trace.c"
    if not qcom_path.is_file() or not makefile_path.is_file():
        raise SystemExit("pinned UFS Qualcomm source or Makefile is missing")

    declaration = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    qcom = qcom_path.read_text(encoding="utf-8")

    if declaration not in qcom:
        include_anchors = (
            '#include "ufs_quirks.h"\n',
            '#include "ufshci.h"\n',
            '#include "ufs-qcom.h"\n',
        )
        for anchor in include_anchors:
            if anchor in qcom:
                qcom = replace_once(
                    qcom,
                    anchor,
                    anchor + declaration,
                    "declare persistent diagnostic helper in ufs-qcom.c",
                )
                break
        else:
            raise SystemExit("no verified include anchor found in ufs-qcom.c")

    dev_anchor = "\tstruct device *dev = &pdev->dev;\n"
    declarations = (
        dev_anchor
        + '\tconst char *a52_status = "<absent>";\n'
        + '\tconst char *a52_compat = "<absent>";\n'
        + "\tint a52_available = 0;\n"
    )
    qcom = replace_once(
        qcom,
        dev_anchor,
        declarations,
        "add UFS probe diagnostic locals",
    )

    probe_anchor = "\t/* Perform generic probe */\n"
    begin_markers = triplet(
        "PROBE_BEGIN dev=%s node=%s avail=%d status=%s compat=%s",
        "dev_name(dev), dev->of_node ? dev->of_node->full_name : \"<none>\", "
        "a52_available, a52_status, a52_compat",
        "\t",
    )
    begin_block = (
        "\tif (dev->of_node) {\n"
        "\t\ta52_available = of_device_is_available(dev->of_node);\n"
        "\t\tof_property_read_string(dev->of_node, \"status\", &a52_status);\n"
        "\t\tof_property_read_string(dev->of_node, \"compatible\", &a52_compat);\n"
        "\t}\n"
        + begin_markers
        + "\n"
        + probe_anchor
    )
    qcom = replace_once(
        qcom,
        probe_anchor,
        begin_block,
        "instrument UFS Qualcomm probe entry",
    )

    init_anchor = "\terr = ufshcd_pltfrm_init(pdev, &ufs_hba_qcom_vops);\n"
    end_markers = triplet(
        "PROBE_END dev=%s ret=%d drvdata=%s",
        "dev_name(dev), err, platform_get_drvdata(pdev) ? \"present\" : \"none\"",
        "\t",
    )
    qcom = replace_once(
        qcom,
        init_anchor,
        init_anchor + end_markers,
        "instrument UFS Qualcomm probe result",
    )

    live_source = r'''// SPDX-License-Identifier: GPL-2.0-only
#include <linux/device.h>
#include <linux/init.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>

extern void a52_persistent_diag_mark(const char *fmt, ...);

static void __init a52_ufs_trace_node(const char *tag, const char *path,
                                     const char *fallback_compatible)
{
    struct device_node *np;
    struct platform_device *pdev;
    const char *status = "<absent>";
    const char *compatible = "<absent>";
    const char *driver = "<unbound>";
    int available;

    np = of_find_node_by_path(path);
    if (!np && fallback_compatible)
        np = of_find_compatible_node(NULL, NULL, fallback_compatible);

    if (!np) {
        a52_persistent_diag_mark("A52UFS copy=1 LIVE tag=%s node=missing path=%s\n", tag, path);
        a52_persistent_diag_mark("A52UFS copy=2 LIVE tag=%s node=missing path=%s\n", tag, path);
        a52_persistent_diag_mark("A52UFS copy=3 LIVE tag=%s node=missing path=%s\n", tag, path);
        return;
    }

    available = of_device_is_available(np);
    of_property_read_string(np, "status", &status);
    of_property_read_string(np, "compatible", &compatible);
    pdev = of_find_device_by_node(np);
    if (pdev && pdev->dev.driver)
        driver = pdev->dev.driver->name;

    a52_persistent_diag_mark("A52UFS copy=1 LIVE tag=%s node=%s avail=%d status=%s compat=%s pdev=%s driver=%s\n",
                             tag, np->full_name, available, status, compatible,
                             pdev ? "present" : "none", driver);
    a52_persistent_diag_mark("A52UFS copy=2 LIVE tag=%s node=%s avail=%d status=%s compat=%s pdev=%s driver=%s\n",
                             tag, np->full_name, available, status, compatible,
                             pdev ? "present" : "none", driver);
    a52_persistent_diag_mark("A52UFS copy=3 LIVE tag=%s node=%s avail=%d status=%s compat=%s pdev=%s driver=%s\n",
                             tag, np->full_name, available, status, compatible,
                             pdev ? "present" : "none", driver);

    if (pdev)
        put_device(&pdev->dev);
    of_node_put(np);
}

static int __init a52_ufs_live_trace_init(void)
{
    a52_persistent_diag_mark("A52UFS copy=1 LIVE_SCAN begin\n");
    a52_persistent_diag_mark("A52UFS copy=2 LIVE_SCAN begin\n");
    a52_persistent_diag_mark("A52UFS copy=3 LIVE_SCAN begin\n");

    a52_ufs_trace_node("host", "/soc/ufshc@1d84000", "qcom,ufshc");
    a52_ufs_trace_node("phy", "/soc/ufsphy_mem@1d87000", NULL);

    a52_persistent_diag_mark("A52UFS copy=1 LIVE_SCAN end\n");
    a52_persistent_diag_mark("A52UFS copy=2 LIVE_SCAN end\n");
    a52_persistent_diag_mark("A52UFS copy=3 LIVE_SCAN end\n");
    return 0;
}
late_initcall_sync(a52_ufs_live_trace_init);
'''

    makefile = makefile_path.read_text(encoding="utf-8")
    make_anchor = "obj-$(CONFIG_SCSI_UFS_QCOM) += ufs_qcom.o\n"
    make_line = "obj-$(CONFIG_SCSI_UFS_QCOM) += a52-ufs-live-trace.o\n"
    if make_line not in makefile:
        makefile = replace_once(
            makefile,
            make_anchor,
            make_anchor + make_line,
            "build live UFS trace object",
        )

    checks = {
        "helper_declared": declaration in qcom,
        "probe_begin_triplet": all(
            qcom.count(f"A52UFS copy={copy} PROBE_BEGIN") == 1 for copy in (1, 2, 3)
        ),
        "probe_end_triplet": all(
            qcom.count(f"A52UFS copy={copy} PROBE_END") == 1 for copy in (1, 2, 3)
        ),
        "generic_probe_preserved": init_anchor in qcom,
        "live_host_path": "/soc/ufshc@1d84000" in live_source,
        "live_phy_path": "/soc/ufsphy_mem@1d87000" in live_source,
        "live_triplets": all(
            f"A52UFS copy={copy} LIVE" in live_source for copy in (1, 2, 3)
        ),
        "makefile_object": make_line in makefile,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("UFS live/probe staging audit failed: " + ", ".join(failed))

    qcom_path.write_text(qcom, encoding="utf-8")
    live_path.write_text(live_source, encoding="utf-8")
    makefile_path.write_text(makefile, encoding="utf-8")

    (output / "patched-ufs-qcom.c").write_text(qcom, encoding="utf-8")
    (output / "a52-ufs-live-trace.c").write_text(live_source, encoding="utf-8")
    (output / "patched-ufs-Makefile").write_text(makefile, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": (
                    "distinguish disabled/missing live UFS DT nodes from a Qualcomm "
                    "UFS platform probe failure after Android init exit 127"
                ),
                "trace_points": [
                    "late-init live UFS host node state",
                    "late-init live UFS PHY node state",
                    "Qualcomm UFS probe entry",
                    "ufshcd_pltfrm_init return code",
                ],
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
