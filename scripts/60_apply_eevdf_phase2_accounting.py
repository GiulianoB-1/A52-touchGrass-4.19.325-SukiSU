#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 60_apply_eevdf_phase2_accounting.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
sched_h = kernel / "kernel/sched/sched.h"
fair_c = kernel / "kernel/sched/fair.c"

sh = sched_h.read_text()
fc = fair_c.read_text()

if "EEVDF phase 2 accounting" not in sh:
    anchor = """	u64			exec_clock;
	u64			min_vruntime;
#ifndef CONFIG_64BIT
	u64			min_vruntime_copy;
#endif
"""
    repl = """	u64			exec_clock;
	u64			min_vruntime;
#ifndef CONFIG_64BIT
	u64			min_vruntime_copy;
#endif

	/* EEVDF phase 2 accounting: weighted virtual-time state. */
	s64			avg_vruntime;
	unsigned long		avg_load;
"""
    if sh.count(anchor) != 1:
        raise SystemExit("cfs_rq min_vruntime anchor mismatch")
    sh = sh.replace(anchor, repl, 1)
    sched_h.write_text(sh)

if "eevdf_avg_vruntime_add" not in fc:
    anchor = """static inline int entity_before(struct sched_entity *a,
				struct sched_entity *b)
{
	return (s64)(a->vruntime - b->vruntime) < 0;
}

"""
    helpers = """static inline int entity_before(struct sched_entity *a,
				struct sched_entity *b)
{
	return (s64)(a->vruntime - b->vruntime) < 0;
}

/*
 * EEVDF phase 2 accounting.
 *
 * These helpers mirror the weighted virtual-time accounting used by
 * upstream EEVDF.  Task selection is intentionally still legacy CFS/WALT.
 */
static inline s64 eevdf_entity_key(struct cfs_rq *cfs_rq,
				   struct sched_entity *se)
{
	return (s64)(se->vruntime - cfs_rq->min_vruntime);
}

static void eevdf_avg_vruntime_add(struct cfs_rq *cfs_rq,
				   struct sched_entity *se)
{
	unsigned long weight = scale_load_down(se->load.weight);

	cfs_rq->avg_vruntime += eevdf_entity_key(cfs_rq, se) * weight;
	cfs_rq->avg_load += weight;
}

static void eevdf_avg_vruntime_sub(struct cfs_rq *cfs_rq,
				   struct sched_entity *se)
{
	unsigned long weight = scale_load_down(se->load.weight);

	cfs_rq->avg_vruntime -= eevdf_entity_key(cfs_rq, se) * weight;
	cfs_rq->avg_load -= weight;
}

static inline void eevdf_avg_vruntime_update(struct cfs_rq *cfs_rq, s64 delta)
{
	cfs_rq->avg_vruntime -= cfs_rq->avg_load * delta;
}

static u64 eevdf_avg_vruntime(struct cfs_rq *cfs_rq)
{
	struct sched_entity *curr = cfs_rq->curr;
	s64 avg = cfs_rq->avg_vruntime;
	long load = cfs_rq->avg_load;

	if (curr && curr->on_rq) {
		unsigned long weight = scale_load_down(curr->load.weight);

		avg += eevdf_entity_key(cfs_rq, curr) * weight;
		load += weight;
	}

	if (load) {
		if (avg < 0)
			avg -= (load - 1);
		avg = div_s64(avg, load);
	}

	return cfs_rq->min_vruntime + avg;
}

static int eevdf_entity_eligible(struct cfs_rq *cfs_rq,
				 struct sched_entity *se)
{
	struct sched_entity *curr = cfs_rq->curr;
	s64 avg = cfs_rq->avg_vruntime;
	long load = cfs_rq->avg_load;

	if (curr && curr->on_rq) {
		unsigned long weight = scale_load_down(curr->load.weight);

		avg += eevdf_entity_key(cfs_rq, curr) * weight;
		load += weight;
	}

	return avg >= eevdf_entity_key(cfs_rq, se) * load;
}

"""
    if fc.count(anchor) != 1:
        raise SystemExit("entity_before anchor mismatch")
    fc = fc.replace(anchor, helpers, 1)

old = """	/* ensure we never gain time by being placed backwards. */
	cfs_rq->min_vruntime = max_vruntime(cfs_rq->min_vruntime, vruntime);
#ifndef CONFIG_64BIT
"""
new = """	/* ensure we never gain time by being placed backwards. */
	{
		u64 old_min_vruntime = cfs_rq->min_vruntime;

		cfs_rq->min_vruntime = max_vruntime(cfs_rq->min_vruntime, vruntime);
		if ((s64)(cfs_rq->min_vruntime - old_min_vruntime) > 0)
			eevdf_avg_vruntime_update(cfs_rq,
				(s64)(cfs_rq->min_vruntime - old_min_vruntime));
	}
#ifndef CONFIG_64BIT
"""
if old in fc and "old_min_vruntime" not in fc:
    fc = fc.replace(old, new, 1)

old = """	rb_insert_color_cached(&se->run_node,
			       &cfs_rq->tasks_timeline, leftmost);
}

static void __dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
	rb_erase_cached(&se->run_node, &cfs_rq->tasks_timeline);
}
"""
new = """	rb_insert_color_cached(&se->run_node,
			       &cfs_rq->tasks_timeline, leftmost);
	eevdf_avg_vruntime_add(cfs_rq, se);
}

static void __dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
	eevdf_avg_vruntime_sub(cfs_rq, se);
	rb_erase_cached(&se->run_node, &cfs_rq->tasks_timeline);
}
"""
if old in fc and "eevdf_avg_vruntime_add(cfs_rq, se);" not in fc:
    fc = fc.replace(old, new, 1)

fair_c.write_text(fc)

for needle in [
    "avg_vruntime",
    "avg_load",
    "eevdf_avg_vruntime_add",
    "eevdf_avg_vruntime_sub",
    "eevdf_avg_vruntime_update",
    "eevdf_avg_vruntime(",
    "eevdf_entity_eligible(",
]:
    if needle not in (sched_h.read_text() + fair_c.read_text()):
        raise SystemExit(f"missing phase 2 element: {needle}")

print("EEVDF phase 2 weighted virtual-time accounting applied")
print("task_selection=legacy-cfs-walt")
print("eevdf_pick=disabled")
