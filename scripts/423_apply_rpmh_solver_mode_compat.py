#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1"
RSC = Path("drivers/soc/qcom/rpmh-rsc.c")
RPMH = Path("drivers/soc/qcom/rpmh.c")
INTERNAL = Path("drivers/soc/qcom/rpmh-internal.h")
COMPAT = Path("a52-port-compat.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase423 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_slice(text: str, signature: str, next_signature: str) -> str:
    a = text.find(signature)
    b = text.find(next_signature, a + len(signature))
    if a < 0 or b < 0:
        raise SystemExit("Phase423 function boundary missing: " + signature)
    return text[a:b]


def patch_internal(text: str) -> str:
    if MARK in text:
        return text
    old = """struct rpmh_ctrlr {
\tstruct list_head cache;
\tspinlock_t cache_lock;
\tbool dirty;
\tstruct list_head batch_cache;
};
"""
    new = """struct rpmh_ctrlr {
\tstruct list_head cache;
\tspinlock_t cache_lock;
\tbool dirty;
\tstruct list_head batch_cache;
\t/* A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1 */
\tbool in_solver_mode;
};
"""
    text = one(text, old, new, "rpmh_ctrlr solver flag")

    drv_old = """	int id;
	int num_tcs;
"""
    drv_new = """	int id;
	/* A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1 */
	bool in_solver_mode;
	int num_tcs;
"""
    text = one(text, drv_old, drv_new, "rsc_drv solver flag")

    anchor = """void rpmh_rsc_invalidate(struct rsc_drv *drv);

void rpmh_tx_done"""
    repl = """void rpmh_rsc_invalidate(struct rsc_drv *drv);

/* A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1 */
void a52_p423_solver_refused(struct rsc_drv *drv, enum rpmh_state state);

void rpmh_tx_done"""
    return one(text, anchor, repl, "internal helper prototype")


def patch_compat(text: str) -> str:
    if MARK in text:
        return text

    old = "#define rpmh_mode_solver_set(d,e) do{}while(0)\n"
    new = """/* A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1
 * Restore the legacy SDE -> RPMh software solver-state notification only.
 * Hardware solver programming remains owned by sde_rsc_hw state_update().
 */
int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable);
#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))
"""
    return one(text, old, new, "solver stub replacement")


P423_RSC_TOP = r'''
/* A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1
 *
 * Phase423 restores only software awareness of Display RSC solver mode.
 * The SDE RSC hardware state machine is already programmed by the imported
 * TouchGrass display driver.  Do not invalidate WAKE/SLEEP here: keeping the
 * downstream invalidate-before-borrow difference out preserves a one-variable
 * A/B against Phase422.
 */
static atomic_t a52_p423_refused = ATOMIC_INIT(0);
static atomic_t a52_p423_borrow_solver = ATOMIC_INIT(0);
static atomic_t a52_p423_mode_changes = ATOMIC_INIT(0);

static bool a52_p423_solver_mode(struct rsc_drv *drv)
{
	unsigned long flags;
	bool enabled;

	if (!drv)
		return false;

	spin_lock_irqsave(&drv->client.cache_lock, flags);
	enabled = drv->client.in_solver_mode;
	spin_unlock_irqrestore(&drv->client.cache_lock, flags);
	return enabled;
}

void a52_p423_solver_refused(struct rsc_drv *drv, enum rpmh_state state)
{
	int n;

	if (!a52_p301_disp_rsc(drv) || state != RPMH_ACTIVE_ONLY_STATE)
		return;

	n = atomic_inc_return(&a52_p423_refused);
	a52_ackfr_record("P423 REF n=%d flag=%u st=%u",
		n, a52_p423_solver_mode(drv) ? 1U : 0U, (unsigned int)state);
}

static void a52_p423_note_borrow_solver(struct rsc_drv *drv,
					const struct tcs_group *tcs)
{
	int n;

	if (!a52_p301_disp_rsc(drv) || !tcs ||
	    tcs->type == ACTIVE_TCS || !READ_ONCE(drv->in_solver_mode))
		return;

	n = atomic_inc_return(&a52_p423_borrow_solver);
	a52_ackfr_record("P423 BORROW n=%d flag=1 ty=%d off=%d use=%u",
		n, tcs->type, tcs->offset,
		(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR));
}
'''


P423_RSC_SETTER = r'''
int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)
{
	struct rsc_drv *drv;
	struct tcs_group *tcs;
	unsigned long flags;
	bool busy, old, old_drv;
	unsigned int loops = 0;
	int m, n;

	if (!dev || !dev->parent)
		return -EINVAL;

	drv = dev_get_drvdata(dev->parent);
	if (!drv)
		return -ENODEV;

	/*
	 * TouchGrass waits for the active AMC group to drain before entering
	 * solver mode.  Display RSC has zero ACTIVE TCSes, so this deliberately
	 * checks the borrowed WAKE group.  IRQs are restored between retries so
	 * the completion IRQ that frees a borrowed TCS can run.
	 */
	tcs = &drv->tcs[ACTIVE_TCS];
	if (!tcs->num_tcs)
		tcs = &drv->tcs[WAKE_TCS];

	if (enable) {
		for (;;) {
			busy = false;
			spin_lock_irqsave(&drv->lock, flags);
			for (m = tcs->offset;
			     m < tcs->offset + tcs->num_tcs; m++) {
				if (!tcs_is_free(drv, m)) {
					busy = true;
					break;
				}
			}
			if (!busy)
				break;
			spin_unlock_irqrestore(&drv->lock, flags);
			loops++;
			cpu_relax();
		}
	} else {
		spin_lock_irqsave(&drv->lock, flags);
	}

	/*
	 * Pinned 5.10 documents this lock order explicitly:
	 * drv->lock, then rpmh_ctrlr.cache_lock.
	 */
	old_drv = drv->in_solver_mode;
	drv->in_solver_mode = enable;
	spin_lock(&drv->client.cache_lock);
	old = drv->client.in_solver_mode;
	drv->client.in_solver_mode = enable;
	spin_unlock(&drv->client.cache_lock);
	spin_unlock_irqrestore(&drv->lock, flags);

	n = atomic_inc_return(&a52_p423_mode_changes);
	if (a52_p301_disp_rsc(drv))
		a52_ackfr_record(
			"P423 SOL n=%d en=%u up=%u/%u low=%u/%u loops=%u ref=%d bor=%d",
			n, enable ? 1U : 0U, old ? 1U : 0U,
			a52_p423_solver_mode(drv) ? 1U : 0U,
			old_drv ? 1U : 0U,
			READ_ONCE(drv->in_solver_mode) ? 1U : 0U,
			loops, atomic_read(&a52_p423_refused),
			atomic_read(&a52_p423_borrow_solver));

	return 0;
}
EXPORT_SYMBOL_GPL(a52_rpmh_mode_solver_set_compat);
'''


def patch_rsc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1",
        "A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1",
        "static bool a52_p301_disp_rsc(const struct rsc_drv *drv)",
        "static bool tcs_is_free(struct rsc_drv *drv, int tcs_id)",
        "int rpmh_rsc_send_data(struct rsc_drv *drv, const struct tcs_request *msg)",
        "static bool rpmh_rsc_ctrlr_is_busy(struct rsc_drv *drv)",
    ):
        if token not in text:
            raise SystemExit("Phase423 RSC prerequisite missing: " + token)

    anchor = """static bool a52_p301_disp_rsc(const struct rsc_drv *drv)
{
\treturn drv && drv->name && !strcmp(drv->name, "disp_rsc");
}
"""
    text = one(text, anchor, anchor + P423_RSC_TOP, "RSC diagnostics")

    lock_anchor = """\tspin_lock_irq(&drv->lock);

\t/* Wait forever for a free tcs. It better be there eventually! */
"""
    lock_new = """\tspin_lock_irq(&drv->lock);

\t/* Close the upper-layer check-to-send race. TouchGrass also keeps a
\t * DRV-local solver flag; return the same -EBUSY expected by msm_bus.
\t */
\tif (msg->state == RPMH_ACTIVE_ONLY_STATE &&
\t    READ_ONCE(drv->in_solver_mode)) {
\t\tspin_unlock_irq(&drv->lock);
\t\ta52_p423_solver_refused(drv, msg->state);
\t\treturn -EBUSY;
\t}

\t/* Wait forever for a free tcs. It better be there eventually! */
"""
    text = one(text, lock_anchor, lock_new, "lower solver race guard")

    trigger_anchor = """\t__tcs_buffer_write(drv, tcs_id, 0, msg);
\t__tcs_set_trigger(drv, tcs_id, true);
"""
    trigger_new = """\t__tcs_buffer_write(drv, tcs_id, 0, msg);
\t/* Count actual borrowed-WAKE triggers while solver is set. Expected 0. */
\tif (msg->state == RPMH_ACTIVE_ONLY_STATE)
\t\ta52_p423_note_borrow_solver(drv, tcs);
\t__tcs_set_trigger(drv, tcs_id, true);
"""
    text = one(text, trigger_anchor, trigger_new,
               "borrowed WAKE trigger invariant")

    busy_anchor = """static bool rpmh_rsc_ctrlr_is_busy(struct rsc_drv *drv)
{
\tint m;
\tstruct tcs_group *tcs = &drv->tcs[ACTIVE_TCS];

\t/*
\t * If we made an active request on a RSC that does not have a
\t * dedicated TCS for active state use, then re-purposed wake TCSes
\t * should be checked for not busy, because we used wake TCSes for
\t * active requests in this case.
\t */
\tif (!tcs->num_tcs)
\t\ttcs = &drv->tcs[WAKE_TCS];

\tfor (m = tcs->offset; m < tcs->offset + tcs->num_tcs; m++) {
\t\tif (!tcs_is_free(drv, m))
\t\t\treturn true;
\t}

\treturn false;
}
"""
    text = one(text, busy_anchor, busy_anchor + "\n" + P423_RSC_SETTER,
               "solver compat setter")

    return text


P423_RPMH_CHECK = r'''
/* A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1
 * TouchGrass rejects ACTIVE_ONLY at the RPMh API while Display RSC hardware
 * is in solver mode.  The imported msm_bus display-RSC path specifically
 * treats -EBUSY as the expected refusal.
 */
static int a52_p423_check_ctrlr_state(struct rpmh_ctrlr *ctrlr,
				      enum rpmh_state state)
{
	unsigned long flags;
	bool solver;

	if (!ctrlr || state != RPMH_ACTIVE_ONLY_STATE)
		return 0;

	spin_lock_irqsave(&ctrlr->cache_lock, flags);
	solver = ctrlr->in_solver_mode;
	spin_unlock_irqrestore(&ctrlr->cache_lock, flags);
	if (!solver)
		return 0;

	a52_p423_solver_refused(ctrlr_to_drv(ctrlr), state);
	return -EBUSY;
}
'''


def patch_rpmh(text: str) -> str:
    if MARK in text:
        return text

    for token in (
        "#define ctrlr_to_drv(ctrlr)",
        "static struct rpmh_ctrlr *get_rpmh_ctrlr(const struct device *dev)",
        "static int __rpmh_write(const struct device *dev, enum rpmh_state state,",
        "int rpmh_write_batch(const struct device *dev, enum rpmh_state state,",
    ):
        if token not in text:
            raise SystemExit("Phase423 RPMh prerequisite missing: " + token)

    getter = """static struct rpmh_ctrlr *get_rpmh_ctrlr(const struct device *dev)
{
\tstruct rsc_drv *drv = dev_get_drvdata(dev->parent);

\treturn &drv->client;
}
"""
    text = one(text, getter, getter + "\n" + P423_RPMH_CHECK,
               "RPMh solver-state checker")

    def add_public_check(src: str, signature: str, export: str, label: str) -> str:
        start = src.find(signature)
        end = src.find(export, start + len(signature))
        if start < 0 or end < 0:
            raise SystemExit("Phase423 RPMh function boundary missing: " + label)
        fn = src[start:end]
        anchor = "\tint ret;\n"
        if fn.count(anchor) != 1:
            raise SystemExit(
                f"Phase423 {label}: expected one int ret anchor, found {fn.count(anchor)}"
            )
        fn = fn.replace(
            anchor,
            anchor
            + "\n\tret = a52_p423_check_ctrlr_state(get_rpmh_ctrlr(dev), state);\n"
            + "\tif (ret)\n"
            + "\t\treturn ret;\n",
            1,
        )
        return src[:start] + fn + src[end:]

    text = add_public_check(
        text, "int rpmh_write_async(", "EXPORT_SYMBOL(rpmh_write_async);",
        "rpmh_write_async refusal",
    )
    text = add_public_check(
        text, "int rpmh_write(", "EXPORT_SYMBOL(rpmh_write);",
        "rpmh_write refusal",
    )

    batch = """\tif (!cmd || !n)
\t\treturn -EINVAL;

\twhile (n[count] > 0)
"""
    batch_new = """\tif (!cmd || !n)
\t\treturn -EINVAL;

\t/* rpmh_write_batch() bypasses __rpmh_write(); enforce the same solver
\t * refusal here because this is the imported display msm_bus path.
\t */
\tret = a52_p423_check_ctrlr_state(ctrlr, state);
\tif (ret)
\t\treturn ret;

\twhile (n[count] > 0)
"""
    text = one(text, batch, batch_new, "rpmh_write_batch refusal")
    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE422_DISPLAY_SMMU_FAULT_PROBE_V1" not in text:
        raise SystemExit("Phase423 recorder requires Phase422")

    retained = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "P420", 4) &&
"""
    retained_new = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "P423", 4) &&
\t    strncmp(fmt, "P420", 4) &&
"""
    text = one(text, retained, retained_new, "retention admission")

    normal = """if (strncmp(fmt, "P420", 4) &&
    strncmp(fmt, "P419", 4) &&
"""
    normal_new = """if (strncmp(fmt, "P423", 4) &&
    strncmp(fmt, "P420", 4) &&
    strncmp(fmt, "P419", 4) &&
"""
    text = one(text, normal, normal_new, "normal admission")

    clean = """\tif (!fmt || (
\t    strncmp(fmt, "P420", 4) &&
\t    strncmp(fmt, "P419", 4) &&
"""
    clean_new = """\tif (!fmt || (
\t    strncmp(fmt, "P423", 4) &&
\t    strncmp(fmt, "P420", 4) &&
\t    strncmp(fmt, "P419", 4) &&
"""
    text = one(text, clean, clean_new, "Phase402 admission")
    text += "\n/* " + MARK + ": P423 admitted to sequential recorder only. */\n"
    return text


def validate(root: Path) -> None:
    rsc = (root / RSC).read_text(errors="replace")
    rpmh = (root / RPMH).read_text(errors="replace")
    internal = (root / INTERNAL).read_text(errors="replace")
    compat = (root / COMPAT).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")

    joined = "\n".join((rsc, rpmh, internal, compat, rec))
    for token in (
        MARK,
        "bool in_solver_mode;",
        "drv->in_solver_mode = enable;",
        "a52_p423_check_ctrlr_state",
        "return -EBUSY;",
        "a52_p423_solver_refused",
        "a52_rpmh_mode_solver_set_compat",
        "spin_lock_irqsave(&drv->lock, flags);",
        "spin_lock(&drv->client.cache_lock);",
        "drv->client.in_solver_mode = enable;",
        "P423 SOL n=%d en=%u up=%u/%u low=%u/%u loops=%u ref=%d bor=%d",
        "P423 REF n=%d flag=%u st=%u",
        "P423 BORROW n=%d flag=1 ty=%d off=%d use=%u",
        '#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))',
        'strncmp(fmt, "P423", 4)',
    ):
        if token not in joined:
            raise SystemExit("Phase423 validation missing: " + token)

    if "#define rpmh_mode_solver_set(d,e) do{}while(0)" in compat:
        raise SystemExit("Phase423 solver stub still present")

    # TouchGrass checks all public write APIs before allocations/cache work.
    if rpmh.count("a52_p423_check_ctrlr_state(") != 4:
        raise SystemExit("Phase423 expected one checker definition + three RPMh call sites")
    if rpmh.count("a52_p423_check_ctrlr_state(get_rpmh_ctrlr(dev), state);") != 2:
        raise SystemExit("Phase423 async/sync upper solver checks missing")
    if rpmh.count("a52_p423_check_ctrlr_state(ctrlr, state);") != 1:
        raise SystemExit("Phase423 batch upper solver check missing")

    setter = function_slice(
        rsc,
        "int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)",
        "EXPORT_SYMBOL_GPL(a52_rpmh_mode_solver_set_compat);",
    )
    if "if (enable)" not in setter or "tcs_is_free(drv, m)" not in setter:
        raise SystemExit("Phase423 enable path does not wait for borrowed TCS")
    if ("drv->client.in_solver_mode = enable;" not in setter or
            "drv->in_solver_mode = enable;" not in setter):
        raise SystemExit("Phase423 true/false upper+lower solver assignment missing")

    send = function_slice(
        rsc,
        "int rpmh_rsc_send_data(struct rsc_drv *drv, const struct tcs_request *msg)",
        "static int find_slots(",
    )
    if "READ_ONCE(drv->in_solver_mode)" not in send or "return -EBUSY;" not in send:
        raise SystemExit("Phase423 lower solver race guard missing")
    if "a52_p423_note_borrow_solver(drv, tcs);" not in send:
        raise SystemExit("Phase423 borrowed-WAKE trigger diagnostic missing")

    # Deliberately leave TouchGrass's invalidate-before-borrow out of Phase423.
    get_tcs = function_slice(
        rsc,
        "static struct tcs_group *get_tcs_for_msg(struct rsc_drv *drv,",
        "static const struct tcs_request *get_req_from_tcs(",
    )
    if "rpmh_rsc_invalidate" in get_tcs or "tcs_invalidate" in get_tcs:
        raise SystemExit("Phase423 accidentally added invalidate-before-borrow")

    # Phase423 must not touch the already-proven DSI S00-S10 source.
    if "P423" in (root / Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")).read_text(errors="replace"):
        raise SystemExit("Phase423 unexpectedly modified DSI controller")


def apply(root: Path) -> None:
    for rel, patcher in (
        (INTERNAL, patch_internal),
        (COMPAT, patch_compat),
        (RSC, patch_rsc),
        (RPMH, patch_rpmh),
        (REC, patch_rec),
    ):
        p = root / rel
        if not p.is_file():
            raise SystemExit("Phase423 source missing: " + str(rel))
        p.write_text(patcher(p.read_text(errors="replace")))
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
    else:
        apply(ns.root)
    print("Phase423 RPMh solver-mode compatibility: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
