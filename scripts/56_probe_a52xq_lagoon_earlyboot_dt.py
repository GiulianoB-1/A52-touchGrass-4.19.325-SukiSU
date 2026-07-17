#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"
TOUCHGRASS_SHA = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
SOURCE_ROOT = Path("arch/arm64/boot/dts/vendor/qcom")
DEST_ROOT = Path("arch/arm64/boot/dts/qcom")
ROOT_SOURCE = Path("lagoon.dtsi")
ROOT_DEST = Path("lagoon-earlyboot-probe.dtsi")
WRAPPER_DTS = Path("lagoon-earlyboot-probe.dts")
ANGLE_INCLUDE = re.compile(r'^\s*#include\s+<([^>]+)>', re.MULTILINE)
LOCAL_INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)
REFERENCE_ONLY_PREFIXES = ("camera/", "lagoon-audio", "lagoon-sde", "lagoon-vidc")


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


def normalize_local_include(source_root: Path, current: Path, include_name: str) -> Path | None:
    candidate = (source_root / current.parent / include_name).resolve()
    try:
        return candidate.relative_to(source_root.resolve())
    except ValueError:
        return None


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()

    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")

    source_root = (touchgrass / SOURCE_ROOT).resolve()
    dest_root = (gki / DEST_ROOT).resolve()
    if not (source_root / ROOT_SOURCE).is_file():
        raise SystemExit(f"missing touchGrass base DTS: {SOURCE_ROOT / ROOT_SOURCE}")

    artifact.mkdir(parents=True, exist_ok=True)
    dest_root.mkdir(parents=True, exist_ok=True)

    queue: deque[tuple[Path, str]] = deque([(ROOT_SOURCE, "<root>")])
    visited: set[Path] = set()
    local_rows: list[dict[str, str]] = []
    angle_includes: set[str] = set()
    staged_paths: list[str] = []
    unresolved_local = 0
    reference_only = 0

    while queue:
        relative, included_from = queue.popleft()
        if relative in visited:
            continue
        visited.add(relative)
        source = source_root / relative

        if not source.is_file():
            unresolved_local += 1
            local_rows.append({
                "relative_path": relative.as_posix(),
                "included_from": included_from,
                "action": "unresolved",
                "scope": "unknown",
                "source_sha256": "<absent>",
                "gki_before_sha256": "<absent>",
                "gki_after_sha256": "<absent>",
            })
            continue

        destination_relative = ROOT_DEST if relative == ROOT_SOURCE else relative
        destination = dest_root / destination_relative
        before = sha256(destination) if destination.is_file() else "<absent>"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        staged_paths.append((DEST_ROOT / destination_relative).as_posix())

        relative_text = relative.as_posix()
        scope = "reference-only" if relative_text.startswith(REFERENCE_ONLY_PREFIXES) else "early-boot"
        reference_only += int(scope == "reference-only")
        local_rows.append({
            "relative_path": relative_text,
            "included_from": included_from,
            "action": "copied-touchgrass",
            "scope": scope,
            "source_sha256": sha256(source),
            "gki_before_sha256": before,
            "gki_after_sha256": sha256(destination),
        })

        text = source.read_text(errors="replace")
        angle_includes.update(ANGLE_INCLUDE.findall(text))
        for include_name in LOCAL_INCLUDE.findall(text):
            child = normalize_local_include(source_root, relative, include_name)
            if child is None:
                unresolved_local += 1
                local_rows.append({
                    "relative_path": include_name,
                    "included_from": relative_text,
                    "action": "outside-source-root",
                    "scope": "unknown",
                    "source_sha256": "<unresolved>",
                    "gki_before_sha256": "<unresolved>",
                    "gki_after_sha256": "<unresolved>",
                })
            else:
                queue.append((child, relative_text))

    wrapper = dest_root / WRAPPER_DTS
    wrapper.write_text(
        '/dts-v1/;\n\n'
        '#include "lagoon-earlyboot-probe.dtsi"\n\n'
        '/ {\n'
        '\tmodel = "Samsung A52xq Lagoon early-boot compile probe";\n'
        '\tcompatible = "qcom,lagoon-mtp", "qcom,lagoon";\n'
        '};\n'
    )
    staged_paths.append((DEST_ROOT / WRAPPER_DTS).as_posix())

    binding_rows: list[dict[str, str]] = []
    unresolved_binding = 0
    for include_name in sorted(angle_includes):
        relative = Path("include") / include_name
        gki_path = gki / relative
        touchgrass_path = touchgrass / relative
        existed = gki_path.is_file()
        action = "kept-gki"

        if not existed and touchgrass_path.is_file():
            gki_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(touchgrass_path, gki_path)
            staged_paths.append(relative.as_posix())
            action = "copied-touchgrass"
        elif not existed:
            action = "unresolved"
            unresolved_binding += 1

        binding_rows.append({
            "include": include_name,
            "action": action,
            "touchgrass_sha256": sha256(touchgrass_path) if touchgrass_path.is_file() else "<absent>",
            "gki_before_sha256": sha256(gki_path) if existed else "<absent>",
            "gki_after_sha256": sha256(gki_path) if gki_path.is_file() else "<absent>",
        })

    write_tsv(
        artifact / "local-include-graph.tsv",
        ["relative_path", "included_from", "action", "scope",
         "source_sha256", "gki_before_sha256", "gki_after_sha256"],
        local_rows,
    )
    write_tsv(
        artifact / "binding-resolution.tsv",
        ["include", "action", "touchgrass_sha256", "gki_before_sha256", "gki_after_sha256"],
        binding_rows,
    )
    (artifact / "source-summary.tsv").write_text(
        "source_scope\tlocal_file_count\treference_only_file_count\t"
        "unresolved_local_include_count\tbinding_include_count\tunresolved_binding_count\n"
        f"recursive-lagoon-base-include-graph\t{len(visited)}\t{reference_only}\t"
        f"{unresolved_local}\t{len(binding_rows)}\t{unresolved_binding}\n"
    )

    existing_paths = sorted({path for path in staged_paths if (gki / path).exists()})
    subprocess.run(["git", "-C", str(gki), "add", "-N", "--", *existing_paths], check=True)
    patch = output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    (artifact / "lagoon-earlyboot-dt-port.patch").write_text(patch + ("\n" if patch else ""))

    metadata = [
        "artifact_type=a52xq-gki-5.10-lagoon-earlyboot-dt-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"local_file_count={len(visited)}",
        f"reference_only_file_count={reference_only}",
        f"unresolved_local_include_count={unresolved_local}",
        f"binding_include_count={len(binding_rows)}",
        f"unresolved_binding_count={unresolved_binding}",
        "source_scope=recursive-lagoon-base-include-graph",
        "reference_only_subsystems=camera,display,audio,video",
        "output_scope=syntax-and-reference-validation-only",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 40) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "syntax error", "reference to non-existent node",
        "not found", "No such file", "undefined", "parse error",
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
    if len(rows) != 1 or rows[0].get("probe") != "lagoon-base-dt":
        raise SystemExit("device-tree compile status mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    row = rows[0]
    report = [
        "# A52xq GKI 5.10 Lagoon early-boot device-tree probe", "",
        "## Scope", "",
        "- Root source: `lagoon.dtsi`",
        "- Its local include graph is staged recursively for reference resolution.",
        "- Camera, display, audio, and video files are reference-only and are not driver-port approvals.",
        "- The generated DTB is a compile probe and is not flashable.", "",
        "## Result", "",
        f"- preprocessing: **{row['preprocess_result']}**",
        f"- DTC compile: **{row['dtc_result']}**",
        f"- exit code: `{row['exit_code']}`",
        f"- preprocessed bytes: `{row['preprocessed_bytes']}`",
        f"- DTB bytes: `{row['dtb_bytes']}`", "",
        "First diagnostics:", "",
    ]
    report.extend(
        f"- `{line.replace('`', chr(39))}`"
        for line in diagnostics(artifact / "logs" / "lagoon-base-dt.log")
    )
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
