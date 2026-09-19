#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 109_apply_eevdf_efficiency_p1.py <kernel-tree>")

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


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

# This is an A52/4.19 adaptation of the Linux 6.17 EEVDF run-to-parity
# correctness series. The native series also fixes NO_RUN_TO_PARITY. Our
# backport has run-to-parity permanently enabled, so only the applicable
# protection/min-slice/wakeup/lag/resched semantics are carried here.

# -------------------------------------------------------------------------
# 1. Give protection its own state and augment the tree with min_slice.
#    Phase77 reused vlag as an on-rq protection marker. Later EEVDF uses a
#    dedicated protection endpoint, which avoids overloading saved lag state.
# -------------------------------------------------------------------------
field_anchor = "\tu64\t\t\t\tdeadline;\n\tu64\t\t\t\tmin_deadline;\n"
field_repl = (
    "\tu64\t\t\t\tdeadline;\n"
    "\tu64\t\t\t\tmin_deadline;\n"
    "\tu64\t\t\t\tmin_slice;\n"
)
sh = replace_once(sh, field_anchor, field_repl, "sched_entity_min_slice")

runtime_anchor = "\ts64\t\t\t\tvlag;\n\tu64\t\t\t\tslice;\n"
runtime_repl = (
    "\ts64\t\t\t\tvlag;\n"
    "\tu64\t\t\t\tslice;\n"
    "\tu64\t\t\t\tvprot;\n"
)
sh = replace_once(sh, runtime_anchor, runtime_repl, "sched_entity_vprot")

init_anchor = (
    "\tp->se.deadline\t\t\t= 0;\n"
    "\tp->se.min_deadline\t\t= 0;\n"
    "\tp->se.vlag\t\t\t= 0;\n"
    "\tp->se.slice\t\t\t= 0;\n"
)
init_repl = (
    "\tp->se.deadline\t\t\t= 0;\n"
    "\tp->se.min_deadline\t\t= 0;\n"
    "\tp->se.min_slice\t\t\t= 0;\n"
    "\tp->se.vlag\t\t\t= 0;\n"
    "\tp->se.slice\t\t\t= 0;\n"
    "\tp->se.vprot\t\t\t= 0;\n"
)
cc = replace_once(cc, init_anchor, init_repl, "sched_entity_init")

compute_anchor = '''static inline u64 eevdf_min_deadline_compute(struct sched_entity *se)
{
\tu64 min = se->deadline;

\tif (se->run_node.rb_left) {
\t\tstruct sched_entity *left =
\t\t\trb_entry(se->run_node.rb_left, struct sched_entity, run_node);
\t\tif ((s64)(left->min_deadline - min) < 0)
\t\t\tmin = left->min_deadline;
\t}
\tif (se->run_node.rb_right) {
\t\tstruct sched_entity *right =
\t\t\trb_entry(se->run_node.rb_right, struct sched_entity, run_node);
\t\tif ((s64)(right->min_deadline - min) < 0)
\t\t\tmin = right->min_deadline;
\t}
\treturn min;
}
'''
compute_repl = compute_anchor + '''
static inline u64 eevdf_min_slice_compute(struct sched_entity *se)
{
\tu64 min = se->slice ? se->slice : eevdf_base_slice();

\tif (se->run_node.rb_left) {
\t\tstruct sched_entity *left =
\t\t\trb_entry(se->run_node.rb_left, struct sched_entity, run_node);
\t\tif (left->min_slice < min)
\t\t\tmin = left->min_slice;
\t}
\tif (se->run_node.rb_right) {
\t\tstruct sched_entity *right =
\t\t\trb_entry(se->run_node.rb_right, struct sched_entity, run_node);
\t\tif (right->min_slice < min)
\t\t\tmin = right->min_slice;
\t}
\treturn min;
}
'''
fc = replace_once(fc, compute_anchor, compute_repl, "min_slice_compute")

prop_anchor = '''static void eevdf_min_deadline_propagate(struct rb_node *rb,
\t\t\t\t\t struct rb_node *stop)
{
\twhile (rb != stop) {
\t\tstruct sched_entity *se =
\t\t\trb_entry(rb, struct sched_entity, run_node);
\t\tu64 min = eevdf_min_deadline_compute(se);

\t\tif (se->min_deadline == min)
\t\t\tbreak;
\t\tse->min_deadline = min;
\t\trb = rb_parent(&se->run_node);
\t}
}
'''
prop_repl = '''static void eevdf_min_deadline_propagate(struct rb_node *rb,
\t\t\t\t\t struct rb_node *stop)
{
\twhile (rb != stop) {
\t\tstruct sched_entity *se =
\t\t\trb_entry(rb, struct sched_entity, run_node);
\t\tu64 min_deadline = eevdf_min_deadline_compute(se);
\t\tu64 min_slice = eevdf_min_slice_compute(se);

\t\tif (se->min_deadline == min_deadline && se->min_slice == min_slice)
\t\t\tbreak;
\t\tse->min_deadline = min_deadline;
\t\tse->min_slice = min_slice;
\t\trb = rb_parent(&se->run_node);
\t}
}
'''
fc = replace_once(fc, prop_anchor, prop_repl, "augment_propagate")

copy_anchor = '''\tnew_se->min_deadline = old_se->min_deadline;
}

static void eevdf_min_deadline_rotate'''
copy_repl = '''\tnew_se->min_deadline = old_se->min_deadline;
\tnew_se->min_slice = old_se->min_slice;
}

static void eevdf_min_deadline_rotate'''
fc = replace_once(fc, copy_anchor, copy_repl, "augment_copy")

rotate_anchor = '''\tnew_se->min_deadline = old_se->min_deadline;
\told_se->min_deadline = eevdf_min_deadline_compute(old_se);
}

static const struct rb_augment_callbacks'''
rotate_repl = '''\tnew_se->min_deadline = old_se->min_deadline;
\tnew_se->min_slice = old_se->min_slice;
\told_se->min_deadline = eevdf_min_deadline_compute(old_se);
\told_se->min_slice = eevdf_min_slice_compute(old_se);
}

static const struct rb_augment_callbacks'''
fc = replace_once(fc, rotate_anchor, rotate_repl, "augment_rotate")

enqueue_anchor = '''\tse->min_deadline = se->deadline;
\trb_link_node(&se->run_node, parent, link);'''
enqueue_repl = '''\tse->min_deadline = se->deadline;
\tse->min_slice = se->slice ? se->slice : eevdf_base_slice();
\trb_link_node(&se->run_node, parent, link);'''
fc = replace_once(fc, enqueue_anchor, enqueue_repl, "enqueue_min_slice")

# -------------------------------------------------------------------------
# 2. Add explicit protection helpers. cfs_rq_min_slice() mirrors the later
#    EEVDF invariant using our existing augmented tree.
# -------------------------------------------------------------------------
picker_sig = "static struct sched_entity *eevdf_pick_entity(struct cfs_rq *cfs_rq)\n"
if picker_sig not in fc:
    raise SystemExit("eevdf picker signature missing")

helpers = r'''static inline u64 eevdf_cfs_rq_min_slice(struct cfs_rq *cfs_rq)
{
\tstruct rb_node *root = cfs_rq->tasks_timeline.rb_root.rb_node;
\tstruct sched_entity *curr = cfs_rq->curr;
\tu64 min_slice = ~0ULL;

\tif (curr && curr->on_rq)
\t\tmin_slice = curr->slice ? curr->slice : eevdf_base_slice();
\tif (root) {
\t\tstruct sched_entity *se =
\t\t\trb_entry(root, struct sched_entity, run_node);
\t\tmin_slice = min(min_slice, se->min_slice);
\t}
\tif (min_slice == ~0ULL)
\t\tmin_slice = eevdf_base_slice();
\treturn min_slice;
}

static inline bool eevdf_protect_slice(struct sched_entity *se)
{
\treturn (s64)(se->vprot - se->vruntime) > 0;
}

static inline void eevdf_set_protect_slice(struct cfs_rq *cfs_rq,
\t\t\t\t\t   struct sched_entity *se)
{
\tu64 entity_slice = se->slice ? se->slice : eevdf_base_slice();
\tu64 quantum = min(eevdf_cfs_rq_min_slice(cfs_rq), entity_slice);
\tu64 vprot = se->deadline;

\tif (quantum != entity_slice) {
\t\tu64 candidate = se->vruntime + calc_delta_fair(quantum, se);
\t\tif ((s64)(candidate - vprot) < 0)
\t\t\tvprot = candidate;
\t}
\tse->vprot = vprot;
}

static inline void eevdf_update_protect_slice(struct cfs_rq *cfs_rq,
\t\t\t\t\t      struct sched_entity *se)
{
\tu64 quantum = eevdf_cfs_rq_min_slice(cfs_rq);
\tu64 candidate = se->vruntime + calc_delta_fair(quantum, se);

\tif ((s64)(candidate - se->vprot) < 0)
\t\tse->vprot = candidate;
}

static inline void eevdf_cancel_protect_slice(struct sched_entity *se)
{
\tif (eevdf_protect_slice(se))
\t\tse->vprot = se->vruntime;
}

'''
fc = fc.replace(picker_sig, helpers + picker_sig, 1)

# Replace the old Phase77 direct equality marker with explicit protection.
old_parity = '''\tif (curr && curr->vlag == curr->deadline) {
\t\tEEVDF_STAT_INC(run_to_parity);
\t\treturn curr;
\t}
'''
new_parity = '''\tif (curr && eevdf_protect_slice(curr)) {
\t\tEEVDF_STAT_INC(run_to_parity);
\t\treturn curr;
\t}
'''
fc = replace_once(fc, old_parity, new_parity, "picker_protection")

old_set_next = '''\tif (unlikely(sched_eevdf_enabled))
\t\tse->vlag = se->deadline;
'''
new_set_next = '''\tif (unlikely(sched_eevdf_enabled))
\t\teevdf_set_protect_slice(cfs_rq, se);
'''
fc = replace_once(fc, old_set_next, new_set_next, "set_next_protection")

# -------------------------------------------------------------------------
# 3. Avoid spurious wakeup preemption. Temporarily cancel protection and
#    verify that the waking entity is what the EEVDF picker would select.
#    If not, restore protection and shorten it for any newly queued shorter
#    slice, matching the later lag/protection correction.
# -------------------------------------------------------------------------
wakeup_anchor = '''\tif (unlikely(sched_eevdf_enabled)) {
\t\tif (eevdf_should_preempt(cfs_rq_of(se), se, pse)) {
\t\t\tEEVDF_STAT_INC(wakeup_preempt);
\t\t\tgoto preempt;
\t\t}
\t\treturn;
\t}
'''
wakeup_repl = '''\tif (unlikely(sched_eevdf_enabled)) {
\t\tstruct cfs_rq *cfs_rq = cfs_rq_of(se);

\t\tif (eevdf_should_preempt(cfs_rq, se, pse)) {
\t\t\tu64 saved_vprot = se->vprot;
\t\t\tstruct sched_entity *pick;

\t\t\teevdf_cancel_protect_slice(se);
\t\t\tpick = eevdf_pick_entity(cfs_rq);
\t\t\tif (pick == pse) {
\t\t\t\tEEVDF_STAT_INC(wakeup_preempt);
\t\t\t\tgoto preempt;
\t\t\t}
\t\t\tse->vprot = saved_vprot;
\t\t}

\t\t/* A newly queued shorter slice must shorten current protection. */
\t\teevdf_update_protect_slice(cfs_rq, se);
\t\treturn;
\t}
'''
fc = replace_once(fc, wakeup_anchor, wakeup_repl, "wakeup_picker_validation")

# -------------------------------------------------------------------------
# 4. Reschedule when the protected quantum ends, not only when the request
#    deadline is exhausted. This is the key later run-to-parity correction.
# -------------------------------------------------------------------------
tick_anchor = '''\tif (cfs_rq->nr_running > 1) {
\t\tif (unlikely(sched_eevdf_enabled)) {
\t\t\tif ((s64)(curr->vruntime - curr->deadline) >= 0)
\t\t\t\tresched_curr(rq_of(cfs_rq));
\t\t} else {
\t\t\tcheck_preempt_tick(cfs_rq, curr);
\t\t}
\t}
'''
tick_repl = '''\tif (cfs_rq->nr_running > 1) {
\t\tif (unlikely(sched_eevdf_enabled)) {
\t\t\tif (!eevdf_protect_slice(curr))
\t\t\t\tresched_curr(rq_of(cfs_rq));
\t\t} else {
\t\t\tcheck_preempt_tick(cfs_rq, curr);
\t\t}
\t}
'''
fc = replace_once(fc, tick_anchor, tick_repl, "protected_period_resched")

# Keep min_slice coherent at the places our backport directly rewrites the
# entity request/deadline outside normal enqueue.
fc = fc.replace(
    "\tse->min_deadline = se->deadline;\n\n\tif (unlikely(sched_eevdf_enabled)) {",
    "\tse->min_deadline = se->deadline;\n\tse->min_slice = se->slice ? se->slice : eevdf_base_slice();\n\n\tif (unlikely(sched_eevdf_enabled)) {",
)
fc = fc.replace(
    "\t\t\tse->min_deadline = se->deadline;\n\t\t\teevdf_stat_max_vlag(vlag);",
    "\t\t\tse->min_deadline = se->deadline;\n\t\t\tse->min_slice = se->slice ? se->slice : eevdf_base_slice();\n\t\t\teevdf_stat_max_vlag(vlag);",
)
fc = fc.replace(
    "\t\tse->min_deadline = se->deadline;\n\t\tEEVDF_STAT_INC(yield);",
    "\t\tse->min_deadline = se->deadline;\n\t\tse->min_slice = se->slice ? se->slice : eevdf_base_slice();\n\t\tEEVDF_STAT_INC(yield);",
)

# Phase77 no longer owns on-rq protection through vlag. Real lag still gets
# written by dequeue/reweight paths.
if "se->vlag = se->deadline;" in fc:
    raise SystemExit("legacy vlag protection marker remains")

sched_h.write_text(sh)
core_c.write_text(cc)
fair_c.write_text(fc)

checks = {
    sched_h: ["min_slice;", "vprot;"],
    fair_c: [
        "eevdf_cfs_rq_min_slice",
        "eevdf_set_protect_slice",
        "eevdf_update_protect_slice",
        "eevdf_protect_slice(curr)",
        "pick = eevdf_pick_entity(cfs_rq);",
        "if (!eevdf_protect_slice(curr))",
        "se->min_slice = se->slice ? se->slice : eevdf_base_slice();",
    ],
}
for path, needles in checks.items():
    text = path.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing EEVDF efficiency element: {needle!r}")

print("EEVDF efficiency/correctness P1 applied")
print("source_model=linux-6.17-run-to-parity-series-adapted")
print("protect_state=dedicated-vprot")
print("run_to_parity=min-enqueued-slice")
print("wakeup_preempt=picker-validated")
print("protected_period=resched-on-expiry")
print("no_run_to_parity=not-applicable-always-enabled-backport")
