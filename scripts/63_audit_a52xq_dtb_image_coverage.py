#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REQUIRED_BUILTIN_COMPATIBLES = (
    "arm,psci-1.0",
    "arm,gic-v3",
    "arm,armv8-timer",
    "qcom,cmd-db",
    "qcom,smem",
    "qcom,rpmh-rsc",
    "lagoon-llcc-v1",
    "qcom,lagoon-gcc",
    "qcom,lagoon-pinctrl",
    "qcom,lagoon-camcc",
    "qcom,lagoon-dispcc",
    "qcom,lagoon-gpucc",
    "qcom,lagoon-npucc",
    "qcom,lagoon-videocc",
    "qcom,lagoon-debugcc",
    "qcom,ufshc",
    "qcom,smmu-v2",
    "qcom,msm-geni-console",
    "qcom,sm6350-aggre1-noc",
    "qcom,sm6350-aggre2-noc",
    "qcom,sm6350-clk-virt",
    "qcom,sm6350-compute-noc",
    "qcom,sm6350-config-noc",
    "qcom,sm6350-dc-noc",
    "qcom,sm6350-gem-noc",
    "qcom,sm6350-mmss-noc",
    "qcom,sm6350-npu-noc",
    "qcom,sm6350-system-noc",
)

GENERIC_COMPATIBLES = {
    "simple-bus", "syscon", "fixed-clock", "operating-points-v2",
    "shared-dma-pool", "removed-dma-pool", "arm,primecell",
    "arm,arch-cache", "arm,armv8", "usb-nop-xceiv",
}

PRIORITY_KEYWORDS = (
    "clock", "clk", "gcc", "pinctrl", "llcc", "rpmh", "regulator",
    "spmi", "pdc", "watchdog", "smmu", "iommu", "ufs", "ufshc",
    "phy", "geni", "serial", "noc", "interconnect", "qmp", "power-on",
)

STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
NODE_RE = re.compile(r'^(?:(?:[A-Za-z_][A-Za-z0-9_]*):\s*)?([^\s{]+)\s*\{\s*$')


@dataclass
class Node:
    path: str
    parent_enabled: bool
    own_status: str = "okay"
    compatibles: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.parent_enabled and self.own_status not in {"disabled", "reserved", "fail", "failed"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_strings(statement: str) -> tuple[str, ...]:
    return tuple(bytes(value, "utf-8").decode("unicode_escape") for value in STRING_RE.findall(statement))


def parse_dts(path: Path) -> list[Node]:
    nodes: list[Node] = []
    stack: list[Node] = []
    statement = ""
    for raw in path.read_text(errors="replace").splitlines():
        line = re.sub(r'/\*.*?\*/', '', raw).strip()
        if not line or line.startswith("/") and line.endswith("/;"):
            continue

        if statement:
            statement += " " + line
            if ";" not in line:
                continue
            line = statement
            statement = ""
        elif "=" in line and ";" not in line:
            statement = line
            continue

        match = NODE_RE.match(line)
        if match:
            name = match.group(1)
            if name == "/":
                node_path = "/"
            else:
                parent_path = stack[-1].path if stack else "/"
                node_path = (parent_path.rstrip("/") + "/" + name) if parent_path != "/" else "/" + name
            parent_enabled = stack[-1].enabled if stack else True
            node = Node(path=node_path, parent_enabled=parent_enabled)
            nodes.append(node)
            stack.append(node)
            continue

        if line.startswith("};") or line == "}":
            if stack:
                stack.pop()
            continue

        if not stack:
            continue
        if line.startswith("status") and "=" in line:
            values = decode_strings(line)
            if values:
                stack[-1].own_status = values[0]
        elif line.startswith("compatible") and "=" in line:
            stack[-1].compatibles = decode_strings(line)

    if statement:
        raise SystemExit("unterminated DTS property statement")
    return nodes


def image_strings(path: Path) -> set[str]:
    result = subprocess.check_output(["strings", "-a", str(path)], text=True, errors="replace")
    return set(result.splitlines())


def classify(compatible: str, required: set[str]) -> str:
    if compatible in required:
        return "required-early-boot"
    if compatible in GENERIC_COMPATIBLES or compatible.startswith("android,"):
        return "generic-or-metadata"
    if compatible.startswith("qcom,lagoon") and compatible not in required:
        return "board-or-unported-lagoon"
    if any(keyword in compatible.lower() for keyword in PRIORITY_KEYWORDS):
        return "priority-gap"
    return "optional-or-late-boot"


def audit(args: argparse.Namespace) -> None:
    image = args.image.resolve()
    dtb = args.dtb.resolve()
    dts = args.dts.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for path in (image, dtb, dts):
        if not path.is_file():
            raise SystemExit(f"missing audit input: {path}")

    nodes = parse_dts(dts)
    strings = image_strings(image)
    required = set(REQUIRED_BUILTIN_COMPATIBLES)

    all_count: Counter[str] = Counter()
    enabled_count: Counter[str] = Counter()
    enabled_paths: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for compatible in node.compatibles:
            all_count[compatible] += 1
            if node.enabled:
                enabled_count[compatible] += 1
                enabled_paths[compatible].append(node.path)

    all_compatibles = sorted(all_count)
    rows: list[dict[str, str]] = []
    for compatible in all_compatibles:
        match = compatible in strings
        rows.append({
            "compatible": compatible,
            "category": classify(compatible, required),
            "total_node_count": str(all_count[compatible]),
            "enabled_node_count": str(enabled_count[compatible]),
            "exact_image_string_match": "yes" if match else "no",
            "result": "covered" if match else "uncovered",
            "enabled_example_paths": ";".join(enabled_paths[compatible][:5]),
        })

    with (output / "compatible-coverage.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "compatible", "category", "total_node_count", "enabled_node_count",
                "exact_image_string_match", "result", "enabled_example_paths",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)

    required_rows = []
    missing_required = 0
    for compatible in REQUIRED_BUILTIN_COMPATIBLES:
        match = compatible in strings
        missing_required += int(not match)
        required_rows.append({
            "compatible": compatible,
            "enabled_node_count": str(enabled_count[compatible]),
            "exact_image_string_match": "yes" if match else "no",
            "result": "pass" if match else "fail",
        })
    with (output / "required-early-boot-coverage.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["compatible", "enabled_node_count", "exact_image_string_match", "result"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(required_rows)

    enabled_unique = {compatible for compatible, count in enabled_count.items() if count}
    covered_enabled = {compatible for compatible in enabled_unique if compatible in strings}
    uncovered_enabled = enabled_unique - covered_enabled
    priority_uncovered = sorted(
        (compatible for compatible in uncovered_enabled if classify(compatible, required) == "priority-gap"),
        key=lambda compatible: (-enabled_count[compatible], compatible),
    )

    report = [
        "# A52xq GKI 5.10 DT-to-Image compatible coverage", "",
        "## Safety", "",
        "- This is a static audit of the existing non-flashable Image and DTB.",
        "- Exact string presence is evidence that a compatible is compiled into the Image, not proof that hardware probes successfully.",
        "- No boot image, installer, or flashable package is produced.", "",
        "## Summary", "",
        f"- DT nodes parsed: `{len(nodes)}`",
        f"- Unique compatible strings: `{len(all_compatibles)}`",
        f"- Unique compatible strings on enabled nodes: `{len(enabled_unique)}`",
        f"- Enabled compatibles found exactly in Image: `{len(covered_enabled)}`",
        f"- Enabled compatibles not found exactly in Image: `{len(uncovered_enabled)}`",
        f"- Required early-boot compatible failures: `{missing_required}`", "",
        "## Highest-priority uncovered enabled compatibles", "",
    ]
    if priority_uncovered:
        report.extend(
            f"- `{compatible}`: {enabled_count[compatible]} enabled node(s)"
            for compatible in priority_uncovered[:50]
        )
    else:
        report.append("- None")
    report.extend(["", "## Interpretation", "",
        "The required early-boot list is a deliberately narrow compile-invariant derived from the subsystems already ported and built in Workflows 53 through 62.",
        "Uncovered compatibles remain a porting roadmap. Many are optional, late-boot, or reference-only vendor subsystems and are not all required for an initial controlled boot probe.",
    ])
    (output / "DT-IMAGE-COVERAGE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-dt-image-compatible-coverage-not-flashable",
        f"image_sha256={sha256(image)}",
        f"dtb_sha256={sha256(dtb)}",
        f"parsed_node_count={len(nodes)}",
        f"unique_compatible_count={len(all_compatibles)}",
        f"enabled_unique_compatible_count={len(enabled_unique)}",
        f"covered_enabled_compatible_count={len(covered_enabled)}",
        f"uncovered_enabled_compatible_count={len(uncovered_enabled)}",
        f"required_early_boot_count={len(REQUIRED_BUILTIN_COMPATIBLES)}",
        f"required_early_boot_failures={missing_required}",
        "coverage_method=exact-compatible-string-presence-in-built-arm64-image",
        "flashable=no",
    ]
    (output / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    shutil.copy2(dts, output / "decompiled-lagoon.dts")
    files = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    with (output / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n")

    if missing_required:
        raise SystemExit(f"{missing_required} required early-boot compatibles are absent from Image strings")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--dts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    audit(parser.parse_args())


if __name__ == "__main__":
    main()
