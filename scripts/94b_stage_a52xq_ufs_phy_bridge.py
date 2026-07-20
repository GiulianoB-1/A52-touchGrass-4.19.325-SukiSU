#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path


# Replay the complete Run 30 staging implementation, then remove the remaining
# compile-probe-only Lagoon auxiliary clock drivers exposed by the hardware boot.
RUN30_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "ba2bb9f2c44c6614dc5a6bd3264584e60a2eb9d7/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"
QUARANTINED_AUX_CLOCK_SYMBOLS = (
    "CONFIG_NPU_CC_LAGOON",
    "CONFIG_QCOM_CLK_DEBUG",
    "CONFIG_DEBUG_CC_LAGOON",
)


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run30_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run30-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run30.py"
        request = urllib.request.Request(
            RUN30_STAGE_URL, headers={"User-Agent": "a52-stage94b-run31-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())

        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run(
            [sys.executable, str(previous), *sys.argv[1:]],
            check=True,
            env=env,
        )


def quarantine_auxiliary_clock_probes(gki: Path, output: Path) -> dict:
    """Remove remaining nonessential clock compile probes from the UFS test image."""
    config_path = gki.parents[1] / "workflow68" / "extracted" / "integrated.config"
    if not config_path.is_file():
        raise SystemExit(
            "Workflow 68 integrated configuration is missing; cannot quarantine "
            "remaining compile-only Lagoon auxiliary clocks"
        )

    text = config_path.read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    for symbol in QUARANTINED_AUX_CLOCK_SYMBOLS:
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
    snapshot = output / "workflow68-integrated-config-run31.config"
    snapshot.write_text(text, encoding="utf-8")

    return {
        "status": "quarantined",
        "reason": (
            "Run 30 hardware capture completed hundreds of initcalls, then "
            "entered clk_debug_lagoon_init and raised Oops: Fatal exception "
            "before UFS validation"
        ),
        "scope": (
            "remaining nonessential Lagoon NPU and debug-clock compile probes; "
            "SDM_GCC_LAGOON remains enabled"
        ),
        "source_config": str(config_path),
        "symbols": list(QUARANTINED_AUX_CLOCK_SYMBOLS),
        "checks": checks,
    }


def merge_report(output: Path, quarantine_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("Run 30 UFS bridge stage report is missing")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = quarantine_report.get("checks", {})
    report.setdefault("checks", {})["aux_clock_probes_quarantined"] = bool(
        checks and all(checks.values())
    )
    report["aux_clock_probe_quarantine"] = quarantine_report
    if not report["checks"]["aux_clock_probes_quarantined"]:
        raise SystemExit("auxiliary clock-probe quarantine contains a failed audit")

    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run30_stage()
    quarantine_report = quarantine_auxiliary_clock_probes(gki, output)
    merge_report(output, quarantine_report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run31-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
