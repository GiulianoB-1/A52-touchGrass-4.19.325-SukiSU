#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 65_apply_eevdf_phase7_placement_consistency.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

# Move the runtime gate declaration before place_entity(), because phase 7
# placement needs to branch on it.
late_decl = "static bool sched_eevdf_enabled __read_mostly;\n\n"
if fc.count(late_decl) == 1:
    fc = fc.replace(late_decl, "", 1)

place_anchor = """static void
place_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int initial)
{
	u64 vruntime = cfs_rq->min_vruntime;
"""

place_block = """static bool sched_eevdf_enabled __read_mostly;

/* EEVDF phase 7 placement consistency. */
static void eevdf_place_entity(struct cfs_rq *cfs_rq,
			       struct sched_entity *se, int initial)
{
	u64 slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC);
	u64 vslice = calc_delta_fair(slice, se);
	u64 vruntime = eevdf_avg_vruntime(cfs_rq);
	s64 lag = se->vlag;

	/*
	 * Preserve stored virtual lag across sleep/migration. Adding an entity
	 * changes V itself, so compensate using the same weighted-average
	 * relationship used by upstream EEVDF placement.
	 */
	if (cfs_rq->nr_running && lag) {
		struct sched_entity *curr = cfs_rq->curr;
		unsigned long load = cfs_rq->avg_load;
		unsigned long weight = scale_load_down(se->load.weight);

		if (curr && curr->on_rq)
			load += scale_load_down(curr->load.weight);

		if (load) {
			lag *= load + weight;
			lag = div_s64(lag, load);
		}
	}

	se->slice = slice;
	se->vruntime = vruntime - lag;

	/*
	 * New tasks enter with half a request so they do not receive a full
	 * fresh-service advantage over tasks already competing on the rq.
	 */
	if (initial)
		vslice >>= 1;

	se->deadline = se->vruntime + vslice;
	se->min_deadline = se->deadline;
}

static void
place_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int initial)
{
	if (unlikely(sched_eevdf_enabled)) {
		eevdf_place_entity(cfs_rq, se, initial);
		return;
	}

	u64 vruntime = cfs_rq->min_vruntime;
"""

if "EEVDF phase 7 placement consistency" not in fc:
    if fc.count(place_anchor) != 1:
        raise SystemExit("place_entity anchor mismatch")
    fc = fc.replace(place_anchor, place_block, 1)

# Ensure forked children do not inherit stale lag from the parent's copied
# sched_entity state before EEVDF initial placement.
fork_anchor = """	cfs_rq = task_cfs_rq(current);
	curr = cfs_rq->curr;
	if (curr) {
		update_curr(cfs_rq);
		se->vruntime = curr->vruntime;
	}
	place_entity(cfs_rq, se, 1);
"""
fork_repl = """	cfs_rq = task_cfs_rq(current);
	curr = cfs_rq->curr;
	if (curr) {
		update_curr(cfs_rq);
		se->vruntime = curr->vruntime;
	}
	if (unlikely(sched_eevdf_enabled))
		se->vlag = 0;
	place_entity(cfs_rq, se, 1);
"""
if fc.count(fork_anchor) != 1:
    raise SystemExit("task_fork_fair anchor mismatch")
fc = fc.replace(fork_anchor, fork_repl, 1)

fair_c.write_text(fc)

text = fair_c.read_text()
for needle in [
    "EEVDF phase 7 placement consistency",
    "eevdf_place_entity(",
    "lag *= load + weight",
    "se->deadline = se->vruntime + vslice",
    "se->vlag = 0",
]:
    if needle not in text:
        raise SystemExit(f"missing phase 7 element: {needle}")

print("EEVDF phase 7 placement consistency applied")
print("legacy_cfs_walt_default=yes")
print("eevdf_boot_toggle=sched_eevdf=1")
print("lag_preserving_placement=gated")
print("fork_initial_deadline=gated")
