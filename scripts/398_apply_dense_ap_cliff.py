#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE398_DENSE_AP_CLIFF_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase398 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text

    if "A52_PHASE396_NOC_IRQ_EVIDENCE_V1" not in text:
        raise SystemExit("Phase398 requires inherited Phase396 NoC observer")
    if "A52_PHASE393_IRQ_RPMH_SMMU_CLIFF_V1" not in text:
        raise SystemExit("Phase398 requires inherited Phase393 hardirq sideband")

    text = one(text,
        "#define A52_R341_INTERVAL_MS 250U\n",
        "/* " + MARK + " */\n#define A52_R341_INTERVAL_MS 10U\n",
        "R341 dense interval")
    text = one(text,
        "#define A52_R341_LIMIT 120U\n",
        "#define A52_R341_LIMIT 400U\n",
        "R341 dense limit")

    text = one(text,
        "\tu64 next_dense = 17600ULL;\n",
        "\tu64 next_dense = 16000ULL;\n",
        "N396 dense start")
    text = one(text,
        "\t\tif (now_ms >= 17600ULL && now_ms <= 18600ULL &&\n",
        "\t\tif (now_ms >= 16000ULL && now_ms <= 17200ULL &&\n",
        "N396 dense window")
    text = one(text,
        "\t\t\tnext_dense += 20ULL;\n",
        "\t\t\tnext_dense += 10ULL;\n",
        "N396 dense cadence")
    text = one(text,
        "\t\tif (now_ms >= 17400ULL && now_ms <= 18800ULL)\n"
        "\t\t\tusleep_range(2000, 3000);\n",
        "\t\tif (now_ms >= 15800ULL && now_ms <= 17400ULL)\n"
        "\t\t\tusleep_range(1000, 2000);\n",
        "N396 dense sleep window")

    text = one(text,
        '"BOOT rs=ready phase=396 focus=noc-irq roots=%u copies=2 crc=crc32c"',
        '"BOOT rs=ready phase=398 focus=ap-cliff roots=%u copies=2 crc=crc32c"',
        "Phase398 boot identity")

    return text


def validate(root: Path) -> None:
    text = (root / REC).read_text(errors="replace")
    required = (
        MARK,
        "#define A52_R341_INTERVAL_MS 10U",
        "#define A52_R341_LIMIT 400U",
        "u64 next_dense = 16000ULL;",
        "now_ms >= 16000ULL && now_ms <= 17200ULL",
        "next_dense += 10ULL;",
        "now_ms >= 15800ULL && now_ms <= 17400ULL",
        "usleep_range(1000, 2000);",
        "BOOT rs=ready phase=398 focus=ap-cliff roots=%u copies=2 crc=crc32c",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase398 missing token: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    p = ns.root / REC
    if not p.is_file():
        raise SystemExit("Phase398 recorder source missing")

    if not ns.check_only:
        p.write_text(patch(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase398 dense AP cliff probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
