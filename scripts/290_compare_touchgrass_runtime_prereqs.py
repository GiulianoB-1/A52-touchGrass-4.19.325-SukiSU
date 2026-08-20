#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path

FOCUS = [
    "msm/dsi/dsi_phy.c",
    "msm/dsi/dsi_phy.h",
    "msm/dsi/dsi_phy_hw.h",
    "msm/dsi/dsi_phy_hw_v4_0.c",
    "msm/dsi/dsi_phy_timing_calc.c",
    "msm/dsi/dsi_phy_timing_v4_0.c",
    "msm/dsi/dsi_pwr.c",
    "msm/dsi/dsi_pwr.h",
    "msm/dsi/dsi_clk.h",
    "msm/dsi/dsi_clk_manager.c",
    "msm/dsi/dsi_catalog.c",
    "msm/dsi/dsi_display.c",
    "msm/dsi/dsi_display.h",
    "msm/dsi/dsi_ctrl.c",
    "msm/dsi/dsi_ctrl_hw_cmn.c",
]

PREREQ_WORDS = (
    "phy", "pll", "clk", "clock", "pwr", "power", "regulator", "gdsc",
    "reset", "supply", "lane", "catalog", "display", "dsi",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(path: Path) -> str:
    return path.read_text(errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--touchgrass", required=True)
    ap.add_argument("--gki-display", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    tg = Path(args.touchgrass)
    gki = Path(args.gki_display)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "diffs").mkdir(exist_ok=True)

    if not tg.is_dir():
        raise SystemExit(f"missing TouchGrass display tree: {tg}")
    if not gki.is_dir():
        raise SystemExit(f"missing reconstructed GKI display tree: {gki}")

    tg_files = {p.relative_to(tg).as_posix(): p for p in tg.rglob("*") if p.is_file()}
    gki_files = {p.relative_to(gki).as_posix(): p for p in gki.rglob("*") if p.is_file()}
    common = sorted(set(tg_files) & set(gki_files))
    missing = sorted(set(tg_files) - set(gki_files))
    extra = sorted(set(gki_files) - set(tg_files))

    modified = []
    identical = []
    for rel in common:
        if sha(tg_files[rel]) == sha(gki_files[rel]):
            identical.append(rel)
        else:
            modified.append(rel)

    prereq_modified = [
        rel for rel in modified
        if any(word in rel.lower() for word in PREREQ_WORDS)
    ]

    rows = []
    for rel in FOCUS:
        t = tg_files.get(rel)
        g = gki_files.get(rel)
        state = "missing"
        tsha = gsha = None
        if t and g:
            tsha, gsha = sha(t), sha(g)
            state = "identical" if tsha == gsha else "modified"
            if state == "modified":
                diff = difflib.unified_diff(
                    text(t).splitlines(True), text(g).splitlines(True),
                    fromfile=f"TouchGrass/{rel}", tofile=f"Phase289/{rel}", n=5,
                )
                safe = rel.replace("/", "__") + ".diff"
                (out / "diffs" / safe).write_text("".join(diff))
        elif t:
            state = "missing-in-gki"
        elif g:
            state = "extra-in-gki"
        rows.append({"path": rel, "state": state, "touchgrass_sha256": tsha, "phase289_sha256": gsha})

    summary = {
        "touchgrass_tree": str(tg),
        "phase289_tree": str(gki),
        "counts": {
            "touchgrass_files": len(tg_files),
            "phase289_files": len(gki_files),
            "common": len(common),
            "identical": len(identical),
            "modified": len(modified),
            "missing_in_gki": len(missing),
            "extra_in_gki": len(extra),
            "runtime_prereq_modified": len(prereq_modified),
        },
        "focus": rows,
        "modified": modified,
        "runtime_prereq_modified": prereq_modified,
        "missing_in_gki": missing,
        "extra_in_gki": extra,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (out / "modified-files.txt").write_text("\n".join(modified) + ("\n" if modified else ""))
    (out / "runtime-prereq-modified.txt").write_text("\n".join(prereq_modified) + ("\n" if prereq_modified else ""))
    (out / "missing-in-gki.txt").write_text("\n".join(missing) + ("\n" if missing else ""))
    (out / "extra-in-gki.txt").write_text("\n".join(extra) + ("\n" if extra else ""))

    print(json.dumps(summary["counts"], sort_keys=True))
    print("FOCUS")
    for row in rows:
        print(f"{row['state']:14s} {row['path']}")
    print("RUNTIME_PREREQ_MODIFIED")
    for rel in prereq_modified:
        print(rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
