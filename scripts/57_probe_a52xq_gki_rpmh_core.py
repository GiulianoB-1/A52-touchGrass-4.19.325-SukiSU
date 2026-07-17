#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"

PROBES = {
    "command-db": (
        "drivers/soc/qcom/cmd-db.c",
        "drivers/soc/qcom/cmd-db.o",
        "CONFIG_QCOM_COMMAND_DB",
        "Qualcomm command database",
    ),
    "rpmh-core": (
        "drivers/soc/qcom/rpmh.c",
        "drivers/soc/qcom/rpmh.o",
        "CONFIG_QCOM_RPMH",
        "Qualcomm RPMh transport core",
    ),
    "rpmh-rsc": (
        "drivers/soc/qcom/rpmh-rsc.c",
        "drivers/soc/qcom/rpmh-rsc.o",
        "CONFIG_QCOM_RPMH",
        "Qualcomm RPMh resource state coordinator",
    ),
    "rpmh-regulator": (
        "drivers/regulator/qcom-rpmh-regulator.c",
        "drivers/regulator/qcom-rpmh-regulator.o",
        "CONFIG_REGULATOR_QCOM_RPMH",
        "Qualcomm RPMh regulator provider",
    ),
    "rpmh-clock": (
        "drivers/clk/qcom/clk-rpmh.c",
        "drivers/clk/qcom/clk-rpmh.o",
        "CONFIG_QCOM_CLK_RPMH",
        "Qualcomm RPMh clock provider",
    ),
    "rpmh-power-domain": (
        "drivers/soc/qcom/rpmhpd.c",
        "drivers/soc/qcom/rpmhpd.o",
        "CONFIG_QCOM_RPMHPD",
        "Qualcomm RPMh power-domain provider",
    ),
}

SNAPSHOTS = (
    "drivers/soc/qcom/Kconfig",
    "drivers/soc/qcom/Makefile",
    "drivers/regulator/Kconfig",
    "drivers/regulator/Makefile",
    "drivers/clk/qcom/Kconfig",
    "drivers/clk/qcom/Makefile",
    "drivers/interconnect/Kconfig",
    "drivers/interconnect/Makefile",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(root: Path) -> str:
    import subprocess

    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def inventory(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    artifact = args.output.resolve()
    head = git_head(gki)
    if head != GKI_SHA:
        raise SystemExit(f"unexpected GKI revision: {head}")

    artifact.mkdir(parents=True, exist_ok=True)
    snapshot_dir = artifact / "source-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    probe_rows: list[dict[str, str]] = []
    for probe, (source, target, symbol, purpose) in PROBES.items():
        source_path = gki / source
        probe_rows.append({
            "probe": probe,
            "source": source,
            "target": target,
            "config_symbol": symbol,
            "purpose": purpose,
            "source_present": "yes" if source_path.is_file() else "no",
            "source_sha256": sha256(source_path) if source_path.is_file() else "<absent>",
        })
    write_tsv(
        artifact / "rpmh-source-inventory.tsv",
        [
            "probe", "source", "target", "config_symbol", "purpose",
            "source_present", "source_sha256",
        ],
        probe_rows,
    )

    snapshot_rows: list[dict[str, str]] = []
    for relative in SNAPSHOTS:
        source = gki / relative
        destination = snapshot_dir / relative.replace("/", "__")
        if source.is_file():
            shutil.copy2(source, destination)
            snapshot_rows.append({
                "path": relative,
                "present": "yes",
                "sha256": sha256(source),
            })
        else:
            snapshot_rows.append({
                "path": relative,
                "present": "no",
                "sha256": "<absent>",
            })
    write_tsv(
        artifact / "kconfig-makefile-inventory.tsv",
        ["path", "present", "sha256"],
        snapshot_rows,
    )

    interconnect_root = gki / "drivers/interconnect"
    interconnect_files = sorted(
        path.relative_to(gki).as_posix()
        for path in interconnect_root.rglob("*")
        if path.is_file()
    ) if interconnect_root.is_dir() else []
    (artifact / "interconnect-source-files.txt").write_text(
        "\n".join(interconnect_files) + ("\n" if interconnect_files else "")
    )

    qcom_interconnect = [
        path for path in interconnect_files
        if path.startswith("drivers/interconnect/qcom/")
    ]
    metadata = [
        "artifact_type=a52xq-gki-5.10-rpmh-core-compile-probe-not-flashable",
        f"gki_commit={head}",
        f"planned_probes={len(PROBES)}",
        f"present_probe_sources={sum(row['source_present'] == 'yes' for row in probe_rows)}",
        f"interconnect_file_count={len(interconnect_files)}",
        f"qcom_interconnect_file_count={len(qcom_interconnect)}",
        "source_policy=compile-pinned-gki-native-rpmh-implementations",
        "interconnect_policy=inventory-only-until-provider-set-is-confirmed",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 14) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "undefined reference", "No rule to make target",
        "No such file", "implicit declaration", "undeclared", "incomplete type",
    )
    selected: list[str] = []
    lines = path.read_text(errors="replace").splitlines()
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = line.strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= limit:
            break
    return selected or [line.strip() for line in lines[-10:] if line.strip()] or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status_path = args.status_file.resolve()
    with status_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe") for row in rows} != set(PROBES):
        raise SystemExit("RPMh probe status set mismatch")
    shutil.copy2(status_path, artifact / "compile-status.tsv")

    counts = {
        result: sum(row.get("result") == result for row in rows)
        for result in ("compiled", "compile-failed", "config-blocked", "source-missing")
    }
    report = [
        "# A52xq GKI 5.10 Qualcomm RPMh core probe", "",
        "## Result", "",
        f"- compiled: **{counts['compiled']}**",
        f"- compile failures: **{counts['compile-failed']}**",
        f"- Kconfig blocked: **{counts['config-blocked']}**",
        f"- source missing: **{counts['source-missing']}**", "",
        "The interconnect tree is inventoried but not yet treated as a ported provider set.", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "",
            f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
            f"- source present: `{row['source_present']}`",
            f"- result: **{row['result']}**",
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
    metadata.extend(f"{key.replace('-', '_')}={value}" for key, value in counts.items())
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(
        path for path in artifact.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    inventory_parser = commands.add_parser("inventory")
    inventory_parser.add_argument("--gki", type=Path, required=True)
    inventory_parser.add_argument("--output", type=Path, required=True)
    inventory_parser.set_defaults(func=inventory)

    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--status-file", type=Path, required=True)
    finalize_parser.set_defaults(func=finalize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
