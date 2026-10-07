#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE446F_SKIP_LATE_SMMU_COMPONENT_MATCH_V1"

def die(msg: str) -> None:
    raise SystemExit("Phase446f: " + msg)

def patch(s: str) -> str:
    if MARK in s:
        return s

    fn = "static int add_display_components(struct device *dev,"
    if fn not in s:
        die("add_display_components anchor missing")

    marker_anchor = fn
    marker = (
        'static const char a52_p446f_marker[] __used = "' + MARK + '";\n\n'
    )
    s = s.replace(marker_anchor, marker + marker_anchor, 1)

    old = """			a52_p446_mark(2U,1U,!!smmu_pdev->dev.driver);
			component_match_add(dev, matchptr, compare_of, smmu_node);
			a52_ackfr_record("DRMCOMP smmu-match added node=%s",
					smmu_node->full_name);
"""
    new = """			a52_p446_mark(2U,1U,!!smmu_pdev->dev.driver);
			/* A52_PHASE446F_SKIP_LATE_SMMU_COMPONENT_MATCH_V1
			 * Keep the Phase201 early find/create path and the already-probed
			 * SMMU client, but do not add this child to the DRM master match.
			 */
			a52_p446_mark(0x190U, 1U, !!smmu_pdev->dev.driver);
			a52_ackfr_record("DRMCOMP smmu-match skipped node=%s driver=%d client=%d",
					smmu_node->full_name, !!smmu_pdev->dev.driver,
					!!platform_get_drvdata(smmu_pdev));
"""
    if s.count(old) != 1:
        die(f"SMMU late component-match anchor count changed: {s.count(old)}")
    return s.replace(old, new, 1)

def check(s: str) -> None:
    required = (
        MARK,
        'a52_p446_mark(0x190U, 1U, !!smmu_pdev->dev.driver);',
        'DRMCOMP smmu-match skipped node=%s driver=%d client=%d',
        'of_find_compatible_node(np, NULL,',
        '"qcom,smmu_sde_unsec"',
        'of_find_device_by_node(smmu_node)',
        'of_platform_device_create(smmu_node,',
    )
    missing = [x for x in required if x not in s]
    if missing:
        die("contract missing: " + ", ".join(missing))

    start = s.find('smmu_node = of_find_compatible_node')
    end = s.find('a52_ackfr_record("DRMCOMP collect exit', start)
    if start < 0 or end < 0:
        die("SMMU collection block bounds missing")
    block = s[start:end]
    if 'component_match_add(dev, matchptr, compare_of, smmu_node);' in block:
        die("late SMMU component_match_add still active")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    p = a.root / "drivers/a52_display/msm/msm_drv.c"
    if not p.exists():
        die(f"missing {p}")

    s = p.read_text(errors="replace")
    if not a.check_only:
        s = patch(s)
        p.write_text(s)

    check(p.read_text(errors="replace"))
    print("Phase446f GKI: late SMMU DRM component match skipped, early child kept: PASS")

if __name__ == "__main__":
    main()
