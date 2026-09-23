#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 MGLRU Modern P4: upstream 081488051d28 underprotected page cache"
P1_MARKER = "A52 MGLRU Modern P1: Android17 9eff4247"
P3_MARKER = "A52 MGLRU Modern P3: upstream c28ac3c7 skip special VMAs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, marker: str, next_marker: str | None = None) -> tuple[int, int]:
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"function marker not found: {marker}")

    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"opening brace not found: {marker}")

    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1

    raise SystemExit(f"unterminated function: {marker}")


def patch_add_page(root: Path) -> None:
    path = root / "include/linux/mm_inline.h"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: page-cache placement")
        return

    start, end = function_bounds(text, "static inline bool lru_gen_add_page(")
    func = text[start:end]

    old = """	/*
	 * There are three common cases for this page:
	 * 1. If it's hot, e.g., freshly faulted in or previously hot and
	 *    migrated, add it to the youngest generation.
	 * 2. If it's cold but can't be evicted immediately, i.e., an anon page
	 *    not in swapcache or a dirty page pending writeback, add it to the
	 *    second oldest generation.
	 * 3. Everything else (clean, cold) is added to the oldest generation.
	 */
	if (PageActive(page))
		gen = lru_gen_from_seq(lrugen->max_seq);
	else if ((type == LRU_GEN_ANON && !PageSwapCache(page)) ||
		 (PageReclaim(page) && (PageDirty(page) || PageWriteback(page))))
		gen = lru_gen_from_seq(lrugen->min_seq[type] + 1);
	else
		gen = lru_gen_from_seq(lrugen->min_seq[type]);
"""

    new = f"""	/*
	 * {MARKER}
	 *
	 * Keep repeatedly useful file-cache pages away from a short-lived oldest
	 * generation. This is the page-based 4.19 adaptation of upstream
	 * 081488051d28 (mm/mglru: fix underprotected page cache).
	 *
	 * 1. Hot pages go to the youngest generation.
	 * 2. Pages that cannot be evicted immediately go to the second youngest.
	 * 3. Reclaim rotations and very shallow generation windows use the oldest.
	 * 4. Other inactive pages get the second oldest generation, giving the
	 *    PID/refault feedback loop enough time to identify reusable page cache.
	 */
	if (PageActive(page))
		gen = lru_gen_from_seq(lrugen->max_seq);
	else if ((type == LRU_GEN_ANON && !PageSwapCache(page)) ||
		 (PageReclaim(page) && (PageDirty(page) || PageWriteback(page))))
		gen = lru_gen_from_seq(lrugen->max_seq - 1);
	else if (reclaiming ||
		 lrugen->min_seq[type] + MIN_NR_GENS >= lrugen->max_seq)
		gen = lru_gen_from_seq(lrugen->min_seq[type]);
	else
		gen = lru_gen_from_seq(lrugen->min_seq[type] + 1);
"""

    func = replace_once(func, old, new, "lru_gen_add_page placement")
    text = text[:start] + func + text[end:]
    path.write_text(text)
    print(f"[patched] {path}: second-youngest/second-oldest placement")


def patch_sort_page(root: Path) -> None:
    path = root / "mm/vmscan.c"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: saturated fd-reference protection")
        return

    if P1_MARKER not in text:
        raise SystemExit("Modern MGLRU P1 marker missing from mm/vmscan.c")
    if P3_MARKER not in text:
        raise SystemExit("Modern MGLRU P3 marker missing from mm/vmscan.c")

    old_helper = """static int page_lru_tier(struct page *page)
{
	int refs;
	unsigned long flags = READ_ONCE(page->flags);

	refs = (flags & LRU_REFS_FLAGS) == LRU_REFS_FLAGS ?
	       ((flags & LRU_REFS_MASK) >> LRU_REFS_PGOFF) + 1 : 0;

	return lru_tier_from_refs(refs);
}
"""
    new_helper = f"""/* {MARKER} */
static int page_lru_refs(struct page *page)
{{
	unsigned long flags = READ_ONCE(page->flags);

	return (flags & LRU_REFS_FLAGS) == LRU_REFS_FLAGS ?
	       ((flags & LRU_REFS_MASK) >> LRU_REFS_PGOFF) + 1 : 0;
}}

static int page_lru_tier(struct page *page)
{{
	return lru_tier_from_refs(page_lru_refs(page));
}}
"""
    text = replace_once(text, old_helper, new_helper, "page_lru_refs helper")

    start, end = function_bounds(text, "static bool sort_page(")
    func = text[start:end]

    func = replace_once(
        func,
        """	int tier = page_lru_tier(page);
	int delta = hpage_nr_pages(page);
""",
        """	int tier = page_lru_tier(page);
	int refs = page_lru_refs(page);
	int delta = hpage_nr_pages(page);
""",
        "sort_page refs declaration",
    )

    func = replace_once(
        func,
        """	if (tier > tier_idx) {
""",
        """	/*
	 * Pages that saturated the fd-access reference counter deserve one more
	 * generation even if the PID controller has not raised their tier yet.
	 */
	if (tier > tier_idx || refs == BIT(LRU_REFS_WIDTH)) {
""",
        "sort_page saturated ref protection",
    )

    text = text[:start] + func + text[end:]
    path.write_text(text)
    print(f"[patched] {path}: protect saturated fd-access pages")


def patch_refault(root: Path) -> None:
    path = root / "mm/workingset.c"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: refault restore saturation")
        return

    start, end = function_bounds(text, "void lru_gen_refault(")
    func = text[start:end]

    # Phase80 is deliberately required. P4 builds on its corrected semantics:
    # all refaults are counted, recent refaults are activations.
    if "mod_lruvec_state(lruvec, WORKINGSET_REFAULT, delta);" not in func:
        raise SystemExit("Phase80 unconditional WORKINGSET_REFAULT accounting missing")
    if "mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);" not in func:
        raise SystemExit("Phase80 recent WORKINGSET_ACTIVATE accounting missing")

    old = """	/*
	 * Count the following two cases as stalls:
	 * 1. For pages accessed through page tables, hotter pages pushed out
	 *    hot pages which refaulted immediately.
	 * 2. For pages accessed through file descriptors, numbers of accesses
	 *    might have been beyond the limit.
	 */
	if (lru_gen_in_fault() || refs + workingset == BIT(LRU_REFS_WIDTH)) {
		SetPageWorkingset(page);
		mod_lruvec_state(lruvec, WORKINGSET_RESTORE, delta);
	}
"""

    new = f"""	/*
	 * {MARKER}
	 *
	 * Page-table refaults remain stalls. For fd-accessed file cache, start
	 * preserving saturated reference history one step before the old hard
	 * limit; sort_page() now protects that saturated tier explicitly.
	 */
	if (lru_gen_in_fault() ||
	    refs + workingset >= BIT(LRU_REFS_WIDTH) - 1) {{
		set_mask_bits(&page->flags, 0,
			      LRU_REFS_MASK | BIT(PG_workingset));
		mod_lruvec_state(lruvec, WORKINGSET_RESTORE, delta);
	}}
"""

    func = replace_once(func, old, new, "lru_gen_refault saturation semantics")
    text = text[:start] + func + text[end:]
    path.write_text(text)
    print(f"[patched] {path}: preserve saturated fd-access history")


def audit(root: Path) -> None:
    mm_inline = (root / "include/linux/mm_inline.h").read_text()
    vmscan = (root / "mm/vmscan.c").read_text()
    workingset = (root / "mm/workingset.c").read_text()

    checks = [
        (MARKER in mm_inline, "P4 placement marker"),
        (MARKER in vmscan, "P4 vmscan marker"),
        (MARKER in workingset, "P4 refault marker"),
        ("lrugen->max_seq - 1" in mm_inline, "second-youngest placement"),
        ("lrugen->min_seq[type] + MIN_NR_GENS >= lrugen->max_seq" in mm_inline,
         "shallow-window guard"),
        ("static int page_lru_refs(struct page *page)" in vmscan,
         "page_lru_refs helper"),
        ("tier > tier_idx || refs == BIT(LRU_REFS_WIDTH)" in vmscan,
         "saturated fd-access protection"),
        ("refs + workingset >= BIT(LRU_REFS_WIDTH) - 1" in workingset,
         "early saturated-ref restore"),
        ("LRU_REFS_MASK | BIT(PG_workingset)" in workingset,
         "ref history saturation"),
        ("mod_lruvec_state(lruvec, WORKINGSET_REFAULT, delta);" in workingset,
         "Phase80 refault accounting preserved"),
        ("mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);" in workingset,
         "Phase80 activation accounting preserved"),
        (P1_MARKER in vmscan, "Modern MGLRU P1 preserved"),
        ("ptep_clear_young_notify(pvmw->vma, addr, pte + i)" in vmscan,
         "Modern MGLRU P2 notifier aging preserved"),
        (P3_MARKER in vmscan, "Modern MGLRU P3 preserved"),
        ("A52 MGLRU Efficiency P4: clear seed PTE before look-around bailouts" in vmscan,
         "existing MGLRU efficiency P4 preserved"),
    ]

    for ok, label in checks:
        if not ok:
            raise SystemExit(f"audit failed: {label}")

    # The old underprotected placement must be gone from this function.
    start, end = function_bounds(mm_inline, "static inline bool lru_gen_add_page(")
    add = mm_inline[start:end]
    if "lru_gen_from_seq(lrugen->min_seq[type] + 1);" not in add:
        raise SystemExit("audit failed: second-oldest placement missing")
    if add.count("lru_gen_from_seq(lrugen->max_seq - 1);") != 1:
        raise SystemExit("audit failed: unexpected second-youngest placement count")

    print("[audit] Modern MGLRU P4 underprotected page-cache fix: PASS")
    print("[audit] Phase80 workingset accounting already present: PASS")
    print("[audit] Modern MGLRU P1/P2/P3 preserved: PASS")
    print("[audit] no swappiness, LMKD, zram or scheduler policy changed")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_add_page(root)
    patch_sort_page(root)
    patch_refault(root)
    audit(root)

    print("[done] A52 Modern MGLRU P4 applied")
    print("[source] upstream 081488051d28: mm/mglru: fix underprotected page cache")
    print("[benefit] protects repeatedly reused file cache from overly short oldest generations")
    print("[unchanged] uclamp, WALT, schedutil, swappiness, zram and LMKD policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
