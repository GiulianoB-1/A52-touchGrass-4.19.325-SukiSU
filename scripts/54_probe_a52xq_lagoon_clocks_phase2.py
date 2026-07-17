#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"
TOUCHGRASS_SHA = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"

CLOCKS = {
    "camcc-lagoon": (
        "drivers/clk/qcom/camcc-lagoon.c",
        "drivers/clk/qcom/camcc-lagoon.o",
        "CONFIG_CAM_CC_LAGOON",
        "CAM_CC_LAGOON",
        "Lagoon camera clock controller",
    ),
    "dispcc-lagoon": (
        "drivers/clk/qcom/dispcc-lagoon.c",
        "drivers/clk/qcom/dispcc-lagoon.o",
        "CONFIG_DISP_CC_LAGOON",
        "DISP_CC_LAGOON",
        "Lagoon display clock controller",
    ),
    "gpucc-lagoon": (
        "drivers/clk/qcom/gpucc-lagoon.c",
        "drivers/clk/qcom/gpucc-lagoon.o",
        "CONFIG_GPU_CC_LAGOON",
        "GPU_CC_LAGOON",
        "Lagoon GPU clock controller",
    ),
    "videocc-lagoon": (
        "drivers/clk/qcom/videocc-lagoon.c",
        "drivers/clk/qcom/videocc-lagoon.o",
        "CONFIG_VIDEO_CC_LAGOON",
        "VIDEO_CC_LAGOON",
        "Lagoon video clock controller",
    ),
}

CLOCK_API_FILES = (
    "drivers/clk/qcom/clk-alpha-pll.h",
    "drivers/clk/qcom/clk-alpha-pll.c",
    "drivers/clk/qcom/common.h",
    "drivers/clk/qcom/common.c",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(errors="replace")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n")


def skip_braced_block(lines: list[str], index: int, context: str) -> int:
    depth = lines[index].count("{") - lines[index].count("}")
    index += 1
    while index < len(lines) and depth > 0:
        depth += lines[index].count("{") - lines[index].count("}")
        index += 1
    if depth != 0:
        raise SystemExit(f"unterminated {context}")
    return index


def adapt_clock_source(path: Path) -> None:
    lines = path.read_text(errors="replace").splitlines()
    result: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped == '#include "vdd-level-lagoon.h"':
            index += 1
            continue
        if "DEFINE_VDD_REGULATORS(" in line or "DEFINE_VDD_REGS_INIT(" in line:
            index += 1
            continue
        if ".enable_safe_config =" in line or ".cal_l =" in line:
            index += 1
            continue
        if "&clk_branch2_hw_ctl_ops" in line:
            result.append(line.replace("&clk_branch2_hw_ctl_ops", "&clk_branch2_ops"))
            index += 1
            continue

        if ".vdd_class =" in line:
            index += 1
            if index < len(lines) and ".num_rate_max =" in lines[index]:
                index += 1
            if index >= len(lines) or ".rate_max =" not in lines[index]:
                raise SystemExit(f"malformed VDD rate table in {path}")
            index = skip_braced_block(lines, index, f"VDD rate table in {path}")
            continue

        if re.match(
            r"^vdd_[A-Za-z0-9_]+\.regulator\[0\] = devm_regulator_get",
            stripped,
        ):
            index += 1
            if index < len(lines) and lines[index].lstrip().startswith("if (IS_ERR("):
                index = skip_braced_block(lines, index, f"regulator error block in {path}")
            continue

        result.append(line)
        index += 1

    text = "\n".join(result) + "\n"
    forbidden = (
        '"vdd-level-lagoon.h"',
        "DEFINE_VDD_REGULATORS",
        "DEFINE_VDD_REGS_INIT",
        ".vdd_class =",
        ".num_rate_max =",
        ".rate_max =",
        ".enable_safe_config =",
        ".cal_l =",
        "clk_branch2_hw_ctl_ops",
        "devm_regulator_get",
    )
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f"unsupported downstream clock tokens remain in {path}: {leftovers}")
    path.write_text(text)


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def snapshot_clock_api(gki: Path, artifact: Path) -> None:
    snapshot = artifact / "api-snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    for relative in CLOCK_API_FILES:
        source = gki / relative
        if not source.is_file():
            raise SystemExit(f"missing pinned clock API file: {relative}")
        shutil.copy2(source, snapshot / Path(relative).name)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()

    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")
    if not (gki / "drivers/clk/qcom/gcc-lagoon.c").is_file():
        raise SystemExit("Workflow 53 phase-1 source was not staged first")
    llcc_core = gki / "drivers/soc/qcom/llcc-qcom.c"
    if "lagoon_cfg" not in llcc_core.read_text(errors="replace"):
        raise SystemExit("Workflow 53 Lagoon LLCC integration is missing")

    artifact.mkdir(parents=True, exist_ok=True)
    snapshot_clock_api(gki, artifact)

    rows: list[dict[str, str]] = []
    kconfig_blocks: list[str] = []

    for probe, (relative, _target, _config, symbol, description) in CLOCKS.items():
        source = touchgrass / relative
        destination = gki / relative
        if not source.is_file():
            raise SystemExit(f"missing touchGrass source: {relative}")
        before = sha256(destination) if destination.is_file() else "<absent>"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        adapt_clock_source(destination)
        rows.append(
            {
                "probe": probe,
                "relative_path": relative,
                "purpose": description,
                "source_sha256": sha256(source),
                "gki_before_sha256": before,
                "gki_after_sha256": sha256(destination),
            }
        )
        kconfig_blocks.append(
            f"config {symbol}\n"
            f"\ttristate \"{description}\"\n"
            f"\tdepends on COMMON_CLK_QCOM\n"
            f"\tselect QCOM_GDSC\n"
            f"\thelp\n"
            f"\t  Non-flashable A52xq GKI 5.10 compile-probe support."
        )
        object_name = Path(relative).name.replace(".c", ".o")
        append_once(
            gki / "drivers/clk/qcom/Makefile",
            object_name,
            f"obj-$(CONFIG_{symbol}) += {object_name}",
        )

    append_once(
        gki / "drivers/clk/qcom/Kconfig",
        "config CAM_CC_LAGOON",
        "\n\n".join(kconfig_blocks),
    )

    fields = [
        "probe",
        "relative_path",
        "purpose",
        "source_sha256",
        "gki_before_sha256",
        "gki_after_sha256",
    ]
    write_tsv(artifact / "staged-files.tsv", fields, rows)

    fragment = ["# Lagoon phase-2 peripheral clock compile probes"]
    fragment.extend(f"{config}=y" for _, _, config, _, _ in CLOCKS.values())
    (artifact / "lagoon-clocks-phase2.fragment").write_text("\n".join(fragment) + "\n")

    new_paths = [relative for relative, *_ in CLOCKS.values()]
    subprocess.run(["git", "-C", str(gki), "add", "-N", "--", *new_paths], check=True)
    patch = output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    if not patch:
        raise SystemExit("phase-2 staging produced no GKI diff")
    (artifact / "lagoon-clocks-phase2-port.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-lagoon-clocks-phase2-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"planned_probes={len(CLOCKS)}",
        "phase1_dependency=gcc-lagoon,pinctrl-lagoon,llcc-lagoon",
        "clock_api_snapshot=clk-alpha-pll.h,clk-alpha-pll.c,common.h,common.c",
        "compat_removed=downstream-vdd,cal-l,safe-config,branch-hw-ctl",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    lines = path.read_text(errors="replace").splitlines()
    patterns = (
        "error:",
        "fatal error:",
        "undefined reference",
        "No rule to make target",
        "No such file or directory",
    )
    selected: list[str] = []
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= 10:
            break
    if not selected:
        selected = [line.strip() for line in lines[-8:] if line.strip()]
    return selected or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe") for row in rows} != set(CLOCKS):
        raise SystemExit("phase-2 compile status probe set mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    passed = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    report = [
        "# A52xq GKI 5.10 Lagoon peripheral clocks phase-2 probe",
        "",
        "## Result",
        "",
        f"- compiled: **{passed}**",
        f"- compile failures: **{failed}**",
        f"- Kconfig blocked: **{blocked}**",
        "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend(
            [
                f"### `{probe}`",
                "",
                f"- target: `{row['target']}`",
                f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
                f"- result: **{row['result']}**",
                f"- object produced: `{row['object_produced']}`",
                "",
                "First diagnostics:",
                "",
            ]
        )
        report.extend(
            f"- `{line.replace('`', chr(39))}`"
            for line in diagnostics(artifact / "logs" / f"{probe}.log")
        )
        report.append("")
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend(
        [
            f"compiled_success={passed}",
            f"compile_failed={failed}",
            f"config_blocked={blocked}",
        ]
    )
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(
        path
        for path in artifact.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--gki", type=Path, required=True)
    stage_parser.add_argument("--touchgrass", type=Path, required=True)
    stage_parser.add_argument("--output", type=Path, required=True)
    stage_parser.set_defaults(func=stage)

    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--status-file", type=Path, required=True)
    finalize_parser.set_defaults(func=finalize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
