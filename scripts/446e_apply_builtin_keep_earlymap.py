#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE446E_GKI_KEEP_EARLYMAP_BUILTIN_V1"
TARGET = "static bool a52_p446c_keep_earlymap;\n"
REPLACEMENT = (
    "static bool a52_p446c_keep_earlymap = true;\n"
    'static const char a52_p446e_marker[] __used = "' + MARK + '";\n'
)

def die(msg: str) -> None:
    raise SystemExit("Phase446e: " + msg)

def patch(path: Path) -> None:
    s = path.read_text(errors="replace")
    if MARK in s:
        return
    for token in (
        "A52_PHASE446C_TAKEOVER_WINDOW_V1",
        "A52_PHASE446C_KEEP_EARLYMAP_GATE",
        "if (a52_p446c_keep_earlymap && i == MSM_SMMU_DOMAIN_UNSECURE)",
        "a52_p446_mark(0x150U, (u32)i, 1U);",
    ):
        if token not in s:
            die("missing prerequisite: " + token)
    if s.count(TARGET) != 1:
        die(f"keep_earlymap declaration: expected 1 anchor, found {s.count(TARGET)}")
    path.write_text(s.replace(TARGET, REPLACEMENT, 1))

def check(path: Path) -> None:
    s = path.read_text(errors="replace")
    required = (
        MARK,
        "static bool a52_p446c_keep_earlymap = true;",
        "A52_PHASE446C_KEEP_EARLYMAP_GATE",
        "if (a52_p446c_keep_earlymap && i == MSM_SMMU_DOMAIN_UNSECURE)",
        "a52_p446_mark(0x150U, (u32)i, 1U);",
    )
    missing = [x for x in required if x not in s]
    if missing:
        die("contract missing: " + ", ".join(missing))
    if TARGET in s:
        die("uninitialized keep_earlymap declaration still present")
    print("Phase446e GKI built-in keep-EARLY_MAP discriminator: PASS")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    path = a.root / "drivers/a52_display/msm/sde/sde_kms.c"
    if not path.exists():
        die(f"missing {path}")
    if not a.check_only:
        patch(path)
    check(path)

if __name__ == "__main__":
    main()
