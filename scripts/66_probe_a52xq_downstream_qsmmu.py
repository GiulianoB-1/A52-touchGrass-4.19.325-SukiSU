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
SOURCE_FILES = (
    "drivers/iommu/arm-smmu.c",
    "drivers/iommu/arm-smmu-regs.h",
    "drivers/iommu/arm-smmu-debug.h",
    "drivers/iommu/iommu-logger.h",
)
DEST_DIR = Path("drivers/iommu/legacy-qsmmu")
TARGET = "drivers/iommu/legacy-qsmmu/arm-smmu-downstream.o"
SYSTEM_INCLUDE = re.compile(r'^\s*#\s*include\s+<([^>]+)>', re.MULTILINE)
COMPAT_HEADER = "qsmmu-compat.h"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def append_once(path: Path, marker: str, line: str) -> None:
    text = path.read_text(errors="replace")
    if marker not in text:
        path.write_text(text.rstrip() + "\n" + line.rstrip() + "\n")


def copy_normalized_source(source: Path, target: Path) -> int:
    lines = source.read_text(errors="replace").splitlines()
    normalized = [line.rstrip(" \t") for line in lines]
    changed = sum(before != after for before, after in zip(lines, normalized))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(normalized) + "\n")
    target.chmod(0o644)
    return changed


def adapt_downstream_driver(path: Path) -> list[str]:
    text = path.read_text(errors="replace")
    adaptations: list[str] = []

    legacy_dma = "#include <asm/dma-iommu.h>\n"
    if legacy_dma in text:
        text = text.replace(legacy_dma, "", 1)
        adaptations.append("removed-unused-legacy-asm-dma-iommu-include")

    samsung_debug = "#include <linux/sec_debug.h>\n"
    if samsung_debug in text:
        text = text.replace(samsung_debug, f'#include "{COMPAT_HEADER}"\n', 1)
        adaptations.append("replaced-samsung-sec-debug-with-local-noop-hooks")

    path.write_text(text)
    return adaptations


def write_compat_header(path: Path) -> None:
    path.write_text(
        "/* Isolated compile-probe compatibility hooks. */\n"
        "#ifndef A52XQ_LEGACY_QSMMU_COMPAT_H\n"
        "#define A52XQ_LEGACY_QSMMU_COMPAT_H\n\n"
        "static inline void sec_debug_save_smmu_info_asf_fatal(void) {}\n"
        "static inline void sec_debug_save_smmu_info_fatal(void) {}\n\n"
        "#endif\n"
    )
    path.chmod(0o644)


def header_path(root: Path, include_name: str) -> Path:
    if include_name.startswith("asm/"):
        return root / "arch/arm64/include" / include_name
    return root / "include" / include_name


def stage_missing_header_graph(
    gki: Path,
    touchgrass: Path,
    seed_paths: list[Path],
) -> tuple[list[dict[str, str]], list[str], int, int]:
    queue: deque[tuple[str, str]] = deque()
    for seed in seed_paths:
        for include_name in SYSTEM_INCLUDE.findall(seed.read_text(errors="replace")):
            queue.append((include_name, seed.relative_to(gki).as_posix()))

    visited: set[str] = set()
    rows: list[dict[str, str]] = []
    staged_paths: list[str] = []
    normalized_lines = 0
    unresolved = 0

    while queue:
        include_name, requested_by = queue.popleft()
        if include_name in visited:
            continue
        visited.add(include_name)
        gki_header = header_path(gki, include_name)
        downstream_header = header_path(touchgrass, include_name)

        if gki_header.is_file():
            rows.append({
                "include": include_name,
                "requested_by": requested_by,
                "action": "kept-gki",
                "downstream_sha256": sha256(downstream_header) if downstream_header.is_file() else "<absent>",
                "gki_before_sha256": sha256(gki_header),
                "gki_after_sha256": sha256(gki_header),
                "normalized_trailing_whitespace_lines": "0",
            })
            continue

        if not downstream_header.is_file():
            unresolved += 1
            rows.append({
                "include": include_name,
                "requested_by": requested_by,
                "action": "unresolved-in-both-pinned-trees",
                "downstream_sha256": "<absent>",
                "gki_before_sha256": "<absent>",
                "gki_after_sha256": "<absent>",
                "normalized_trailing_whitespace_lines": "0",
            })
            continue

        downstream_hash = sha256(downstream_header)
        changed = copy_normalized_source(downstream_header, gki_header)
        normalized_lines += changed
        staged_relative = gki_header.relative_to(gki).as_posix()
        staged_paths.append(staged_relative)
        rows.append({
            "include": include_name,
            "requested_by": requested_by,
            "action": "copied-pinned-touchgrass-missing-from-gki",
            "downstream_sha256": downstream_hash,
            "gki_before_sha256": "<absent>",
            "gki_after_sha256": sha256(gki_header),
            "normalized_trailing_whitespace_lines": str(changed),
        })

        for dependency in SYSTEM_INCLUDE.findall(downstream_header.read_text(errors="replace")):
            queue.append((dependency, staged_relative))

    return rows, staged_paths, normalized_lines, unresolved


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()
    artifact.mkdir(parents=True, exist_ok=True)

    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")

    destination = gki / DEST_DIR
    destination.mkdir(parents=True, exist_ok=True)
    source_rows: list[dict[str, str]] = []
    staged_source_paths: list[Path] = []
    normalized_source_lines = 0
    adaptations: list[str] = []

    for relative in SOURCE_FILES:
        source = touchgrass / relative
        if not source.is_file():
            raise SystemExit(f"missing downstream QSMMU source: {relative}")
        name = Path(relative).name
        if name == "arm-smmu.c":
            name = "arm-smmu-downstream.c"
        target = destination / name
        changed = copy_normalized_source(source, target)
        normalized_source_lines += changed
        if name == "arm-smmu-downstream.c":
            adaptations.extend(adapt_downstream_driver(target))
        staged_source_paths.append(target)
        source_rows.append({
            "source_path": relative,
            "staged_path": target.relative_to(gki).as_posix(),
            "bytes": str(target.stat().st_size),
            "sha256": sha256(target),
            "normalized_trailing_whitespace_lines": str(changed),
        })

    compat_path = destination / COMPAT_HEADER
    write_compat_header(compat_path)
    staged_source_paths.append(compat_path)
    source_rows.append({
        "source_path": "<generated-isolated-compatibility-hooks>",
        "staged_path": compat_path.relative_to(gki).as_posix(),
        "bytes": str(compat_path.stat().st_size),
        "sha256": sha256(compat_path),
        "normalized_trailing_whitespace_lines": "0",
    })

    header_rows, staged_header_paths, normalized_header_lines, unresolved_headers = (
        stage_missing_header_graph(gki, touchgrass, staged_source_paths)
    )

    (destination / "Makefile").write_text(
        "obj-$(CONFIG_ARM_SMMU) += arm-smmu-downstream.o\n"
    )
    append_once(
        gki / "drivers/iommu/Makefile",
        "legacy-qsmmu/",
        "obj-$(CONFIG_ARM_SMMU) += legacy-qsmmu/",
    )

    write_tsv(
        artifact / "staged-files.tsv",
        [
            "source_path", "staged_path", "bytes", "sha256",
            "normalized_trailing_whitespace_lines",
        ],
        source_rows,
    )
    write_tsv(
        artifact / "compatibility-headers.tsv",
        [
            "include", "requested_by", "action", "downstream_sha256",
            "gki_before_sha256", "gki_after_sha256",
            "normalized_trailing_whitespace_lines",
        ],
        header_rows,
    )
    (artifact / "source-adaptations.txt").write_text("\n".join(adaptations) + "\n")

    staged = [row["staged_path"] for row in source_rows]
    staged.extend(staged_header_paths)
    staged.append((DEST_DIR / "Makefile").as_posix())
    subprocess.run(["git", "-C", str(gki), "add", "-N", "--", *staged], check=True)
    patch = output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    (artifact / "downstream-qsmmu-stage.patch").write_text(patch + "\n")

    copied_headers = sum(
        row["action"] == "copied-pinned-touchgrass-missing-from-gki"
        for row in header_rows
    )
    kept_headers = sum(row["action"] == "kept-gki" for row in header_rows)
    metadata = [
        "artifact_type=a52xq-gki-5.10-downstream-qsmmu-isolated-object-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"compile_target={TARGET}",
        f"staged_source_count={len(source_rows)}",
        f"source_adaptation_count={len(adaptations)}",
        f"compatibility_header_checks={len(header_rows)}",
        f"copied_missing_downstream_headers={copied_headers}",
        f"preserved_existing_gki_headers={kept_headers}",
        f"unresolved_header_count={unresolved_headers}",
        f"normalized_trailing_whitespace_lines={normalized_source_lines + normalized_header_lines}",
        "debug_hook_policy=samsung-crash-report-hooks-noop-only-in-isolated-compile-probe",
        "legacy_dma_header_policy=removed-because-no-declared-symbol-is-used-by-downstream-driver",
        "header_policy=preserve-existing-gki-copy-only-downstream-headers-absent-from-gki",
        "normalization_policy=line-endings-trailing-horizontal-whitespace-and-file-mode-only",
        "staging_policy=isolated-parallel-driver-no-replacement-of-gki-arm-smmu",
        "compiler_error_limit=unlimited",
        "link_test=no",
        "flashable=no",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 240) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "No rule to make target", "No such file or directory",
        "implicit declaration", "unknown type name", "redefinition", "conflicting types",
        "warning:",
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
    return selected or [line.strip() for line in lines[-30:] if line.strip()] or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1 or rows[0].get("target") != TARGET:
        raise SystemExit("downstream QSMMU compile status mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")
    row = rows[0]

    report = [
        "# A52xq downstream QSMMU v500 isolated compile probe", "",
        "## Safety", "",
        "- The downstream source is staged under an isolated directory.",
        "- GKI's working ARM-SMMU driver is not replaced.",
        "- Existing GKI headers remain authoritative; only absent downstream headers are copied.",
        "- Samsung crash-report hooks are no-op only inside this isolated compile probe.",
        "- This is an object-only compile probe with no link, Image, DTB, or boot packaging.", "",
        "## Result", "",
        f"- target: `{row['target']}`",
        f"- result: **{row['result']}**",
        f"- exit code: `{row['exit_code']}`",
        f"- object produced: `{row['object_produced']}`", "",
        "## Compiler diagnostics", "",
    ]
    report.extend(
        f"- `{line.replace('`', chr(39))}`"
        for line in diagnostics(artifact / "logs" / "downstream-qsmmu.log")
    )
    report.extend(["", "## Interpretation", "",
        "The compiler runs without Clang's default error-count cap, so this report inventories the complete visible 4.19-to-5.10 API boundary for the isolated QSMMU object.",
    ])
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([
        f"compile_result={row['result']}",
        f"compile_exit_code={row['exit_code']}",
        f"object_produced={row['object_produced']}",
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
    final_parser = commands.add_parser("finalize")
    final_parser.add_argument("--output", type=Path, required=True)
    final_parser.add_argument("--status-file", type=Path, required=True)
    final_parser.set_defaults(func=finalize)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
