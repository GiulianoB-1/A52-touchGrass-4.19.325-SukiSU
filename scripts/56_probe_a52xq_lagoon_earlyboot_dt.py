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
SOURCE_DTS = Path("arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi")
STAGED_DTS = Path("arch/arm64/boot/dts/qcom/lagoon-earlyboot-probe.dtsi")
WRAPPER_DTS = Path("arch/arm64/boot/dts/qcom/lagoon-earlyboot-probe.dts")
ANGLE_INCLUDE = re.compile(r'^\s*#include\s+<([^>]+)>', re.MULTILINE)


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
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()

    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")

    source = touchgrass / SOURCE_DTS
    if not source.is_file():
        raise SystemExit(f"missing touchGrass base DTS: {SOURCE_DTS}")
    source_text = source.read_text(errors="replace")

    forbidden_local_overlays = (
        "lagoon-audio-overlay.dtsi",
        "lagoon-sde-display.dtsi",
        "lagoon-camera.dtsi",
        "lagoon-vidc.dtsi",
    )
    local_includes = re.findall(r'^\s*#include\s+"([^"]+)"', source_text, re.MULTILINE)
    forbidden_found = sorted(set(local_includes).intersection(forbidden_local_overlays))
    if forbidden_found:
        raise SystemExit(f"base lagoon.dtsi unexpectedly includes excluded overlays: {forbidden_found}")

    artifact.mkdir(parents=True, exist_ok=True)
    destination = gki / STAGED_DTS
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = sha256(destination) if destination.is_file() else "<absent>"
    shutil.copy2(source, destination)

    wrapper = gki / WRAPPER_DTS
    wrapper.write_text(
        "/dts-v1/;\n\n"
        "#include \"lagoon-earlyboot-probe.dtsi\"\n\n"
        "/ {\n"
        "\tmodel = \"Samsung A52xq Lagoon early-boot compile probe\";\n"
        "\tcompatible = \"qcom,lagoon-mtp\", \"qcom,lagoon\";\n"
        "};\n"
    )

    binding_rows: list[dict[str, str]] = []
    staged_paths = [STAGED_DTS.as_posix(), WRAPPER_DTS.as_posix()]
    for include_name in sorted(set(ANGLE_INCLUDE.findall(source_text))):
        relative = Path("include") / include_name
        gki_path = gki / relative
        touchgrass_path = touchgrass / relative
        existed = gki_path.is_file()
        source_hash = sha256(touchgrass_path) if touchgrass_path.is_file() else "<absent>"
        before_hash = sha256(gki_path) if existed else "<absent>"

        action = "kept-gki"
        if not existed:
            if not touchgrass_path.is_file():
                raise SystemExit(f"missing binding in both trees: {include_name}")
            gki_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(touchgrass_path, gki_path)
            staged_paths.append(relative.as_posix())
            action = "copied-touchgrass"

        binding_rows.append({
            "include": include_name,
            "action": action,
            "touchgrass_sha256": source_hash,
            "gki_before_sha256": before_hash,
            "gki_after_sha256": sha256(gki_path),
        })

    write_tsv(
        artifact / "binding-resolution.tsv",
        ["include", "action", "touchgrass_sha256", "gki_before_sha256", "gki_after_sha256"],
        binding_rows,
    )
    (artifact / "source-summary.tsv").write_text(
        "source\tgki_destination\tgki_before_sha256\ttouchgrass_sha256\tgki_after_sha256\n"
        f"{SOURCE_DTS.as_posix()}\t{STAGED_DTS.as_posix()}\t{before}\t"
        f"{sha256(source)}\t{sha256(destination)}\n"
    )

    subprocess.run(["git", "-C", str(gki), "add", "-N", "--", *staged_paths], check=True)
    patch = output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    if not patch:
        raise SystemExit("device-tree staging produced no GKI diff")
    (artifact / "lagoon-earlyboot-dt-port.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-lagoon-earlyboot-dt-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"binding_include_count={len(binding_rows)}",
        "source_scope=lagoon.dtsi-only",
        "excluded_overlays=samsung-board,display,camera,audio,wifi,modem",
        "output_scope=syntax-and-reference-validation-only",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 30) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    lines = path.read_text(errors="replace").splitlines()
    patterns = (
        "error:", "fatal error:", "syntax error", "reference to non-existent node",
        "not found", "No such file", "undefined", "parse error",
    )
    selected: list[str] = []
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= limit:
            break
    if not selected:
        selected = [line.strip() for line in lines[-12:] if line.strip()]
    return selected or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1 or rows[0].get("probe") != "lagoon-base-dt":
        raise SystemExit("device-tree compile status mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    row = rows[0]
    report = [
        "# A52xq GKI 5.10 Lagoon early-boot device-tree probe", "",
        "## Scope", "",
        "- Source: `lagoon.dtsi` only",
        "- Samsung board and multimedia overlays are deliberately excluded.",
        "- The generated DTB is a compile probe and is not flashable.", "",
        "## Result", "",
        f"- preprocessing: **{row['preprocess_result']}**",
        f"- DTC compile: **{row['dtc_result']}**",
        f"- exit code: `{row['exit_code']}`",
        f"- preprocessed bytes: `{row['preprocessed_bytes']}`",
        f"- DTB bytes: `{row['dtb_bytes']}`", "",
        "First diagnostics:", "",
    ]
    report.extend(f"- `{line.replace('`', chr(39))}`" for line in diagnostics(artifact / "logs" / "lagoon-base-dt.log"))
    report.append("")
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([
        f"preprocess_result={row['preprocess_result']}",
        f"dtc_result={row['dtc_result']}",
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
