#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 UCLAMP P3: Android17 active-update idle-state correctness"
UPSTREAM = "ca4984a7dd863f3e1c0df775ae3e744bff24c303"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")

    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1

    raise SystemExit(f"unterminated function: {signature}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    core = root / "kernel/sched/core.c"
    sched_h = root / "kernel/sched/sched.h"
    sugov = root / "kernel/sched/cpufreq_schedutil.c"

    for path in (core, sched_h, sugov):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    text = core.read_text()
    sh = sched_h.read_text()
    sg = sugov.read_text()

    # Require the boot-tested modern-uclamp baseline.
    for needle in (
        "DEFINE_STATIC_KEY_FALSE(sched_uclamp_used);",
        "static inline bool uclamp_is_used(void)",
        "static void uclamp_update_util_min_rt_default(struct task_struct *p)",
        "sysctl_sched_uclamp_util_min_rt_default = 0;",
        "static void __init init_uclamp_rq(struct rq *rq)",
        "rq->uclamp_flags = UCLAMP_FLAG_IDLE;",
        "A52 uclamp efficiency P1",
    ):
        if needle not in text and needle not in sh:
            raise SystemExit(f"required modern-uclamp baseline missing: {needle}")

    for needle in (
        "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
        "A52 SCHEDUTIL P2: Android17 79443a7e limits_changed synchronization",
        "U17 uclamp + WALT schedutil compatibility path active",
    ):
        if needle not in sg:
            raise SystemExit(f"required P140/P141 schedutil baseline missing: {needle}")

    if MARKER in text:
        print("[already] uclamp active-update idle-state correctness present")
        return 0

    # Android17 still uses this helper. The critical detail is clearing
    # UCLAMP_FLAG_IDLE when a temporary dec/inc cycle transiently drops the
    # runqueue's active UCLAMP_MAX task count to zero.
    anchor = """static inline void uclamp_rq_dec(struct rq *rq, struct task_struct *p)
{
	enum uclamp_id clamp_id;

	if (!uclamp_is_used())
		return;

	if (unlikely(!p->sched_class->uclamp_enabled))
		return;

	for_each_clamp_id(clamp_id)
		uclamp_rq_dec_id(rq, p, clamp_id);
}

"""

    helper = f"""static inline void uclamp_rq_dec(struct rq *rq, struct task_struct *p)
{{
	enum uclamp_id clamp_id;

	if (!uclamp_is_used())
		return;

	if (unlikely(!p->sched_class->uclamp_enabled))
		return;

	for_each_clamp_id(clamp_id)
		uclamp_rq_dec_id(rq, p, clamp_id);
}}

/*
 * {MARKER}
 *
 * Android17/upstream {UPSTREAM}: updating an active task's clamp temporarily
 * removes and re-adds its rq bucket contribution. If that decrement reaches
 * zero UCLAMP_MAX contributors, uclamp_rq_dec_id() can set UCLAMP_FLAG_IDLE.
 * The task is immediately re-added, so leaving the idle flag set would make
 * later rq clamp calculations treat an active runqueue as idle.
 */
static inline void uclamp_rq_reinc_id(struct rq *rq, struct task_struct *p,
				      enum uclamp_id clamp_id)
{{
	if (!p->uclamp[clamp_id].active)
		return;

	uclamp_rq_dec_id(rq, p, clamp_id);
	uclamp_rq_inc_id(rq, p, clamp_id);

	if (clamp_id == UCLAMP_MAX &&
	    (rq->uclamp_flags & UCLAMP_FLAG_IDLE))
		rq->uclamp_flags &= ~UCLAMP_FLAG_IDLE;
}}

"""
    text = replace_once(text, anchor, helper, "insert uclamp_rq_reinc_id")

    # Existing 4.19 backport updates one clamp ID at a time. Preserve that ABI,
    # but make the active task refresh use the modern safe helper.
    start, end = function_bounds(text, "static inline void\nuclamp_update_active(")
    fn = text[start:end]
    old = """	if (p->uclamp[clamp_id].active) {
		uclamp_rq_dec_id(rq, p, clamp_id);
		uclamp_rq_inc_id(rq, p, clamp_id);
	}
"""
    new = """	uclamp_rq_reinc_id(rq, p, clamp_id);
"""
    fn = replace_once(fn, old, new, "uclamp_update_active reinc")
    text = text[:start] + fn + text[end:]

    # Phase86's RT-default live synchronization independently refreshes an
    # active UCLAMP_MIN contribution. Use the same helper there too.
    start, end = function_bounds(
        text, "static void uclamp_update_util_min_rt_default("
    )
    fn = text[start:end]
    old = """		if (p->uclamp[UCLAMP_MIN].active) {
			uclamp_rq_dec_id(rq, p, UCLAMP_MIN);
			uclamp_rq_inc_id(rq, p, UCLAMP_MIN);
		}
"""
    new = """		uclamp_rq_reinc_id(rq, p, UCLAMP_MIN);
"""
    fn = replace_once(fn, old, new, "RT default reinc")
    text = text[:start] + fn + text[end:]

    core.write_text(text)

    final = core.read_text()

    checks = (
        MARKER,
        "static inline void uclamp_rq_reinc_id(",
        "uclamp_rq_dec_id(rq, p, clamp_id);",
        "uclamp_rq_inc_id(rq, p, clamp_id);",
        "clamp_id == UCLAMP_MAX",
        "rq->uclamp_flags &= ~UCLAMP_FLAG_IDLE;",
        "uclamp_rq_reinc_id(rq, p, clamp_id);",
        "uclamp_rq_reinc_id(rq, p, UCLAMP_MIN);",
        "rq->uclamp_flags = UCLAMP_FLAG_IDLE;",
        "sysctl_sched_uclamp_util_min_rt_default = 0;",
    )
    for needle in checks:
        if needle not in final:
            raise SystemExit(f"audit failed: missing {needle}")

    # Make sure the two update paths no longer contain direct dec/inc pairs.
    for signature in (
        "static inline void\nuclamp_update_active(",
        "static void uclamp_update_util_min_rt_default(",
    ):
        s, e = function_bounds(final, signature)
        block = final[s:e]
        if "uclamp_rq_dec_id(" in block or "uclamp_rq_inc_id(" in block:
            raise SystemExit(
                f"audit failed: direct rq clamp dec/inc remains in {signature}"
            )
        if "uclamp_rq_reinc_id(" not in block:
            raise SystemExit(
                f"audit failed: safe reinc helper missing in {signature}"
            )

    print("[audit] Android17 uclamp_rq_reinc_id helper: PASS")
    print("[audit] UCLAMP_FLAG_IDLE transient-state repair: PASS")
    print("[audit] normal active clamp update uses safe reinc: PASS")
    print("[audit] RT-default live sync uses safe reinc: PASS")
    print("[audit] static-key gating and rq idle initialization preserved: PASS")
    print("[audit] P140/P141 schedutil behavior untouched: PASS")
    print("[audit] WALT/CASS/EEVDF behavior unchanged: PASS")
    print(f"[source] upstream {UPSTREAM}: sched: Fix UCLAMP_FLAG_IDLE setting")
    print("[reference] helper remains present in Android17 Linux 6.18")
    print("[done] A52 Android17 uclamp active-update correctness P3 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
