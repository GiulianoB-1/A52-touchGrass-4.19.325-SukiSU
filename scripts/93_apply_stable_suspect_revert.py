#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("usage: 93_apply_stable_suspect_revert.py <kernel-tree> <pagealloc24|mutex32|deadline38|hrtimer39>")

root = Path(sys.argv[1])
variant = sys.argv[2]
valid = {"pagealloc24", "mutex32", "deadline38", "hrtimer39"}
if variant not in valid:
    raise SystemExit(f"unknown variant {variant!r}; expected one of {sorted(valid)}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def scoped(path: Path, start: str, end: str):
    text = path.read_text()
    a = text.index(start)
    b = text.index(end, a)
    return text, a, b, text[a:b]


def put_scope(path: Path, text: str, a: int, b: int, body: str):
    path.write_text(text[:a] + body + text[b:])


report = [f"variant={variant}", "base=full-phase91b-linux-4.19.325-no-phase90"]

if variant == "pagealloc24":
    path = root / "mm/page_alloc.c"
    text, a, b, body = scoped(
        path,
        "static inline void __free_one_page",
        "static inline bool page_expected_state",
    )

    # The vendor/upstream three-way merge can legitimately leave this function
    # in a mixed semantic state because Samsung added compaction_capture()
    # inside the same loop that upstream changed in 4.19.207.  Normalize the
    # entire four-part #24 change back to the known-good 4.19.206 semantics
    # instead of assuming all four upstream anchors survived together.
    pairs = (
        (
            "max_order = min_t(unsigned int, MAX_ORDER - 1, pageblock_order);",
            "max_order = min_t(unsigned int, MAX_ORDER, pageblock_order + 1);",
            "pagealloc max_order initialization",
        ),
        (
            "while (order < max_order) {",
            "while (order < max_order - 1) {",
            "pagealloc merge loop",
        ),
        (
            "if (order < MAX_ORDER - 1) {",
            "if (max_order < MAX_ORDER) {",
            "pagealloc upper-order condition",
        ),
        (
            "max_order = order + 1;",
            "max_order++;",
            "pagealloc max_order increment",
        ),
    )
    states = []
    for modern, old, label in pairs:
        modern_count = body.count(modern)
        old_count = body.count(old)
        if modern_count == 1 and old_count == 0:
            body = body.replace(modern, old, 1)
            states.append(f"{label}=reverted-modern")
        elif modern_count == 0 and old_count == 1:
            states.append(f"{label}=already-419206")
        else:
            raise SystemExit(
                f"{label}: unrecognized hybrid counts modern={modern_count} old={old_count}"
            )

    # Samsung's compaction capture hook is part of the vendor allocator and
    # must remain present.  The point of this variant is to restore only the
    # 4.19.206 buddy-merge semantics around it.
    if "compaction_capture(capc, page, order, migratetype)" not in body:
        raise SystemExit("pagealloc Samsung compaction_capture hook disappeared")

    for _, old, label in pairs:
        if body.count(old) != 1:
            raise SystemExit(f"{label}: normalized 4.19.206 anchor missing")

    put_scope(path, text, a, b, body)
    report += [
        "reverted_stable_commit=dd8b408964e77e9b23c4fc6e0cca61bc9345a01f",
        "subject=mm/page_alloc: speed up the iteration of max_order",
        "samsung_compaction_capture=preserved",
        *states,
    ]

elif variant == "mutex32":
    path = root / "kernel/locking/mutex.c"
    text, a, b, body = scoped(
        path,
        "__mutex_lock_common(struct mutex *lock",
        "static int __sched\n__mutex_lock(",
    )
    body = replace_once(
        body,
        "\tstruct mutex_waiter waiter;\n",
        "\tstruct mutex_waiter waiter;\n\tbool first = false;\n",
        "mutex outer first state",
    )
    body = replace_once(
        body,
        "\tset_current_state(state);\n\tfor (;;) {\n\t\tbool first;\n",
        "\tset_current_state(state);\n\tfor (;;) {\n",
        "mutex loop-local first",
    )
    modern = (
        "\t\tfirst = __mutex_waiter_is_first(lock, &waiter);\n"
        "\t\tif (first)\n"
        "\t\t\t__mutex_set_flag(lock, MUTEX_FLAG_HANDOFF);\n"
    )
    condition = "(use_ww_ctx && ww_ctx) || !first" if "const bool use_ww_ctx" in body else "ww_ctx || !first"
    old = (
        "\t\t/*\n"
        "\t\t * ww_mutex needs to always recheck its position since its waiter\n"
        "\t\t * list is not FIFO ordered.\n"
        "\t\t */\n"
        f"\t\tif ({condition}) {{\n"
        "\t\t\tfirst = __mutex_waiter_is_first(lock, &waiter);\n"
        "\t\t\tif (first)\n"
        "\t\t\t\t__mutex_set_flag(lock, MUTEX_FLAG_HANDOFF);\n"
        "\t\t}\n"
    )
    body = replace_once(body, modern, old, "mutex HANDOFF block")
    put_scope(path, text, a, b, body)
    report += [
        "reverted_stable_commit=b7a041073f4e72083dcd688a003a59bf9d9c9124",
        "subject=locking/mutex: Fix HANDOFF condition",
    ]

elif variant == "deadline38":
    path = root / "kernel/sched/deadline.c"
    text, a, b, body = scoped(
        path,
        "static void migrate_task_rq_dl",
        "static void check_preempt_equal_dl",
    )
    body = replace_once(
        body,
        "\t\tupdate_rq_clock(rq);\n\t\tsub_running_bw(&p->dl, &rq->dl);",
        "\t\tsub_running_bw(&p->dl, &rq->dl);",
        "deadline rq-clock update",
    )
    put_scope(path, text, a, b, body)
    report += [
        "reverted_stable_commit=293fe77dbfe68c5794be4e76c9212003e9304d24",
        "subject=sched/deadline: Fix missing clock update in migrate_task_rq_dl()",
    ]

elif variant == "hrtimer39":
    path = root / "kernel/time/hrtimer.c"
    text = path.read_text()

    # Revert only the 4.19.207 restart/reprogramming algorithm while preserving
    # Samsung's HRTIMER_STATE_PINNED state extension and all later A52/vendor
    # hooks elsewhere in hrtimer.c.
    a = text.index("static inline int\nremove_hrtimer(")
    b = text.index("static inline ktime_t hrtimer_update_lowres", a)
    old_remove = """static inline int
remove_hrtimer(struct hrtimer *timer, struct hrtimer_clock_base *base, bool restart)
{
\tu8 state = timer->state;

\tif (state & HRTIMER_STATE_ENQUEUED) {
\t\tint reprogram;

\t\t/*
\t\t * Remove the timer and force reprogramming when high
\t\t * resolution mode is active and the timer is on the current
\t\t * CPU. If we remove a timer on another CPU, reprogramming is
\t\t * skipped. The interrupt event on this CPU is fired and
\t\t * reprogramming happens in the interrupt handler. This is a
\t\t * rare case and less expensive than a smp call.
\t\t */
\t\tdebug_deactivate(timer);
\t\treprogram = base->cpu_base == this_cpu_ptr(&hrtimer_bases);

\t\tif (!restart)
\t\t\tstate = HRTIMER_STATE_INACTIVE;

\t\t__remove_hrtimer(timer, base, state, reprogram);
\t\ttimer->state &= ~HRTIMER_STATE_PINNED;
\t\treturn 1;
\t}
\treturn 0;
}

"""
    text = text[:a] + old_remove + text[b:]

    a = text.index("static int __hrtimer_start_range_ns(")
    b = text.index("\n/**\n * hrtimer_start_range_ns", a)
    old_start = """static int __hrtimer_start_range_ns(struct hrtimer *timer, ktime_t tim,
\t\t\t\t    u64 delta_ns, const enum hrtimer_mode mode,
\t\t\t\t    struct hrtimer_clock_base *base)
{
\tstruct hrtimer_clock_base *new_base;

\t/* Remove an active timer from the queue: */
\tremove_hrtimer(timer, base, true);

\tif (mode & HRTIMER_MODE_REL)
\t\ttim = ktime_add_safe(tim, base->get_time());

\ttim = hrtimer_update_lowres(timer, tim, mode);

\thrtimer_set_expires_range_ns(timer, tim, delta_ns);

\t/* Switch the timer base, if necessary: */
\tnew_base = switch_hrtimer_base(timer, base, mode & HRTIMER_MODE_PINNED);

\t/* Update pinned state */
\ttimer->state &= ~HRTIMER_STATE_PINNED;
\ttimer->state |= (!!(mode & HRTIMER_MODE_PINNED)) << HRTIMER_PINNED_SHIFT;

\treturn enqueue_hrtimer(timer, new_base, mode);
}
"""
    text = text[:a] + old_start + text[b:]
    text = replace_once(
        text,
        "ret = remove_hrtimer(timer, base, false, false);",
        "ret = remove_hrtimer(timer, base, false);",
        "hrtimer cancel call",
    )
    if "remove_hrtimer(timer, base, true," in text:
        raise SystemExit("hrtimer keep_local start call remains")
    path.write_text(text)
    report += [
        "reverted_stable_commit=e6c3fefc6bb11bef1bd8adfe37e0f317303ab751",
        "subject=hrtimer: Avoid double reprogramming in __hrtimer_start_range_ns()",
        "samsung_pinned_state=preserved",
    ]

# Common safety checks.
for rel in (
    "mm/page_alloc.c",
    "kernel/locking/mutex.c",
    "kernel/sched/deadline.c",
    "kernel/time/hrtimer.c",
):
    data = (root / rel).read_text()
    if "<<<<<<<" in data or ">>>>>>>" in data or "|||||||" in data:
        raise SystemExit(f"merge marker found after {variant}: {rel}")

out = root.parent.parent / "artifacts" / f"phase93-{variant}-revert.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(report) + "\n")
print(out.read_text(), end="")
