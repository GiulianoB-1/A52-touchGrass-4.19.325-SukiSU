#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path


# Replay the already-audited QMP bridge implementation byte-for-byte, then add
# the provider compatibility stage identified by the Run 23 hardware capture.
ORIGINAL_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "b25df34a002bf327c60253f665e4b3ee09863547/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_proven_qmp_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )
    with tempfile.TemporaryDirectory(prefix="a52-stage94b-") as tmp:
        original = Path(tmp) / "stage94b-original.py"
        request = urllib.request.Request(
            ORIGINAL_URL, headers={"User-Agent": "a52-stage94b-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            original.write_bytes(response.read())
        subprocess.run(
            [sys.executable, str(original), *sys.argv[1:]],
            check=True,
            env=env,
        )


def stage_rpmh_providers(gki: Path, output: Path) -> dict:
    script = Path(__file__).resolve().with_name(PROVIDER_SCRIPT)
    if not script.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {script}")

    provider_output = output / "rpmh-provider-bridge"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--gki",
            str(gki),
            "--output",
            str(provider_output),
        ],
        check=True,
    )
    report_path = provider_output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("RPMh provider bridge did not produce its stage report")
    return json.loads(report_path.read_text(encoding="utf-8"))


def merge_reports(output: Path, provider_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("proven QMP bridge did not produce its stage report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    provider_checks = provider_report.get("checks", {})
    report.setdefault("checks", {})["rpmh_provider_bridge_staged"] = bool(
        provider_checks and all(provider_checks.values())
    )
    report["rpmh_provider_bridge"] = {
        "status": provider_report.get("status"),
        "lagoon_clock_compatible": provider_report.get(
            "lagoon_clock_compatible"
        ),
        "lagoon_qlink_ids": provider_report.get("lagoon_qlink_ids"),
        "downstream_regulator_source": provider_report.get(
            "downstream_regulator_source"
        ),
        "checks": provider_checks,
    }
    if not report["checks"]["rpmh_provider_bridge_staged"]:
        raise SystemExit("RPMh provider bridge report contains a failed audit")
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_proven_qmp_stage()
    provider_report = stage_rpmh_providers(gki, output)
    merge_reports(output, provider_report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
