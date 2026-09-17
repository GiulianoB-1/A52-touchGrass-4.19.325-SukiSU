#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 108_apply_mglru_modern_p3.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"

if not vmscan.is_file():
    raise SystemExit(f"missing required file: {vmscan}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


s = vmscan.read_text()

# P3 is intentionally layered on top of the runtime-proven P1 + P2 chain.
for marker in (
    "A52 MGLRU Modern P1: Android17 9eff4247",
    "A52 MGLRU Modern P1: Android a7adb988 per-zone forward progress",
    "A52 MGLRU Modern P1: migration can happen before addition",
):
    if marker not in s:
        raise SystemExit(f"Modern MGLRU P1 marker missing: {marker}")

# P2 must already have converted look-around PTE aging to the notifier-aware
# helper. P3 must preserve that behavior while fixing swap eligibility.
if "ptep_clear_young_notify(pvmw->vma, addr, pte + i)" not in s:
    raise SystemExit("Modern MGLRU P2 notifier-aware look-around marker missing")

# ---------------------------------------------------------------------------
# Modern P3
# Android/common reference:
#   5e1d25ac2ab670561949d82de7b5027e5a9676d5
# FROMGIT: BACKPORT: Multi-gen LRU: Fix can_swap in lru_gen_look_around()
# Upstream/mm reference:
#   fdf19e8c8f1cdcee4eccf4c98a875f44f39d8b9d
#
# walk->can_swap is tied to the reclaim walk and is not guaranteed to describe
# the lruvec/page that triggered this rmap look-around. Derive swap eligibility
# from the triggering page instead, matching the page-based Android backport.
# ---------------------------------------------------------------------------

look_start = s.find("void lru_gen_look_around(struct page_vma_mapped_walk *pvmw)")
look_end = s.find(
    "/******************************************************************************\n *                          the eviction",
    look_start,
)
if look_start < 0 or look_end < 0:
    raise SystemExit("lru_gen_look_around boundaries not found")

look = s[look_start:look_end]

if "A52 MGLRU Modern P3:" in look:
    raise SystemExit("Modern MGLRU P3 appears to be already applied")

look = replace_once(
    look,
    "\tstruct page *page = pvmw->page;\n",
    "\tstruct page *page = pvmw->page;\n"
    "\t/* A52 MGLRU Modern P3: Android/common 5e1d25ac can_swap from trigger page */\n"
    "\tbool can_swap = !page_is_file_lru(page);\n",
    "trigger-page can_swap declaration",
)

look = replace_once(
    look,
    "page = get_pfn_page(pfn, memcg, pgdat, !walk || walk->can_swap);",
    "page = get_pfn_page(pfn, memcg, pgdat, can_swap);",
    "look-around get_pfn_page can_swap",
)

# Structural safety checks inside this function only.
checks = [
    (
        "P3 marker missing",
        "A52 MGLRU Modern P3: Android/common 5e1d25ac can_swap from trigger page" in look,
    ),
    (
        "trigger-page can_swap declaration missing",
        "bool can_swap = !page_is_file_lru(page);" in look,
    ),
    (
        "fixed get_pfn_page call missing",
        "page = get_pfn_page(pfn, memcg, pgdat, can_swap);" in look,
    ),
    (
        "P2 notifier-aware clear was lost",
        "ptep_clear_young_notify(pvmw->vma, addr, pte + i)" in look,
    ),
    (
        "P2 notifier-aware PTE prefilter was lost",
        "!pte_young(pte[i]) && !mm_has_notifiers(pvmw->vma->vm_mm)" in look,
    ),
]
for label, ok in checks:
    if not ok:
        raise SystemExit(label)

if "!walk || walk->can_swap" in look:
    raise SystemExit("stale walk->can_swap remains in lru_gen_look_around")

s = s[:look_start] + look + s[look_end:]
vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase108-modern-p3.txt").write_text(
    "phase=108\n"
    "baseline_mglru=phase107-modern-p2\n"
    "lookaround_can_swap_fix=5e1d25ac2ab670561949d82de7b5027e5a9676d5\n"
    "upstream_mm=fdf19e8c8f1cdcee4eccf4c98a875f44f39d8b9d\n"
    "can_swap_source=trigger-page-lru-type\n"
    "p2_notifier_aging=preserved\n"
    "target=rmap-lookaround-swap-eligibility-correctness\n"
)

print("Phase108 Modern MGLRU P3 applied")
