#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
from pathlib import Path

REQUIRED_SYMBOLS = [
    ("qcom-core", "QCOM_SCM", "secure monitor calls"),
    ("qcom-core", "QCOM_SMEM", "Qualcomm shared memory"),
    ("qcom-core", "QCOM_RPMH", "RPMh power management"),
    ("qcom-core", "QCOM_COMMAND_DB", "RPMh command database"),
    ("qcom-core", "QCOM_AOSS_QMP", "AOSS messaging"),
    ("serial", "QCOM_GENI_SE", "GENI serial engine"),
    ("serial", "SERIAL_QCOM_GENI", "Qualcomm GENI UART"),
    ("serial", "SERIAL_QCOM_GENI_CONSOLE", "early serial console"),
    ("clock", "COMMON_CLK_QCOM", "Qualcomm common clock framework"),
    ("clock", "QCOM_CLK_RPMH", "RPMh clocks"),
    ("pinctrl", "PINCTRL_QCOM", "Qualcomm pin control"),
    ("regulator", "REGULATOR_QCOM_RPMH", "RPMh regulators"),
    ("interconnect", "INTERCONNECT_QCOM", "Qualcomm interconnect providers"),
    ("iommu", "ARM_SMMU", "ARM SMMU"),
    ("storage", "SCSI_UFS_QCOM", "Qualcomm UFS host"),
]

PORT_FILES = [
    ("phase-1-bindings", "include/dt-bindings/clock/qcom,gcc-lagoon.h", "GCC clock IDs"),
    ("phase-1-bindings", "include/dt-bindings/clock/qcom,camcc-lagoon.h", "camera clock IDs"),
    ("phase-1-bindings", "include/dt-bindings/clock/qcom,dispcc-lagoon.h", "display clock IDs"),
    ("phase-1-bindings", "include/dt-bindings/clock/qcom,gpucc-lagoon.h", "GPU clock IDs"),
    ("phase-1-bindings", "include/dt-bindings/clock/qcom,videocc-lagoon.h", "video clock IDs"),
    ("phase-1-bindings", "include/dt-bindings/phy/qcom,lagoon-qmp-usb3.h", "USB3 PHY IDs"),
    ("phase-2-platform", "drivers/clk/qcom/gcc-lagoon.c", "early platform clocks"),
    ("phase-2-platform", "drivers/pinctrl/qcom/pinctrl-lagoon.c", "TLMM pin control"),
    ("phase-2-platform", "drivers/soc/qcom/llcc-lagoon.c", "last-level cache controller"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi", "SoC topology"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon.dts", "base DT build target"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon-pinctrl.dtsi", "board pin states"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon-regulators.dtsi", "RPMh regulators"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon-qupv3.dtsi", "GENI/QUP serial buses"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon-bus.dtsi", "bus/interconnect topology"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/lagoon-pm.dtsi", "power management"),
    ("phase-3-dt-core", "arch/arm64/boot/dts/vendor/qcom/msm-arm-smmu-lagoon.dtsi", "SMMU topology"),
    ("phase-4-board", "arch/arm64/boot/dts/vendor/qcom/lagoon-mtp.dtsi", "MTP board base"),
    ("phase-4-board", "arch/arm64/boot/dts/vendor/qcom/lagoon-mtp.dts", "MTP board target"),
    ("phase-4-board", "arch/arm64/boot/dts/samsung/lagoon-sec-system-update-overlay.dts", "Samsung common overlay"),
    ("phase-4-board", "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r00.dts", "A52 board revision r00"),
    ("phase-4-board", "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r01.dts", "A52 board revision r01"),
    ("phase-4-board", "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r02.dts", "A52 board revision r02"),
]

CONFIG_DEF_RE = re.compile(r"^\s*(?:menu)?config\s+([A-Za-z0-9_]+)\s*$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def kernel_version(tree: Path) -> str:
    return subprocess.check_output(
        ["make", "-s", "-C", str(tree), "kernelversion"], text=True
    ).strip()


def git_head(tree: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(tree), "rev-parse", "HEAD"], text=True
    ).strip()


def parse_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        if raw.startswith("CONFIG_") and "=" in raw:
            key, value = raw.split("=", 1)
            result[key.removeprefix("CONFIG_")] = value
        elif raw.startswith("# CONFIG_") and raw.endswith(" is not set"):
            key = raw[len("# CONFIG_") : -len(" is not set")]
            result[key] = "n"
    return result


def find_kconfig_definitions(tree: Path) -> dict[str, list[str]]:
    wanted = {symbol for _, symbol, _ in REQUIRED_SYMBOLS}
    found: dict[str, list[str]] = {symbol: [] for symbol in wanted}
    for path in tree.rglob("Kconfig*"):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            match = CONFIG_DEF_RE.match(line)
            if match and match.group(1) in wanted:
                rel = path.relative_to(tree).as_posix()
                found[match.group(1)].append(f"{rel}:{number}")
    return found


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--resolved-config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for tree in (args.gki, args.touchgrass):
        if not (tree / ".git").is_dir():
            raise SystemExit(f"missing git tree: {tree}")
    if not args.base_config.is_file():
        raise SystemExit(f"missing base config: {args.base_config}")

    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    definitions = find_kconfig_definitions(args.gki)
    base = parse_config(args.base_config)
    resolved = parse_config(args.resolved_config) if args.resolved_config else {}

    fragment_lines = [
        "# A52xq Android 12 GKI 5.10 early-boot configuration seed",
        "# Analysis/bring-up input only. This file is not a flashable kernel.",
    ]
    symbol_rows: list[dict[str, str]] = []
    for group, symbol, reason in REQUIRED_SYMBOLS:
        locations = definitions.get(symbol, [])
        defined = bool(locations)
        if defined:
            fragment_lines.append(f"CONFIG_{symbol}=y")
        symbol_rows.append(
            {
                "group": group,
                "symbol": f"CONFIG_{symbol}",
                "reason": reason,
                "kconfig_defined": "yes" if defined else "no",
                "kconfig_locations": " | ".join(locations) or "<none>",
                "base_gki_value": base.get(symbol, "<absent>"),
                "requested_value": "y" if defined else "<not-requested>",
                "resolved_value": resolved.get(symbol, "<not-resolved>") if args.resolved_config else "<pending>",
                "resolution": (
                    "enabled"
                    if args.resolved_config and resolved.get(symbol) == "y"
                    else "blocked-by-dependencies"
                    if args.resolved_config and defined
                    else "symbol-absent"
                    if not defined
                    else "pending"
                ),
            }
        )

    fragment_path = out / "a52xq-early-boot.fragment"
    fragment_path.write_text("\n".join(fragment_lines) + "\n")

    write_tsv(
        out / "early-boot-config-status.tsv",
        [
            "group",
            "symbol",
            "reason",
            "kconfig_defined",
            "kconfig_locations",
            "base_gki_value",
            "requested_value",
            "resolved_value",
            "resolution",
        ],
        symbol_rows,
    )

    port_rows: list[dict[str, str]] = []
    for phase, rel, purpose in PORT_FILES:
        src = args.touchgrass / rel
        dst = args.gki / rel
        port_rows.append(
            {
                "phase": phase,
                "relative_path": rel,
                "purpose": purpose,
                "touchgrass_present": "yes" if src.is_file() else "no",
                "touchgrass_sha256": sha256(src) if src.is_file() else "<missing>",
                "same_path_in_gki": "yes" if dst.is_file() else "no",
                "action": (
                    "compare-and-adapt"
                    if src.is_file() and dst.is_file()
                    else "port-and-adapt"
                    if src.is_file()
                    else "source-missing"
                ),
            }
        )
    write_tsv(
        out / "source-port-manifest.tsv",
        [
            "phase",
            "relative_path",
            "purpose",
            "touchgrass_present",
            "touchgrass_sha256",
            "same_path_in_gki",
            "action",
        ],
        port_rows,
    )

    enabled = sum(1 for row in symbol_rows if row["resolution"] == "enabled")
    blocked = sum(1 for row in symbol_rows if row["resolution"] == "blocked-by-dependencies")
    absent = sum(1 for row in symbol_rows if row["resolution"] == "symbol-absent")
    missing_sources = sum(1 for row in port_rows if row["touchgrass_present"] == "no")

    report = [
        "# A52xq GKI 5.10 early-boot seed",
        "",
        "This artifact converts Workflow 51's gap analysis into the first controlled porting input. It is not flashable.",
        "",
        "## Inputs",
        "",
        f"- touchGrass tree: `{git_head(args.touchgrass)}` (`{kernel_version(args.touchgrass)}`)",
        f"- GKI tree: `{git_head(args.gki)}` (`{kernel_version(args.gki)}`)",
        f"- base GKI config: `{args.base_config.name}`",
        "",
        "## Configuration seed",
        "",
        f"- requested Kconfig symbols: **{len(REQUIRED_SYMBOLS)}**",
        f"- symbols enabled after dependency resolution: **{enabled}**" if args.resolved_config else "- dependency resolution: **pending**",
        f"- symbols blocked by dependencies: **{blocked}**" if args.resolved_config else "",
        f"- symbols absent from this GKI Kconfig tree: **{absent}**",
        "",
        "The generated `a52xq-early-boot.fragment` is the starting configuration overlay. Any requested symbol that remains disabled must be resolved before a diagnostic kernel build.",
        "",
        "## First source-port sequence",
        "",
        "1. Port DT binding headers required by the Lagoon clock and PHY drivers.",
        "2. Port and compile `gcc-lagoon`, `pinctrl-lagoon`, and `llcc-lagoon` individually.",
        "3. Port the minimal Lagoon DT core: clocks, pinctrl, RPMh regulators, QUP/GENI, SMMU, reserved memory, and UFS.",
        "4. Add the Samsung A52 board overlays only after the generic Lagoon DT compiles.",
        "5. Build a diagnostic-only kernel with console and ramoops. Do not add display, touch, camera, audio, Wi-Fi, modem, or root changes yet.",
        "",
        f"- source files in controlled manifest: **{len(PORT_FILES)}**",
        f"- missing from the working touchGrass tree: **{missing_sources}**",
        "",
        "## Gate for Workflow 53",
        "",
        "Workflow 53 may begin compile-probing the first platform source group only when all foundational Kconfig symbols are either enabled or explicitly mapped to their 5.10 replacement symbols.",
        "",
    ]
    (out / "PORTING-SEED-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-early-boot-seed-not-flashable",
        f"touchgrass_commit={git_head(args.touchgrass)}",
        f"touchgrass_kernel_version={kernel_version(args.touchgrass)}",
        f"gki_commit={git_head(args.gki)}",
        f"gki_kernel_version={kernel_version(args.gki)}",
        f"requested_symbols={len(REQUIRED_SYMBOLS)}",
        f"resolved_config_present={'yes' if args.resolved_config else 'no'}",
        f"resolved_enabled={enabled}",
        f"resolved_blocked={blocked}",
        f"kconfig_absent={absent}",
        f"controlled_port_files={len(PORT_FILES)}",
    ]
    (out / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(path for path in out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    with (out / "SHA256SUMS").open("w") as f:
        for path in files:
            f.write(f"{sha256(path)}  {path.name}\n")


if __name__ == "__main__":
    main()
