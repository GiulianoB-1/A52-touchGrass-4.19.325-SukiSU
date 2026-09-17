#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 107_apply_mglru_modern_p2.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"
mmu_notifier = root / "include/linux/mmu_notifier.h"

for p in (vmscan, mmu_notifier):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


s = vmscan.read_text()
notifier_h = mmu_notifier.read_text()

# P2 is intentionally layered on top of the runtime-proven P1.
for marker in (
    "A52 MGLRU Modern P1: Android17 9eff4247",
    "A52 MGLRU Modern P1: Android a7adb988 per-zone forward progress",
    "A52 MGLRU Modern P1: migration can happen before addition",
):
    if marker not in s:
        raise SystemExit(f"Modern MGLRU P1 marker missing: {marker}")

# Linux 4.19 already provides the notifier-aware accessed-bit helpers we need.
for helper in (
    "ptep_clear_young_notify",
    "pmdp_clear_young_notify",
    "mm_has_notifiers",
):
    if helper not in notifier_h:
        raise SystemExit(f"required Linux 4.19 MMU notifier helper missing: {helper}")

# ---------------------------------------------------------------------------
# Modern P2
# Upstream / Android common reference:
#   1d4832becdc2cdb2cffe2a6050c9d9fd8ff1c58c
# mm: multi-gen LRU: use {ptep,pmdp}_clear_young_notify()
#
# MGLRU's page-table walkers clear accessed bits while aging memory. If an mm
# has MMU notifiers (KVM is the important example), clearing only the primary
# page-table young bit can leave secondary-MMU accessed state stale. That makes
# VM memory appear younger than it really is and can push reclaim pressure onto
# unrelated memory. 4.19 already has the notifier-aware helpers, so adapt the
# modern fix to the old page-based MGLRU rather than importing folio-era code.
# ---------------------------------------------------------------------------

if "#include <linux/mmu_notifier.h>" not in s:
    s = replace_once(
        s,
        "#include <linux/debugfs.h>\n",
        "#include <linux/debugfs.h>\n#include <linux/mmu_notifier.h>\n",
        "MMU notifier include",
    )

# 1) Main PTE aging walk: if an mm has notifiers, an old host PTE can still
#    represent a young secondary mapping. Let the notifier-aware clear decide.
pte_start = s.find("static bool walk_pte_range(")
pte_end = s.find("static void walk_pmd_range_locked(", pte_start)
if pte_start < 0 or pte_end < 0:
    raise SystemExit("walk_pte_range boundaries not found")
pte = s[pte_start:pte_end]

pte = replace_once(
    pte,
    """\t\tif (!pte_young(pte[i])) {\n\t\t\tpriv->mm_stats[MM_PTE_OLD]++;\n\t\t\tcontinue;\n\t\t}\n""",
    """\t\t/* A52 MGLRU Modern P2: secondary MMUs may still report young */\n\t\tif (!pte_young(pte[i]) && !mm_has_notifiers(walk->mm)) {\n\t\t\tpriv->mm_stats[MM_PTE_OLD]++;\n\t\t\tcontinue;\n\t\t}\n""",
    "notifier-aware PTE prefilter",
)
pte = replace_once(
    pte,
    "ptep_test_and_clear_young(walk->vma, addr, pte + i)",
    "ptep_clear_young_notify(walk->vma, addr, pte + i)",
    "notifier-aware PTE clear",
)
s = s[:pte_start] + pte + s[pte_end:]

# 2) PMD aging. Leaf PMDs use the notifier-aware helper. For non-leaf PMDs,
#    do not clear the PMD young bit behind an MMU notifier's back. Instead let
#    the PTE walk perform notifier-aware aging.
pmd_locked_start = s.find("static void walk_pmd_range_locked(")
pmd_locked_end = s.find("#else\nstatic void walk_pmd_range_locked(", pmd_locked_start)
if pmd_locked_start < 0 or pmd_locked_end < 0:
    raise SystemExit("walk_pmd_range_locked boundaries not found")
pmd_locked = s[pmd_locked_start:pmd_locked_end]

pmd_locked = replace_once(
    pmd_locked,
    """\t\tif (!pmd_trans_huge(pmd[i])) {\n\t\t\tif (IS_ENABLED(CONFIG_ARCH_HAS_NONLEAF_PMD_YOUNG) &&\n\t\t\t    get_cap(LRU_GEN_NONLEAF_YOUNG))\n\t\t\t\tpmdp_test_and_clear_young(vma, addr, pmd + i);\n\t\t\tgoto next;\n\t\t}\n""",
    """\t\tif (!pmd_trans_huge(pmd[i])) {\n\t\t\tif (IS_ENABLED(CONFIG_ARCH_HAS_NONLEAF_PMD_YOUNG) &&\n\t\t\t    get_cap(LRU_GEN_NONLEAF_YOUNG) &&\n\t\t\t    !mm_has_notifiers(walk->mm))\n\t\t\t\tpmdp_test_and_clear_young(vma, addr, pmd + i);\n\t\t\tgoto next;\n\t\t}\n""",
    "non-leaf PMD notifier guard",
)
pmd_locked = replace_once(
    pmd_locked,
    """\t\tif (!pmdp_test_and_clear_young(vma, addr, pmd + i))\n\t\t\tgoto next;\n""",
    """\t\tif (!pmdp_clear_young_notify(vma, addr, pmd + i))\n\t\t\tgoto next;\n""",
    "notifier-aware leaf PMD clear",
)
s = s[:pmd_locked_start] + pmd_locked + s[pmd_locked_end:]

# 3) PMD outer walk: don't reject a leaf PMD solely because the host PMD young
#    bit is clear when a notifier-backed secondary MMU may still have activity.
pmd_start = s.find("static void walk_pmd_range(pud_t *pud")
pmd_end = s.find("static int walk_pud_range(", pmd_start)
if pmd_start < 0 or pmd_end < 0:
    raise SystemExit("walk_pmd_range boundaries not found")
pmd = s[pmd_start:pmd_end]

pmd = replace_once(
    pmd,
    """\t\t\tif (!pmd_young(val)) {\n\t\t\t\tpriv->mm_stats[MM_PTE_OLD]++;\n\t\t\t\tcontinue;\n\t\t\t}\n""",
    """\t\t\tif (!pmd_young(val) && !mm_has_notifiers(walk->mm)) {\n\t\t\t\tpriv->mm_stats[MM_PTE_OLD]++;\n\t\t\t\tcontinue;\n\t\t\t}\n""",
    "notifier-aware leaf PMD prefilter",
)
pmd = replace_once(
    pmd,
    """#ifdef CONFIG_ARCH_HAS_NONLEAF_PMD_YOUNG\n\t\tif (get_cap(LRU_GEN_NONLEAF_YOUNG)) {\n\t\t\tif (!pmd_young(val))\n\t\t\t\tcontinue;\n\n\t\t\twalk_pmd_range_locked(pud, addr, vma, walk, &pos);\n\t\t}\n#endif\n""",
    """#ifdef CONFIG_ARCH_HAS_NONLEAF_PMD_YOUNG\n\t\tif (get_cap(LRU_GEN_NONLEAF_YOUNG) && !mm_has_notifiers(walk->mm)) {\n\t\t\tif (!pmd_young(val))\n\t\t\t\tcontinue;\n\n\t\t\twalk_pmd_range_locked(pud, addr, vma, walk, &pos);\n\t\t}\n#endif\n""",
    "non-leaf PMD outer notifier guard",
)
s = s[:pmd_start] + pmd + s[pmd_end:]

# 4) Rmap locality look-around: adjacent PTEs also need notifier-aware aging.
look_start = s.find("void lru_gen_look_around(struct page_vma_mapped_walk *pvmw)")
look_end = s.find("/******************************************************************************\n *                          the eviction", look_start)
if look_start < 0 or look_end < 0:
    raise SystemExit("lru_gen_look_around boundaries not found")
look = s[look_start:look_end]

look = replace_once(
    look,
    """\t\tif (!pte_young(pte[i]))\n\t\t\tcontinue;\n""",
    """\t\tif (!pte_young(pte[i]) && !mm_has_notifiers(pvmw->vma->vm_mm))\n\t\t\tcontinue;\n""",
    "look-around notifier-aware PTE prefilter",
)
look = replace_once(
    look,
    "ptep_test_and_clear_young(pvmw->vma, addr, pte + i)",
    "ptep_clear_young_notify(pvmw->vma, addr, pte + i)",
    "look-around notifier-aware PTE clear",
)
s = s[:look_start] + look + s[look_end:]

# Structural safety checks.
checks = [
    ("MMU notifier include missing", "#include <linux/mmu_notifier.h>" in s),
    ("PTE notifier helper missing from MGLRU", "ptep_clear_young_notify(walk->vma, addr, pte + i)" in s),
    ("leaf PMD notifier helper missing from MGLRU", "pmdp_clear_young_notify(vma, addr, pmd + i)" in s),
    ("walk PTE notifier prefilter missing", "!pte_young(pte[i]) && !mm_has_notifiers(walk->mm)" in s),
    ("look-around notifier prefilter missing", "!pte_young(pte[i]) && !mm_has_notifiers(pvmw->vma->vm_mm)" in s),
    ("non-leaf PMD notifier guard missing", "get_cap(LRU_GEN_NONLEAF_YOUNG) && !mm_has_notifiers(walk->mm)" in s),
]
for label, ok in checks:
    if not ok:
        raise SystemExit(label)

# Make sure the two page-based MGLRU PTE clear sites no longer bypass notifiers.
for start_marker, end_marker, label in (
    ("static bool walk_pte_range(", "static void walk_pmd_range_locked(", "walk_pte_range"),
    ("void lru_gen_look_around(", "/******************************************************************************\n *                          the eviction", "lru_gen_look_around"),
):
    a = s.find(start_marker)
    b = s.find(end_marker, a)
    if a < 0 or b < 0:
        raise SystemExit(f"{label}: boundaries missing during final audit")
    if "ptep_test_and_clear_young(" in s[a:b]:
        raise SystemExit(f"{label}: raw ptep_test_and_clear_young remains")

vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase107-modern-p2.txt").write_text(
    "phase=107\n"
    "baseline_mglru=phase106-modern-p1\n"
    "mmu_notifier_young_fix=1d4832becdc2cdb2cffe2a6050c9d9fd8ff1c58c\n"
    "pte_aging=notifier-aware\n"
    "leaf_pmd_aging=notifier-aware\n"
    "nonleaf_pmd=do-not-clear-behind-notifier\n"
    "rmap_lookaround=notifier-aware-adjacent-pte-aging\n"
    "target=kvm-secondary-mmu-aging-correctness\n"
)

print("Phase107 Modern MGLRU P2 applied")
