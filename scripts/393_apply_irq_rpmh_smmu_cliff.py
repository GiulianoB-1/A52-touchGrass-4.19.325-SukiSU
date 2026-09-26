#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE393_IRQ_RPMH_SMMU_CLIFF_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
RPMH = Path("drivers/soc/qcom/rpmh-rsc.c")
SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase393 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase393 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase393 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase393 closing brace missing: {sig}")


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    text = one(text,
        "#define A52_R341_INTERVAL_MS 500U\n",
        "#define A52_R341_INTERVAL_MS 10U\n",
        "R341 interval")
    text = one(text,
        "#define A52_R341_LIMIT 30U\n",
        "#define A52_R341_LIMIT 120U\n",
        "R341 limit")

    sig = "static void __used a52_r341_start(void)"
    _, end = function_bounds(text, sig)

    block = r'''

/* A52_PHASE393_IRQ_RPMH_SMMU_CLIFF_V1
 *
 * Phase392 proved the independent scheduler heartbeat survives through
 * 18.344091 s but misses its next 500 ms frontier target (~18.406 s).
 * Arm the already-existing Phase341 raw hardirq sideband only after loop49
 * triggers. CPU0/5/7 pinned hrtimers fire every 10 ms for up to 1.2 s.
 *
 * This raw sideband is independent of kthreads/system_wq/R48 and lives at
 * 0xB1BFC000..0xB1BFF7FF. If these pulses stop at the same instant as P385,
 * timer/IRQ/global AP progress is implicated. If they continue, the problem is
 * above hardirq level (scheduler/task/locking).
 */
static struct task_struct *a52_r393_irq_arm_task;

static int a52_r393_irq_arm_fn(void *unused)
{
	u64 trigger;

	(void)unused;
	while (!kthread_should_stop()) {
		trigger = a52_ackfr_frontier_trigger_ns();
		if (trigger)
			break;
		if (msleep_interruptible(1) && kthread_should_stop())
			return 0;
	}

	if (kthread_should_stop())
		return 0;

	a52_ackfr_record("P393 IRQARM t=%llu int=%u lim=%u",
		(unsigned long long)trigger,
		A52_R341_INTERVAL_MS, A52_R341_LIMIT);
	a52_r341_start();
	return 0;
}

static int __init a52_r393_irq_arm_init(void)
{
	a52_r393_irq_arm_task =
		kthread_run(a52_r393_irq_arm_fn, NULL, "a52_r393_irq");
	if (IS_ERR(a52_r393_irq_arm_task)) {
		a52_ackfr_record("P393 IRQARM err=%ld",
			PTR_ERR(a52_r393_irq_arm_task));
		a52_r393_irq_arm_task = NULL;
	}
	return 0;
}
late_initcall(a52_r393_irq_arm_init);
'''
    return text[:end] + block + text[end:]


def rpmh_helpers() -> str:
    return r'''

/* A52_PHASE393_IRQ_RPMH_SMMU_CLIFF_V1 */
static bool a52_r393_frontier_ms(u64 *elapsed_ms)
{
	u64 start = a52_ackfr_frontier_trigger_ns();
	u64 now;

	if (!start)
		return false;
	now = ktime_get_boottime_ns();
	if (now < start)
		return false;
	*elapsed_ms = div_u64(now - start, NSEC_PER_MSEC);
	return *elapsed_ms >= 200ULL && *elapsed_ms <= 900ULL;
}

static void a52_r393_rpmh_send(const struct tcs_request *msg, int rc, bool done)
{
	u64 ms;

	if (!a52_r393_frontier_ms(&ms) || !msg)
		return;
	if (done) {
		a52_ackfr_record("R393 RET ms=%llu rc=%d st=%u n=%u",
			(unsigned long long)ms, rc, msg->state, msg->num_cmds);
		return;
	}
	if (msg->num_cmds)
		a52_ackfr_record("R393 SEND ms=%llu st=%u n=%u a=%x d=%x w=%u",
			(unsigned long long)ms, msg->state, msg->num_cmds,
			msg->cmds[0].addr, msg->cmds[0].data,
			msg->wait_for_compl ? 1U : 0U);
	else
		a52_ackfr_record("R393 SEND ms=%llu st=%u n=0",
			(unsigned long long)ms, msg->state);
}

static void a52_r393_rpmh_irq(unsigned long status)
{
	u64 ms;

	if (a52_r393_frontier_ms(&ms))
		a52_ackfr_record("R393 IRQ ms=%llu st=%lx",
			(unsigned long long)ms, status);
}
'''


def patch_rpmh(text: str) -> str:
    if MARK in text:
        return text

    inc_anchor = '#include <linux/kernel.h>\n'
    for inc in (
        '#include <linux/ktime.h>\n',
        '#include <linux/a52_ack_secure_flight_recorder.h>\n',
    ):
        if inc not in text:
            text = one(text, inc_anchor, inc_anchor + inc, "RPMh include")

    trace_anchor = '#include "trace-rpmh.h"\n'
    text = one(text, trace_anchor, trace_anchor + rpmh_helpers(), "RPMh helpers")

    sig = "int rpmh_rsc_send_data(struct rsc_drv *drv, const struct tcs_request *msg)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]

    candidates = ["\tdo {\n", "\tmight_sleep();\n", "\ttcs = get_tcs_for_msg(drv, msg);\n"]
    positions = [(fn.find(x), x) for x in candidates if fn.find(x) >= 0]
    if not positions:
        raise SystemExit("Phase393 RPMh send executable anchor missing")
    pos, anchor = min(positions)
    fn = fn[:pos] + '\ta52_r393_rpmh_send(msg, 0, false);\n' + fn[pos:]

    retpos = fn.rfind("\treturn ret;")
    if retpos < 0:
        raise SystemExit("Phase393 RPMh send return missing")
    fn = fn[:retpos] + '\ta52_r393_rpmh_send(msg, ret, true);\n' + fn[retpos:]
    text = text[:start] + fn + text[end:]

    sig = "static irqreturn_t tcs_tx_done(int irq, void *p)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]
    needle = "irq_status = readl_relaxed"
    p = fn.find(needle)
    if p < 0:
        raise SystemExit("Phase393 RPMh IRQ status read missing")
    semi = fn.find(";", p)
    if semi < 0:
        raise SystemExit("Phase393 RPMh IRQ status statement missing")
    semi += 1
    fn = fn[:semi] + "\n\ta52_r393_rpmh_irq(irq_status);" + fn[semi:]
    return text[:start] + fn + text[end:]


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text

    inc_anchor = '#include <linux/kernel.h>\n'
    inc = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if inc not in text:
        text = one(text, inc_anchor, inc_anchor + inc, "SMMU include")

    sig = "static irqreturn_t arm_smmu_context_fault(int irq, void *dev)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]
    p = fn.find("dev_err_ratelimited")
    if p < 0:
        raise SystemExit("Phase393 SMMU context report anchor missing")
    inject = (
        '\ta52_ackfr_record("M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",\n'
        '\t\tirq, idx, fsr, fsynr, iova);\n\n'
    )
    fn = fn[:p] + inject + fn[p:]
    text = text[:start] + fn + text[end:]

    sig = "static irqreturn_t arm_smmu_global_fault(int irq, void *dev)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]
    p = fn.find("dev_err_ratelimited")
    if p < 0:
        raise SystemExit("Phase393 SMMU global report anchor missing")
    inject = (
        '\ta52_ackfr_record("M393 G irq=%d g=%x s0=%x s1=%x s2=%x",\n'
        '\t\tirq, gfsr, gfsynr0, gfsynr1, gfsynr2);\n\n'
    )
    fn = fn[:p] + inject + fn[p:]
    return text[:start] + fn + text[end:]


def validate(root: Path) -> None:
    r = (root / REC).read_text()
    p = (root / RPMH).read_text()
    s = (root / SMMU).read_text()

    for token in (
        MARK,
        "#define A52_R341_INTERVAL_MS 10U",
        "#define A52_R341_LIMIT 120U",
        '"a52_r393_irq"',
        "P393 IRQARM",
        "a52_r341_start();",
    ):
        if token not in r:
            raise SystemExit("Phase393 recorder token missing: " + token)

    for token in (
        MARK,
        "a52_r393_rpmh_send(msg, 0, false)",
        "a52_r393_rpmh_send(msg, ret, true)",
        "a52_r393_rpmh_irq(irq_status)",
        "R393 SEND",
        "R393 RET",
        "R393 IRQ",
    ):
        if token not in p:
            raise SystemExit("Phase393 RPMh token missing: " + token)

    for token in (
        "M393 C irq=%d cb=%d fsr=%x syn=%x iova=%lx",
        "M393 G irq=%d g=%x s0=%x s1=%x s2=%x",
    ):
        if token not in s:
            raise SystemExit("Phase393 SMMU token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    for rel in (REC, RPMH, SMMU):
        if not (root / rel).is_file():
            raise SystemExit("Phase393 source missing: " + str(rel))

    if not ns.check_only:
        p = root / REC
        p.write_text(patch_recorder(p.read_text()))
        p = root / RPMH
        p.write_text(patch_rpmh(p.read_text()))
        p = root / SMMU
        p.write_text(patch_smmu(p.read_text()))

    validate(root)
    print("Phase393 IRQ/RPMh/SMMU cliff discriminator: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
