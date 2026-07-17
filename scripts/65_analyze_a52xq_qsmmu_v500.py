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
TOKENS = (
    "qcom,qsmmu-v500",
    "qcom,qsmmuv500-tbu",
    "qcom,smmu-v2",
    "qsmmu-v500",
    "qsmmuv500",
)
LOCAL_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
SYSTEM_INCLUDE = re.compile(r'^\s*#\s*include\s+<([^>]+)>', re.MULTILINE)
FUNCTION_DEF = re.compile(
    r'(?m)^(?:static\s+)?(?:inline\s+)?(?:[A-Za-z_][A-Za-z0-9_\s\*]+?)\s+'
    r'([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{'
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def git_grep(root: Path, token: str) -> list[tuple[str, int, str]]:
    completed = subprocess.run(
        ["git", "-C", str(root), "grep", "-n", "-F", token, "--"],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode not in (0, 1):
        raise SystemExit(f"git grep failed in {root}: {completed.stderr.strip()}")
    rows: list[tuple[str, int, str]] = []
    for raw in completed.stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) != 3:
            continue
        path, line, text = parts
        try:
            number = int(line)
        except ValueError:
            continue
        rows.append((path, number, text.strip()))
    return rows


def file_kind(path: str) -> str:
    suffix = Path(path).suffix
    if path.startswith("drivers/iommu/"):
        return "iommu-driver"
    if path.startswith("arch/") and suffix in {".dts", ".dtsi"}:
        return "device-tree"
    if path.startswith("include/"):
        return "header-or-binding"
    if Path(path).name in {"Kconfig", "Makefile"}:
        return "build-metadata"
    return "other"


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def local_include_graph(root: Path, seeds: set[Path]) -> set[Path]:
    queue: deque[Path] = deque(sorted(seeds))
    visited: set[Path] = set()
    while queue:
        relative = queue.popleft()
        if relative in visited:
            continue
        visited.add(relative)
        source = root / relative
        if not source.is_file() or source.suffix not in {".c", ".h"}:
            continue
        text = source.read_text(errors="replace")
        for include_name in LOCAL_INCLUDE.findall(text):
            candidates = [relative.parent / include_name, Path("drivers/iommu") / include_name]
            for candidate in candidates:
                normalized = Path(candidate.as_posix())
                if (root / normalized).is_file() and normalized not in visited:
                    queue.append(normalized)
                    break
    return visited


def copy_snapshot(root: Path, relative_paths: set[Path], destination: Path) -> None:
    for relative in sorted(relative_paths):
        source = root / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def analyze_source(root: Path, source_name: str, paths: set[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for relative in sorted(paths):
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(errors="replace") if path.suffix in {".c", ".h"} else ""
        rows.append({
            "source_tree": source_name,
            "relative_path": relative.as_posix(),
            "bytes": str(path.stat().st_size),
            "sha256": sha256(path),
            "local_includes": ",".join(sorted(set(LOCAL_INCLUDE.findall(text)))),
            "system_includes": ",".join(sorted(set(SYSTEM_INCLUDE.findall(text)))),
            "function_definition_count": str(len(set(FUNCTION_DEF.findall(text)))),
            "function_definitions": ",".join(sorted(set(FUNCTION_DEF.findall(text)))[:120]),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()
    artifact.mkdir(parents=True, exist_ok=True)

    gki_head = command_output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = command_output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")

    hit_rows: list[dict[str, str]] = []
    hit_paths: dict[str, set[Path]] = {"gki": set(), "touchgrass": set()}
    token_counts: dict[tuple[str, str], int] = {}
    for source_name, root in (("gki", gki), ("touchgrass", touchgrass)):
        for token in TOKENS:
            hits = git_grep(root, token)
            token_counts[(source_name, token)] = len(hits)
            for relative, line, text in hits:
                hit_paths[source_name].add(Path(relative))
                hit_rows.append({
                    "source_tree": source_name,
                    "token": token,
                    "relative_path": relative,
                    "line": str(line),
                    "file_kind": file_kind(relative),
                    "text": text,
                })
    write_tsv(
        artifact / "compatible-and-symbol-hits.tsv",
        ["source_tree", "token", "relative_path", "line", "file_kind", "text"],
        hit_rows,
    )

    downstream_iommu_seeds = {
        path for path in hit_paths["touchgrass"]
        if path.as_posix().startswith("drivers/iommu/") and path.suffix in {".c", ".h"}
    }
    downstream_graph = local_include_graph(touchgrass, downstream_iommu_seeds)

    for metadata in (
        Path("drivers/iommu/Makefile"), Path("drivers/iommu/Kconfig"),
        Path("drivers/iommu/Kconfig.arm"), Path("drivers/iommu/arm-smmu.c"),
        Path("drivers/iommu/arm-smmu.h"),
    ):
        if (touchgrass / metadata).is_file():
            downstream_graph.add(metadata)

    gki_reference_paths = {
        Path("drivers/iommu/arm/arm-smmu/Makefile"),
        Path("drivers/iommu/arm/arm-smmu/arm-smmu.c"),
        Path("drivers/iommu/arm/arm-smmu/arm-smmu.h"),
        Path("drivers/iommu/arm/arm-smmu/arm-smmu-impl.c"),
        Path("drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c"),
        Path("drivers/iommu/Kconfig"),
        Path("drivers/iommu/Makefile"),
    }
    gki_reference_paths = {path for path in gki_reference_paths if (gki / path).is_file()}

    copy_snapshot(touchgrass, downstream_graph, artifact / "source-snapshot" / "touchgrass")
    copy_snapshot(gki, gki_reference_paths, artifact / "source-snapshot" / "gki")

    source_rows = analyze_source(touchgrass, "touchgrass", downstream_graph)
    source_rows.extend(analyze_source(gki, "gki", gki_reference_paths))
    write_tsv(
        artifact / "source-inventory.tsv",
        [
            "source_tree", "relative_path", "bytes", "sha256", "local_includes",
            "system_includes", "function_definition_count", "function_definitions",
        ],
        source_rows,
    )

    downstream_c = sorted(path.as_posix() for path in downstream_graph if path.suffix == ".c")
    downstream_h = sorted(path.as_posix() for path in downstream_graph if path.suffix == ".h")
    gki_exact_qsmmu = sum(token_counts[("gki", token)] for token in TOKENS[:2])
    tg_exact_qsmmu = sum(token_counts[("touchgrass", token)] for token in TOKENS[:2])

    metadata = [
        "artifact_type=a52xq-gki-5.10-qsmmu-v500-source-gap-analysis-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"gki_exact_qsmmu_compatible_hits={gki_exact_qsmmu}",
        f"touchgrass_exact_qsmmu_compatible_hits={tg_exact_qsmmu}",
        f"downstream_candidate_c_files={len(downstream_c)}",
        f"downstream_candidate_header_files={len(downstream_h)}",
        f"downstream_snapshot_files={len(downstream_graph)}",
        f"gki_reference_files={len(gki_reference_paths)}",
        "analysis_scope=source-discovery-local-include-graph-and-api-inventory",
        "automatic_compatible_aliasing=forbidden",
        "flashable=no",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    report = [
        "# A52xq downstream QSMMU v500 port-scope analysis", "",
        "## Safety", "",
        "- This artifact is source analysis only and is not flashable.",
        "- It does not add an ARM-SMMU compatible alias or modify the integrated Image.",
        "- The downstream parent/TBU topology must not be mapped to generic MMU-500 without source-level review.", "",
        "## Discovery result", "",
        f"- GKI exact QSMMU/TBU compatible hits: `{gki_exact_qsmmu}`",
        f"- touchGrass exact QSMMU/TBU compatible hits: `{tg_exact_qsmmu}`",
        f"- downstream candidate C files: `{len(downstream_c)}`",
        f"- downstream candidate header files: `{len(downstream_h)}`", "",
        "## Downstream candidate C files", "",
    ]
    report.extend(f"- `{path}`" for path in downstream_c or ["<none>"])
    report.extend(["", "## Downstream candidate headers", ""])
    report.extend(f"- `{path}`" for path in downstream_h or ["<none>"])
    report.extend([
        "", "## Next gate", "",
        "The next workflow should compile the discovered downstream object set against pinned GKI 5.10 and classify each failure as Kbuild, header/API, or architecture-model incompatibility.",
    ])
    (artifact / "PORT-SCOPE-REPORT.md").write_text("\n".join(report) + "\n")

    files = sorted(path for path in artifact.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")

    if tg_exact_qsmmu == 0:
        raise SystemExit("pinned downstream source contains no QSMMU v500/TBU compatible hits")
    if not downstream_c:
        raise SystemExit("no downstream IOMMU C candidates were discovered")


if __name__ == "__main__":
    main()
