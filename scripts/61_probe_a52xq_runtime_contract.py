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
    "legacy-cgroups": {
        "target": ",".join((
            "kernel/cgroup/cgroup.o", "kernel/cgroup/rstat.o",
            "kernel/cgroup/namespace.o", "kernel/cgroup/cgroup-v1.o",
            "kernel/cgroup/freezer.o", "kernel/cgroup/legacy_freezer.o",
            "block/blk-cgroup.o", "block/blk-cgroup-rwstat.o",
        )),
        "config": "CONFIG_CGROUPS",
        "description": "Legacy Android cgroup v1 freezer and blkio contract",
        "enable": (
            "CONFIG_CGROUPS", "CONFIG_CGROUP_FREEZER", "CONFIG_BLK_CGROUP",
            "CONFIG_BLK_CGROUP_RWSTAT",
        ),
        "sources": (
            "kernel/cgroup/cgroup.c", "kernel/cgroup/rstat.c",
            "kernel/cgroup/namespace.c", "kernel/cgroup/cgroup-v1.c",
            "kernel/cgroup/freezer.c", "kernel/cgroup/legacy_freezer.c",
            "block/blk-cgroup.c", "block/blk-cgroup-rwstat.c",
        ),
    },
    "geni-console": {
        "target": ",".join((
            "drivers/soc/qcom/qcom-geni-se.o",
            "drivers/tty/serial/serial_core.o",
            "drivers/tty/serial/earlycon.o",
            "drivers/tty/serial/qcom_geni_serial.o",
        )),
        "config": "CONFIG_SERIAL_QCOM_GENI_CONSOLE",
        "description": "Qualcomm GENI UART, earlycon, and kernel console",
        "enable": (
            "CONFIG_ARCH_QCOM", "CONFIG_TTY", "CONFIG_SERIAL_CORE",
            "CONFIG_QCOM_GENI_SE", "CONFIG_SERIAL_QCOM_GENI",
            "CONFIG_SERIAL_QCOM_GENI_CONSOLE", "CONFIG_SERIAL_EARLYCON",
        ),
        "sources": (
            "drivers/soc/qcom/qcom-geni-se.c",
            "drivers/tty/serial/serial_core.c",
            "drivers/tty/serial/earlycon.c",
            "drivers/tty/serial/qcom_geni_serial.c",
        ),
    },
    "pstore-core": {
        "target": "fs/pstore/inode.o,fs/pstore/platform.o,fs/pstore/pmsg.o",
        "config": "CONFIG_PSTORE",
        "description": "Persistent-store filesystem, console capture, and Android pmsg",
        "enable": (
            "CONFIG_PSTORE", "CONFIG_PSTORE_CONSOLE", "CONFIG_PSTORE_PMSG",
        ),
        "sources": (
            "fs/pstore/inode.c", "fs/pstore/platform.c", "fs/pstore/pmsg.c",
        ),
    },
    "ramoops": {
        "target": "fs/pstore/ram.o,fs/pstore/ram_core.o",
        "config": "CONFIG_PSTORE_RAM",
        "description": "Reserved-memory ramoops backend",
        "enable": (
            "CONFIG_PSTORE", "CONFIG_PSTORE_CONSOLE", "CONFIG_PSTORE_PMSG",
            "CONFIG_PSTORE_RAM",
        ),
        "sources": ("fs/pstore/ram.c", "fs/pstore/ram_core.c"),
    },
}


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
            for relative in data["sources"]
            if (gki / relative).is_file()
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
            "probe", "description", "target", "config_symbol",
            "enable_symbols", "source_files", "missing_sources", "source_sha256",
        ],
        rows,
    )
    metadata = [
        "artifact_type=a52xq-gki-5.10-runtime-contract-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"planned_probes={len(PROBES)}",
        "probe_scope=legacy-cgroups,geni-console,pstore-core,ramoops",
        "source_policy=pinned-official-gki-only",
        "output_scope=individual-object-compilation-only",
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
        raise SystemExit("runtime-contract compile status probe set mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    compiled = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    missing = sum(row.get("result") == "source-missing" for row in rows)
    report = [
        "# A52xq GKI 5.10 runtime-contract probe", "", "## Result", "",
        f"- compiled: **{compiled}**", f"- compile failures: **{failed}**",
        f"- Kconfig blocked: **{blocked}**", f"- source missing: **{missing}**", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "", f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
            f"- result: **{row['result']}**", f"- exit code: `{row['exit_code']}`",
            f"- object produced: `{row['object_produced']}`", "", "First diagnostics:", "",
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
