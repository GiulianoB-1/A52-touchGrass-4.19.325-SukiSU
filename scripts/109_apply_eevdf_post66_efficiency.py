#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 109_apply_eevdf_post66_efficiency.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
sched_h = root / "include/linux/sched.h"
core_c = root / "kernel/sched/core.c"
fair_c = root / "kernel/sched/fair.c"

for p in (sched_h, core_c, fair_c):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

sh = sched_h.read_text()
cc = core_c.read_text()
fc = fair_c.read_text()

marker = "A52 EEVDF post-6.6 protection correctness"
if marker in fc:
    print("EEVDF post-6.6 protection correctness already applied")
    raise SystemExit(0)

# This patch is intentionally narrow. The A52 port currently uses one common
# request slice (eevdf_base_slice()), so the later Linux 6.17 fixes whose only
# effect is handling *different* custom slice lengths do not change behaviour
# here. What does apply is the later explicit slice-protection model and the
# rule that expiry of the protected period must cause a new scheduling choice.
required = (
    "EEVDF lifecycle-correctness instrumentation.",
    "static bool eevdf_update_deadline",
    "curr->vlag == curr->deadline",
    "se->vlag = se->deadline;",
    "return eevdf_pick_entity(cfs_rq) == se;",
)
for needle in required:
    if needle not in fc:
        raise SystemExit(f"missing expected Phase77 EEVDF element: {needle!r}")

# ---------------------------------------------------------------------------
# 1. Give slice protection its own state instead of overloading vlag.
#    Modern EEVDF moved away from the old Linux 6.6 vlag==deadline sentinel.
# ---------------------------------------------------------------------------
field_anchor = "\ts64\t\t\t\tvlag;\n\tu64\t\t\t\tslice;\n"
field_repl = "\ts64\t\t\t\tvlag;\n\tu64\t\t\t\tslice;\n\tu64\t\t\t\tvprot;\n"
if sh.count(field_anchor) != 1:
    raise SystemExit(f"sched_entity vlag/slice anchor mismatch: {sh.count(field_anchor)}")
sh = sh.replace(field_anchor, field_repl, 1)

init_anchor = "\tp->se.vlag\t\t\t= 0;\n\tp->se.slice\t\t\t= 0;\n"
init_repl = "\tp->se.vlag\t\t\t= 0;\n\tp->se.slice\t\t\t= 0;\n\tp->se.vprot\t\t\t= 0;\n"
if cc.count(init_anchor) != 1:
    raise SystemExit(f"sched_entity init anchor mismatch: {cc.count(init_anchor)}")
cc = cc.replace(init_anchor, init_repl, 1)

picker_decl = "static struct sched_entity *eevdf_pick_entity(struct cfs_rq *cfs_rq)"
if fc.count(picker_decl) != 1:
    raise SystemExit("eevdf picker declaration mismatch")

helpers = r'''/*
 * A52 EEVDF post-6.6 protection correctness.
 *
 * Linux 6.6 stashed the selected deadline in vlag while the entity ran.
 * Later EEVDF gained an explicit protected-until virtual runtime. Keep the
 * same one-slice behaviour used by this A52 port, but stop aliasing real lag
 * state with run-to-parity state.
 */
static inline void eevdf_set_protect_slice(struct sched_entity *se)
{
	se->vprot = se->deadline;
}

static inline bool eevdf_protect_slice(struct sched_entity *se)
{
	return (s64)(se->vprot - se->vruntime) > 0;
}

static inline void eevdf_cancel_protect_slice(struct sched_entity *se)
{
	if (eevdf_protect_slice(se))
		se->vprot = se->vruntime;
}

'''
fc = fc.replace(picker_decl, helpers + picker_decl, 1)

old_picker = """	if (curr && curr->vlag == curr->deadline) {
		EEVDF_STAT_INC(run_to_parity);
		return curr;
	}
"""
new_picker = """	if (curr && eevdf_protect_slice(curr)) {
		EEVDF_STAT_INC(run_to_parity);
		return curr;
	}
"""
if fc.count(old_picker) != 1:
    raise SystemExit("old run-to-parity picker sentinel not found exactly once")
fc = fc.replace(old_picker, new_picker, 1)

old_set_next = """	if (unlikely(sched_eevdf_enabled))
		se->vlag = se->deadline;
"""
new_set_next = """	if (unlikely(sched_eevdf_enabled))
		eevdf_set_protect_slice(se);
"""
if fc.count(old_set_next) != 1:
    raise SystemExit("set_next_entity protection anchor mismatch")
fc = fc.replace(old_set_next, new_set_next, 1)

# ---------------------------------------------------------------------------
# 2. Mirror the later EEVDF rule: when the request expires OR the protected
#    period has ended, force a new scheduling choice. Phase74 already made
#    deadline renewal return a boolean; use it together with vprot.
# ---------------------------------------------------------------------------
old_update = """	curr->vruntime += calc_delta_fair(delta_exec, curr);
	if (eevdf_update_deadline(cfs_rq, curr) &&
	    unlikely(sched_eevdf_enabled) &&
	    cfs_rq->nr_running > 1)
		resched_curr(rq_of(cfs_rq));
	update_min_vruntime(cfs_rq);
"""
new_update = """	curr->vruntime += calc_delta_fair(delta_exec, curr);
	{
		bool expired = eevdf_update_deadline(cfs_rq, curr);

		if (unlikely(sched_eevdf_enabled) &&
		    cfs_rq->nr_running > 1 &&
		    (expired || !eevdf_protect_slice(curr))) {
			resched_curr(rq_of(cfs_rq));
			clear_buddies(cfs_rq, curr);
		}
	}
	update_min_vruntime(cfs_rq);
"""
if fc.count(old_update) != 1:
    raise SystemExit("update_curr EEVDF reschedule anchor mismatch")
fc = fc.replace(old_update, new_update, 1)

# ---------------------------------------------------------------------------
# 3. Preserve the old implicit cancellation semantics. With the Linux 6.6
#    sentinel, changing deadline or overwriting vlag automatically ended
#    protection. With explicit vprot we must cancel it intentionally on yield
#    and when reweighting the currently running entity.
# ---------------------------------------------------------------------------
yield_anchor = """	if (unlikely(sched_eevdf_enabled)) {
		u64 slice = se->slice ? se->slice : eevdf_base_slice();

		se->deadline += calc_delta_fair(slice, se);
"""
yield_repl = """	if (unlikely(sched_eevdf_enabled)) {
		u64 slice = se->slice ? se->slice : eevdf_base_slice();

		eevdf_cancel_protect_slice(se);
		se->deadline += calc_delta_fair(slice, se);
"""
if fc.count(yield_anchor) != 1:
    raise SystemExit("yield protection-cancel anchor mismatch")
fc = fc.replace(yield_anchor, yield_repl, 1)

reweight_anchor = """		if (eevdf) {
			avruntime = eevdf_avg_vruntime(cfs_rq);
			eevdf_update_entity_lag(cfs_rq, se);
"""
reweight_repl = """		if (eevdf) {
			avruntime = eevdf_avg_vruntime(cfs_rq);
			if (curr)
				eevdf_cancel_protect_slice(se);
			eevdf_update_entity_lag(cfs_rq, se);
"""
if fc.count(reweight_anchor) != 1:
    raise SystemExit("reweight current protection-cancel anchor mismatch")
fc = fc.replace(reweight_anchor, reweight_repl, 1)

# Add a human-readable runtime marker to the existing status node.
status_anchor = '\tseq_printf(m, "base_slice_ns=%u\\n", sysctl_sched_min_granularity);\n'
status_repl = status_anchor + '\tseq_puts(m, "protection_model=vprot\\n");\n'
if fc.count(status_anchor) != 1:
    raise SystemExit("eevdf_status base_slice anchor mismatch")
fc = fc.replace(status_anchor, status_repl, 1)

sched_h.write_text(sh)
core_c.write_text(cc)
fair_c.write_text(fc)

# Static safety/audit checks.
all_text = sh + "\n" + cc + "\n" + fc
for needle in (
    "u64\t\t\t\tvprot;",
    "eevdf_set_protect_slice(",
    "eevdf_protect_slice(",
    "eevdf_cancel_protect_slice(",
    "protection_model=vprot",
    "expired || !eevdf_protect_slice(curr)",
):
    if needle not in all_text:
        raise SystemExit(f"missing EEVDF efficiency element: {needle!r}")

if "curr->vlag == curr->deadline" in fc:
    raise SystemExit("old vlag==deadline protection sentinel still present")
if "se->vlag = se->deadline;" in fc:
    raise SystemExit("old vlag protection assignment still present")

print("A52 EEVDF post-6.6 protection correctness applied")
print("source_lineage=linux-6.6-sentinel-to-later-vprot-model")
print("deadline_expiry_resched=retained")
print("protected_period_end_resched=enabled")
print("yield_protection_cancel=explicit")
print("reweight_protection_cancel=explicit")
print("different_slice_series=not-applicable-uniform-a52-base-slice")
