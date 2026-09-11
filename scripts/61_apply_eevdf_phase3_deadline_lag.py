#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 61_apply_eevdf_phase3_deadline_lag.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

if "EEVDF phase 3 deadline/lag bookkeeping" not in fc:
    anchor = """static inline u64 calc_delta_fair(u64 delta, struct sched_entity *se)
{
	if (unlikely(se->load.weight != NICE_0_LOAD))
		delta = __calc_delta(delta, NICE_0_LOAD, &se->load);

	return delta;
}

"""
    block = """static inline u64 calc_delta_fair(u64 delta, struct sched_entity *se)
{
	if (unlikely(se->load.weight != NICE_0_LOAD))
		delta = __calc_delta(delta, NICE_0_LOAD, &se->load);

	return delta;
}

/*
 * EEVDF phase 3 deadline/lag bookkeeping.
 *
 * We intentionally keep legacy CFS/WALT task selection active. These
 * helpers only maintain request slice, virtual deadline and stored lag
 * state so the next phase can introduce EEVDF tree selection safely.
 */
static void eevdf_update_deadline(struct cfs_rq *cfs_rq,
				  struct sched_entity *se)
{
	u64 slice;

	if (se->slice && (s64)(se->vruntime - se->deadline) < 0)
		return;

	/*
	 * Linux 6.6 uses sched_base_slice. This 4.19 vendor tree does not have
	 * that tunable, so use its minimum CFS granularity as the request size
	 * until we add a dedicated EEVDF base-slice tunable in a later phase.
	 */
	slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC);
	se->slice = slice;
	se->deadline = se->vruntime + calc_delta_fair(slice, se);
	se->min_deadline = se->deadline;

	(void)cfs_rq;
}

static void eevdf_update_entity_lag(struct cfs_rq *cfs_rq,
				    struct sched_entity *se)
{
	s64 lag, limit;
	u64 slice = se->slice;

	if (!slice)
		slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC);

	lag = (s64)(eevdf_avg_vruntime(cfs_rq) - se->vruntime);
	limit = (s64)calc_delta_fair(max_t(u64, 2 * slice, TICK_NSEC), se);

	if (lag > limit)
		lag = limit;
	else if (lag < -limit)
		lag = -limit;

	se->vlag = lag;
}

"""
    if fc.count(anchor) != 1:
        raise SystemExit("calc_delta_fair anchor mismatch")
    fc = fc.replace(anchor, block, 1)

old = """	curr->vruntime += calc_delta_fair(delta_exec, curr);
	update_min_vruntime(cfs_rq);
"""
new = """	curr->vruntime += calc_delta_fair(delta_exec, curr);
	eevdf_update_deadline(cfs_rq, curr);
	update_min_vruntime(cfs_rq);
"""
if old not in fc:
    raise SystemExit("update_curr anchor mismatch")
fc = fc.replace(old, new, 1)

old = """	clear_buddies(cfs_rq, se);

	if (se != cfs_rq->curr)
		__dequeue_entity(cfs_rq, se);
"""
new = """	clear_buddies(cfs_rq, se);

	/*
	 * Capture lag while the entity is still logically runnable and while
	 * phase 2 weighted virtual-time accounting still includes it.
	 */
	eevdf_update_entity_lag(cfs_rq, se);

	if (se != cfs_rq->curr)
		__dequeue_entity(cfs_rq, se);
"""
if old not in fc:
    raise SystemExit("dequeue_entity anchor mismatch")
fc = fc.replace(old, new, 1)

fair_c.write_text(fc)

text = fair_c.read_text()
for needle in [
    "EEVDF phase 3 deadline/lag bookkeeping",
    "eevdf_update_deadline(",
    "eevdf_update_entity_lag(",
    "se->deadline = se->vruntime + calc_delta_fair",
    "se->vlag = lag",
]:
    if needle not in text:
        raise SystemExit(f"missing phase 3 element: {needle}")

print("EEVDF phase 3 deadline and lag bookkeeping applied")
print("task_selection=legacy-cfs-walt")
print("deadline_state=active")
print("lag_capture=active")
print("eevdf_pick=disabled")
