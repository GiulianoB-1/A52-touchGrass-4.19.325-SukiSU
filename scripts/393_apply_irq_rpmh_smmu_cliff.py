#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE393_IRQ_RPMH_SMMU_CLIFF_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
RPMH = Path("drivers/soc/qcom/rpmh-rsc.c")
SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
PM = Path("drivers/base/power/runtime.c")
CPUIDLE = Path("drivers/cpuidle/cpuidle.c")


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

    # Use the 2 MiB Phase392 pool for reliability as well as depth.
    # Three physical copies still retain 5,450 records, more than twice the
    # complete Phase392 boot history, and allow majority-bit recovery.
    text = one(text,
        "#define A52_P392_CAPACITY\t((A52_P392_BYTES - A52_P392_HEADER_BYTES) / A52_P392_RECORD_BYTES)\n",
        "#define A52_P392_COPIES\t3U\n"
        "#define A52_P392_CAPACITY\t((A52_P392_BYTES - A52_P392_HEADER_BYTES) / (A52_P392_RECORD_BYTES * A52_P392_COPIES))\n"
        "#define A52_P392_COPY_BYTES\t(A52_P392_CAPACITY * A52_P392_RECORD_BYTES)\n",
        "P392 triple-copy geometry")
    text = one(text,
        "#define A52_P392_VERSION\t1U\n",
        "#define A52_P392_VERSION\t2U\n",
        "P392 mirrored-layout version")
    text = one(text,
        "\tu64 dropped;\n\tsize_t len;\n",
        "\tu64 dropped;\n\tsize_t len;\n\tunsigned int copy;\n",
        "P392 copy variable")
    text = one(text,
        "\tslot = (rec.seq - 1ULL) % A52_P392_CAPACITY;\n"
        "\tmemcpy_toio((u8 __iomem *)a52_p392_base + A52_P392_HEADER_BYTES +\n"
        "\t\t    slot * A52_P392_RECORD_BYTES, &rec, sizeof(rec));\n",
        "\tslot = (rec.seq - 1ULL) % A52_P392_CAPACITY;\n"
        "\tfor (copy = 0; copy < A52_P392_COPIES; copy++)\n"
        "\t\tmemcpy_toio((u8 __iomem *)a52_p392_base + A52_P392_HEADER_BYTES +\n"
        "\t\t\t    copy * A52_P392_COPY_BYTES +\n"
        "\t\t\t    slot * A52_P392_RECORD_BYTES, &rec, sizeof(rec));\n",
        "P392 triple-copy write")
    text = one(text,
        "\tBUILD_BUG_ON(A52_P392_HEADER_BYTES +\n"
        "\t\t     A52_P392_CAPACITY * A52_P392_RECORD_BYTES !=\n"
        "\t\t     A52_P392_BYTES);\n",
        "\tBUILD_BUG_ON(A52_P392_HEADER_BYTES +\n"
        "\t\t     A52_P392_COPIES * A52_P392_COPY_BYTES >\n"
        "\t\t     A52_P392_BYTES);\n",
        "P392 triple-copy geometry assertion")

    # Reuse the existing reserved64 field in the Phase341 raw hrtimer sideband.
    # The same jiffies/version sequence also exists in Phase340 and Phase342,
    # so scope this replacement to a52_r341_sideband_write() specifically.
    # Upper 32 bits = interrupted PID; lower bits = preempt count + IRQ-off.
    r341_sig = "static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)"
    r341_start, r341_end = function_bounds(text, r341_sig)
    r341_fn = text[r341_start:r341_end]
    r341_fn = one(r341_fn,
        "\tslot.jiffies64 = get_jiffies_64();\n"
        "\tslot.version = 1U;\n",
        "\tslot.jiffies64 = get_jiffies_64();\n"
        "\tslot.reserved64 = ((u64)(u32)current->pid << 32) |\n"
        "\t\t((u64)(u32)preempt_count() << 1) |\n"
        "\t\t(irqs_disabled() ? 1ULL : 0ULL);\n"
        "\tslot.version = 1U;\n",
        "R341 interrupted task state")
    text = text[:r341_start] + r341_fn + text[r341_end:]

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



def pm_helpers() -> str:
    return r'''

/* A52_PHASE393_PM_IDLE_CLIFF_V1 */
static bool a52_r393_pm_window(u64 *elapsed_ms)
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

static void a52_r393_pm_mark(const char *op, struct device *dev,
			     int flags, int ret, bool done)
{
	u64 ms;

	if (!a52_r393_pm_window(&ms) || !dev)
		return;
	a52_ackfr_record("D393 %s ms=%llu dev=%s fl=%x st=%d use=%d ret=%d done=%u",
		op, (unsigned long long)ms, dev_name(dev), flags,
		dev->power.runtime_status,
		atomic_read(&dev->power.usage_count), ret, done ? 1U : 0U);
}
'''


def patch_pm(text: str) -> str:
    if "A52_PHASE393_PM_IDLE_CLIFF_V1" in text:
        return text

    inc_anchor = "#include <linux/pm_runtime.h>\n"
    if inc_anchor not in text:
        inc_anchor = "#include <linux/device.h>\n"
    for inc in (
        "#include <linux/ktime.h>\n",
        "#include <linux/a52_ack_secure_flight_recorder.h>\n",
    ):
        if inc not in text:
            text = one(text, inc_anchor, inc_anchor + inc, "runtime PM include")

    marker = "static int rpm_idle(struct device *dev, int rpmflags)"
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit("Phase393 runtime PM insertion point missing")
    text = text[:pos] + pm_helpers() + text[pos:]

    for sig, call, op in (
        ("int __pm_runtime_suspend(struct device *dev, int rpmflags)",
         "retval = rpm_suspend(dev, rpmflags);", "S"),
        ("int __pm_runtime_resume(struct device *dev, int rpmflags)",
         "retval = rpm_resume(dev, rpmflags);", "R"),
    ):
        start, end = function_bounds(text, sig)
        fn = text[start:end]
        if call not in fn:
            raise SystemExit("Phase393 runtime PM call missing in " + sig)
        repl = (
            f'a52_r393_pm_mark("{op}+", dev, rpmflags, 0, false);\n\t'
            + call +
            f'\n\ta52_r393_pm_mark("{op}-", dev, rpmflags, retval, true);'
        )
        fn = fn.replace(call, repl, 1)
        text = text[:start] + fn + text[end:]
    return text


def cpuidle_helpers() -> str:
    return r'''

/* A52_PHASE393_PM_IDLE_CLIFF_V1 */
static bool a52_r393_idle_window(u64 *elapsed_ms)
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
'''


def patch_cpuidle(text: str) -> str:
    if "A52_PHASE393_PM_IDLE_CLIFF_V1" in text:
        return text

    inc_anchor = "#include <linux/cpuidle.h>\n"
    for inc in (
        "#include <linux/ktime.h>\n",
        "#include <linux/a52_ack_secure_flight_recorder.h>\n",
    ):
        if inc not in text:
            text = one(text, inc_anchor, inc_anchor + inc, "cpuidle include")

    sig = "int cpuidle_enter_state(struct cpuidle_device *dev, struct cpuidle_driver *drv,"
    start, end = function_bounds(text, sig)
    text = text[:start] + cpuidle_helpers() + text[start:]
    start, end = function_bounds(text, sig)
    fn = text[start:end]

    modern = "entered_state = target_state->enter(dev, drv, index);"
    if modern in fn:
        before = (
            'do { u64 a52_ms; if (index >= 2 && a52_r393_idle_window(&a52_ms))\n'
            '\t\ta52_ackfr_record("I393 E ms=%llu cpu=%u st=%d fl=%x",\n'
            '\t\t\t(unsigned long long)a52_ms, dev->cpu, index, target_state->flags); } while (0);\n\t'
        )
        after = (
            '\n\tdo { u64 a52_ms; if (index >= 2 && a52_r393_idle_window(&a52_ms))\n'
            '\t\ta52_ackfr_record("I393 X ms=%llu cpu=%u st=%d ret=%d",\n'
            '\t\t\t(unsigned long long)a52_ms, dev->cpu, index, entered_state); } while (0);'
        )
        fn = fn.replace(modern, before + modern + after, 1)
    else:
        legacy = "entered_state = cpuidle_enter_ops(dev, drv, next_state);"
        if legacy not in fn:
            raise SystemExit("Phase393 cpuidle enter call missing")
        before = (
            'do { u64 a52_ms; if (next_state >= 2 && a52_r393_idle_window(&a52_ms))\n'
            '\t\ta52_ackfr_record("I393 E ms=%llu cpu=%u st=%d",\n'
            '\t\t\t(unsigned long long)a52_ms, dev->cpu, next_state); } while (0);\n\t'
        )
        after = (
            '\n\tdo { u64 a52_ms; if (next_state >= 2 && a52_r393_idle_window(&a52_ms))\n'
            '\t\ta52_ackfr_record("I393 X ms=%llu cpu=%u st=%d ret=%d",\n'
            '\t\t\t(unsigned long long)a52_ms, dev->cpu, next_state, entered_state); } while (0);'
        )
        fn = fn.replace(legacy, before + legacy + after, 1)
    return text[:start] + fn + text[end:]

def validate(root: Path) -> None:
    r = (root / REC).read_text()
    p = (root / RPMH).read_text()
    s = (root / SMMU).read_text()
    pm = (root / PM).read_text()
    idle = (root / CPUIDLE).read_text()

    for token in (
        MARK,
        "#define A52_R341_INTERVAL_MS 10U",
        "#define A52_R341_LIMIT 120U",
        '"a52_r393_irq"',
        "P393 IRQARM",
        "a52_r341_start();",
        "#define A52_P392_COPIES\t3U",
        "#define A52_P392_VERSION\t2U",
        "copy * A52_P392_COPY_BYTES",
        "current->pid << 32",
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


    for token in (
        "A52_PHASE393_PM_IDLE_CLIFF_V1",
        'a52_r393_pm_mark("S+"',
        'a52_r393_pm_mark("S-"',
        'a52_r393_pm_mark("R+"',
        'a52_r393_pm_mark("R-"',
        "D393 %s ms=%llu dev=%s",
    ):
        if token not in pm:
            raise SystemExit("Phase393 runtime PM token missing: " + token)

    for token in (
        "A52_PHASE393_PM_IDLE_CLIFF_V1",
        "I393 E ms=%llu",
        "I393 X ms=%llu",
    ):
        if token not in idle:
            raise SystemExit("Phase393 cpuidle token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    for rel in (REC, RPMH, SMMU, PM, CPUIDLE):
        if not (root / rel).is_file():
            raise SystemExit("Phase393 source missing: " + str(rel))

    if not ns.check_only:
        p = root / REC
        p.write_text(patch_recorder(p.read_text()))
        p = root / RPMH
        p.write_text(patch_rpmh(p.read_text()))
        p = root / SMMU
        p.write_text(patch_smmu(p.read_text()))
        p = root / PM
        p.write_text(patch_pm(p.read_text()))
        p = root / CPUIDLE
        p.write_text(patch_cpuidle(p.read_text()))

    validate(root)
    print("Phase393 causal cliff capture: IRQ/RPMh/SMMU/PM/cpuidle + triple persistence: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
