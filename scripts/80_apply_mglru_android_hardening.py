#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 80_apply_mglru_android_hardening.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
mmzone = root / "include/linux/mmzone.h"
vmscan = root / "mm/vmscan.c"
workingset = root / "mm/workingset.c"

for p in (mmzone, vmscan, workingset):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# ---------------------------------------------------------------------------
# Android hardening 1:
# BACKPORT: mm: Multi-gen LRU: remove wait_event_killable()
#
# Android field telemetry showed >100 ms reclaim tail latency caused by
# priority inversion on lruvec->mm_state.wait.  The Android fix also changes
# the MM-list iteration bookkeeping so concurrent walkers no longer depend on
# nr_walkers/waiters.  Keep the struct fields for ABI/layout compatibility,
# but stop using them.
#
# Canonical Android common reference:
#   22c657e2b3829f2d7e4512c54ec89a3a8b9199d9
# Upstream reference:
#   7f63cf2d9b9bbe7b90f808927558a66ff737d399
# ---------------------------------------------------------------------------

s = mmzone.read_text()
old = """	/* where the current iteration starts (inclusive) */
	struct list_head *head;
	/* where the last iteration ends (exclusive) */
	struct list_head *tail;
	/* to wait for the last page table walker to finish */
	struct wait_queue_head wait;
	/* Bloom filters flip after each iteration */
	unsigned long *filters[NR_BLOOM_FILTERS];
	/* the mm stats for debugging */
	unsigned long stats[NR_HIST_GENS][NR_MM_STATS];
	/* the number of concurrent page table walkers */
	int nr_walkers;
"""
new = """	/* where the current iteration continues after */
	struct list_head *head;
	/* where the last iteration ended before */
	struct list_head *tail;
	/* unused; kept for ABI/layout compatibility */
	struct wait_queue_head wait;
	/* Bloom filters flip after each iteration */
	unsigned long *filters[NR_BLOOM_FILTERS];
	/* the mm stats for debugging */
	unsigned long stats[NR_HIST_GENS][NR_MM_STATS];
	/* unused; kept for ABI/layout compatibility */
	int nr_walkers;
"""
s = replace_once(s, old, new, "mmzone walker state")
mmzone.write_text(s)

s = vmscan.read_text()

old = """		if (lruvec->mm_state.tail == &mm->lru_gen.list)
			lruvec->mm_state.tail = lruvec->mm_state.tail->next;

		if (lruvec->mm_state.head != &mm->lru_gen.list)
			continue;

		lruvec->mm_state.head = lruvec->mm_state.head->next;
		if (lruvec->mm_state.head == &mm_list->fifo)
			WRITE_ONCE(lruvec->mm_state.seq, lruvec->mm_state.seq + 1);
"""
new = """		/* where the current iteration continues after */
		if (lruvec->mm_state.head == &mm->lru_gen.list)
			lruvec->mm_state.head = lruvec->mm_state.head->prev;

		/* where the last iteration ended before */
		if (lruvec->mm_state.tail == &mm->lru_gen.list)
			lruvec->mm_state.tail = lruvec->mm_state.tail->next;
"""
s = replace_once(s, old, new, "lru_gen_del_mm walker bookkeeping")

start = s.find("static bool iterate_mm_list(struct lruvec *lruvec")
end = s.find("static bool iterate_mm_list_nowalk", start)
if start < 0 or end < 0:
    raise SystemExit("iterate_mm_list function boundaries not found")
new_func = """static bool iterate_mm_list(struct lruvec *lruvec, struct lru_gen_mm_walk *walk,
			    struct mm_struct **iter)
{
	bool first = false;
	bool last = false;
	struct mm_struct *mm = NULL;
	struct mem_cgroup *memcg = lruvec_memcg(lruvec);
	struct lru_gen_mm_list *mm_list = get_mm_list(memcg);
	struct lru_gen_mm_state *mm_state = &lruvec->mm_state;

	/*
	 * mm_state->seq is incremented after each iteration of mm_list. There
	 * are three interesting cases for this page table walker:
	 * 1. It tries to start a new iteration with a stale max_seq: there is
	 *    nothing left to do.
	 * 2. It started the next iteration: reset the Bloom filter so a fresh
	 *    set of PTE tables can be recorded.
	 * 3. It ended the current iteration: reset the mm stats counters and
	 *    tell the caller to increment max_seq.
	 */
	if (*iter)
		mmput_async(*iter);
	else if (walk->max_seq <= READ_ONCE(mm_state->seq))
		return false;

	spin_lock(&mm_list->lock);

	VM_BUG_ON(mm_state->seq + 1 < walk->max_seq);

	if (walk->max_seq <= mm_state->seq)
		goto done;

	if (!mm_state->head)
		mm_state->head = &mm_list->fifo;

	if (mm_state->head == &mm_list->fifo)
		first = true;

	do {
		mm_state->head = mm_state->head->next;
		if (mm_state->head == &mm_list->fifo) {
			WRITE_ONCE(mm_state->seq, mm_state->seq + 1);
			last = true;
			break;
		}

		/* full scan for those added after the last iteration */
		if (!mm_state->tail || mm_state->tail == mm_state->head) {
			mm_state->tail = mm_state->head->next;
			walk->full_scan = true;
		}

		mm = list_entry(mm_state->head, struct mm_struct, lru_gen.list);
		if (should_skip_mm(mm, walk))
			mm = NULL;
	} while (!mm);

done:
	if (mm && first)
		reset_bloom_filter(lruvec, walk->max_seq + 1);

	if (*iter || last)
		reset_mm_stats(lruvec, walk, last);

	spin_unlock(&mm_list->lock);

	*iter = mm;

	return last;
}

"""
s = s[:start] + new_func + s[end:]

start = s.find("static bool iterate_mm_list_nowalk")
end = s.find("/******************************************************************************\n *                          refault feedback loop", start)
if start < 0 or end < 0:
    raise SystemExit("iterate_mm_list_nowalk function boundaries not found")
new_func = """static bool iterate_mm_list_nowalk(struct lruvec *lruvec, unsigned long max_seq)
{
	bool success = false;
	struct mem_cgroup *memcg = lruvec_memcg(lruvec);
	struct lru_gen_mm_list *mm_list = get_mm_list(memcg);
	struct lru_gen_mm_state *mm_state = &lruvec->mm_state;

	if (max_seq <= READ_ONCE(mm_state->seq))
		return false;

	spin_lock(&mm_list->lock);

	VM_BUG_ON(mm_state->seq + 1 < max_seq);

	if (max_seq > mm_state->seq) {
		mm_state->head = NULL;
		mm_state->tail = NULL;
		WRITE_ONCE(mm_state->seq, mm_state->seq + 1);
		reset_mm_stats(lruvec, NULL, true);
		success = true;
	}

	spin_unlock(&mm_list->lock);

	return success;
}

"""
s = s[:start] + new_func + s[end:]

old = """	do {
		err = -EBUSY;

		/* page_update_gen() requires stable page_memcg() */
"""
new = """	do {
		DEFINE_MAX_SEQ(lruvec);

		err = -EBUSY;

		/* another thread might have called inc_max_seq() */
		if (walk->max_seq != max_seq)
			break;

		/* page_update_gen() requires stable page_memcg() */
"""
s = replace_once(s, old, new, "walk_mm generation race check")

start = s.find("static bool try_to_inc_max_seq(struct lruvec *lruvec")
end = s.find("static long get_nr_evictable", start)
if start < 0 or end < 0:
    raise SystemExit("try_to_inc_max_seq function boundaries not found")
new_func = """static bool try_to_inc_max_seq(struct lruvec *lruvec, unsigned long max_seq,
			       struct scan_control *sc, bool can_swap, bool full_scan)
{
	bool success;
	struct lru_gen_mm_walk *walk;
	struct mm_struct *mm = NULL;

	VM_BUG_ON(max_seq > READ_ONCE(lruvec->lrugen.max_seq));

	/*
	 * If the hardware doesn't automatically set the accessed bit, fallback
	 * to lru_gen_look_around(), which only clears the accessed bit in a
	 * handful of PTEs. Spreading the work out over a period of time usually
	 * is less efficient, but it avoids bursty page faults.
	 */
	if (!full_scan && (!arch_has_hw_pte_young() || !get_cap(LRU_GEN_MM_WALK))) {
		success = iterate_mm_list_nowalk(lruvec, max_seq);
		goto done;
	}

	walk = alloc_mm_walk();
	if (!walk) {
		success = iterate_mm_list_nowalk(lruvec, max_seq);
		goto done;
	}

	walk->lruvec = lruvec;
	walk->max_seq = max_seq;
	walk->can_swap = can_swap;
	walk->full_scan = full_scan;

	do {
		success = iterate_mm_list(lruvec, walk, &mm);
		if (mm)
			walk_mm(lruvec, mm, walk);
	} while (mm);

	free_mm_walk(walk);
done:
	/*
	 * Android telemetry found >100 ms tail latency when direct reclaimers
	 * slept on mm_state.wait behind a lower-priority page-table walker.
	 * Do not wait here. The walker that completes the generation advances
	 * max_seq; competing reclaimers simply retry through normal reclaim.
	 */
	if (success) {
		VM_BUG_ON(max_seq != READ_ONCE(lruvec->lrugen.max_seq));
		inc_max_seq(lruvec);
		wakeup_flusher_threads(WB_REASON_VMSCAN);
	}

	return success;
}

"""
s = s[:start] + new_func + s[end:]

old = """	lruvec->mm_state.seq = MIN_NR_GENS;
	init_waitqueue_head(&lruvec->mm_state.wait);
"""
new = """	lruvec->mm_state.seq = MIN_NR_GENS;
"""
s = replace_once(s, old, new, "lru_gen_init_lruvec waitqueue initialization")

vmscan.write_text(s)

# ---------------------------------------------------------------------------
# Android hardening 2:
# BACKPORT: FROMGIT: Multi-gen LRU: fix workingset accounting
#
# Refaults must be counted regardless of whether their shadow entry still
# belongs to the current oldest generation.  Only recent refaults count as
# workingset activations.  Also, promotion during MGLRU aging is not itself
# a workingset activation.
#
# Canonical Android common reference:
#   40259b07af18a32a176706341075482aa23d3515
# Upstream/mm reference:
#   02ad728453d2ddb09d7ce5e59854ebb27544d488
#
# Linux 4.19 has aggregate WORKINGSET_* node counters rather than the later
# anon/file BASE counters, so preserve the same semantics using those.
# ---------------------------------------------------------------------------

s = workingset.read_text()
old = """	lruvec = mem_cgroup_lruvec(pgdat, memcg);
	lrugen = &lruvec->lrugen;
	min_seq = READ_ONCE(lrugen->min_seq[type]);
"""
new = """	lruvec = mem_cgroup_lruvec(pgdat, memcg);
	lrugen = &lruvec->lrugen;

	/* Count every refault for Android/LMKD thrashing detection. */
	mod_lruvec_state(lruvec, WORKINGSET_REFAULT, delta);

	min_seq = READ_ONCE(lrugen->min_seq[type]);
"""
# This anchor exists once in lru_gen_refault and once in lru_gen_eviction only
# if min_seq follows immediately. Scope it to text after lru_gen_refault.
refault_pos = s.find("void lru_gen_refault(struct page *page, void *shadow)")
if refault_pos < 0:
    raise SystemExit("lru_gen_refault not found")
prefix, tail = s[:refault_pos], s[refault_pos:]
tail = replace_once(tail, old, new, "lru_gen_refault unconditional refault accounting")
s = prefix + tail

old = """	hist = lru_hist_from_seq(min_seq);
	tier = lru_tier_from_refs(refs + workingset);
	atomic_long_add(delta, &lrugen->refaulted[hist][type][tier]);
	mod_lruvec_state(lruvec, WORKINGSET_REFAULT, delta);
"""
new = """	hist = lru_hist_from_seq(min_seq);
	tier = lru_tier_from_refs(refs + workingset);
	atomic_long_add(delta, &lrugen->refaulted[hist][type][tier]);
	mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);
"""
s = replace_once(s, old, new, "lru_gen_refault activation accounting")
workingset.write_text(s)

s = vmscan.read_text()
old = """		WRITE_ONCE(lrugen->protected[hist][type][tier - 1],
			   lrugen->protected[hist][type][tier - 1] + delta);
		__mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);
		return true;
"""
new = """		WRITE_ONCE(lrugen->protected[hist][type][tier - 1],
			   lrugen->protected[hist][type][tier - 1] + delta);
		return true;
"""
s = replace_once(s, old, new, "sort_page promotion accounting")
vmscan.write_text(s)

# Structural safety checks.
v = vmscan.read_text()
w = workingset.read_text()
m = mmzone.read_text()

checks = [
    ("MGLRU wait still present in try_to_inc_max_seq",
     "wait_event_killable(lruvec->mm_state.wait" not in v),
    ("MGLRU wake-up waitqueue still present",
     "wake_up_all(&lruvec->mm_state.wait)" not in v),
    ("MGLRU waitqueue still initialized",
     "init_waitqueue_head(&lruvec->mm_state.wait)" not in v),
    ("nr_walkers still used by MGLRU implementation",
     "mm_state->nr_walkers" not in v),
    ("walker generation race guard missing",
     "if (walk->max_seq != max_seq)" in v),
    ("walker head reset missing",
     "mm_state->head = NULL;" in v),
    ("walker tail reset missing",
     "mm_state->tail = NULL;" in v),
    ("ABI wait field missing",
     "struct wait_queue_head wait;" in m),
    ("ABI nr_walkers field missing",
     "int nr_walkers;" in m),
    ("unconditional workingset refault accounting missing",
     "mod_lruvec_state(lruvec, WORKINGSET_REFAULT, delta);" in w),
    ("workingset activation accounting missing",
     "mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);" in w),
]

for label, ok in checks:
    if not ok:
        raise SystemExit(label)

# Ensure the old bogus aging-promotion activation is gone specifically from
# sort_page(). Other conventional-LRU WORKINGSET_ACTIVATE uses remain valid.
sort_start = v.find("static bool sort_page(")
sort_end = v.find("static bool isolate_page(", sort_start)
if sort_start < 0 or sort_end < 0:
    raise SystemExit("sort_page boundaries missing")
if "__mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);" in v[sort_start:sort_end]:
    raise SystemExit("sort_page still counts aging promotion as workingset activation")

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase80-android-hardening.txt").write_text(
    "phase=80\n"
    "mglru_wait_event_killable=removed\n"
    "mm_walker_concurrency=android-no-wait-semantics\n"
    "abi_wait_field=retained-unused\n"
    "abi_nr_walkers_field=retained-unused\n"
    "workingset_refault=account-all-refaults\n"
    "workingset_activate=recent-refaults-only\n"
    "aging_promotion_activation=removed\n"
    "android_wait_fix=22c657e2b3829f2d7e4512c54ec89a3a8b9199d9\n"
    "android_workingset_fix=40259b07af18a32a176706341075482aa23d3515\n"
)

print("Phase80 MGLRU Android hardening applied")
