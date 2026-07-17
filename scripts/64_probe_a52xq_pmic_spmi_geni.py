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

PROBES = {
    "spmi-core": {
        "target": "drivers/spmi/spmi.o",
        "config": "CONFIG_SPMI",
        "description": "Linux SPMI bus core",
        "enable": ("CONFIG_ARCH_QCOM", "CONFIG_SPMI"),
        "sources": ("drivers/spmi/spmi.c",),
    },
    "spmi-pmic-arb": {
        "target": "drivers/spmi/spmi-pmic-arb.o",
        "config": "CONFIG_SPMI_MSM_PMIC_ARB",
        "description": "Qualcomm SPMI PMIC arbiter and interrupt controller",
        "enable": ("CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_SPMI", "CONFIG_SPMI_MSM_PMIC_ARB"),
        "sources": ("drivers/spmi/spmi-pmic-arb.c",),
    },
    "spmi-pmic-mfd": {
        "target": "drivers/mfd/qcom-spmi-pmic.o",
        "config": "CONFIG_MFD_SPMI_PMIC",
        "description": "Qualcomm SPMI PMIC core device",
        "enable": ("CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_SPMI", "CONFIG_MFD_CORE", "CONFIG_MFD_SPMI_PMIC"),
        "sources": ("drivers/mfd/qcom-spmi-pmic.c",),
    },
    "spmi-pmic-pinctrl": {
        "target": "drivers/pinctrl/qcom/pinctrl-spmi-gpio.o,drivers/pinctrl/qcom/pinctrl-spmi-mpp.o",
        "config": "CONFIG_PINCTRL_QCOM_SPMI_PMIC",
        "description": "Qualcomm PMIC GPIO and MPP pin controllers",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_SPMI", "CONFIG_PINCTRL",
            "CONFIG_GPIOLIB", "CONFIG_PINCTRL_QCOM_SPMI_PMIC",
        ),
        "sources": (
            "drivers/pinctrl/qcom/pinctrl-spmi-gpio.c",
            "drivers/pinctrl/qcom/pinctrl-spmi-mpp.c",
        ),
    },
    "spmi-temp-alarm": {
        "target": "drivers/thermal/qcom/qcom-spmi-temp-alarm.o",
        "config": "CONFIG_QCOM_SPMI_TEMP_ALARM",
        "description": "Qualcomm PMIC thermal alarm sensor",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_SPMI", "CONFIG_THERMAL",
            "CONFIG_QCOM_SPMI_TEMP_ALARM",
        ),
        "sources": ("drivers/thermal/qcom/qcom-spmi-temp-alarm.c",),
    },
    "spmi-adc5": {
        "target": "drivers/iio/adc/qcom-vadc-common.o,drivers/iio/adc/qcom-spmi-adc5.o",
        "config": "CONFIG_QCOM_SPMI_ADC5",
        "description": "Qualcomm SPMI ADC5/ADC7 and common VADC helpers",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_SPMI", "CONFIG_IIO",
            "CONFIG_QCOM_VADC_COMMON", "CONFIG_QCOM_SPMI_ADC5",
        ),
        "sources": (
            "drivers/iio/adc/qcom-vadc-common.c",
            "drivers/iio/adc/qcom-spmi-adc5.c",
        ),
    },
    "spmi-pmic-clkdiv": {
        "target": "drivers/clk/qcom/clk-spmi-pmic-div.o",
        "config": "CONFIG_SPMI_PMIC_CLKDIV",
        "description": "Qualcomm PMIC SPMI clock divider",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_SPMI", "CONFIG_COMMON_CLK",
            "CONFIG_SPMI_PMIC_CLKDIV",
        ),
        "sources": ("drivers/clk/qcom/clk-spmi-pmic-div.c",),
    },
    "qcom-watchdog": {
        "target": "drivers/watchdog/qcom-wdt.o",
        "config": "CONFIG_QCOM_WDT",
        "description": "Upstream Qualcomm platform watchdog",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_WATCHDOG", "CONFIG_WATCHDOG_CORE",
            "CONFIG_QCOM_WDT",
        ),
        "sources": ("drivers/watchdog/qcom-wdt.c",),
    },
    "geni-i2c": {
        "target": "drivers/soc/qcom/qcom-geni-se.o,drivers/i2c/busses/i2c-qcom-geni.o",
        "config": "CONFIG_I2C_QCOM_GENI",
        "description": "Qualcomm GENI wrapper and I2C controller",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_I2C", "CONFIG_QCOM_GENI_SE",
            "CONFIG_I2C_QCOM_GENI",
        ),
        "sources": (
            "drivers/soc/qcom/qcom-geni-se.c",
            "drivers/i2c/busses/i2c-qcom-geni.c",
        ),
    },
}

COMPATIBLES = (
    "qcom,spmi-pmic-arb",
    "qcom,spmi-pmic",
    "qcom,spmi-gpio",
    "qcom,spmi-temp-alarm",
    "qcom,spmi-adc5",
    "qcom,spmi-adc7",
    "qcom,spmi-clkdiv",
    "qcom,qpnp-power-on",
    "qcom,msm-watchdog",
    "qcom,i2c-geni",
    "qcom,qupv3-geni-se",
    "qcom,msm-geni-serial-hs",
    "qcom,gpi-dma",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def exact_source_hits(gki: Path, compatible: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(gki), "grep", "-l", "-F", compatible, "--", "drivers"],
        check=False, text=True, capture_output=True,
    )
    if completed.returncode not in (0, 1):
        raise SystemExit(f"git grep failed for {compatible}: {completed.stderr.strip()}")
    return sorted(line.strip() for line in completed.stdout.splitlines() if line.strip())


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    artifact = args.output.resolve()
    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    if gki_head != GKI_SHA:
        raise SystemExit(f"unexpected GKI revision: {gki_head}")

    artifact.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for probe, data in PROBES.items():
        missing = [relative for relative in data["sources"] if not (gki / relative).is_file()]
        source_hashes = [
            f"{relative}={sha256(gki / relative)}"
            for relative in data["sources"] if (gki / relative).is_file()
        ]
        rows.append({
            "probe": probe,
            "description": str(data["description"]),
            "target": str(data["target"]),
            "config_symbol": str(data["config"]),
            "enable_symbols": ",".join(data["enable"]),
            "source_files": ",".join(data["sources"]),
            "missing_sources": ",".join(missing),
            "source_sha256": ";".join(source_hashes),
        })
    write_tsv(
        artifact / "probe-plan.tsv",
        [
            "probe", "description", "target", "config_symbol", "enable_symbols",
            "source_files", "missing_sources", "source_sha256",
        ],
        rows,
    )

    compatible_rows = []
    for compatible in COMPATIBLES:
        hits = exact_source_hits(gki, compatible)
        compatible_rows.append({
            "compatible": compatible,
            "exact_gki_source_match": "yes" if hits else "no",
            "matching_files": ",".join(hits),
            "result": "available-exact" if hits else "alias-or-port-required",
        })
    write_tsv(
        artifact / "compatible-source-coverage.tsv",
        ["compatible", "exact_gki_source_match", "matching_files", "result"],
        compatible_rows,
    )

    exact = sum(row["exact_gki_source_match"] == "yes" for row in compatible_rows)
    metadata = [
        "artifact_type=a52xq-gki-5.10-pmic-spmi-geni-provider-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"planned_probes={len(PROBES)}",
        f"compatible_checks={len(COMPATIBLES)}",
        f"compatible_exact_source_matches={exact}",
        f"compatible_alias_or_port_required={len(COMPATIBLES) - exact}",
        "source_policy=pinned-official-gki-only",
        "output_scope=individual-object-compilation-and-source-match-audit-only",
        "flashable=no",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 20) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "undefined reference", "No rule to make target",
        "No such file or directory", "implicit declaration", "warning:",
    )
    selected: list[str] = []
    lines = path.read_text(errors="replace").splitlines()
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= limit:
            break
    return selected or [line.strip() for line in lines[-12:] if line.strip()] or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe") for row in rows} != set(PROBES):
        raise SystemExit("PMIC/SPMI/GENI compile status probe set mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    compiled = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    missing = sum(row.get("result") == "source-missing" for row in rows)
    report = [
        "# A52xq GKI 5.10 PMIC, SPMI, watchdog, and GENI provider probe", "",
        "## Result", "",
        f"- compiled: **{compiled}**",
        f"- compile failures: **{failed}**",
        f"- Kconfig blocked: **{blocked}**",
        f"- source missing: **{missing}**", "",
        "## Compatibility interpretation", "",
        "An exact source match means the pinned GKI tree contains that downstream-compatible string.",
        "A missing exact match does not by itself prove the subsystem is absent; it can indicate a compatible alias or child-node contract that needs separate review.", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "",
            f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
            f"- result: **{row['result']}**",
            f"- exit code: `{row['exit_code']}`",
            f"- object produced: `{row['object_produced']}`", "",
            "First diagnostics:", "",
        ])
        report.extend(
            f"- `{line.replace('`', chr(39))}`"
            for line in diagnostics(artifact / "logs" / f"{probe}.log")
        )
        report.append("")
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([
        f"compiled_success={compiled}", f"compile_failed={failed}",
        f"config_blocked={blocked}", f"source_missing={missing}",
    ])
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(path for path in artifact.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--gki", type=Path, required=True)
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
