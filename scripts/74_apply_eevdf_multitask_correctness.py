#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 74_apply_eevdf_multitask_correctness.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

# ---------------------------------------------------------------------------
# 1. Move the runtime gate declaration early enough for update_curr().
# ---------------------------------------------------------------------------
decl = "static bool sched_eevdf_enabled __read_mostly;\n\n"
if fc.count(decl) != 1:
    raise SystemExit(f"expected one sched_eevdf_enabled declaration, found {fc.count(decl)}")
fc = fc.replace(decl, "", 1)

phase3_anchor = """/*
 * EEVDF phase 3 deadline/lag bookkeeping.
 *
"""
if fc.count(phase3_anchor) != 1:
    raise SystemExit("phase3 anchor mismatch")
fc = fc.replace(phase3_anchor, decl + phase3_anchor, 1)

# ---------------------------------------------------------------------------
# 2. Upstream-style deadline renewal semantics.
#    If a request expires, renew the deadline AND tell update_curr() that
#    a reschedule is required. The old port renewed the deadline first and
#    then entity_tick() tested the renewed value, losing the expiry event.
# ---------------------------------------------------------------------------
old_deadline = """static void eevdf_update_deadline(struct cfs_rq *cfs_rq,
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
"""
new_deadline = """static bool eevdf_update_deadline(struct cfs_rq *cfs_rq,
				  struct sched_entity *se)
{
	u64 slice;

	if (se->slice && (s64)(se->vruntime - se->deadline) < 0)
		return false;

	/*
	 * Linux 6.6 uses sched_base_slice. This 4.19 vendor tree does not have
	 * that tunable, so use its minimum CFS granularity as the request size.
	 */
	slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC);
	se->slice = slice;
	se->deadline = se->vruntime + calc_delta_fair(slice, se);
	se->min_deadline = se->deadline;

	(void)cfs_rq;
	return true;
}
"""
if fc.count(old_deadline) != 1:
    raise SystemExit("eevdf_update_deadline anchor mismatch")
fc = fc.replace(old_deadline, new_deadline, 1)

old_update = """	curr->vruntime += calc_delta_fair(delta_exec, curr);
	eevdf_update_deadline(cfs_rq, curr);
	update_min_vruntime(cfs_rq);
"""
new_update = """	curr->vruntime += calc_delta_fair(delta_exec, curr);
	if (eevdf_update_deadline(cfs_rq, curr) &&
	    unlikely(sched_eevdf_enabled) &&
	    cfs_rq->nr_running > 1)
		resched_curr(rq_of(cfs_rq));
	update_min_vruntime(cfs_rq);
"""
if fc.count(old_update) != 1:
    raise SystemExit("update_curr deadline anchor mismatch")
fc = fc.replace(old_update, new_update, 1)

# ---------------------------------------------------------------------------
# 3. Replace the phase-4 single-path tree walk with the v6.6-style
#    eligible/min-deadline search. The previous walk could descend one path
#    and never return to a sibling subtree containing a better eligible
#    deadline.
# ---------------------------------------------------------------------------
old_picker_start = fc.index("static struct sched_entity *eevdf_pick_entity(struct cfs_rq *cfs_rq)")
old_picker_end = fc.index("\n#ifdef CONFIG_SCHED_DEBUG", old_picker_start)
old_picker = fc[old_picker_start:old_picker_end]

new_picker = r'''static struct sched_entity *eevdf_pick_entity(struct cfs_rq *cfs_rq)
{
	struct rb_node *node = cfs_rq->tasks_timeline.rb_root.rb_node;
	struct sched_entity *curr = cfs_rq->curr;
	struct sched_entity *best = NULL;
	struct sched_entity *best_left = NULL;

	if (curr && (!curr->on_rq || !eevdf_entity_eligible(cfs_rq, curr)))
		curr = NULL;
	best = curr;

	while (node) {
		struct sched_entity *se =
			rb_entry(node, struct sched_entity, run_node);

		/*
		 * The rb-tree is ordered by vruntime. If this entity is not
		 * eligible, no entity in its right subtree can be eligible.
		 */
		if (!eevdf_entity_eligible(cfs_rq, se)) {
			node = node->rb_left;
			continue;
		}

		if (!best || (s64)(se->deadline - best->deadline) < 0)
			best = se;

		/*
		 * Every entity in a left branch of an eligible node is
		 * eligible. Remember the branch with the earliest augmented
		 * deadline, matching the upstream v6.6 search strategy.
		 */
		if (node->rb_left) {
			struct sched_entity *left =
				rb_entry(node->rb_left, struct sched_entity, run_node);

			if (!best_left ||
			    (s64)(left->min_deadline - best_left->min_deadline) < 0)
				best_left = left;

			if (left->min_deadline == se->min_deadline)
				break;
		}

		if (se->deadline == se->min_deadline)
			break;

		node = node->rb_right;
	}

	if (!best_left ||
	    (best && (s64)(best_left->min_deadline - best->deadline) > 0))
		goto out;

	node = &best_left->run_node;
	while (node) {
		struct sched_entity *se =
			rb_entry(node, struct sched_entity, run_node);

		if (se->deadline == se->min_deadline) {
			best = se;
			break;
		}

		if (node->rb_left) {
			struct sched_entity *left =
				rb_entry(node->rb_left, struct sched_entity, run_node);

			if (left->min_deadline == se->min_deadline) {
				node = node->rb_left;
				continue;
			}
		}

		node = node->rb_right;
	}

out:
	/*
	 * Do not silently fall back to legacy buddy selection while EEVDF is
	 * enabled. If accounting temporarily yields no eligible entity, the
	 * vruntime-leftmost entity is the safest forward-progress fallback.
	 */
	if (!best)
		best = __pick_first_entity(cfs_rq);

	return best;
}
'''
fc = fc[:old_picker_start] + new_picker + fc[old_picker_end:]

# ---------------------------------------------------------------------------
# 4. Wakeup preemption must be exclusively EEVDF while the gate is enabled.
#    The previous port fell through to the legacy CFS wakeup test whenever
#    EEVDF said "do not preempt".
# ---------------------------------------------------------------------------
old_should = """static bool eevdf_should_preempt(struct cfs_rq *cfs_rq,
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
new_should = """static bool eevdf_should_preempt(struct cfs_rq *cfs_rq,
				 struct sched_entity *curr,
				 struct sched_entity *se)
{
	(void)curr;

	if (!eevdf_entity_eligible(cfs_rq, se))
		return false;

	return eevdf_pick_entity(cfs_rq) == se;
}
"""
if fc.count(old_should) != 1:
    raise SystemExit("eevdf_should_preempt anchor mismatch")
fc = fc.replace(old_should, new_should, 1)

old_wakeup = """	if (unlikely(sched_eevdf_enabled) &&
	    eevdf_should_preempt(cfs_rq_of(se), se, pse))
		goto preempt;

	if (wakeup_preempt_entity(se, pse) == 1) {
"""
new_wakeup = """	if (unlikely(sched_eevdf_enabled)) {
		if (eevdf_should_preempt(cfs_rq_of(se), se, pse))
			goto preempt;
		return;
	}

	if (wakeup_preempt_entity(se, pse) == 1) {
"""
if fc.count(old_wakeup) != 1:
    raise SystemExit("wakeup fallback anchor mismatch")
fc = fc.replace(old_wakeup, new_wakeup, 1)

# ---------------------------------------------------------------------------
# 5. update_curr() now owns EEVDF deadline-expiry rescheduling. Remove the
#    stale post-update test from entity_tick(); legacy CFS keeps its old tick
#    preemption path when the EEVDF gate is off.
# ---------------------------------------------------------------------------
old_tick = """	if (cfs_rq->nr_running > 1) {
		if (unlikely(sched_eevdf_enabled)) {
			if ((s64)(curr->vruntime - curr->deadline) >= 0)
				resched_curr(rq_of(cfs_rq));
		} else {
			check_preempt_tick(cfs_rq, curr);
		}
	}
"""
new_tick = """	if (cfs_rq->nr_running > 1 &&
	    likely(!sched_eevdf_enabled))
		check_preempt_tick(cfs_rq, curr);
"""
if fc.count(old_tick) != 1:
    raise SystemExit("entity_tick EEVDF anchor mismatch")
fc = fc.replace(old_tick, new_tick, 1)

# ---------------------------------------------------------------------------
# 6. Legacy child-runs-first swaps only vruntime. Under EEVDF that leaves
#    vruntime/deadline inconsistent, so keep it strictly on the legacy path.
# ---------------------------------------------------------------------------
old_fork = """	if (sysctl_sched_child_runs_first && curr && entity_before(curr, se)) {
"""
new_fork = """	if (!sched_eevdf_enabled &&
	    sysctl_sched_child_runs_first && curr && entity_before(curr, se)) {
"""
if fc.count(old_fork) != 1:
    raise SystemExit("task_fork child-runs-first anchor mismatch")
fc = fc.replace(old_fork, new_fork, 1)

fair_c.write_text(fc)

text = fair_c.read_text()
checks = [
    "static bool eevdf_update_deadline",
    "cfs_rq->nr_running > 1)\n\t\tresched_curr",
    "struct sched_entity *best_left = NULL",
    "return eevdf_pick_entity(cfs_rq) == se;",
    "if (unlikely(sched_eevdf_enabled)) {\n\t\tif (eevdf_should_preempt",
    "likely(!sched_eevdf_enabled)",
    "if (!sched_eevdf_enabled &&\n\t    sysctl_sched_child_runs_first",
]
for needle in checks:
    if needle not in text:
        raise SystemExit(f"missing multitask correctness element: {needle}")

print("EEVDF multitask correctness fixes applied")
print("compiler_change=none")
print("deadline_expiry_resched=upstream_style")
print("picker_search=v6.6_style")
print("wakeup_preempt=eevdf_exclusive_when_enabled")
print("legacy_tick_preempt=disabled_when_eevdf_enabled")
print("child_runs_first=legacy_only")
