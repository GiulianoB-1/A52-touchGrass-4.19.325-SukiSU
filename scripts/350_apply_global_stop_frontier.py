#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
STOP = Path("kernel/stop_machine.c")
MARK = "A52_PHASE350_GLOBAL_STOP_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase350 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE350_GLOBAL_STOP_FRONTIER_V1
 *
 * Phase349 proved the failing boot never reaches DRM open/atomic commit/DSI
 * message transmission, while Phase341 still shows CPU0/5/7 all losing their
 * pinned hard-IRQ timers at the same ~12 s frontier.  Probe the generic CPU
 * stopper / stop_machine machinery directly.
 *
 * Dedicated raw pmsg-tail sideband, intentionally separate from Phase340-349:
 *   0xB1BF0000..0xB1BF3FFF, two mirrored 8 KiB copies.
 *
 * Layout is a fixed event x CPU matrix. Every write overwrites the same cell,
 * so the dump contains the LAST occurrence of each event on each CPU before
 * global progress stops. This is especially useful for multi_cpu_stop state.
 */
#define A52_R350_SIDEBAND_PHYS  0xB1BF0000ULL
#define A52_R350_SIDEBAND_BYTES 0x4000U
#define A52_R350_COPY_BYTES     0x2000U
#define A52_R350_SLOT_BYTES     64U
#define A52_R350_EVENT_COUNT    16U
#define A52_R350_CPU_COUNT      8U
#define A52_R350_MAGIC          0x303533504f545347ULL
#define A52_R350_COMMIT         0x350c0de5U

struct a52_r350_slot {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 jiffies64;
	u32 event;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 state;
	u32 arg0;
	u32 commit;
	u32 version;
};

static void *a52_r350_sideband;

void a52_p350_mark(u32 event, u32 arg0)
{
	struct a52_r350_slot slot;
	unsigned int cpu;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r350_sideband))
		return;
	cpu = (unsigned int)raw_smp_processor_id();
	if (event >= A52_R350_EVENT_COUNT || cpu >= A52_R350_CPU_COUNT)
		return;

	BUILD_BUG_ON(sizeof(struct a52_r350_slot) != A52_R350_SLOT_BYTES);
	BUILD_BUG_ON(A52_R350_EVENT_COUNT * A52_R350_CPU_COUNT *
		A52_R350_SLOT_BYTES != A52_R350_COPY_BYTES);

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_R350_MAGIC;
	slot.ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.jiffies64 = get_jiffies_64();
	slot.event = event;
	slot.cpu = cpu;
	slot.pid = (u32)current->pid;
	slot.tgid = (u32)current->tgid;
	slot.state = (u32)preempt_count();
	slot.arg0 = arg0;
	slot.commit = A52_R350_COMMIT;
	slot.version = 1U;

	pos = (event * A52_R350_CPU_COUNT + cpu) * A52_R350_SLOT_BYTES;
	dst0 = (u8 *)a52_r350_sideband + pos;
	dst1 = (u8 *)a52_r350_sideband + A52_R350_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static void a52_r350_start(void)
{
	a52_r350_sideband = memremap(A52_R350_SIDEBAND_PHYS,
		A52_R350_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r350_sideband) {
		a52_ackfr_record("P276 350A map=0");
		return;
	}

	memset(a52_r350_sideband, 0, A52_R350_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_r350_sideband, A52_R350_SIDEBAND_BYTES);
	a52_p350_mark(0U, 0U);
	a52_ackfr_record("P276 350A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase350 requires Phase343 recorder lineage")
    anchor = "static void *a52_r343_sideband;\n"
    if anchor not in text:
        # Keep helper placement stable even if the exact Phase343 local name moves.
        anchor = "#define A52_R343_SIDEBAND_PHYS"
        pos = text.find(anchor)
        if pos < 0:
            raise SystemExit("Phase350 Phase343 insertion anchor missing")
        # Insert before the Phase343 block.
        return patch_rec_fallback(text, pos)
    text = one(text, anchor, RAW_BLOCK + anchor, "raw helper insertion")
    text = one(text, "\ta52_r343_start();\n", "\ta52_r343_start();\n\ta52_r350_start();\n", "late-init start")
    return text


def patch_rec_fallback(text: str, pos: int) -> str:
    text = text[:pos] + RAW_BLOCK + text[pos:]
    text = one(text, "\ta52_r343_start();\n", "\ta52_r343_start();\n\ta52_r350_start();\n", "late-init start")
    return text


def patch_stop(text: str) -> str:
    if MARK in text:
        return text

    include_anchor = "#include <linux/sched/wake_q.h>\n"
    if include_anchor not in text:
        raise SystemExit("Phase350 stop_machine include anchor missing")
    text = one(
        text,
        include_anchor,
        include_anchor +
        "extern void a52_p350_mark(u32 event, u32 arg0); /* " + MARK + " */\n",
        "stop_machine declaration",
    )

    # stop_cpus(): caller-side entry/return. If __stop_cpus() never returns,
    # event 1 survives without event 2.
    text = one(
        text,
        "int stop_cpus(const struct cpumask *cpumask, cpu_stop_fn_t fn, void *arg)\n"
        "{\n\tint ret;\n\n\t/* static works are used, process one request at a time */\n",
        "int stop_cpus(const struct cpumask *cpumask, cpu_stop_fn_t fn, void *arg)\n"
        "{\n\tint ret;\n\n\ta52_p350_mark(1U, (u32)cpumask_weight(cpumask));\n"
        "\t/* static works are used, process one request at a time */\n",
        "stop_cpus enter",
    )
    text = one(
        text,
        "\tret = __stop_cpus(cpumask, fn, arg);\n\tmutex_unlock(&stop_cpus_mutex);\n\treturn ret;\n",
        "\tret = __stop_cpus(cpumask, fn, arg);\n"
        "\ta52_p350_mark(2U, (u32)ret);\n"
        "\tmutex_unlock(&stop_cpus_mutex);\n\treturn ret;\n",
        "stop_cpus exit",
    )

    # multi_cpu_stop(): one instance runs on each stopped CPU. Event 4 is
    # overwritten on every state transition and therefore preserves the LAST
    # state each CPU reached (PREPARE, DISABLE_IRQ, RUN or EXIT).
    text = one(
        text,
        "\tbool is_active;\n\n\t/*\n\t * When called from stop_machine_from_inactive_cpu(), irq might\n",
        "\tbool is_active;\n\n"
        "\ta52_p350_mark(3U, 0U);\n"
        "\t/*\n\t * When called from stop_machine_from_inactive_cpu(), irq might\n",
        "multi_cpu_stop enter",
    )
    text = one(
        text,
        "\t\tif (newstate != curstate) {\n\t\t\tcurstate = newstate;\n\t\t\tswitch (curstate) {\n",
        "\t\tif (newstate != curstate) {\n\t\t\tcurstate = newstate;\n"
        "\t\t\ta52_p350_mark(4U, (u32)curstate);\n"
        "\t\t\tswitch (curstate) {\n",
        "multi_cpu_stop state",
    )
    text = one(
        text,
        "\t} while (curstate != MULTI_STOP_EXIT);\n\n\tlocal_irq_restore(flags);\n\treturn err;\n",
        "\t} while (curstate != MULTI_STOP_EXIT);\n\n"
        "\ta52_p350_mark(5U, (u32)err);\n"
        "\tlocal_irq_restore(flags);\n\treturn err;\n",
        "multi_cpu_stop exit",
    )

    # stop_machine_cpuslocked() and stop_machine() wrapper boundaries.
    text = one(
        text,
        "\tlockdep_assert_cpus_held();\n\n\tif (!stop_machine_initialized) {\n",
        "\ta52_p350_mark(6U, msdata.num_threads);\n"
        "\tlockdep_assert_cpus_held();\n\n\tif (!stop_machine_initialized) {\n",
        "stop_machine_cpuslocked enter",
    )
    text = one(
        text,
        "\t/* No CPUs can come up or down during this. */\n\tcpus_read_lock();\n"
        "\tret = stop_machine_cpuslocked(fn, data, cpus);\n"
        "\tcpus_read_unlock();\n\treturn ret;\n",
        "\t/* No CPUs can come up or down during this. */\n"
        "\ta52_p350_mark(8U, 0U);\n"
        "\tcpus_read_lock();\n\tret = stop_machine_cpuslocked(fn, data, cpus);\n"
        "\tcpus_read_unlock();\n"
        "\ta52_p350_mark(9U, (u32)ret);\n"
        "\treturn ret;\n",
        "stop_machine wrapper",
    )

    # Inactive-CPU hotplug path: entry and return.
    text = one(
        text,
        "\tstruct cpu_stop_done done;\n\tint ret;\n\n"
        "\t/* Local CPU must be inactive and CPU hotplug in progress. */\n",
        "\tstruct cpu_stop_done done;\n\tint ret;\n\n"
        "\ta52_p350_mark(10U, 0U);\n"
        "\t/* Local CPU must be inactive and CPU hotplug in progress. */\n",
        "inactive stop enter",
    )
    text = one(
        text,
        "\tmutex_unlock(&stop_cpus_mutex);\n\treturn ret ?: done.ret;\n}\n",
        "\tmutex_unlock(&stop_cpus_mutex);\n"
        "\ta52_p350_mark(11U, (u32)(ret ?: done.ret));\n"
        "\treturn ret ?: done.ret;\n}\n",
        "inactive stop exit",
    )

    # Generic stopper work. Events 12/13 are overwritten with the latest work
    # on each CPU. If a callback hangs, ENTER survives without a matching EXIT.
    text = one(
        text,
        "\t\tstruct cpu_stop_done *done = work->done;\n\t\tint ret;\n\n"
        "\t\t/* cpu stop callbacks must not sleep, make in_atomic() == T */\n",
        "\t\tstruct cpu_stop_done *done = work->done;\n\t\tint ret;\n\n"
        "\t\ta52_p350_mark(12U, (u32)(unsigned long)fn);\n"
        "\t\t/* cpu stop callbacks must not sleep, make in_atomic() == T */\n",
        "stopper work enter",
    )
    text = one(
        text,
        "\t\tpreempt_count_inc();\n\t\tret = fn(arg);\n\t\tif (done) {\n",
        "\t\tpreempt_count_inc();\n\t\tret = fn(arg);\n"
        "\t\ta52_p350_mark(13U, (u32)ret);\n"
        "\t\tif (done) {\n",
        "stopper work exit",
    )
    return text


def validate(before_rec: str, after_rec: str, before_stop: str, after_stop: str) -> None:
    combined = after_rec + after_stop
    for token in (
        MARK,
        "A52_R350_SIDEBAND_PHYS  0xB1BF0000ULL",
        "A52_R350_SIDEBAND_BYTES 0x4000U",
        "A52_R350_EVENT_COUNT    16U",
        "A52_R350_CPU_COUNT      8U",
        "A52_R350_MAGIC          0x303533504f545347ULL",
        "A52_R350_COMMIT         0x350c0de5U",
        "a52_r350_start();",
        "a52_p350_mark(1U",
        "a52_p350_mark(3U",
        "a52_p350_mark(4U",
        "a52_p350_mark(8U",
        "a52_p350_mark(10U",
        "a52_p350_mark(12U",
        "a52_p350_mark(13U",
    ):
        if token not in combined:
            raise SystemExit("Phase350 required token missing: " + token)

    # No stop-machine synchronization primitive, IRQ operation, wait, state
    # transition, callback invocation or CPU mask is changed.
    for token in (
        "local_irq_disable(", "hard_irq_disable(", "local_irq_restore(",
        "mutex_lock(&stop_cpus_mutex)", "mutex_unlock(&stop_cpus_mutex)",
        "queue_stop_cpus_work(", "wait_for_completion(", "cpu_relax(",
        "set_state(", "ack_state(", "ret = fn(arg);",
        "ret = stop_machine_cpuslocked(fn, data, cpus);",
    ):
        if before_stop.count(token) != after_stop.count(token):
            raise SystemExit("Phase350 changed protected stop-machine token: " + token)

    if after_rec.count("a52_r350_start();") != before_rec.count("a52_r350_start();") + 1:
        raise SystemExit("Phase350 expected exactly one late-init start")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    rp = ns.root / REC
    sp = ns.root / STOP
    if not rp.is_file() or not sp.is_file():
        raise SystemExit("Phase350 source missing")

    r = rp.read_text()
    s = sp.read_text()
    if MARK in r and MARK in s:
        print("Phase350 global-stop frontier audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase350 marker missing in check-only mode")

    nr = patch_rec(r)
    ns_stop = patch_stop(s)
    validate(r, nr, s, ns_stop)
    rp.write_text(nr)
    sp.write_text(ns_stop)
    print("Phase350 global-stop frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
