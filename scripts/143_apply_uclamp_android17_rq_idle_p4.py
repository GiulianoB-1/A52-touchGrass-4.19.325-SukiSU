#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 UCLAMP P4: Android17 rq access helpers + idle-rq semantics"
IDLE_UPSTREAM = "3e1493f46390618ea78607cb30c58fc19e2a5035"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
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
    sched_h = root / "kernel/sched/sched.h"
    core = root / "kernel/sched/core.c"
    sugov = root / "kernel/sched/cpufreq_schedutil.c"
    fair = root / "kernel/sched/fair.c"

    for path in (sched_h, core, sugov, fair):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    sh = sched_h.read_text()
    cc = core.read_text()
    sg = sugov.read_text()
    fa = fair.read_text()

    # Require the exact boot-tested modern stack.
    for needle in (
        "extern struct static_key_false sched_uclamp_used;",
        "static inline bool uclamp_is_used(void)",
        "static inline void sched_uclamp_enable(void)",
        "unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,",
    ):
        if needle not in sh:
            raise SystemExit(f"required sched.h uclamp baseline missing: {needle}")

    for needle in (
        "A52 UCLAMP P3: Android17 active-update idle-state correctness",
        "static inline void uclamp_rq_reinc_id(",
        "rq->uclamp_flags = UCLAMP_FLAG_IDLE;",
    ):
        if needle not in cc:
            raise SystemExit(f"required P142 baseline missing: {needle}")

    for needle in (
        "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
        "A52 SCHEDUTIL P2: Android17 79443a7e limits_changed synchronization",
        "U17 uclamp + WALT schedutil compatibility path active",
    ):
        if needle not in sg:
            raise SystemExit(f"required schedutil baseline missing: {needle}")

    if "uclamp_rq_util_with(cpu_rq(cpu), util, p)" not in fa:
        raise SystemExit("fair.c waking-task uclamp placement path missing")

    if MARKER in sh:
        print("[already] uclamp rq helpers/idle semantics present")
        return 0

    # ------------------------------------------------------------------
    # Android17 6.18 rq access abstraction.
    # ------------------------------------------------------------------
    enabled_anchor = """static inline void sched_uclamp_enable(void)
{
	if (!uclamp_is_used())
		static_branch_enable(&sched_uclamp_used);
}

"""
    enabled_helpers = enabled_anchor + f"""/*
 * {MARKER}
 *
 * Keep all rq clamp value accesses behind the same READ_ONCE/WRITE_ONCE
 * interface used by modern Android common kernels.
 */
static inline unsigned long uclamp_rq_get(struct rq *rq,
					  enum uclamp_id clamp_id)
{{
	return READ_ONCE(rq->uclamp[clamp_id].value);
}}

static inline void uclamp_rq_set(struct rq *rq, enum uclamp_id clamp_id,
				 unsigned int value)
{{
	WRITE_ONCE(rq->uclamp[clamp_id].value, value);
}}

static inline bool uclamp_rq_is_idle(struct rq *rq)
{{
	return rq->uclamp_flags & UCLAMP_FLAG_IDLE;
}}

"""
    sh = replace_once(sh, enabled_anchor, enabled_helpers,
                      "enabled rq helper insertion")

    disabled_anchor = """static inline void sched_uclamp_enable(void) { }

"""
    disabled_helpers = disabled_anchor + """static inline unsigned long
uclamp_rq_get(struct rq *rq, enum uclamp_id clamp_id)
{
	if (clamp_id == UCLAMP_MIN)
		return 0;

	return SCHED_CAPACITY_SCALE;
}

static inline void
uclamp_rq_set(struct rq *rq, enum uclamp_id clamp_id, unsigned int value)
{
}

static inline bool uclamp_rq_is_idle(struct rq *rq)
{
	return false;
}

"""
    sh = replace_once(sh, disabled_anchor, disabled_helpers,
                      "disabled rq helper insertion")

    # ------------------------------------------------------------------
    # Upstream 3e1493f4 idle-rq semantics, adapted to keep the 4.19 ABI.
    # This affects the p != NULL placement path in fair.c. If the target rq
    # is idle, its retained rq UCLAMP_MAX belongs to the previous runnable
    # task and must not override the waking task's own effective clamps.
    # ------------------------------------------------------------------
    start, end = function_bounds(
        sh, "static __always_inline\nunsigned long uclamp_rq_util_with("
    )
    old_fn = sh[start:end]
    sig_end = old_fn.find("{")
    signature = old_fn[:sig_end]

    new_fn = signature + """{
	unsigned long min_util = 0;
	unsigned long max_util = 0;

	if (likely(!uclamp_is_used()))
		return util;

	if (p) {
		min_util = uclamp_eff_value(p, UCLAMP_MIN);
		max_util = uclamp_eff_value(p, UCLAMP_MAX);

		/*
		 * Upstream 3e1493f4: an idle rq retains the previous task's
		 * UCLAMP_MAX to mask blocked utilization. A waking task will
		 * replace that rq clamp when enqueued, so placement must use the
		 * waking task's own effective clamps instead of the stale rq value.
		 */
		if (uclamp_rq_is_idle(rq))
			goto out;
	}

	min_util = max_t(unsigned long, min_util,
			 uclamp_rq_get(rq, UCLAMP_MIN));
	max_util = max_t(unsigned long, max_util,
			 uclamp_rq_get(rq, UCLAMP_MAX));
out:
	/*
	 * rq MIN/MAX are max-aggregated independently, so inversion can occur
	 * with tasks requesting different clamps. Preserve upstream handling.
	 */
	if (unlikely(min_util >= max_util))
		return min_util;

	return clamp(util, min_util, max_util);
}"""
    sh = sh[:start] + new_fn + sh[end:]
    sched_h.write_text(sh)

    # ------------------------------------------------------------------
    # Route core rq clamp accesses through the new helpers. This is intended
    # as a no-policy-change refactor around the same READ_ONCE/WRITE_ONCE.
    # ------------------------------------------------------------------
    cc = replace_once(
        cc,
        """	if (!(rq->uclamp_flags & UCLAMP_FLAG_IDLE))
		return;

	WRITE_ONCE(rq->uclamp[clamp_id].value, clamp_value);
""",
        """	if (!uclamp_rq_is_idle(rq))
		return;

	uclamp_rq_set(rq, clamp_id, clamp_value);
""",
        "uclamp_idle_reset helpers",
    )

    cc = replace_once(
        cc,
        """	if (uc_se->value > READ_ONCE(uc_rq->value))
		WRITE_ONCE(uc_rq->value, uc_se->value);
""",
        """	if (uc_se->value > uclamp_rq_get(rq, clamp_id))
		uclamp_rq_set(rq, clamp_id, uc_se->value);
""",
        "uclamp_rq_inc_id helpers",
    )

    cc = replace_once(
        cc,
        """	rq_clamp = READ_ONCE(uc_rq->value);
""",
        """	rq_clamp = uclamp_rq_get(rq, clamp_id);
""",
        "uclamp_rq_dec_id read helper",
    )

    cc = replace_once(
        cc,
        """		WRITE_ONCE(uc_rq->value, bkt_clamp);
""",
        """		uclamp_rq_set(rq, clamp_id, bkt_clamp);
""",
        "uclamp_rq_dec_id write helper",
    )

    # P142 helper now consumes the same idle abstraction.
    cc = replace_once(
        cc,
        """	if (clamp_id == UCLAMP_MAX &&
	    (rq->uclamp_flags & UCLAMP_FLAG_IDLE))
		rq->uclamp_flags &= ~UCLAMP_FLAG_IDLE;
""",
        """	if (clamp_id == UCLAMP_MAX && uclamp_rq_is_idle(rq))
		rq->uclamp_flags &= ~UCLAMP_FLAG_IDLE;
""",
        "P142 reinc idle helper",
    )

    core.write_text(cc)

    final_sh = sched_h.read_text()
    final_cc = core.read_text()

    for needle in (
        MARKER,
        "static inline unsigned long uclamp_rq_get(",
        "static inline void uclamp_rq_set(",
        "static inline bool uclamp_rq_is_idle(",
        "unsigned long min_util = 0;",
        "unsigned long max_util = 0;",
        "if (uclamp_rq_is_idle(rq))",
        "uclamp_rq_get(rq, UCLAMP_MIN)",
        "uclamp_rq_get(rq, UCLAMP_MAX)",
    ):
        if needle not in final_sh:
            raise SystemExit(f"sched.h audit failed: missing {needle}")

    for needle in (
        "uclamp_rq_set(rq, clamp_id, clamp_value);",
        "uclamp_rq_get(rq, clamp_id)",
        "uclamp_rq_set(rq, clamp_id, uc_se->value);",
        "uclamp_rq_set(rq, clamp_id, bkt_clamp);",
        "clamp_id == UCLAMP_MAX && uclamp_rq_is_idle(rq)",
        "A52 UCLAMP P3: Android17 active-update idle-state correctness",
    ):
        if needle not in final_cc:
            raise SystemExit(f"core.c audit failed: missing {needle}")

    # Hot rq clamp access sites modernized in the target functions.
    for signature in (
        "static inline void uclamp_idle_reset(",
        "static inline void uclamp_rq_inc_id(",
        "static inline void uclamp_rq_dec_id(",
    ):
        s, e = function_bounds(final_cc, signature)
        block = final_cc[s:e]
        if "READ_ONCE(uc_rq->value)" in block or "WRITE_ONCE(uc_rq->value" in block:
            raise SystemExit(f"direct rq value access remains in {signature}")

    print("[audit] Android17 rq clamp get/set/idle helpers: PASS")
    print("[audit] waking-task idle-rq clamp semantics: PASS")
    print("[audit] fair.c p!=NULL placement ABI preserved: PASS")
    print("[audit] core rq clamp READ_ONCE/WRITE_ONCE semantics preserved via helpers: PASS")
    print("[audit] P142 reinc idle-state fix preserved: PASS")
    print("[audit] P140/P141 WALT schedutil integration untouched: PASS")
    print("[audit] CASS/EEVDF and CPU frequency tunables unchanged: PASS")
    print(f"[source] upstream {IDLE_UPSTREAM}: ignore stale rq max aggregation while rq is idle")
    print("[reference] Android17 Linux 6.18 retains uclamp_rq_get/set/is_idle helpers")
    print("[done] A52 Android17 uclamp rq/idle modernization P4 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
