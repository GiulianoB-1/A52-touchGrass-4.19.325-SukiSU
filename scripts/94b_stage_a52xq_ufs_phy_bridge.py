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


# Replay the complete Run 33 staging implementation, then adapt only Samsung's
# legacy qcom,ufs-phy-qmp-v3 clock-name ABI to the upstream QMP driver.
RUN33_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "53a91f777fc132fc39c013f4c6bb8131d9ddd037/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"

LEGACY_CLOCK_LIST = '''static const char * const a52_lagoon_ufs_phy_clk_l[] = {
	"ref_clk_src", "ref_clk", "ref_aux_clk",
};

'''


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run33_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run33-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run33.py"
        request = urllib.request.Request(
            RUN33_STAGE_URL, headers={"User-Agent": "a52-stage94b-run34-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())

        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run(
            [sys.executable, str(previous), *sys.argv[1:]],
            check=True,
            env=env,
        )


def initializer_span(text: str, anchor: str, label: str) -> tuple[int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"{label}: anchor missing")
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
                end = text.find(";", pos)
                if end < 0:
                    raise SystemExit(f"{label}: initializer terminator missing")
                return start, end + 1
    raise SystemExit(f"{label}: closing brace missing")


def bridge_legacy_phy_clock_names(gki: Path, output: Path) -> dict:
    source_path = gki / "drivers/phy/qualcomm/phy-qcom-qmp.c"
    if not source_path.is_file():
        raise SystemExit("Run 33 patched QMP source is missing")

    text = source_path.read_text(encoding="utf-8")
    if "a52_lagoon_ufs_phy_clk_l" in text:
        raise SystemExit("legacy Lagoon UFS PHY clock bridge already present")

    clock_anchor = '''static const char * const sdm845_ufs_phy_clk_l[] = {
	"ref", "ref_aux",
};

'''
    if text.count(clock_anchor) != 1:
        raise SystemExit(
            "sdm845 UFS PHY clock-list anchor: expected exactly one match, "
            f"found {text.count(clock_anchor)}"
        )
    text = text.replace(clock_anchor, clock_anchor + LEGACY_CLOCK_LIST, 1)

    cfg_anchor = "static const struct qmp_phy_cfg sdm845_ufsphy_cfg = {"
    cfg_start, cfg_end = initializer_span(text, cfg_anchor, "sdm845 UFS PHY config")
    cfg = text[cfg_start:cfg_end]
    legacy_cfg = cfg.replace(
        "static const struct qmp_phy_cfg sdm845_ufsphy_cfg = {",
        "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg = {",
        1,
    )
    legacy_cfg = legacy_cfg.replace(
        ".clk_list\t\t= sdm845_ufs_phy_clk_l,",
        ".clk_list\t\t= a52_lagoon_ufs_phy_clk_l,",
        1,
    )
    legacy_cfg = legacy_cfg.replace(
        ".num_clks\t\t= ARRAY_SIZE(sdm845_ufs_phy_clk_l),",
        ".num_clks\t\t= ARRAY_SIZE(a52_lagoon_ufs_phy_clk_l),",
        1,
    )
    if legacy_cfg == cfg:
        raise SystemExit("legacy Lagoon UFS PHY config transformation made no changes")
    if (
        "a52_lagoon_ufsphy_cfg" not in legacy_cfg
        or "a52_lagoon_ufs_phy_clk_l" not in legacy_cfg
    ):
        raise SystemExit("legacy Lagoon UFS PHY config transformation audit failed")
    text = text[:cfg_end] + "\n\n" + legacy_cfg + text[cfg_end:]

    old_match = '\t\t.compatible = "qcom,ufs-phy-qmp-v3",\n\t\t.data = &sdm845_ufsphy_cfg,'
    new_match = '\t\t.compatible = "qcom,ufs-phy-qmp-v3",\n\t\t.data = &a52_lagoon_ufsphy_cfg,'
    if text.count(old_match) != 1:
        raise SystemExit(
            "legacy compatible match entry: expected exactly one match, "
            f"found {text.count(old_match)}"
        )
    text = text.replace(old_match, new_match, 1)

    checks = {
        "legacy_clock_list_present": LEGACY_CLOCK_LIST in text,
        "legacy_ref_clk_src": '"ref_clk_src", "ref_clk", "ref_aux_clk"' in text,
        "legacy_dedicated_config": (
            "static const struct qmp_phy_cfg a52_lagoon_ufsphy_cfg" in text
            and ".clk_list\t\t= a52_lagoon_ufs_phy_clk_l," in text
            and ".num_clks\t\t= ARRAY_SIZE(a52_lagoon_ufs_phy_clk_l)," in text
        ),
        "legacy_compatible_uses_dedicated_config": new_match in text,
        "upstream_sdm845_clock_names_unchanged": clock_anchor in text,
        "upstream_sdm845_compatible_unchanged": (
            '.compatible = "qcom,sdm845-qmp-ufs-phy",' in text
            and ".data = &sdm845_ufsphy_cfg," in text
        ),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit("legacy UFS PHY clock bridge audit failed: " + ", ".join(failed))

    source_path.write_text(text, encoding="utf-8")
    (output / "patched-phy-qcom-qmp-run34.c").write_text(text, encoding="utf-8")

    return {
        "status": "bridged",
        "reason": (
            "Run 33 proved RPMh regulator registration but QMP probe failed with "
            "-ENOENT while requesting upstream clock name ref. The shipped Samsung "
            "node exposes three downstream names: ref_clk_src, ref_clk and ref_aux_clk."
        ),
        "scope": (
            "qcom,ufs-phy-qmp-v3 only; native upstream sdm845-qmp-ufs-phy "
            "clock names and configuration remain unchanged"
        ),
        "compatible": "qcom,ufs-phy-qmp-v3",
        "clock_names": ["ref_clk_src", "ref_clk", "ref_aux_clk"],
        "checks": checks,
    }


def merge_report(output: Path, clock_report: dict) -> None:
    report_path = output / "stage-report.json"
    if not report_path.is_file():
        raise SystemExit("Run 33 UFS bridge stage report is missing")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = clock_report.get("checks", {})
    report.setdefault("checks", {})["legacy_ufs_phy_clock_names_bridged"] = bool(
        checks and all(checks.values())
    )
    report["legacy_ufs_phy_clock_bridge"] = clock_report
    report["compatibility_bridge"] = {
        "from": "qcom,ufs-phy-qmp-v3",
        "to_configuration": "a52_lagoon_ufsphy_cfg",
    }
    if not report["checks"]["legacy_ufs_phy_clock_names_bridged"]:
        raise SystemExit("legacy UFS PHY clock-name bridge audit failed")

    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run33_stage()
    clock_report = bridge_legacy_phy_clock_names(gki, output)
    merge_report(output, clock_report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run34-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
