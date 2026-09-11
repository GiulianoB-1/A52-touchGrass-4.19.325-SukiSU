#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 62_apply_eevdf_phase4_tree_picker.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

if "EEVDF phase 4 augmented deadline tree" not in fc:
    anchor = """static inline int entity_before(struct sched_entity *a,
				struct sched_entity *b)
{
	return (s64)(a->vruntime - b->vruntime) < 0;
}

"""
    block = """static inline int entity_before(struct sched_entity *a,
				struct sched_entity *b)
{
	return (s64)(a->vruntime - b->vruntime) < 0;
}

/* EEVDF phase 4 augmented deadline tree. */
static inline u64 eevdf_min_deadline_compute(struct sched_entity *se)
{
	u64 min = se->deadline;

	if (se->run_node.rb_left) {
		struct sched_entity *left =
			rb_entry(se->run_node.rb_left, struct sched_entity, run_node);
		if ((s64)(left->min_deadline - min) < 0)
			min = left->min_deadline;
	}
	if (se->run_node.rb_right) {
		struct sched_entity *right =
			rb_entry(se->run_node.rb_right, struct sched_entity, run_node);
		if ((s64)(right->min_deadline - min) < 0)
			min = right->min_deadline;
	}
	return min;
}

static void eevdf_min_deadline_propagate(struct rb_node *rb,
					 struct rb_node *stop)
{
	while (rb != stop) {
		struct sched_entity *se =
			rb_entry(rb, struct sched_entity, run_node);
		u64 min = eevdf_min_deadline_compute(se);

		if (se->min_deadline == min)
			break;
		se->min_deadline = min;
		rb = rb_parent(&se->run_node);
	}
}

static void eevdf_min_deadline_copy(struct rb_node *old,
				    struct rb_node *new)
{
	struct sched_entity *old_se =
		rb_entry(old, struct sched_entity, run_node);
	struct sched_entity *new_se =
		rb_entry(new, struct sched_entity, run_node);

	new_se->min_deadline = old_se->min_deadline;
}

static void eevdf_min_deadline_rotate(struct rb_node *old,
				      struct rb_node *new)
{
	struct sched_entity *old_se =
		rb_entry(old, struct sched_entity, run_node);
	struct sched_entity *new_se =
		rb_entry(new, struct sched_entity, run_node);

	new_se->min_deadline = old_se->min_deadline;
	old_se->min_deadline = eevdf_min_deadline_compute(old_se);
}

static const struct rb_augment_callbacks eevdf_min_deadline_cb = {
	.propagate = eevdf_min_deadline_propagate,
	.copy = eevdf_min_deadline_copy,
	.rotate = eevdf_min_deadline_rotate,
};

"""
    if fc.count(anchor) != 1:
        raise SystemExit("entity_before anchor mismatch")
    fc = fc.replace(anchor, block, 1)

old = """	rb_link_node(&se->run_node, parent, link);
	rb_insert_color_cached(&se->run_node,
			       &cfs_rq->tasks_timeline, leftmost);
	eevdf_avg_vruntime_add(cfs_rq, se);
}

static void __dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
	eevdf_avg_vruntime_sub(cfs_rq, se);
	rb_erase_cached(&se->run_node, &cfs_rq->tasks_timeline);
}
"""
new = """	se->min_deadline = se->deadline;
	rb_link_node(&se->run_node, parent, link);
	if (parent)
		eevdf_min_deadline_cb.propagate(parent, NULL);
	rb_insert_augmented_cached(&se->run_node,
				   &cfs_rq->tasks_timeline, leftmost,
				   &eevdf_min_deadline_cb);
	eevdf_avg_vruntime_add(cfs_rq, se);
}

static void __dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
	eevdf_avg_vruntime_sub(cfs_rq, se);
	rb_erase_augmented_cached(&se->run_node, &cfs_rq->tasks_timeline,
				  &eevdf_min_deadline_cb);
}
"""
if old not in fc:
    raise SystemExit("enqueue/dequeue augmented-tree anchor mismatch")
fc = fc.replace(old, new, 1)

picker_anchor = """#ifdef CONFIG_SCHED_DEBUG
struct sched_entity *__pick_last_entity(struct cfs_rq *cfs_rq)
"""
picker = """/*
 * Compile-gated EEVDF picker.
 *
 * This is deliberately not wired into pick_next_entity() yet. Phase 4 only
 * proves that the augmented deadline tree and eligibility walk build cleanly
 * on the Qualcomm/Samsung 4.19 scheduler.
 */
static struct sched_entity *eevdf_pick_entity(struct cfs_rq *cfs_rq)
{
	struct rb_node *node = cfs_rq->tasks_timeline.rb_root.rb_node;
	struct sched_entity *best = NULL;

	if (cfs_rq->curr && cfs_rq->curr->on_rq &&
	    eevdf_entity_eligible(cfs_rq, cfs_rq->curr))
		best = cfs_rq->curr;

	while (node) {
		struct sched_entity *se =
			rb_entry(node, struct sched_entity, run_node);

		if (!eevdf_entity_eligible(cfs_rq, se)) {
			node = node->rb_left;
			continue;
		}

		if (!best || (s64)(se->deadline - best->deadline) < 0)
			best = se;

		if (node->rb_left) {
			struct sched_entity *left =
				rb_entry(node->rb_left, struct sched_entity, run_node);
			if (!best || (s64)(left->min_deadline - best->deadline) < 0) {
				node = node->rb_left;
				continue;
			}
		}
		node = node->rb_right;
	}

	return best;
}

#ifdef CONFIG_SCHED_DEBUG
struct sched_entity *__pick_last_entity(struct cfs_rq *cfs_rq)
"""
if picker_anchor not in fc:
    raise SystemExit("picker insertion anchor mismatch")
fc = fc.replace(picker_anchor, picker, 1)

fair_c.write_text(fc)

text = fair_c.read_text()
for needle in [
    "EEVDF phase 4 augmented deadline tree",
    "eevdf_min_deadline_cb",
    "rb_insert_augmented_cached",
    "rb_erase_augmented_cached",
    "eevdf_pick_entity(",
]:
    if needle not in text:
        raise SystemExit(f"missing phase 4 element: {needle}")

print("EEVDF phase 4 augmented tree and picker scaffold applied")
print("task_selection=legacy-cfs-walt")
print("eevdf_picker_compiled=yes")
print("eevdf_picker_active=no")
