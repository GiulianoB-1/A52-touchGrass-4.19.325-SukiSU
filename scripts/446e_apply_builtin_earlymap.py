#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE446E_BUILTIN_KEEP_EARLYMAP_UNSECURE_V1"


def die(msg: str) -> None:
    raise SystemExit("Phase446e: " + msg)


def patch(s: str) -> str:
    if MARK in s:
        return s

    old = "static bool a52_p446c_keep_earlymap;\n"
    if s.count(old) != 1:
        die(f"keep_earlymap declaration: expected 1 anchor, found {s.count(old)}")

    new = (
        "/* " + MARK + "\n"
        " * Compile-time discriminator: keep firmware EARLY_MAP only for the\n"
        " * unsecure display domain. The existing Phase446c gate then skips\n"
        " * DOMAIN_ATTR_EARLY_MAP=0, so arm_smmu_enable_s1_translations() is\n"
        " * not called for that domain and SCTLR.M remains clear.\n"
        " */\n"
        "static bool a52_p446c_keep_earlymap = true;\n"
        "static const char a52_p446e_marker[] __used = \"" + MARK + "\";\n"
    )
    return s.replace(old, new, 1)


def check(s: str) -> None:
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
    if "static bool a52_p446c_keep_earlymap;\n" in s:
        die("runtime-default-false declaration still present")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    p = a.root / "drivers/a52_display/msm/sde/sde_kms.c"
    if not p.exists():
        die(f"missing {p}")

    s = p.read_text(errors="replace")
    if not a.check_only:
        s = patch(s)
        p.write_text(s)

    check(p.read_text(errors="replace"))
    print("Phase446e GKI: builtin unsecure EARLY_MAP hold PASS")


if __name__ == "__main__":
    main()
