#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 77_apply_eevdf_lifecycle_correctness.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
sched_h = kernel / "kernel/sched/sched.h"
core_c = kernel / "kernel/sched/core.c"
fair_c = kernel / "kernel/sched/fair.c"

sh = sched_h.read_text()
cc = core_c.read_text()
fc = fair_c.read_text()

# ===========================================================================
# 1. Add an explicit first-enqueue flag.
#    Linux EEVDF re-places a forked task on its actual destination rq. The
#    old 4.19 fork path only translated vruntime from the parent's rq.
# ===========================================================================
enqueue_anchor = """#ifdef CONFIG_SMP
#define ENQUEUE_MIGRATED	0x40
#else
#define ENQUEUE_MIGRATED	0x00
#endif

#define RETRY_TASK		((void *)-1UL)
"""
enqueue_repl = """#ifdef CONFIG_SMP
#define ENQUEUE_MIGRATED	0x40
#else
#define ENQUEUE_MIGRATED	0x00
#endif
#define ENQUEUE_INITIAL		0x80

#define RETRY_TASK		((void *)-1UL)
"""
if "ENQUEUE_INITIAL" not in sh:
    if sh.count(enqueue_anchor) != 1:
        raise SystemExit("ENQUEUE_INITIAL anchor mismatch")
    sh = sh.replace(enqueue_anchor, enqueue_repl, 1)

# ===========================================================================
# 2. Initialize every EEVDF sched_entity field centrally, including tasks
#    born in RT/DL classes that can later transition into FAIR.
# ===========================================================================
fork_init_anchor = """	p->se.nr_migrations		= 0;
	p->se.vruntime			= 0;
	p->last_sleep_ts		= 0;
"""
fork_init_repl = """	p->se.nr_migrations		= 0;
	p->se.vruntime			= 0;
	p->se.deadline			= 0;
	p->se.min_deadline		= 0;
	p->se.vlag			= 0;
	p->se.slice			= 0;
	p->last_sleep_ts		= 0;
"""
if cc.count(fork_init_anchor) != 1:
    raise SystemExit("central sched_entity initialization anchor mismatch")
cc = cc.replace(fork_init_anchor, fork_init_repl, 1)

first_enqueue_anchor = "activate_task(rq, p, ENQUEUE_NOCLOCK);"
first_enqueue_repl = "activate_task(rq, p, ENQUEUE_NOCLOCK | ENQUEUE_INITIAL);"
if cc.count(first_enqueue_anchor) != 1:
    raise SystemExit("wake_up_new_task first enqueue anchor mismatch")
cc = cc.replace(first_enqueue_anchor, first_enqueue_repl, 1)

# ===========================================================================
# 3. Runtime diagnostics. Keep hot-path instrumentation per-CPU to avoid a
#    global atomic cacheline in scheduler paths. /proc/eevdf_status aggregates.
# ===========================================================================
if "#include <linux/proc_fs.h>" not in fc:
    inc_anchor = "#include <linux/rbtree_augmented.h>\n"
    if fc.count(inc_anchor) != 1:
        raise SystemExit("rbtree include anchor mismatch")
    fc = fc.replace(
        inc_anchor,
        inc_anchor + "#include <linux/proc_fs.h>\n#include <linux/seq_file.h>\n",
        1,
    )

decl_anchor = "static bool sched_eevdf_enabled __read_mostly;\n\n"
if fc.count(decl_anchor) != 1:
    raise SystemExit("sched_eevdf_enabled declaration mismatch")

# Phase 4 places eevdf_pick_entity() before the Phase 3 helper block where
# Phase 74 moved sched_eevdf_enabled. Runtime counters used by the picker must
# therefore be declared even earlier than the picker itself.
fc = fc.replace(decl_anchor, "", 1)

stats_block = r'''static bool sched_eevdf_enabled __read_mostly;

/*
 * EEVDF lifecycle-correctness instrumentation.
 *
 * These counters are intentionally observational. They must never affect
 * scheduling decisions. Per-CPU storage avoids a shared hot cacheline.
 */
struct eevdf_runtime_stats {
	u64 initial_place;
	u64 wake_place;
	u64 restore_place;
	u64 plain_place;
	u64 lag_clamp;
	u64 reweight_onrq;
	u64 reweight_offrq;
	u64 picker_fallback;
	u64 run_to_parity;
	u64 wakeup_preempt;
	u64 yield;
	u64 deadline_renew;
	u64 walt_latency_policy_seen;
	u64 max_abs_vlag;
};

static DEFINE_PER_CPU(struct eevdf_runtime_stats, eevdf_runtime_stats);

#define EEVDF_STAT_INC(_field)						\
	do {								\
		this_cpu_ptr(&eevdf_runtime_stats)->_field++;		\
	} while (0)

static inline void eevdf_stat_max_vlag(s64 vlag)
{
	struct eevdf_runtime_stats *st = this_cpu_ptr(&eevdf_runtime_stats);
	u64 abs_vlag = vlag < 0 ? (u64)(-vlag) : (u64)vlag;

	if (abs_vlag > st->max_abs_vlag)
		st->max_abs_vlag = abs_vlag;
}

static int eevdf_status_show(struct seq_file *m, void *v)
{
	struct eevdf_runtime_stats total = { };
	int cpu;

	for_each_possible_cpu(cpu) {
		struct eevdf_runtime_stats *st = &per_cpu(eevdf_runtime_stats, cpu);

		total.initial_place += st->initial_place;
		total.wake_place += st->wake_place;
		total.restore_place += st->restore_place;
		total.plain_place += st->plain_place;
		total.lag_clamp += st->lag_clamp;
		total.reweight_onrq += st->reweight_onrq;
		total.reweight_offrq += st->reweight_offrq;
		total.picker_fallback += st->picker_fallback;
		total.run_to_parity += st->run_to_parity;
		total.wakeup_preempt += st->wakeup_preempt;
		total.yield += st->yield;
		total.deadline_renew += st->deadline_renew;
		total.walt_latency_policy_seen += st->walt_latency_policy_seen;
		if (st->max_abs_vlag > total.max_abs_vlag)
			total.max_abs_vlag = st->max_abs_vlag;
	}

	seq_printf(m, "enabled=%u\n", sched_eevdf_enabled ? 1 : 0);
	seq_printf(m, "base_slice_ns=%u\n", sysctl_sched_min_granularity);
	seq_printf(m, "initial_place=%llu\n", total.initial_place);
	seq_printf(m, "wake_place=%llu\n", total.wake_place);
	seq_printf(m, "restore_place=%llu\n", total.restore_place);
	seq_printf(m, "plain_place=%llu\n", total.plain_place);
	seq_printf(m, "lag_clamp=%llu\n", total.lag_clamp);
	seq_printf(m, "reweight_onrq=%llu\n", total.reweight_onrq);
	seq_printf(m, "reweight_offrq=%llu\n", total.reweight_offrq);
	seq_printf(m, "picker_fallback=%llu\n", total.picker_fallback);
	seq_printf(m, "run_to_parity=%llu\n", total.run_to_parity);
	seq_printf(m, "wakeup_preempt=%llu\n", total.wakeup_preempt);
	seq_printf(m, "yield=%llu\n", total.yield);
	seq_printf(m, "deadline_renew=%llu\n", total.deadline_renew);
	seq_printf(m, "walt_latency_policy_seen=%llu\n",
		   total.walt_latency_policy_seen);
	seq_printf(m, "max_abs_vlag_ns=%llu\n", total.max_abs_vlag);

	return 0;
}

static int __init eevdf_status_init(void)
{
	if (!proc_create_single("eevdf_status", 0444, NULL, eevdf_status_show))
		return -ENOMEM;
	return 0;
}
late_initcall(eevdf_status_init);

'''
picker_stats_anchor = "static struct sched_entity *eevdf_pick_entity(struct cfs_rq *cfs_rq)"
if fc.count(picker_stats_anchor) != 1:
    raise SystemExit("picker stats insertion anchor mismatch")
fc = fc.replace(picker_stats_anchor, stats_block + picker_stats_anchor, 1)

# ===========================================================================
# 4. Correct request-size semantics.
#    TICK_NSEC belongs in the lag bound, not in the EEVDF request itself.
# ===========================================================================
phase3_anchor = """/*
 * EEVDF phase 3 deadline/lag bookkeeping.
 *
"""
if fc.count(phase3_anchor) != 1:
    raise SystemExit("phase3 lifecycle anchor mismatch")
fc = fc.replace(
    phase3_anchor,
    """static inline u64 eevdf_base_slice(void)
{
	return max_t(u64, (u64)sysctl_sched_min_granularity, 1ULL);
}

static void clear_buddies(struct cfs_rq *cfs_rq, struct sched_entity *se);

""" + phase3_anchor,
    1,
)

old_deadline = """	slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC);
	se->slice = slice;
	se->deadline = se->vruntime + calc_delta_fair(slice, se);
	se->min_deadline = se->deadline;

	(void)cfs_rq;
	return true;
}
"""
new_deadline = """	slice = eevdf_base_slice();
	se->slice = slice;
	se->deadline = se->vruntime + calc_delta_fair(slice, se);
	se->min_deadline = se->deadline;

	if (unlikely(sched_eevdf_enabled)) {
		EEVDF_STAT_INC(deadline_renew);
		clear_buddies(cfs_rq, se);
	}

	return true;
}
"""
if fc.count(old_deadline) != 1:
    raise SystemExit("deadline renewal slice anchor mismatch")
fc = fc.replace(old_deadline, new_deadline, 1)

old_lag = """	if (!slice)
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
new_lag = """	if (!slice)
		slice = eevdf_base_slice();

	lag = (s64)(eevdf_avg_vruntime(cfs_rq) - se->vruntime);
	limit = (s64)calc_delta_fair(max_t(u64, 2 * slice, TICK_NSEC), se);

	if (lag > limit) {
		lag = limit;
		if (unlikely(sched_eevdf_enabled))
			EEVDF_STAT_INC(lag_clamp);
	} else if (lag < -limit) {
		lag = -limit;
		if (unlikely(sched_eevdf_enabled))
			EEVDF_STAT_INC(lag_clamp);
	}

	se->vlag = lag;
	if (unlikely(sched_eevdf_enabled))
		eevdf_stat_max_vlag(lag);
}
"""
if fc.count(old_lag) != 1:
    raise SystemExit("lag bound anchor mismatch")
fc = fc.replace(old_lag, new_lag, 1)

old_place_slice = "u64 slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC);"
if fc.count(old_place_slice) != 1:
    raise SystemExit("phase7 placement slice anchor mismatch")
fc = fc.replace(old_place_slice, "u64 slice = eevdf_base_slice();", 1)

# Record when Samsung's old latency-special placement would have fired, but do
# not translate that policy until lifecycle invariants are proven on hardware.
place_deadline_anchor = """	se->deadline = se->vruntime + vslice;
	se->min_deadline = se->deadline;
}
"""
place_deadline_repl = """	se->deadline = se->vruntime + vslice;
	se->min_deadline = se->deadline;

#ifdef CONFIG_SCHED_WALT
	if (unlikely(sched_eevdf_enabled) && entity_is_task(se)) {
		struct task_struct *p = task_of(se);

		if (per_task_boost(p) == TASK_BOOST_STRICT_MAX ||
		    walt_low_latency_task(p) || task_rtg_high_prio(p))
			EEVDF_STAT_INC(walt_latency_policy_seen);
	}
#endif
}
"""
if fc.count(place_deadline_anchor) != 1:
    raise SystemExit("phase7 placement deadline anchor mismatch")
fc = fc.replace(place_deadline_anchor, place_deadline_repl, 1)

# ===========================================================================
# 5. EEVDF enqueue/dequeue semantics.
#    When EEVDF is active, never translate vruntime through min_vruntime.
#    Every real enqueue gets destination-rq placement from saved vlag.
# ===========================================================================
renorm_anchor = "bool renorm = !(flags & ENQUEUE_WAKEUP) || (flags & ENQUEUE_MIGRATED);"
renorm_repl = """bool renorm = likely(!sched_eevdf_enabled) &&
		(!(flags & ENQUEUE_WAKEUP) || (flags & ENQUEUE_MIGRATED));"""
if fc.count(renorm_anchor) != 1:
    raise SystemExit("enqueue renorm anchor mismatch")
fc = fc.replace(renorm_anchor, renorm_repl, 1)

enqueue_place_anchor = """	if (flags & ENQUEUE_WAKEUP)
		place_entity(cfs_rq, se, 0);
"""
enqueue_place_repl = """	if (unlikely(sched_eevdf_enabled)) {
		if (flags & ENQUEUE_INITIAL)
			EEVDF_STAT_INC(initial_place);
		else if (flags & ENQUEUE_WAKEUP)
			EEVDF_STAT_INC(wake_place);
		else if (flags & (ENQUEUE_RESTORE | ENQUEUE_MOVE))
			EEVDF_STAT_INC(restore_place);
		else
			EEVDF_STAT_INC(plain_place);

		place_entity(cfs_rq, se, !!(flags & ENQUEUE_INITIAL));
	} else if (flags & ENQUEUE_WAKEUP) {
		place_entity(cfs_rq, se, 0);
	}
"""
if fc.count(enqueue_place_anchor) != 1:
    raise SystemExit("enqueue placement anchor mismatch")
fc = fc.replace(enqueue_place_anchor, enqueue_place_repl, 1)

dequeue_norm_anchor = """	if (!(flags & DEQUEUE_SLEEP))
		se->vruntime -= cfs_rq->min_vruntime;
"""
dequeue_norm_repl = """	if (likely(!sched_eevdf_enabled) && !(flags & DEQUEUE_SLEEP))
		se->vruntime -= cfs_rq->min_vruntime;
"""
if fc.count(dequeue_norm_anchor) != 1:
    raise SystemExit("dequeue vruntime normalization anchor mismatch")
fc = fc.replace(dequeue_norm_anchor, dequeue_norm_repl, 1)

# Remote waking migration also has a legacy source-rq min_vruntime transform.
# Linux stable updates changed details inside this function, so scope the edit
# to migrate_task_rq_fair() instead of matching its entire body.
mig_start = fc.index("static void migrate_task_rq_fair(")
mig_next = fc.index("\nstatic ", mig_start + 1)
if mig_next < 0:
    raise SystemExit("could not bound migrate_task_rq_fair")
mig_chunk = fc[mig_start:mig_next]
mig_if = "if (p->state == TASK_WAKING) {"
if mig_chunk.count(mig_if) != 1:
    raise SystemExit(
        f"migrate_task_rq_fair TASK_WAKING anchor mismatch: {mig_chunk.count(mig_if)}"
    )
mig_chunk = mig_chunk.replace(
    mig_if,
    "if (p->state == TASK_WAKING && likely(!sched_eevdf_enabled)) {",
    1,
)
fc = fc[:mig_start] + mig_chunk + fc[mig_next:]

# ===========================================================================
# 6. Fork lifecycle.
#    Under EEVDF, do not create authoritative source-rq coordinates. The
#    ENQUEUE_INITIAL destination enqueue owns first placement.
# ===========================================================================
fork_anchor = """	if (unlikely(sched_eevdf_enabled))
		se->vlag = 0;
	place_entity(cfs_rq, se, 1);

	if (!sched_eevdf_enabled &&
	    sysctl_sched_child_runs_first && curr && entity_before(curr, se)) {
		/*
		 * Upon rescheduling, sched_class::put_prev_task() will place
		 * 'current' within the tree based on its new key value.
		 */
		swap(curr->vruntime, se->vruntime);
		resched_curr(rq);
	}

	se->vruntime -= cfs_rq->min_vruntime;
"""
fork_repl = """	if (unlikely(sched_eevdf_enabled)) {
		se->vlag = 0;
		se->slice = eevdf_base_slice();
		se->deadline = 0;
		se->min_deadline = 0;
	} else {
		place_entity(cfs_rq, se, 1);

		if (sysctl_sched_child_runs_first && curr &&
		    entity_before(curr, se)) {
			/*
			 * Upon rescheduling, sched_class::put_prev_task() will
			 * place current within the tree based on its new key.
			 */
			swap(curr->vruntime, se->vruntime);
			resched_curr(rq);
		}

		se->vruntime -= cfs_rq->min_vruntime;
	}
"""
if fc.count(fork_anchor) != 1:
    raise SystemExit("task_fork_fair lifecycle anchor mismatch")
fc = fc.replace(fork_anchor, fork_repl, 1)

# ===========================================================================
# 7. Cgroup/class moves.
#    The old helper placed on the OLD cfs_rq and translated only vruntime.
#    EEVDF instead keeps saved vlag and lets the later destination enqueue
#    perform one authoritative placement.
# ===========================================================================
detach_anchor = """	if (!vruntime_normalized(p)) {
		/*
		 * Fix up our vruntime so that the current sleep doesn't
		 * cause 'unlimited' sleep bonus.
		 */
		place_entity(cfs_rq, se, 0);
		se->vruntime -= cfs_rq->min_vruntime;
	}
"""
detach_repl = """	if (likely(!sched_eevdf_enabled) && !vruntime_normalized(p)) {
		/*
		 * Legacy CFS normalization. EEVDF keeps saved vlag instead
		 * and is re-placed only after entering the destination cfs_rq.
		 */
		place_entity(cfs_rq, se, 0);
		se->vruntime -= cfs_rq->min_vruntime;
	}
"""
if fc.count(detach_anchor) != 1:
    raise SystemExit("detach_task_cfs_rq anchor mismatch")
fc = fc.replace(detach_anchor, detach_repl, 1)

attach_anchor = """	if (!vruntime_normalized(p))
		se->vruntime += cfs_rq->min_vruntime;
"""
attach_repl = """	if (likely(!sched_eevdf_enabled) && !vruntime_normalized(p))
		se->vruntime += cfs_rq->min_vruntime;
"""
if fc.count(attach_anchor) != 1:
    raise SystemExit("attach_task_cfs_rq anchor mismatch")
fc = fc.replace(attach_anchor, attach_repl, 1)

# ===========================================================================
# 8. Correct EEVDF reweighting.
#    Preserve real lag and virtual deadline around the original weighted
#    average V. Non-current entities are removed/reinserted so both the
#    vruntime tree and augmented min_deadline heap remain coherent.
# ===========================================================================
rw_start = fc.index("static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,")
rw_end = fc.index("\nvoid reweight_task(", rw_start)
old_rw = fc[rw_start:rw_end]
new_rw = r'''static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,
			    unsigned long weight, unsigned long runnable)
{
	bool eevdf = unlikely(sched_eevdf_enabled);
	bool curr = cfs_rq->curr == se;
	unsigned long old_weight = se->load.weight;
	u64 avruntime = 0;
	s64 vlag = 0;
	s64 vdeadline = 0;

	if (se->on_rq) {
		/* Commit current execution and make V current before reweight. */
		if (eevdf)
			update_curr(cfs_rq);
		else if (curr)
			update_curr(cfs_rq);

		if (eevdf) {
			avruntime = eevdf_avg_vruntime(cfs_rq);
			eevdf_update_entity_lag(cfs_rq, se);
			vlag = se->vlag;
			vdeadline = (s64)(se->deadline - avruntime);

			/*
			 * vruntime and deadline both change below. A non-current
			 * entity must leave the rb-tree so its key, avg_vruntime
			 * contribution and min_deadline augmentation can be rebuilt.
			 */
			if (!curr)
				__dequeue_entity(cfs_rq, se);
		}

		account_entity_dequeue(cfs_rq, se);
		dequeue_runnable_load_avg(cfs_rq, se);
	}
	dequeue_load_avg(cfs_rq, se);

	if (eevdf && old_weight != weight) {
		if (se->on_rq) {
			vlag = div_s64(vlag * (s64)old_weight, weight);
			vdeadline = div_s64(vdeadline * (s64)old_weight, weight);
		} else {
			/*
			 * Off-rq vlag is the portable state that will be used by
			 * destination placement on the next enqueue.
			 */
			se->vlag = div_s64(se->vlag * (s64)old_weight, weight);
			eevdf_stat_max_vlag(se->vlag);
		}
	}

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
		if (eevdf) {
			se->vlag = vlag;
			se->vruntime = avruntime - vlag;
			se->deadline = avruntime + vdeadline;
			se->min_deadline = se->deadline;
			eevdf_stat_max_vlag(vlag);
		}

		account_entity_enqueue(cfs_rq, se);
		enqueue_runnable_load_avg(cfs_rq, se);

		if (eevdf) {
			if (!curr)
				__enqueue_entity(cfs_rq, se);
			update_min_vruntime(cfs_rq);
			EEVDF_STAT_INC(reweight_onrq);
		}
	} else if (eevdf) {
		EEVDF_STAT_INC(reweight_offrq);
	}
}
'''
fc = fc[:rw_start] + new_rw + fc[rw_end:]

# ===========================================================================
# 9. RUN_TO_PARITY continuity.
#    vlag is free while current runs; like Linux 6.6, stash the selected
#    request deadline there and overwrite it with real lag on dequeue.
# ===========================================================================
picker_anchor = """	if (curr && (!curr->on_rq || !eevdf_entity_eligible(cfs_rq, curr)))
		curr = NULL;
	best = curr;

	while (node) {
"""
picker_repl = """	if (curr && (!curr->on_rq || !eevdf_entity_eligible(cfs_rq, curr)))
		curr = NULL;
	best = curr;

	if (curr && curr->vlag == curr->deadline) {
		EEVDF_STAT_INC(run_to_parity);
		return curr;
	}

	while (node) {
"""
if fc.count(picker_anchor) != 1:
    raise SystemExit("RUN_TO_PARITY picker anchor mismatch")
fc = fc.replace(picker_anchor, picker_repl, 1)

fallback_anchor = """	if (!best)
		best = __pick_first_entity(cfs_rq);

	return best;
}
"""
fallback_repl = """	if (!best) {
		best = __pick_first_entity(cfs_rq);
		if (best)
			EEVDF_STAT_INC(picker_fallback);
	}

	return best;
}
"""
if fc.count(fallback_anchor) != 1:
    raise SystemExit("picker fallback anchor mismatch")
fc = fc.replace(fallback_anchor, fallback_repl, 1)

set_next_anchor = """	update_stats_curr_start(cfs_rq, se);
	cfs_rq->curr = se;

	/*
	 * Track our maximum slice length, if the CPU's load is at
"""
set_next_repl = """	update_stats_curr_start(cfs_rq, se);
	cfs_rq->curr = se;

	if (unlikely(sched_eevdf_enabled))
		se->vlag = se->deadline;

	/*
	 * Track our maximum slice length, if the CPU's load is at
"""
if fc.count(set_next_anchor) != 1:
    raise SystemExit("set_next_entity parity marker anchor mismatch")
fc = fc.replace(set_next_anchor, set_next_repl, 1)

# Count actual EEVDF wakeup preemptions.
wakeup_anchor = """	if (unlikely(sched_eevdf_enabled)) {
		if (eevdf_should_preempt(cfs_rq_of(se), se, pse))
			goto preempt;
		return;
	}
"""
wakeup_repl = """	if (unlikely(sched_eevdf_enabled)) {
		if (eevdf_should_preempt(cfs_rq_of(se), se, pse)) {
			EEVDF_STAT_INC(wakeup_preempt);
			goto preempt;
		}
		return;
	}
"""
if fc.count(wakeup_anchor) != 1:
    raise SystemExit("wakeup preemption counter anchor mismatch")
fc = fc.replace(wakeup_anchor, wakeup_repl, 1)

# ===========================================================================
# 10. EEVDF-aware sched_yield().
#     Legacy skip-buddy is not authoritative in the EEVDF picker. Advance the
#     current virtual deadline by one request so another eligible entity wins.
# ===========================================================================
yield_anchor = """	set_skip_buddy(se);
}

static bool yield_to_task_fair"""
yield_repl = """	if (unlikely(sched_eevdf_enabled)) {
		u64 slice = se->slice ? se->slice : eevdf_base_slice();

		se->deadline += calc_delta_fair(slice, se);
		se->min_deadline = se->deadline;
		EEVDF_STAT_INC(yield);
	}

	set_skip_buddy(se);
}

static bool yield_to_task_fair"""
if fc.count(yield_anchor) != 1:
    raise SystemExit("yield_task_fair anchor mismatch")
fc = fc.replace(yield_anchor, yield_repl, 1)

sched_h.write_text(sh)
core_c.write_text(cc)
fair_c.write_text(fc)

# ===========================================================================
# Static invariants for CI.
# ===========================================================================
all_text = sh + "\n" + cc + "\n" + fc
checks = [
    "#define ENQUEUE_INITIAL",
    "ENQUEUE_NOCLOCK | ENQUEUE_INITIAL",
    "p->se.deadline",
    "proc_create_single(\"eevdf_status\"",
    "u64 slice = eevdf_base_slice();",
    "likely(!sched_eevdf_enabled) && !(flags & DEQUEUE_SLEEP)",
    "bool renorm = likely(!sched_eevdf_enabled)",
    "flags & ENQUEUE_INITIAL",
    "p->state == TASK_WAKING && likely(!sched_eevdf_enabled)",
    "se->vlag = div_s64(se->vlag * (s64)old_weight, weight)",
    "__dequeue_entity(cfs_rq, se);",
    "__enqueue_entity(cfs_rq, se);",
    "curr->vlag == curr->deadline",
    "se->vlag = se->deadline;",
    "EEVDF_STAT_INC(picker_fallback)",
    "EEVDF_STAT_INC(walt_latency_policy_seen)",
]
for needle in checks:
    if needle not in all_text:
        raise SystemExit(f"missing lifecycle correctness element: {needle}")

# The request itself must no longer be clamped to TICK_NSEC.
if "slice = max_t(u64, (u64)sysctl_sched_min_granularity, TICK_NSEC)" in fc:
    raise SystemExit("EEVDF request is still incorrectly clamped to TICK_NSEC")

print("EEVDF lifecycle correctness applied")
print("legacy_cfs_walt_path=preserved_when_sched_eevdf_0")
print("cross_rq_min_vruntime_translation=disabled_when_eevdf")
print("destination_placement=all_eevdf_enqueues")
print("fork_initial_enqueue=explicit")
print("reweight=lag_deadline_tree_corrected")
print("run_to_parity=enabled")
print("yield=eevdf_aware")
print("base_slice=min_granularity_not_tick")
print("walt_latency_policy=instrumented_not_yet_modified")
print("runtime_status=/proc/eevdf_status")
