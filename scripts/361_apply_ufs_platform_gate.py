#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DD = Path("drivers/base/dd.c")
CORE = Path("drivers/scsi/ufs/ufshcd.c")
QCOM = Path("drivers/scsi/ufs/ufs-qcom.c")
MARK = "A52_PHASE361_UFS_RETAINED_TRACE_V1"


def retain_phase360(text: str) -> str:
    for copy in (1, 2, 3):
        text = text.replace(
            f"A52_PREPROBE copy={copy} P360 ",
            f"A52GDSC P360 c={copy} ",
        )
    return text


def retain_existing_preprobe(text: str) -> str:
    # Phase346 already contains the old Run40 driver-core UFS trace in dd.c:
    # pinctrl, DMA, PM activate, bus_probe_begin/end and driver_probe_begin/end.
    # The main recorder retains messages beginning with "A52GDSC " after its
    # normal ring fills. Re-prefix only the existing diagnostic strings.
    for copy in (1, 2, 3):
        text = text.replace(
            f"A52_PREPROBE copy={copy} ",
            f"A52GDSC P361 c={copy} PREPROBE ",
        )
    return text


def validate(dd: str, core: str, qcom: str) -> None:
    joined = dd + core + qcom
    required = (
        "A52_UFS_PINCTRL_DEFER_BYPASS",
        "P361 c=1 PREPROBE stage=bus_probe_end",
        "P361 c=1 PREPROBE stage=pm_activate",
        "A52GDSC P360 c=1 HBA stage=",
        "A52GDSC P360 c=1 QCOM stage=devm_phy_get",
    )
    for token in required:
        if token not in joined:
            raise SystemExit("Phase361 required token missing: " + token)

    if "A52_PREPROBE copy=1 " in dd:
        raise SystemExit("Phase361 left unretained copy1 PREPROBE records in dd.c")
    if "A52_PREPROBE copy=1 P360 " in core + qcom:
        raise SystemExit("Phase361 left unretained Phase360 copy1 records")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    dd_path = ns.root / DD
    core_path = ns.root / CORE
    qcom_path = ns.root / QCOM
    for path in (dd_path, core_path, qcom_path):
        if not path.is_file():
            raise SystemExit("Phase361 missing source: " + str(path))

    dd = dd_path.read_text(encoding="utf-8")
    core = core_path.read_text(encoding="utf-8")
    qcom = qcom_path.read_text(encoding="utf-8")

    if "A52_PHASE360_UFS_DEFER_PINPOINT_V1" not in core or \
       "A52_PHASE360_UFS_DEFER_PINPOINT_V1" not in qcom:
        raise SystemExit("Phase361 requires Phase360 source lineage")

    if MARK in dd:
        validate(dd, core, qcom)
        print("Phase361 retained UFS trace audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase361 marker missing in check-only mode")

    if "stage=bus_probe_end" not in dd or "stage=pm_activate" not in dd:
        raise SystemExit(
            "Phase361 requires inherited Run40 driver-core UFS instrumentation"
        )
    if "A52_UFS_PINCTRL_DEFER_BYPASS" not in dd:
        raise SystemExit("Phase361 requires inherited UFS pinctrl bridge")

    marker = f"/* {MARK} */\n"
    anchor = "A52_UFS_PINCTRL_DEFER_BYPASS"
    pos = dd.find(anchor)
    line_start = dd.rfind("\n", 0, pos) + 1
    dd = dd[:line_start] + marker + dd[line_start:]

    dd = retain_existing_preprobe(dd)
    core = retain_phase360(core)
    qcom = retain_phase360(qcom)

    validate(dd, core, qcom)

    dd_path.write_text(dd, encoding="utf-8")
    core_path.write_text(core, encoding="utf-8")
    qcom_path.write_text(qcom, encoding="utf-8")
    print("Phase361 retained UFS trace applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
