#!/usr/bin/env python3
import argparse
import csv
import os
import re
from pathlib import Path

RX = re.compile(r'^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s+warning:\s+(?P<msg>.*?)(?:\s+\[(?P<flag>-W[^\]]+)\])?$')

HIGH = {
    "-Warray-bounds", "-Warray-bounds-pointer-arithmetic",
    "-Wuninitialized", "-Wsometimes-uninitialized",
    "-Wconditional-uninitialized", "-Wshift-overflow",
}
MEDIUM = {"-Wcast-function-type-strict", "-Wtautological-compare"}
LOW = {"-Wimplicit-fallthrough"}

RELEVANT = (
    "drivers/a52_display/",
    "drivers/iommu/arm/arm-smmu/",
    "drivers/soc/qcom/rpmh-rsc.c",
    "drivers/soc/qcom/msm_bus/",
    "drivers/clk/qcom/",
    "drivers/interconnect/",
    "drivers/gpu/drm/",
)

def normalize_file(s: str) -> str:
    s = s.replace("\\", "/")
    marker = "/gki/common/"
    if marker in s:
        return s.split(marker, 1)[1]
    return s.lstrip("./")

def parse(path):
    out = []
    for raw in Path(path).read_text(errors="replace").splitlines():
        m = RX.match(raw.strip())
        if not m:
            continue
        d = m.groupdict()
        d["file"] = normalize_file(d["file"])
        d["line"] = int(d["line"])
        d["col"] = int(d["col"])
        d["flag"] = d["flag"] or ""
        out.append(d)
    return out

def key(d):
    return (d["file"], d["line"], d["flag"], d["msg"])

def severity(d):
    flag = d["flag"]
    msg = d["msg"].lower()
    if flag in HIGH or any(x in msg for x in ("out of bounds", "uninitialized", "undefined behavior", "null pointer", "use-after-free")):
        return "HIGH"
    if flag in MEDIUM or any(x in msg for x in ("incompatible", "cast", "tautological", "shift")):
        return "MEDIUM"
    if flag in LOW:
        return "LOW"
    return "NOISE"

def relevance(d):
    f = d["file"]
    if f.startswith("drivers/a52_display/"):
        return "display/DSI"
    if f.startswith("drivers/iommu/arm/arm-smmu/"):
        return "SMMU/IOVA"
    if f == "drivers/soc/qcom/rpmh-rsc.c":
        return "RPMh/RSC"
    if f.startswith("drivers/soc/qcom/msm_bus/") or f.startswith("drivers/interconnect/") or f.startswith("drivers/clk/qcom/"):
        return "power/bus/clock"
    if f.startswith("drivers/gpu/drm/"):
        return "DRM atomic/core"
    return "other"

def find_function(root, d):
    p = Path(root) / d["file"]
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(errors="replace").splitlines()
    except Exception:
        return ""
    i = min(d["line"] - 1, len(lines) - 1)
    sig = re.compile(r'^\s*(?:[\w\s\*]+\s+)?([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$')
    control = {"if", "for", "while", "switch"}
    for j in range(i, max(-1, i - 120), -1):
        m = sig.match(lines[j])
        if m and m.group(1) not in control:
            return m.group(1)
    return ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clang14", required=True)
    ap.add_argument("--clang23", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--md", required=True)
    args = ap.parse_args()

    a = parse(args.clang14)
    b = parse(args.clang23)
    aset = {key(x) for x in a}

    rows = []
    for d in b:
        if not d["file"].startswith(RELEVANT):
            continue
        row = dict(d)
        row["function"] = find_function(args.root, d)
        row["clang14_present"] = "yes" if key(d) in aset else "no"
        row["severity"] = severity(d)
        row["relevance"] = relevance(d)
        rows.append(row)

    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NOISE": 3}
    rows.sort(key=lambda x: (rank[x["severity"]], x["clang14_present"] == "yes", x["file"], x["line"]))

    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    fields = ["severity", "relevance", "clang14_present", "flag", "file", "line", "col", "function", "msg"]
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    new = [r for r in rows if r["clang14_present"] == "no"]
    counts = {k: sum(1 for r in new if r["severity"] == k) for k in rank}
    with open(args.md, "w", encoding="utf-8") as f:
        f.write("# Phase433A Clang 23 display-relevant findings\n\n")
        f.write("This is a compile-only diagnostic comparison of the exact Phase432 source. No kernel source fixes are applied here.\n\n")
        f.write(f"- Clang 14 parsed warnings: {len(a)}\n")
        f.write(f"- Clang 23 parsed warnings: {len(b)}\n")
        f.write(f"- Clang 23 relevant warnings in target paths: {len(rows)}\n")
        f.write(f"- New vs Clang 14: {len(new)}\n")
        f.write(f"- New HIGH: {counts['HIGH']}\n")
        f.write(f"- New MEDIUM: {counts['MEDIUM']}\n")
        f.write(f"- New LOW: {counts['LOW']}\n")
        f.write(f"- New NOISE: {counts['NOISE']}\n\n")
        if not new:
            f.write("No new Clang 23 warnings were parsed in the targeted paths.\n")
            return
        f.write("| Severity | Area | Warning | Source | Function | Message |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in new[:200]:
            msg = r["msg"].replace("|", "\\|")
            src = f"{r['file']}:{r['line']}:{r['col']}"
            f.write(f"| {r['severity']} | {r['relevance']} | {r['flag'] or '(none)'} | \`{src}\` | \`{r['function']}\` | {msg} |\n")

if __name__ == "__main__":
    main()
