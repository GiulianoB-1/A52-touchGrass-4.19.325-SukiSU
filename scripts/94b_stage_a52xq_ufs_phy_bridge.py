#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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
QUARANTINED_CLOCK_SYMBOLS = (
    "CONFIG_CAM_CC_LAGOON",
    "CONFIG_DISP_CC_LAGOON",
    "CONFIG_GPU_CC_LAGOON",
    "CONFIG_VIDEO_CC_LAGOON",
)


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


def prepare_provider_runner(script: Path, provider_output: Path) -> Path:
    """Normalize one formatting-sensitive Python source anchor in a temp copy.

    The imported downstream C source aligns `.name` and `=` with tabs. The
    provider stage originally required one literal space. Replace only that
    Python replacement block with a regex that still requires exactly one
    qcom,rpmh-regulator driver-name field.
    """
    source = script.read_text(encoding="utf-8")
    old = '''    text = replace_once(
        text,
        '.name = "qcom,rpmh-regulator",',
        '.name = "a52-rpmh-regulator-downstream",',
        "give downstream regulator driver a unique name",
    )
'''
    new = '''    driver_name_matches = list(re.finditer(
        r'(?m)^(?P<indent>\\s*)\\.name\\s*=\\s*"qcom,rpmh-regulator",\\s*$',
        text,
    ))
    if len(driver_name_matches) != 1:
        raise SystemExit(
            "give downstream regulator driver a unique name: expected exactly "
            f"one whitespace-normalized match, found {len(driver_name_matches)}"
        )
    driver_name = driver_name_matches[0]
    text = (
        text[:driver_name.start()]
        + driver_name.group("indent")
        + '.name = "a52-rpmh-regulator-downstream",'
        + text[driver_name.end():]
    )
'''
    if source.count(old) != 1:
        raise SystemExit(
            "provider runner normalization: expected one literal driver-name "
            f"replacement block, found {source.count(old)}"
        )
    patched = source.replace(old, new, 1)
    runner = provider_output / "provider-stage-runner.py"
    runner.write_text(patched, encoding="utf-8")
    return runner


def stage_rpmh_providers(gki: Path, output: Path) -> dict:
    script = Path(__file__).resolve().with_name(PROVIDER_SCRIPT)
    if not script.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {script}")

    provider_output = output / "rpmh-provider-bridge"
    provider_output.mkdir(parents=True, exist_ok=True)
    runner = prepare_provider_runner(script, provider_output)
    result = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--gki",
            str(gki),
            "--output",
            str(provider_output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (provider_output / "stage-process.log").write_text(
        result.stdout or "", encoding="utf-8"
    )
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args)

    report_path = provider_output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("RPMh provider bridge did not produce its stage report")
    return json.loads(report_path.read_text(encoding="utf-8"))


def quarantine_compile_only_clocks(gki: Path, output: Path) -> dict:
    """Keep compile-probe-only peripheral clocks out of the storage test image.

    Run 28 reached the newly working RPMh clock provider, then died immediately
    in cam_cc_lagoon_init(). Camera, display, GPU and video clock controllers
    were all imported by Workflow 54 as non-flashable compile probes using the
    same compatibility adaptation. None is required to validate UFS, so force
    the four symbols off in Workflow 68's source configuration before Workflow
    95 copies it into the diagnostic build directory.
    """
    config_path = gki.parents[1] / "workflow68" / "extracted" / "integrated.config"
    if not config_path.is_file():
        raise SystemExit(
            "Workflow 68 integrated configuration is missing; cannot quarantine "
            "compile-only Lagoon peripheral clocks"
        )

    text = config_path.read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    for symbol in QUARANTINED_CLOCK_SYMBOLS:
        pattern = re.compile(
            rf"(?m)^(?:{re.escape(symbol)}=.*|# {re.escape(symbol)} is not set)$"
        )
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                f"quarantine {symbol}: expected exactly one config entry, "
                f"found {len(matches)}"
            )
        text = pattern.sub(f"# {symbol} is not set", text, count=1)
        checks[symbol] = f"# {symbol} is not set" in text

    config_path.write_text(text, encoding="utf-8")
    snapshot = output / "workflow68-integrated-config-quarantined.config"
    snapshot.write_text(text, encoding="utf-8")

    return {
        "status": "quarantined",
        "reason": (
            "Run 28 hardware capture entered cam_cc_lagoon_init and immediately "
            "raised Oops: Fatal exception before UFS could bind"
        ),
        "scope": "compile-only Lagoon camera, display, GPU and video clock controllers",
        "source_config": str(config_path),
        "symbols": list(QUARANTINED_CLOCK_SYMBOLS),
        "checks": checks,
    }


def merge_reports(output: Path, provider_report: dict, quarantine_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("proven QMP bridge did not produce its stage report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    provider_checks = provider_report.get("checks", {})
    quarantine_checks = quarantine_report.get("checks", {})
    report.setdefault("checks", {})["rpmh_provider_bridge_staged"] = bool(
        provider_checks and all(provider_checks.values())
    )
    report["checks"]["compile_only_clocks_quarantined"] = bool(
        quarantine_checks and all(quarantine_checks.values())
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
    report["compile_only_clock_quarantine"] = quarantine_report
    if not report["checks"]["rpmh_provider_bridge_staged"]:
        raise SystemExit("RPMh provider bridge report contains a failed audit")
    if not report["checks"]["compile_only_clocks_quarantined"]:
        raise SystemExit("compile-only Lagoon clock quarantine contains a failed audit")
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_proven_qmp_stage()
    provider_report = stage_rpmh_providers(gki, output)
    quarantine_report = quarantine_compile_only_clocks(gki, output)
    merge_reports(output, provider_report, quarantine_report)
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
