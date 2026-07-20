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


# Replay the complete Run 31 staging implementation, then restore the numeric
# mode ABI used by Samsung's downstream DT and Qualcomm's downstream driver.
RUN31_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "0da647b8b2112010e5d4db3304d815f9babf8a47/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"

DOWNSTREAM_MODE_ABI_BLOCK = r'''/*
 * Samsung's shipped DTB was compiled against the downstream
 * qcom,rpmh-regulator-levels.h ABI:
 *
 *   RET=0, LPM=1, HPM=2, AUTO=3, PASS=4
 *
 * The upstream qcom,rpmh-regulator.h header swaps HPM and AUTO.  This
 * downstream compatibility driver must interpret the already-baked numeric DT
 * cells using the downstream ABI, while the normal upstream driver keeps its
 * native binding.
 */
#undef RPMH_REGULATOR_MODE_RET
#undef RPMH_REGULATOR_MODE_LPM
#undef RPMH_REGULATOR_MODE_AUTO
#undef RPMH_REGULATOR_MODE_HPM
#undef RPMH_REGULATOR_MODE_PASS
#define RPMH_REGULATOR_MODE_RET		0
#define RPMH_REGULATOR_MODE_LPM		1
#define RPMH_REGULATOR_MODE_HPM		2
#define RPMH_REGULATOR_MODE_AUTO	3
#define RPMH_REGULATOR_MODE_PASS	4

#if RPMH_REGULATOR_MODE_HPM != 2 || RPMH_REGULATOR_MODE_AUTO != 3
#error "Samsung downstream RPMh regulator mode ABI mismatch"
#endif

'''


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run31_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run31-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run31.py"
        request = urllib.request.Request(
            RUN31_STAGE_URL, headers={"User-Agent": "a52-stage94b-run32-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())

        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run(
            [sys.executable, str(previous), *sys.argv[1:]],
            check=True,
            env=env,
        )


def restore_downstream_mode_abi(gki: Path, output: Path) -> dict:
    """Interpret baked Samsung regulator mode cells with the downstream ABI."""
    source_path = gki / "drivers/regulator/a52-rpmh-regulator-downstream.c"
    if not source_path.is_file():
        raise SystemExit(
            "Run 31 downstream RPMh compatibility regulator source is missing"
        )

    text = source_path.read_text(encoding="utf-8")
    include_anchor = '#include <dt-bindings/regulator/qcom,rpmh-regulator.h>\n\n'
    if text.count(include_anchor) != 1:
        raise SystemExit(
            "downstream RPMh binding include: expected exactly one insertion anchor, "
            f"found {text.count(include_anchor)}"
        )
    if "Samsung's shipped DTB was compiled against the downstream" in text:
        raise SystemExit("downstream RPMh regulator mode ABI bridge already present")

    text = text.replace(
        include_anchor,
        include_anchor + DOWNSTREAM_MODE_ABI_BLOCK,
        1,
    )

    checks = {
        "ret_is_0": "#define RPMH_REGULATOR_MODE_RET\t\t0" in text,
        "lpm_is_1": "#define RPMH_REGULATOR_MODE_LPM\t\t1" in text,
        "hpm_is_2": "#define RPMH_REGULATOR_MODE_HPM\t\t2" in text,
        "auto_is_3": "#define RPMH_REGULATOR_MODE_AUTO\t3" in text,
        "pass_is_4": "#define RPMH_REGULATOR_MODE_PASS\t4" in text,
        "compile_time_guard": (
            '#error "Samsung downstream RPMh regulator mode ABI mismatch"' in text
        ),
        "pmic5_ldo_hpm_mapping_retained": (
            "[RPMH_REGULATOR_MODE_HPM] = {" in text
            and "RPMH_REGULATOR_MODE_PMIC5_LDO_HPM" in text
        ),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit(
            "downstream RPMh mode ABI audit failed: " + ", ".join(failed)
        )

    source_path.write_text(text, encoding="utf-8")
    (output / "a52-rpmh-regulator-downstream-run32.c").write_text(
        text, encoding="utf-8"
    )

    return {
        "status": "restored",
        "reason": (
            "Run 31 reached Android init but all storage remained absent. "
            "Hardware logs showed PMIC5 LDO qcom,supported-modes element 0 = 2 "
            "rejected as AUTO, although Samsung's downstream ABI encodes HPM as 2."
        ),
        "scope": (
            "numeric mode interpretation inside the downstream compatibility "
            "regulator only; the upstream RPMh regulator binding is unchanged"
        ),
        "mapping": {
            "RET": 0,
            "LPM": 1,
            "HPM": 2,
            "AUTO": 3,
            "PASS": 4,
        },
        "source": str(source_path),
        "checks": checks,
    }


def merge_report(output: Path, abi_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("Run 31 UFS bridge stage report is missing")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = abi_report.get("checks", {})
    report.setdefault("checks", {})["downstream_regulator_mode_abi_restored"] = bool(
        checks and all(checks.values())
    )
    report["downstream_regulator_mode_abi"] = abi_report
    if not report["checks"]["downstream_regulator_mode_abi_restored"]:
        raise SystemExit("downstream RPMh regulator mode ABI bridge audit failed")

    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run31_stage()
    abi_report = restore_downstream_mode_abi(gki, output)
    merge_report(output, abi_report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run32-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
