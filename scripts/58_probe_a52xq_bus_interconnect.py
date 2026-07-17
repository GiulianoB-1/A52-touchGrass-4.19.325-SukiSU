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

PROBES = {
    "interconnect-core": (
        "drivers/interconnect/core.c",
        "drivers/interconnect/core.o",
        "CONFIG_INTERCONNECT",
        "Generic Linux interconnect core",
    ),
    "interconnect-bulk": (
        "drivers/interconnect/bulk.c",
        "drivers/interconnect/bulk.o",
        "CONFIG_INTERCONNECT",
        "Generic bulk interconnect helpers",
    ),
    "qcom-bcm-voter": (
        "drivers/interconnect/qcom/bcm-voter.c",
        "drivers/interconnect/qcom/icc-bcm-voter.o",
        "CONFIG_INTERCONNECT_QCOM_BCM_VOTER",
        "Qualcomm BCM voter",
    ),
    "qcom-icc-rpmh": (
        "drivers/interconnect/qcom/icc-rpmh.c",
        "drivers/interconnect/qcom/icc-rpmh.o",
        "CONFIG_INTERCONNECT_QCOM_RPMH",
        "Qualcomm RPMh interconnect helper",
    ),
    "qcom-sc7180": (
        "drivers/interconnect/qcom/sc7180.c",
        "drivers/interconnect/qcom/sc7180.o",
        "CONFIG_INTERCONNECT_QCOM_SC7180",
        "Qualcomm SC7180 provider candidate",
    ),
    "qcom-sm8250": (
        "drivers/interconnect/qcom/sm8250.c",
        "drivers/interconnect/qcom/sm8250.o",
        "CONFIG_INTERCONNECT_QCOM_SM8250",
        "Qualcomm SM8250 provider candidate",
    ),
}

SCAN_ROOTS = (
    "drivers/soc/qcom",
    "drivers/platform/msm",
    "drivers/devfreq",
    "include/linux",
    "include/soc/qcom",
    "include/dt-bindings/msm",
)

BUS_TOKENS = (
    "msm_bus",
    "msm-bus",
    "MSM_BUS_",
    "qcom,msm-bus-device",
    "qcom,msm-bus-rsc",
    "qcom,bcm-dev",
    "qcom,bcms",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def classify(path: str, text: str) -> str:
    if "msm_bus" in path or "msm-bus" in path:
        return "legacy-msm-bus-source"
    if "qcom,msm-bus-device" in text or "qcom,msm-bus-rsc" in text:
        return "legacy-provider-compatible"
    if "msm_bus_scale_" in text:
        return "legacy-client-api"
    if "qcom,bcm-dev" in text or "qcom,bcms" in text:
        return "downstream-bcm-description"
    if "MSM_BUS_" in text:
        return "legacy-bus-id-or-api"
    return "bus-related"


def inventory(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()
    gki_head = git_head(gki)
    tg_head = git_head(touchgrass)
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected revisions: gki={gki_head}, touchgrass={tg_head}")

    artifact.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for root_name in SCAN_ROOTS:
        root = touchgrass / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            relative = path.relative_to(touchgrass).as_posix()
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            matched = sorted(token for token in BUS_TOKENS if token in relative or token in text)
            if not matched or relative in seen:
                continue
            seen.add(relative)
            rows.append({
                "path": relative,
                "classification": classify(relative, text),
                "matched_tokens": ",".join(matched),
                "size_bytes": str(path.stat().st_size),
                "sha256": sha256(path),
            })

    rows.sort(key=lambda row: (row["classification"], row["path"]))
    write_tsv(
        artifact / "touchgrass-bus-source-inventory.tsv",
        ["path", "classification", "matched_tokens", "size_bytes", "sha256"],
        rows,
    )

    source_paths = [row["path"] for row in rows if row["path"].endswith((".c", ".h"))]
    (artifact / "touchgrass-bus-source-paths.txt").write_text(
        "\n".join(source_paths) + ("\n" if source_paths else "")
    )

    gki_rows: list[dict[str, str]] = []
    for probe, (source, target, symbol, purpose) in PROBES.items():
        source_path = gki / source
        gki_rows.append({
            "probe": probe,
            "source": source,
            "target": target,
            "config_symbol": symbol,
            "purpose": purpose,
            "source_present": "yes" if source_path.is_file() else "no",
            "source_sha256": sha256(source_path) if source_path.is_file() else "<absent>",
        })
    write_tsv(
        artifact / "gki-interconnect-probe-inventory.tsv",
        [
            "probe", "source", "target", "config_symbol", "purpose",
            "source_present", "source_sha256",
        ],
        gki_rows,
    )

    snapshots = (
        "drivers/interconnect/Kconfig",
        "drivers/interconnect/Makefile",
        "drivers/interconnect/qcom/Kconfig",
        "drivers/interconnect/qcom/Makefile",
    )
    snapshot_dir = artifact / "source-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for relative in snapshots:
        source = gki / relative
        if source.is_file():
            shutil.copy2(source, snapshot_dir / relative.replace("/", "__"))

    classifications: dict[str, int] = {}
    for row in rows:
        classifications[row["classification"]] = classifications.get(row["classification"], 0) + 1
    metadata = [
        "artifact_type=a52xq-gki-5.10-bus-interconnect-characterization-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"touchgrass_bus_related_file_count={len(rows)}",
        f"touchgrass_bus_source_path_count={len(source_paths)}",
        f"planned_gki_probes={len(PROBES)}",
        "migration_question=legacy-msm-bus-port-versus-generic-interconnect-translation",
    ]
    metadata.extend(
        f"classification_{re.sub(r'[^a-z0-9]+', '_', key.lower()).strip('_')}={value}"
        for key, value in sorted(classifications.items())
    )
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
        raise SystemExit("interconnect probe status set mismatch")
    shutil.copy2(status_path, artifact / "compile-status.tsv")

    results = {
        result: sum(row.get("result") == result for row in rows)
        for result in ("compiled", "compile-failed", "config-blocked", "source-missing")
    }
    report = [
        "# A52xq bus and GKI interconnect characterization", "",
        "## GKI object probe result", "",
        f"- compiled: **{results['compiled']}**",
        f"- compile failures: **{results['compile-failed']}**",
        f"- Kconfig blocked: **{results['config-blocked']}**",
        f"- source missing: **{results['source-missing']}**", "",
        "The downstream tree still uses the legacy MSM bus API and DT schema. This artifact inventories that stack before any migration choice is made.", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "",
            f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
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
    metadata.extend(f"{key.replace('-', '_')}={value}" for key, value in results.items())
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
    inventory_parser.add_argument("--touchgrass", type=Path, required=True)
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
