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
# helper. P3 must preserve that behavior.
if "ptep_clear_young_notify(pvmw->vma, addr, pte + i)" not in s:
    raise SystemExit("Modern MGLRU P2 notifier-aware look-around marker missing")

# ---------------------------------------------------------------------------
# Modern P3
# Upstream reference:
#   c28ac3c7eb945fee6e20f47d576af68fdff1392a
# mm/mglru: skip special VMAs in lru_gen_look_around()
#
# Special VMAs such as VM_PFNMAP can contain anonymous COW pages and are not a
# useful target for MGLRU locality look-around. Newer kernels skip them before
# scanning adjacent PTEs; doing so also avoids pte_special()/device-map warning
# paths. This maps directly to our old page-based implementation and does not
# require folio-era get_pfn_folio()/can_swap infrastructure.
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
    "\tif (spin_is_contended(pvmw->ptl))\n\t\treturn;\n\n",
    "\tif (spin_is_contended(pvmw->ptl))\n\t\treturn;\n\n"
    "\t/* A52 MGLRU Modern P3: upstream c28ac3c7 skip special VMAs */\n"
    "\tif (pvmw->vma->vm_flags & VM_SPECIAL)\n"
    "\t\treturn;\n\n",
    "special-VMA guard",
)

# Structural safety checks inside this function only.
checks = [
    (
        "P3 marker missing",
        "A52 MGLRU Modern P3: upstream c28ac3c7 skip special VMAs" in look,
    ),
    (
        "special-VMA guard missing",
        "if (pvmw->vma->vm_flags & VM_SPECIAL)" in look,
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

# This old v9-era MGLRU does not have the later get_pfn_page(..., can_swap)
# helper. Refuse to silently grow such an unrelated dependency in P3.
if "get_pfn_page(" in look:
    raise SystemExit("unexpected newer get_pfn_page helper in old look-around path")

s = s[:look_start] + look + s[look_end:]
vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase108-modern-p3.txt").write_text(
    "phase=108\n"
    "baseline_mglru=phase107-modern-p2\n"
    "special_vma_fix=c28ac3c7eb945fee6e20f47d576af68fdff1392a\n"
    "special_vma_policy=skip-lookaround\n"
    "p2_notifier_aging=preserved\n"
    "target=rmap-lookaround-special-vma-correctness\n"
)

print("Phase108 Modern MGLRU P3 applied")
