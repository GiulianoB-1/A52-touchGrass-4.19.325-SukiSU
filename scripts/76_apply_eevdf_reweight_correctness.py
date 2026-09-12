#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 76_apply_eevdf_reweight_correctness.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

required = (
    "sched_eevdf_enabled",
    "eevdf_avg_vruntime_add",
    "eevdf_avg_vruntime_sub",
    "eevdf_min_deadline_cb",
    "static bool eevdf_update_deadline",
    "EEVDF phase 7 placement consistency",
    "return eevdf_pick_entity(cfs_rq) == se;",
)
for marker in required:
    if marker not in fc:
        raise SystemExit(f"missing fixed EEVDF prerequisite: {marker}")

old = r'''static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,
			    unsigned long weight, unsigned long runnable)
{
	if (se->on_rq) {
		/* commit outstanding execution time */
		if (cfs_rq->curr == se)
			update_curr(cfs_rq);
		account_entity_dequeue(cfs_rq, se);
		dequeue_runnable_load_avg(cfs_rq, se);
	}
	dequeue_load_avg(cfs_rq, se);

	se->runnable_weight = runnable;
	update_load_set(&se->load, weight);

#ifdef CONFIG_SMP
	do {
		u32 divider = LOAD_AVG_MAX - 1024 + se->avg.period_contrib;

		se->avg.load_avg = div_u64(se_weight(se) * se->avg.load_sum, divider);
		se->avg.runnable_load_avg =
			div_u64(se_runnable(se) * se->avg.runnable_load_sum, divider);
	} while (0);
#endif

	enqueue_load_avg(cfs_rq, se);
	if (se->on_rq) {
		account_entity_enqueue(cfs_rq, se);
		enqueue_runnable_load_avg(cfs_rq, se);
	}
}
'''

new = r'''/*
 * EEVDF Phase 76: preserve lag/deadline and weighted virtual-time accounting
 * across scheduler weight changes. Android frequently reweights task-group
 * entities, so leaving the legacy CFS-only path here corrupts EEVDF state.
 */
static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,
			    unsigned long weight, unsigned long runnable)
{
	unsigned long old_weight = se->load.weight;

	if (se->on_rq) {
		/* commit outstanding execution time */
		if (cfs_rq->curr == se)
			update_curr(cfs_rq);
		else if (unlikely(sched_eevdf_enabled))
			eevdf_avg_vruntime_sub(cfs_rq, se);

		account_entity_dequeue(cfs_rq, se);
		dequeue_runnable_load_avg(cfs_rq, se);
	}
	dequeue_load_avg(cfs_rq, se);

	se->runnable_weight = runnable;
	update_load_set(&se->load, weight);

	if (unlikely(sched_eevdf_enabled)) {
		if (!se->on_rq) {
			/*
			 * vlag is virtual lag (V - v_i). Preserve real lag
			 * across a weight change by scaling virtual lag.
			 */
			if (likely(weight))
				se->vlag = div_s64(se->vlag * old_weight, weight);
		} else {
			s64 deadline = (s64)(se->deadline - se->vruntime);

			/*
			 * The same real request has a different virtual length
			 * after reweighting. Rescale the remaining deadline.
			 */
			if (likely(weight))
				deadline = div_s64(deadline * old_weight, weight);
			se->deadline = se->vruntime + deadline;

			/*
			 * Non-current entities remain in the augmented RB tree,
			 * so refresh min_deadline up to the root immediately.
			 */
			if (cfs_rq->curr != se)
				eevdf_min_deadline_cb.propagate(&se->run_node, NULL);
		}
	}

#ifdef CONFIG_SMP
	do {
		u32 divider = LOAD_AVG_MAX - 1024 + se->avg.period_contrib;

		se->avg.load_avg = div_u64(se_weight(se) * se->avg.load_sum, divider);
		se->avg.runnable_load_avg =
			div_u64(se_runnable(se) * se->avg.runnable_load_sum, divider);
	} while (0);
#endif

	enqueue_load_avg(cfs_rq, se);
	if (se->on_rq) {
		account_entity_enqueue(cfs_rq, se);
		enqueue_runnable_load_avg(cfs_rq, se);

		if (unlikely(sched_eevdf_enabled) && cfs_rq->curr != se)
			eevdf_avg_vruntime_add(cfs_rq, se);
	}
}
'''

if fc.count(old) != 1:
    raise SystemExit(f"reweight_entity anchor mismatch: {fc.count(old)}")

fc = fc.replace(old, new, 1)
fair_c.write_text(fc)

text = fair_c.read_text()
checks = (
    "EEVDF Phase 76",
    "unsigned long old_weight = se->load.weight;",
    "eevdf_avg_vruntime_sub(cfs_rq, se);",
    "se->vlag = div_s64(se->vlag * old_weight, weight);",
    "deadline = div_s64(deadline * old_weight, weight);",
    "eevdf_min_deadline_cb.propagate(&se->run_node, NULL);",
    "eevdf_avg_vruntime_add(cfs_rq, se);",
)
for needle in checks:
    if needle not in text:
        raise SystemExit(f"missing Phase 76 reweight element: {needle}")

print("EEVDF Phase 76 reweight correctness applied")
print("legacy_when_sched_eevdf_0=yes")
print("avg_vruntime_reweight=corrected")
print("vlag_reweight=corrected")
print("deadline_reweight=corrected")
print("min_deadline_reweight=corrected")
