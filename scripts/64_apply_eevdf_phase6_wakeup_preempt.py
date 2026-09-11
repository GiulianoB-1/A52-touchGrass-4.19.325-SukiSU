#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 64_apply_eevdf_phase6_wakeup_preempt.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

if "EEVDF phase 6 wakeup/preemption integration" not in fc:
    anchor = """static int
wakeup_preempt_entity(struct sched_entity *curr, struct sched_entity *se)
{
	s64 gran, vdiff = curr->vruntime - se->vruntime;

	if (vdiff <= 0)
		return -1;

	gran = wakeup_gran(se);
	if (vdiff > gran)
		return 1;

	return 0;
}

"""
    block = """static int
wakeup_preempt_entity(struct sched_entity *curr, struct sched_entity *se)
{
	s64 gran, vdiff = curr->vruntime - se->vruntime;

	if (vdiff <= 0)
		return -1;

	gran = wakeup_gran(se);
	if (vdiff > gran)
		return 1;

	return 0;
}

/* EEVDF phase 6 wakeup/preemption integration. */
static bool eevdf_should_preempt(struct cfs_rq *cfs_rq,
				 struct sched_entity *curr,
				 struct sched_entity *se)
{
	bool curr_eligible = eevdf_entity_eligible(cfs_rq, curr);
	bool se_eligible = eevdf_entity_eligible(cfs_rq, se);

	if (!se_eligible)
		return false;
	if (!curr_eligible)
		return true;

	return (s64)(se->deadline - curr->deadline) < 0;
}

"""
    if fc.count(anchor) != 1:
        raise SystemExit("wakeup_preempt_entity anchor mismatch")
    fc = fc.replace(anchor, block, 1)

tick_old = """	if (cfs_rq->nr_running > 1)
		check_preempt_tick(cfs_rq, curr);
}
"""
tick_new = """	if (cfs_rq->nr_running > 1) {
		if (unlikely(sched_eevdf_enabled)) {
			if ((s64)(curr->vruntime - curr->deadline) >= 0)
				resched_curr(rq_of(cfs_rq));
		} else {
			check_preempt_tick(cfs_rq, curr);
		}
	}
}
"""
if fc.count(tick_old) != 1:
    raise SystemExit("entity_tick anchor mismatch")
fc = fc.replace(tick_old, tick_new, 1)

preempt_old = """#endif

	if (wakeup_preempt_entity(se, pse) == 1) {
"""
preempt_new = """#endif

	if (unlikely(sched_eevdf_enabled) &&
	    eevdf_should_preempt(cfs_rq_of(se), se, pse))
		goto preempt;

	if (wakeup_preempt_entity(se, pse) == 1) {
"""
if fc.count(preempt_old) != 1:
    raise SystemExit("wakeup preempt insertion anchor mismatch")
fc = fc.replace(preempt_old, preempt_new, 1)

fair_c.write_text(fc)

text = fair_c.read_text()
for needle in [
    "EEVDF phase 6 wakeup/preemption integration",
    "eevdf_should_preempt(",
    "sched_eevdf_enabled",
    "curr->vruntime - curr->deadline",
]:
    if needle not in text:
        raise SystemExit(f"missing phase 6 element: {needle}")

print("EEVDF phase 6 wakeup/preemption integration applied")
print("legacy_cfs_walt_default=yes")
print("eevdf_boot_toggle=sched_eevdf=1")
print("eevdf_wakeup_preemption=gated")
print("eevdf_tick_preemption=gated")
