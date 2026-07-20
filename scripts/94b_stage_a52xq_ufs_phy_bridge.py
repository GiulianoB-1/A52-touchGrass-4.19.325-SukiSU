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


# Replay the complete Run 29 staging implementation, then apply the one-variable
# Run 30 response to the hardware-captured kernel-stack overflow.
RUN29_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "d3fdb1a65fa74a7a79e10e03808be1aa620b5bc7/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"
KASAN_SYMBOL = "CONFIG_KASAN"


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run29_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run29-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run29.py"
        request = urllib.request.Request(
            RUN29_STAGE_URL, headers={"User-Agent": "a52-stage94b-run30-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())

        # The Run 29 wrapper resolves this helper beside its own __file__.
        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run(
            [sys.executable, str(previous), *sys.argv[1:]],
            check=True,
            env=env,
        )


def quarantine_generic_kasan(gki: Path, output: Path) -> dict:
    """Disable Generic KASAN for the next storage-only hardware bring-up.

    Run 29 removed only four compile-probe clock drivers. The fresh hardware
    capture then reached the first local_irq_enable boundary and panicked with
    "kernel stack overflow" on CPU 0. Keep VMAP_STACK guard detection enabled,
    but remove Generic KASAN's compile-time memory-access instrumentation for
    this controlled retry.
    """
    config_path = gki.parents[1] / "workflow68" / "extracted" / "integrated.config"
    if not config_path.is_file():
        raise SystemExit(
            "Workflow 68 integrated configuration is missing; cannot disable "
            "Generic KASAN for Run 30"
        )

    text = config_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?m)^(?:{re.escape(KASAN_SYMBOL)}=.*|# {re.escape(KASAN_SYMBOL)} is not set)$"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(
            f"quarantine {KASAN_SYMBOL}: expected exactly one config entry, "
            f"found {len(matches)}"
        )

    text = pattern.sub(f"# {KASAN_SYMBOL} is not set", text, count=1)
    config_path.write_text(text, encoding="utf-8")
    snapshot = output / "workflow68-integrated-config-run30.config"
    snapshot.write_text(text, encoding="utf-8")

    return {
        "status": "quarantined",
        "reason": (
            "Run 29 hardware capture passed the cam_cc_lagoon boundary but "
            "panicked at the first local_irq_enable boundary with kernel stack "
            "overflow on CPU 0"
        ),
        "scope": "Generic KASAN only; VMAP_STACK remains enabled",
        "source_config": str(config_path),
        "symbols": [KASAN_SYMBOL],
        "checks": {
            KASAN_SYMBOL: f"# {KASAN_SYMBOL} is not set" in text,
        },
    }


def merge_report(output: Path, kasan_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("Run 29 UFS bridge stage report is missing")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = kasan_report.get("checks", {})
    report.setdefault("checks", {})["generic_kasan_quarantined"] = bool(
        checks and all(checks.values())
    )
    report["generic_kasan_quarantine"] = kasan_report
    if not report["checks"]["generic_kasan_quarantined"]:
        raise SystemExit("Generic KASAN quarantine contains a failed audit")

    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run29_stage()
    kasan_report = quarantine_generic_kasan(gki, output)
    merge_report(output, kasan_report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run30-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
