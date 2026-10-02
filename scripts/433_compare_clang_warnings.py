#!/usr/bin/env python3
"""
Phase433A Clang 14 vs Clang 23 warning-audit report generator.

This is intentionally an offline reporting tool. It does not patch kernel source.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


WARNING_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s+warning:\s+(?P<msg>.*?)(?:\s+\[(?P<flag>-W[^\]]+)\])?\s*$"
)

TARGET_PREFIXES = (
    "drivers/a52_display/",
    "drivers/iommu/arm/arm-smmu/",
    "drivers/soc/qcom/rpmh-rsc.c",
    "drivers/soc/qcom/msm_bus/",
    "drivers/clk/qcom/",
    "drivers/interconnect/",
    "drivers/gpu/drm/",
)

DANGEROUS_PATTERNS = (
    "array-bounds",
    "uninitialized",
    "use-after-free",
    "dangling",
    "null-dereference",
    "shift-overflow",
    "cast-function-type-strict",
    "incompatible-function-pointer",
    "out of bounds",
    "use of uninitialized",
    "null pointer",
)

MEDIUM_PATTERNS = (
    "implicit-fallthrough",
    "tautological",
    "format",
    "enum",
    "pointer-sign",
    "address-of-packed",
    "sign-compare",
    "sometimes-uninitialized",
    "conditional-uninitialized",
)


@dataclass(frozen=True)
class Warning:
    compiler: str
    file: str
    line: int
    col: int
    flag: str
    message: str
    function: str
    relevance: str
    severity: str

    def comparison_key(self):
        return (self.file, self.line, self.flag or self.message)


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(errors="replace")


def normalize_file(raw: str, source_root: Path) -> str:
    raw = raw.strip().replace("\\", "/")
    marker = "/gki/common/"
    if marker in raw:
        return raw.split(marker, 1)[1]

    try:
        p = Path(raw)
        if p.is_absolute():
            return p.resolve().relative_to(source_root.resolve()).as_posix()
    except Exception:
        pass

    for prefix in ("./", "gki/common/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw


def infer_function(source_root: Path, rel_file: str, line_no: int) -> str:
    path = source_root / rel_file
    if not path.is_file():
        return "unknown"

    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return "unknown"

    idx = min(max(line_no - 1, 0), len(lines) - 1)
    start = max(0, idx - 140)
    control = {"if", "for", "while", "switch", "return", "sizeof", "typeof"}

    one_line = re.compile(
        r"^\s*(?:[A-Za-z_][\w\s\*\[\],]*\s+)?([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{?\s*$"
    )
    for i in range(idx, start - 1, -1):
        s = lines[i].strip()
        if not s or s.startswith(("#", "//", "/*", "*")):
            continue
        m = one_line.match(s)
        if m and m.group(1) not in control:
            return m.group(1)

    for i in range(idx, start - 1, -1):
        if "{" not in lines[i]:
            continue
        lo = max(start, i - 5)
        blob = " ".join(x.strip() for x in lines[lo : i + 1])
        m = re.search(r"\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{", blob)
        if m and m.group(1) not in control:
            return m.group(1)

    return "unknown"


def relevance_for(path: str) -> str:
    if path.startswith("drivers/a52_display/"):
        return "DISPLAY"
    if path.startswith("drivers/iommu/arm/arm-smmu/"):
        return "SMMU"
    if path == "drivers/soc/qcom/rpmh-rsc.c":
        return "RPMH_RSC"
    if path.startswith("drivers/soc/qcom/msm_bus/"):
        return "MSM_BUS"
    if path.startswith("drivers/clk/qcom/"):
        return "QCOM_CLK"
    if path.startswith("drivers/interconnect/"):
        return "INTERCONNECT"
    if path.startswith("drivers/gpu/drm/"):
        return "DRM"
    return "OTHER"


def severity_for(flag: str, message: str, relevance: str) -> str:
    blob = f"{flag} {message}".lower()

    if any(p in blob for p in DANGEROUS_PATTERNS):
        return "HIGH" if relevance != "OTHER" else "MEDIUM"

    if any(p in blob for p in MEDIUM_PATTERNS):
        return "MEDIUM" if relevance != "OTHER" else "LOW"

    if relevance in {"DISPLAY", "SMMU", "RPMH_RSC", "MSM_BUS"}:
        return "MEDIUM"

    return "LOW"


def parse_log(path: Path, compiler: str, source_root: Path) -> list[Warning]:
    out: list[Warning] = []
    seen = set()

    for raw_line in read_text(path).splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw_line)
        m = WARNING_RE.match(line)
        if not m:
            continue

        rel = normalize_file(m.group("file"), source_root)
        if not any(rel.startswith(p) if p.endswith("/") else rel == p for p in TARGET_PREFIXES):
            continue

        line_no = int(m.group("line"))
        col = int(m.group("col"))
        flag = m.group("flag") or "UNLABELED"
        msg = m.group("msg").strip()
        fn = infer_function(source_root, rel, line_no)
        relevance = relevance_for(rel)
        severity = severity_for(flag, msg, relevance)

        key = (compiler, rel, line_no, col, flag, msg)
        if key in seen:
            continue
        seen.add(key)

        out.append(
            Warning(
                compiler=compiler,
                file=rel,
                line=line_no,
                col=col,
                flag=flag,
                message=msg,
                function=fn,
                relevance=relevance,
                severity=severity,
            )
        )

    return out


def read_lines(path: Path) -> list[str]:
    return [x.strip() for x in read_text(path).splitlines() if x.strip()]


def write_csv(path: Path, warnings: list[Warning], new_keys: set[tuple]) -> None:
    fields = [
        "compiler",
        "new_in_clang23",
        "severity",
        "relevance",
        "warning_type",
        "source_file",
        "line",
        "column",
        "function",
        "message",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for item in warnings:
            w.writerow(
                {
                    "compiler": item.compiler,
                    "new_in_clang23": "yes" if item.comparison_key() in new_keys else "no",
                    "severity": item.severity,
                    "relevance": item.relevance,
                    "warning_type": item.flag,
                    "source_file": item.file,
                    "line": item.line,
                    "column": item.col,
                    "function": item.function,
                    "message": item.message,
                }
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clang14-log", required=True, type=Path)
    ap.add_argument("--clang23-log", required=True, type=Path)
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--clang14-version", required=True, type=Path)
    ap.add_argument("--clang23-version", required=True, type=Path)
    ap.add_argument("--supported14", required=True, type=Path)
    ap.add_argument("--supported23", required=True, type=Path)
    ap.add_argument("--status14", required=True, type=Path)
    ap.add_argument("--status23", required=True, type=Path)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    w14 = parse_log(args.clang14_log, "clang14", args.source_root)
    w23 = parse_log(args.clang23_log, "clang23", args.source_root)

    keys14 = {w.comparison_key() for w in w14}
    new23 = [w for w in w23 if w.comparison_key() not in keys14]
    new_keys = {w.comparison_key() for w in new23}

    write_csv(args.out_dir / "phase433a-all-warnings.csv", w14 + w23, new_keys)
    write_csv(args.out_dir / "phase433a-new-warnings.csv", new23, new_keys)

    sev = Counter(w.severity for w in new23)
    rel = Counter(w.relevance for w in new23)
    flag_counts = Counter(w.flag for w in new23)

    audit = []
    audit.append("Phase433A Clang 14 vs Clang 23 warning audit")
    audit.append("=" * 58)
    audit.append("")
    audit.append("Compiler A:")
    audit.extend(f"  {x}" for x in read_lines(args.clang14_version))
    audit.append("")
    audit.append("Compiler B:")
    audit.extend(f"  {x}" for x in read_lines(args.clang23_version))
    audit.append("")
    audit.append(f"Clang 14 targeted warnings: {len(w14)}")
    audit.append(f"Clang 23 targeted warnings: {len(w23)}")
    audit.append(f"New Clang 23 diagnostics:   {len(new23)}")
    audit.append(f"Compile status Clang 14: {read_text(args.status14).strip() or 'unknown'}")
    audit.append(f"Compile status Clang 23: {read_text(args.status23).strip() or 'unknown'}")
    audit.append("")
    audit.append("Supported explicit warnings, Clang 14:")
    audit.extend(f"  {x}" for x in read_lines(args.supported14))
    audit.append("")
    audit.append("Supported explicit warnings, Clang 23:")
    audit.extend(f"  {x}" for x in read_lines(args.supported23))
    audit.append("")
    audit.append("New Clang 23 diagnostics by severity:")
    for k in ("HIGH", "MEDIUM", "LOW"):
        audit.append(f"  {k}: {sev.get(k, 0)}")
    audit.append("")
    audit.append("New Clang 23 diagnostics by subsystem:")
    for k, v in sorted(rel.items()):
        audit.append(f"  {k}: {v}")
    audit.append("")
    audit.append("New Clang 23 diagnostics by warning type:")
    for k, v in flag_counts.most_common():
        audit.append(f"  {k}: {v}")
    audit.append("")
    audit.append("Interpretation rule:")
    audit.append("  A changed warning set is diagnostic evidence only.")
    audit.append("  No source fix is implied by this report.")
    audit.append("")

    (args.out_dir / "phase433a-clang23-warning-audit.txt").write_text(
        "\n".join(audit), encoding="utf-8"
    )

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    new23_sorted = sorted(
        new23,
        key=lambda w: (order.get(w.severity, 9), w.relevance, w.file, w.line, w.flag),
    )

    md = []
    md.append("# Phase433A display-relevant findings")
    md.append("")
    md.append(
        "This report contains diagnostics emitted by Clang 23 that were not emitted "
        "at the same source location and warning class by the Clang 14 control build."
    )
    md.append("")
    md.append(
        f"**New diagnostics:** {len(new23)} total "
        f"({sev.get('HIGH', 0)} HIGH, {sev.get('MEDIUM', 0)} MEDIUM, {sev.get('LOW', 0)} LOW)."
    )
    md.append("")

    if not new23_sorted:
        md.append("No new targeted Clang 23 warnings were detected.")
    else:
        md.append("| Severity | Area | Warning | File:line | Function | Diagnostic |")
        md.append("|---|---|---|---|---|---|")
        for w in new23_sorted:
            msg = w.message.replace("|", "\\|")
            fn = w.function.replace("|", "\\|")
            md.append(
                f"| {w.severity} | {w.relevance} | {w.flag} | "
                f"{w.file}:{w.line} | {fn} | {msg} |"
            )

    md.append("")
    md.append("## Review rule")
    md.append("")
    md.append(
        "Review HIGH and MEDIUM findings first. Do not patch warnings automatically. "
        "Confirm the source path and whether the diagnostic can affect the "
        "SurfaceFlinger/HWC, DRM atomic, DSI DMA, RPMh/RSC, clock/bus, or SMMU/IOVA path."
    )

    (args.out_dir / "phase433a-display-relevant-findings.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
