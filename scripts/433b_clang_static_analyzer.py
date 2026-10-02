#!/usr/bin/env python3
import argparse
import csv
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pathlib import Path

TARGET_PREFIXES = (
    "drivers/a52_display/msm/",
    "drivers/iommu/arm/arm-smmu/",
    "drivers/soc/qcom/msm_bus/",
    "drivers/clk/qcom/",
    "drivers/interconnect/",
)
TARGET_EXACT = {"drivers/soc/qcom/rpmh-rsc.c"}
WARN_RE = re.compile(r"^(.*?):(\d+):(\d+): warning: (.*?) \[(clang-analyzer-[^\]]+)\]$")
SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def norm_rel(path: str, root: Path) -> str:
    p = Path(path)
    p = (root / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        return p.relative_to(root.resolve()).as_posix()
    except ValueError:
        s = p.as_posix()
        marker = "/gki/common/"
        return s.split(marker, 1)[1] if marker in s else s


def targeted(rel: str) -> bool:
    return rel in TARGET_EXACT or any(rel.startswith(p) for p in TARGET_PREFIXES)


def area(rel: str) -> str:
    if rel.startswith("drivers/a52_display/msm/"):
        return "DISPLAY"
    if rel.startswith("drivers/iommu/arm/arm-smmu/"):
        return "SMMU"
    if rel == "drivers/soc/qcom/rpmh-rsc.c":
        return "RPMH_RSC"
    if rel.startswith("drivers/soc/qcom/msm_bus/"):
        return "MSM_BUS"
    if rel.startswith("drivers/clk/qcom/"):
        return "QCOM_CLK"
    if rel.startswith("drivers/interconnect/"):
        return "INTERCONNECT"
    return "OTHER"


def severity(checker: str, message: str) -> str:
    s = (checker + " " + message).lower()
    high = (
        "nulldereference", "useafterfree", "doublefree", "newdelete",
        "unix.malloc", "uninitialized", "undefined", "arraybound",
        "outofbound", "divzero", "stackaddressescape", "returnptrrange",
        "shift", "invalidatediterator", "cplusplus.innerpointer",
    )
    medium = (
        "deadstore", "leak", "lock", "stream", "cstring", "security",
        "non-null", "nullability", "iterator", "mismatched",
    )
    if any(t in s for t in high):
        return "HIGH"
    if any(t in s for t in medium):
        return "MEDIUM"
    return "LOW"


def analyze_one(entry, clang_tidy, db_dir, source_root, timeout):
    file_path = entry["file"]
    rel = norm_rel(file_path, source_root)
    cmd = [
        clang_tidy,
        "-p", str(db_dir),
        "-checks=-*,clang-analyzer-*",
        "--quiet",
        "--extra-arg=-Wno-error",
        "--extra-arg=-Wno-error=strict-prototypes",
        file_path,
    ]
    started = time.monotonic()
    try:
        p = subprocess.run(
            cmd,
            cwd=entry.get("directory") or str(source_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        return rel, p.returncode, time.monotonic() - started, p.stdout, False
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        out += f"\nPHASE433B_TIMEOUT after {timeout}s\n"
        return rel, 124, time.monotonic() - started, out, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compile-db", required=True)
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--clang-tidy", default="clang-tidy-23")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    db_path = Path(args.compile_db).resolve()
    db_dir = db_path.parent
    source_root = Path(args.source_root).resolve()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    raw = json.loads(db_path.read_text())
    entries = []
    seen = set()
    for e in raw:
        rel = norm_rel(e.get("file", ""), source_root)
        if not targeted(rel) or rel in seen:
            continue
        seen.add(rel)
        entries.append(e)

    (out / "phase433b-targeted-files.txt").write_text(
        "\n".join(sorted(seen)) + ("\n" if seen else "")
    )
    if not entries:
        (out / "phase433b-status.txt").write_text("2\n")
        raise SystemExit("Phase433B: no targeted translation units found")

    results = []
    workers = max(1, min(args.jobs, 8))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(analyze_one, e, args.clang_tidy, db_dir, source_root, args.timeout) for e in entries]
        for fut in as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda x: x[0])
    full_log = []
    findings = []
    failures = []
    timeouts = []
    for rel, rc, elapsed, output, timed_out in results:
        full_log.append(f"===== {rel} rc={rc} elapsed={elapsed:.1f}s =====\n{output.rstrip()}\n")
        if rc != 0:
            failures.append((rel, rc))
        if timed_out:
            timeouts.append(rel)
        for line in output.splitlines():
            m = WARN_RE.match(line.strip())
            if not m:
                continue
            src, ln, col, msg, checker = m.groups()
            src_rel = norm_rel(src, source_root)
            if not targeted(src_rel):
                continue
            findings.append({
                "severity": severity(checker, msg),
                "area": area(src_rel),
                "checker": checker,
                "source_file": src_rel,
                "line": int(ln),
                "column": int(col),
                "message": msg,
            })

    uniq = {}
    for f in findings:
        key = (f["source_file"], f["line"], f["column"], f["checker"], f["message"])
        uniq[key] = f
    findings = sorted(
        uniq.values(),
        key=lambda f: (SEV_ORDER[f["severity"]], f["area"], f["source_file"], f["line"], f["checker"]),
    )

    (out / "phase433b-static-analysis.log").write_text("\n".join(full_log))
    with (out / "phase433b-findings.csv").open("w", newline="") as fp:
        w = csv.DictWriter(
            fp,
            fieldnames=["severity", "area", "checker", "source_file", "line", "column", "message"],
        )
        w.writeheader()
        w.writerows(findings)

    sev = Counter(f["severity"] for f in findings)
    subs = Counter(f["area"] for f in findings)
    checks = Counter(f["checker"] for f in findings)

    summary = [
        "Phase433B Clang 23 targeted static analysis",
        "==========================================================",
        f"Target translation units: {len(entries)}",
        f"Analyzer findings: {len(findings)}",
        f"Analyzer command failures: {len(failures)}",
        f"Analyzer timeouts: {len(timeouts)}",
        "",
        "Findings by severity:",
    ]
    for k in ("HIGH", "MEDIUM", "LOW"):
        summary.append(f"  {k}: {sev.get(k, 0)}")
    summary += ["", "Findings by subsystem:"]
    for k, v in sorted(subs.items()):
        summary.append(f"  {k}: {v}")
    summary += ["", "Findings by checker:"]
    for k, v in sorted(checks.items(), key=lambda kv: (-kv[1], kv[0])):
        summary.append(f"  {k}: {v}")
    if failures:
        summary += ["", "Analyzer command failures:"] + [f"  {r}: rc={rc}" for r, rc in failures]
    if timeouts:
        summary += ["", "Analyzer timeouts:"] + [f"  {r}" for r in timeouts]
    summary += ["", "Interpretation: static-analyzer findings are evidence to review, not automatic source fixes."]
    (out / "phase433b-static-analysis-summary.txt").write_text("\n".join(summary) + "\n")

    md = [
        "# Phase433B display/platform static-analysis findings",
        "",
        f"**Translation units analyzed:** {len(entries)}  ",
        f"**Findings:** {len(findings)} total ({sev.get('HIGH',0)} HIGH, {sev.get('MEDIUM',0)} MEDIUM, {sev.get('LOW',0)} LOW).",
        "",
        "| Severity | Area | Checker | File:line | Diagnostic |",
        "|---|---|---|---|---|",
    ]
    for f in findings[:200]:
        msg = f["message"].replace("|", "\\|")
        md.append(f"| {f['severity']} | {f['area']} | {f['checker']} | {f['source_file']}:{f['line']} | {msg} |")
    md += [
        "",
        "## Review rule",
        "",
        "Prioritize null dereferences, lifetime/free issues, uninitialized values, bounds errors, lock/resource misuse, and bad error paths in display, SMMU, RPMh/RSC, msm_bus, clock, and interconnect code. Do not patch automatically.",
    ]
    (out / "phase433b-display-relevant-findings.md").write_text("\n".join(md) + "\n")

    status = 1 if failures else 0
    (out / "phase433b-status.txt").write_text(f"{status}\n")
    print("\n".join(summary))
    raise SystemExit(status)


if __name__ == "__main__":
    main()
