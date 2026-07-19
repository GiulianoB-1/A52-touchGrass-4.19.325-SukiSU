#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from a52_diag94_common import instrument_platform_glue, instrument_qcom, replace_once
from a52_diag94_core_scoped import instrument_ufshcd
from a52_diag94_extra import build_live_source
from a52_diag94_printk import instrument_printk
from a52_diag94_sd_scoped import instrument_sd


def make_live_source_compile_time_safe(source: str) -> str:
    old = (
        "\tstatic const unsigned long delays[] = {\n"
        "\t\tmsecs_to_jiffies(1500),\n"
        "\t\tmsecs_to_jiffies(4000),\n"
        "\t};\n"
    )
    new = (
        "\tstatic const unsigned long delays[] = {\n"
        "\t\t(3 * HZ) / 2,\n"
        "\t\t4 * HZ,\n"
        "\t};\n"
    )
    if source.count(old) != 1:
        raise SystemExit(
            "make live recorder delays constant: expected one delay table"
        )
    return source.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a single-shot persistent UFS/storage flight recorder covering "
            "live DT, dependencies, platform/core probe stages, kernel logs, SCSI "
            "disk registration, block devices, Android init kmsg, and PID 1 exit."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    paths = {
        "qcom": gki / "drivers/scsi/ufs/ufs-qcom.c",
        "pltfrm": gki / "drivers/scsi/ufs/ufshcd-pltfrm.c",
        "core": gki / "drivers/scsi/ufs/ufshcd.c",
        "sd": gki / "drivers/scsi/sd.c",
        "printk": gki / "kernel/printk/printk.c",
        "makefile": gki / "drivers/scsi/ufs/Makefile",
        "live": gki / "drivers/scsi/ufs/a52-ufs-live-trace.c",
    }
    missing = [
        str(path)
        for key, path in paths.items()
        if key != "live" and not path.is_file()
    ]
    if missing:
        raise SystemExit("required pinned source files are missing: " + ", ".join(missing))

    qcom = instrument_qcom(paths["qcom"].read_text(encoding="utf-8"))
    pltfrm = instrument_platform_glue(paths["pltfrm"].read_text(encoding="utf-8"))
    core = instrument_ufshcd(paths["core"].read_text(encoding="utf-8"))
    sd = instrument_sd(paths["sd"].read_text(encoding="utf-8"))
    printk = instrument_printk(paths["printk"].read_text(encoding="utf-8"))
    live_source = make_live_source_compile_time_safe(build_live_source())

    makefile = paths["makefile"].read_text(encoding="utf-8")
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
        "qcom_probe_triplets": all(
            qcom.count(f"A52UFS copy={copy} PROBE_BEGIN") == 1
            and qcom.count(f"A52UFS copy={copy} PROBE_END") == 1
            for copy in (1, 2, 3)
        ),
        "platform_stage_markers": all(
            marker in pltfrm
            for marker in (
                "stage=mmio",
                "stage=irq",
                "stage=alloc_host",
                "stage=parse_clocks",
                "stage=parse_regulators",
                "stage=ufshcd_init",
            )
        ),
        "core_stage_markers": all(
            marker in core
            for marker in (
                "stage=init_begin",
                "stage=hba_init",
                "stage=capabilities",
                "stage=dma_mask",
                "stage=memory_alloc",
                "stage=request_irq",
                "stage=scsi_add_host",
                "stage=hba_enable",
                "stage=link_startup",
                "stage=verify_dev_init",
                "stage=complete_dev_init",
                "stage=device_params",
                "stage=config_pwr_mode",
                "stage=scsi_scan_begin",
                "stage=async_scan_end",
                "stage=init_fail",
            )
        ),
        "sd_markers": "A52UFS copy=1 SD stage=probe" in sd
        and "A52UFS copy=1 SD stage=device_add_disk" in sd,
        "storage_printk_mirror": "A52LOG seq=%u" in printk
        and "a52_storage_kmsg_count < 128" in printk,
        "live_snapshots": all(
            marker in live_source
            for marker in (
                "SNAPSHOT begin",
                "PROPS1",
                "PROPS2",
                "SUPPLY",
                "PLATFORM scan=",
                "BLOCK scan=",
                "delayed-500ms",
                "delayed-2s",
                "delayed-6s",
                "(3 * HZ) / 2",
                "4 * HZ",
            )
        ),
        "makefile_object": make_line in makefile,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit(
            "single-shot UFS recorder staging audit failed: " + ", ".join(failed)
        )

    paths["qcom"].write_text(qcom, encoding="utf-8")
    paths["pltfrm"].write_text(pltfrm, encoding="utf-8")
    paths["core"].write_text(core, encoding="utf-8")
    paths["sd"].write_text(sd, encoding="utf-8")
    paths["printk"].write_text(printk, encoding="utf-8")
    paths["live"].write_text(live_source, encoding="utf-8")
    paths["makefile"].write_text(makefile, encoding="utf-8")

    outputs = {
        "patched-ufs-qcom.c": qcom,
        "patched-ufshcd-pltfrm.c": pltfrm,
        "patched-ufshcd.c": core,
        "patched-scsi-sd.c": sd,
        "patched-printk.c": printk,
        "a52-ufs-live-trace.c": live_source,
        "patched-ufs-Makefile": makefile,
    }
    for name, content in outputs.items():
        (output / name).write_text(content, encoding="utf-8")

    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": (
                    "single-shot diagnosis of missing UFS partitions with live DT, "
                    "dependency/resource snapshots, qcom/platform/core stage returns, "
                    "storage-related printk capture, SCSI disk registration, block "
                    "device enumeration, Android init kmsg and PID 1 exit"
                ),
                "trace_points": [
                    "Qualcomm UFS platform probe entry and return",
                    "platform MMIO, IRQ, host allocation, clocks and regulators",
                    "UFS core capability, DMA, IRQ, SCSI host and HBA enable stages",
                    "link startup, device verification, descriptor and power-mode stages",
                    "async scan and SCSI logical-unit scan",
                    "SCSI sd probe and block-disk registration",
                    "storage-related kernel printk mirror, capped at 128 compact messages",
                    "live UFS host/PHY DT properties and resource lengths",
                    "relevant platform-device enumeration",
                    "block-device enumeration at late init, 0.5 s, 2 s and 6 s",
                ],
                "critical_marker_redundancy": 3,
                "verbose_marker_redundancy": 1,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def write_failure_trace() -> None:
    output: Path | None = None
    for index, value in enumerate(sys.argv[:-1]):
        if value == "--output":
            output = Path(sys.argv[index + 1]).resolve()
            break
    if output is None:
        return
    output.mkdir(parents=True, exist_ok=True)
    (output / "stage-error.txt").write_text(traceback.format_exc(), encoding="utf-8")


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:
        write_failure_trace()
        raise
    raise SystemExit(result)
