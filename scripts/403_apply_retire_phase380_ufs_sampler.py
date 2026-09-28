#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE403_RETIRE_PHASE380_UFS_SAMPLER_V1"
UFSCORE = Path("drivers/scsi/ufs/ufshcd.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase403 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_ufs(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        "static void a52_r380_start_sampler(struct ufs_hba *hba)\n",
        "static void __maybe_unused a52_r380_start_sampler(struct ufs_hba *hba)\n",
        "Phase380 sampler function",
    )
    text = one(
        text,
        "\t\ta52_r380_start_sampler(hba);\n",
        f"\t\t/* {MARK}: retire the Phase380 raw UFS sampler exactly as Phase401 does. */\n",
        "Phase380 sampler call",
    )
    text += (
        "\n/* " + MARK + "\n"
        " * Phase402 crash/Phase401 survival discriminator: no runtime behavior\n"
        " * changes except retiring a52_r380_start_sampler().\n"
        " */\n"
        "static const char a52_p403_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "static void __maybe_unused a52_r380_start_sampler(struct ufs_hba *hba)",
    ):
        if token not in text:
            raise SystemExit("Phase403 token missing: " + token)
    if "\t\ta52_r380_start_sampler(hba);\n" in text:
        raise SystemExit("Phase403 Phase380 UFS sampler still active")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    p = ns.root / UFSCORE
    if not p.is_file():
        raise SystemExit("Phase403 UFS core source missing")

    text = p.read_text(errors="replace")
    if not ns.check_only:
        text = patch_ufs(text)
        p.write_text(text)

    validate(p.read_text(errors="replace"))
    print("Phase403 Phase380 UFS sampler retirement: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
