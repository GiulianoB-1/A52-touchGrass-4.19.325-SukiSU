#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 A619 GPU UV P1 STOCK CONTROL: keep top OPP at stock ARC"
TARGET = Path("drivers/gpu/msm/kgsl_gmu.c")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    path = root / TARGET
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: {MARKER}")
        return

    if "A52 A619 GPU UV P1: top OPP one ARC corner" not in text:
        raise SystemExit("P1 undervolt marker missing")
    if "A52 A619 GPU UV P1 diagnostics: persistent sysfs status" not in text:
        raise SystemExit("P1 diagnostics marker missing")

    old = r'''		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[top] = lowered;
		a52_gpu_uv_p1_selected = lowered;
		a52_gpu_uv_p1_arc_to = k - 1;
		a52_gpu_uv_p1_applied = 1;

		dev_info(&gmu->pdev->dev,
			"A52 GPU UV P1: top=%u Hz VLVL request=%u resolved=%u -> %u (ARC %u -> %u)\n",
			freq_tbl[top], requested, resolved, lowered, k, k - 1);
		return;
'''

    new = r'''		lowered = gfx_arc->val[k - 1];

		/*
		 * A52 A619 GPU UV P1 STOCK CONTROL: keep top OPP at stock ARC
		 *
		 * This A/B control deliberately leaves the resolved stock GX vote
		 * unchanged while retaining the same kernel, GPU modernization,
		 * Samsung userspace, Turnip/Vulkan setup, and read-only diagnostics.
		 */
		vlvl_tbl[top] = resolved;
		a52_gpu_uv_p1_selected = resolved;
		a52_gpu_uv_p1_arc_to = k;
		a52_gpu_uv_p1_applied = 0;

		dev_info(&gmu->pdev->dev,
			"A52 GPU UV P1 STOCK CONTROL: top=%u Hz VLVL request=%u resolved=%u retained=%u (ARC %u)\n",
			freq_tbl[top], requested, resolved, resolved, k);
		return;
'''

    text = replace_once(text, old, new, "stock control replacement")
    path.write_text(text)
    print(f"[patched] {path}: top A619 OPP restored to stock resolved ARC vote")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    patch(root)


if __name__ == "__main__":
    main()
