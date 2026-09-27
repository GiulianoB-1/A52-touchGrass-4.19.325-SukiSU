#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE400_IRQ_BUSY_WITNESS_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
IRQH = Path("kernel/irq/handle.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase400 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if MARK in text:
        return text
    anchor = "void a52_ackfr_noc_irq_hit(unsigned int irq);\n"
    return one(text, anchor, anchor + (
        "\n/* " + MARK + " */\n"
        "void a52_ackfr_irq_witness(unsigned int irq, unsigned long hwirq);\n"
    ), "header declaration")


def patch_irq(text: str) -> str:
    if MARK in text:
        return text
    anchor = "\t/* A52_PHASE396_NOC_IRQ_EVIDENCE_V1 */\n\ta52_ackfr_noc_irq_hit(irq);\n"
    repl = (
        "\t/* " + MARK + " */\n"
        "\ta52_ackfr_irq_witness(irq, (unsigned long)desc->irq_data.hwirq);\n"
        "\t/* A52_PHASE396_NOC_IRQ_EVIDENCE_V1 */\n"
        "\ta52_ackfr_noc_irq_hit(irq);\n"
    )
    return one(text, anchor, repl, "generic IRQ hook")


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE399_FIXED_HARDIRQ_WITNESS_V1" not in text:
        raise SystemExit("Phase400 requires Phase399")

    # Phase400 reads CNTVCT directly in the non-interrupt busy witness.
    if "#include <asm/sysreg.h>\n" not in text[:2000]:
        text = one(text, "#include <asm/cacheflush.h>\n",
                   "#include <asm/cacheflush.h>\n#include <asm/sysreg.h>\n",
                   "sysreg include")
    if "#include <uapi/linux/sched/types.h>\n" not in text[:2500]:
        text = one(text, "#include <linux/sched/stat.h>\n",
                   "#include <linux/sched/stat.h>\n#include <uapi/linux/sched/types.h>\n",
                   "sched types include")

    old = """#define A52_P399_WITNESS_SLOT_BYTES 64U
#define A52_P399_WITNESS_SLOTS 3U
#define A52_P399_WITNESS_BYTES (A52_P399_WITNESS_SLOT_BYTES * A52_P399_WITNESS_SLOTS)
#define A52_R341_RING_BYTES (A52_R341_COPY_BYTES - A52_P399_WITNESS_BYTES)
#define A52_R341_SLOTS_PER_COPY (A52_R341_RING_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
#define A52_P399_WITNESS_MAGIC 0x3939335749525148ULL
#define A52_P399_WITNESS_COMMIT 0x399c0de5U
"""
    new = """#define A52_P399_WITNESS_SLOT_BYTES 64U
#define A52_P399_WITNESS_SLOTS 3U
#define A52_P399_WITNESS_BYTES (A52_P399_WITNESS_SLOT_BYTES * A52_P399_WITNESS_SLOTS)
/* A52_PHASE400_IRQ_BUSY_WITNESS_V1
 * Keep Phase399 timer witnesses, then reserve eight fixed generic-IRQ slots
 * and one fixed non-interrupt CPU-execution slot in each R341 copy.
 */
#define A52_P400_IRQ_SLOT_BYTES 64U
#define A52_P400_IRQ_SLOTS 8U
#define A52_P400_IRQ_BYTES (A52_P400_IRQ_SLOT_BYTES * A52_P400_IRQ_SLOTS)
#define A52_P400_BUSY_SLOT_BYTES 64U
#define A52_P400_BUSY_BYTES A52_P400_BUSY_SLOT_BYTES
#define A52_R341_FIXED_BYTES (A52_P399_WITNESS_BYTES + A52_P400_IRQ_BYTES + A52_P400_BUSY_BYTES)
#define A52_R341_RING_BYTES (A52_R341_COPY_BYTES - A52_R341_FIXED_BYTES)
#define A52_R341_SLOTS_PER_COPY (A52_R341_RING_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
#define A52_P399_WITNESS_MAGIC 0x3939335749525148ULL
#define A52_P399_WITNESS_COMMIT 0x399c0de5U
#define A52_P400_IRQ_MAGIC 0x3030345152495248ULL
#define A52_P400_IRQ_COMMIT 0x40010de5U
#define A52_P400_BUSY_MAGIC 0x3030345953554248ULL
#define A52_P400_BUSY_COMMIT 0x40020de5U
#define A52_P400_IRQ_START_NS 16000000000ULL
#define A52_P400_IRQ_END_NS   18000000000ULL
#define A52_P400_BUSY_STRIDE  (1ULL << 20)
#define A52_P400_BUSY_LIMIT   512U
#define A52_P400_BUSY_CPU     5U
"""
    text = one(text, old, new, "R341 fixed geometry")

    anchor = """static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)
"""
    block = r'''/* A52_PHASE400_IRQ_BUSY_WITNESS_V1 */
struct a52_p400_irq_witness {
	u64 magic;
	u64 monotonic_ns;
	u64 cntpct;
	u64 r48_sequence;
	u32 irq;
	u32 hwirq;
	u32 cpu;
	u32 count;
	u32 preempt_count;
	u32 irqs_disabled;
	u32 version;
	u32 commit;
} __packed;

struct a52_p400_busy_witness {
	u64 magic;
	u64 iteration;
	u64 cntpct;
	u64 boottime_ns;
	u64 r48_sequence;
	u32 cpu;
	u32 checkpoint;
	u32 preempt_count;
	u32 irqs_disabled;
	u32 version;
	u32 commit;
} __packed;

static DEFINE_PER_CPU(u32, a52_p400_irq_count);
static struct task_struct *a52_p400_busy_task;

static void *a52_p400_copy_ptr(unsigned int copy, unsigned int offset)
{
	return (u8 *)a52_r341_sideband + copy * A52_R341_COPY_BYTES + offset;
}

void a52_ackfr_irq_witness(unsigned int irq, unsigned long hwirq)
{
	struct a52_p400_irq_witness w;
	u64 ns;
	u32 cpu;
	u32 count;
	unsigned int offset;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r341_sideband))
		return;
	ns = ktime_get_boottime_ns();
	if (ns < A52_P400_IRQ_START_NS || ns > A52_P400_IRQ_END_NS)
		return;
	cpu = (u32)raw_smp_processor_id();
	if (cpu >= A52_P400_IRQ_SLOTS)
		return;
	count = ++per_cpu(a52_p400_irq_count, cpu);

	memset(&w, 0, sizeof(w));
	w.magic = A52_P400_IRQ_MAGIC;
	w.monotonic_ns = ns;
	w.cntpct = read_sysreg(cntpct_el0);
	w.r48_sequence = (u64)atomic64_read(&a52_r179_sequence);
	w.irq = irq;
	w.hwirq = (u32)hwirq;
	w.cpu = cpu;
	w.count = count;
	w.preempt_count = (u32)preempt_count();
	w.irqs_disabled = irqs_disabled() ? 1U : 0U;
	w.version = 1U;
	w.commit = A52_P400_IRQ_COMMIT;

	BUILD_BUG_ON(sizeof(struct a52_p400_irq_witness) != A52_P400_IRQ_SLOT_BYTES);
	offset = A52_R341_RING_BYTES + A52_P399_WITNESS_BYTES +
		cpu * A52_P400_IRQ_SLOT_BYTES;
	dst0 = a52_p400_copy_ptr(0U, offset);
	dst1 = a52_p400_copy_ptr(1U, offset);
	memcpy(dst0, &w, sizeof(w));
	memcpy(dst1, &w, sizeof(w));
	wmb();
	__flush_dcache_area(dst0, sizeof(w));
	__flush_dcache_area(dst1, sizeof(w));
}
EXPORT_SYMBOL_GPL(a52_ackfr_irq_witness);

static void a52_p400_busy_write(u64 iteration, u32 checkpoint)
{
	struct a52_p400_busy_witness w;
	unsigned int offset;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r341_sideband))
		return;
	memset(&w, 0, sizeof(w));
	w.magic = A52_P400_BUSY_MAGIC;
	w.iteration = iteration;
	w.cntpct = read_sysreg(cntpct_el0);
	w.boottime_ns = ktime_get_boottime_ns();
	w.r48_sequence = (u64)atomic64_read(&a52_r179_sequence);
	w.cpu = (u32)raw_smp_processor_id();
	w.checkpoint = checkpoint;
	w.preempt_count = (u32)preempt_count();
	w.irqs_disabled = irqs_disabled() ? 1U : 0U;
	w.version = 1U;
	w.commit = A52_P400_BUSY_COMMIT;

	BUILD_BUG_ON(sizeof(struct a52_p400_busy_witness) != A52_P400_BUSY_SLOT_BYTES);
	offset = A52_R341_RING_BYTES + A52_P399_WITNESS_BYTES +
		A52_P400_IRQ_BYTES;
	dst0 = a52_p400_copy_ptr(0U, offset);
	dst1 = a52_p400_copy_ptr(1U, offset);
	memcpy(dst0, &w, sizeof(w));
	memcpy(dst1, &w, sizeof(w));
	wmb();
	__flush_dcache_area(dst0, sizeof(w));
	__flush_dcache_area(dst1, sizeof(w));
}

static int a52_p400_busy_fn(void *unused)
{
	struct sched_param sp = { .sched_priority = 1 };
	u64 iteration = 0ULL;
	u64 next = A52_P400_BUSY_STRIDE;
	u64 start_cnt;
	u64 cntfrq;
	u64 stop_delta;
	u32 checkpoint = 0U;

	(void)unused;
	sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);
	start_cnt = read_sysreg(cntpct_el0);
	cntfrq = read_sysreg(cntfrq_el0);
	stop_delta = cntfrq ? cntfrq * 2ULL : 0ULL;
	a52_p400_busy_write(0ULL, 0U);

	while (!kthread_should_stop() && checkpoint < A52_P400_BUSY_LIMIT) {
		iteration++;
		if (unlikely(iteration == next)) {
			u64 now_cnt;

			checkpoint++;
			a52_p400_busy_write(iteration, checkpoint);
			now_cnt = read_sysreg(cntpct_el0);
			if (stop_delta && now_cnt - start_cnt >= stop_delta)
				break;
			next += A52_P400_BUSY_STRIDE;
		}
		cpu_relax();
	}

	a52_p400_busy_write(iteration, checkpoint | 0x80000000U);
	return 0;
}

static void a52_p400_start_busy(void)
{
	if (READ_ONCE(a52_p400_busy_task))
		return;
	a52_p400_busy_task = kthread_create(a52_p400_busy_fn, NULL, "a52-p400");
	if (IS_ERR(a52_p400_busy_task)) {
		a52_p400_busy_task = NULL;
		return;
	}
	if (A52_P400_BUSY_CPU < nr_cpu_ids && cpu_online(A52_P400_BUSY_CPU))
		kthread_bind(a52_p400_busy_task, A52_P400_BUSY_CPU);
	wake_up_process(a52_p400_busy_task);
}

'''
    text = one(text, anchor, block + anchor, "Phase400 helpers")

    old = """\tBUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES % A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES + A52_P399_WITNESS_BYTES !=
\t\t     A52_R341_COPY_BYTES);

\ta52_r341_sideband = memremap(A52_R341_SIDEBAND_PHYS,
"""
    new = """\tBUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES % A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES + A52_R341_FIXED_BYTES !=
\t\t     A52_R341_COPY_BYTES);

\ta52_r341_sideband = memremap(A52_R341_SIDEBAND_PHYS,
"""
    text = one(text, old, new, "geometry assertion")

    old = """\tmemset((u8 *)a52_r341_sideband + A52_R341_RING_BYTES, 0,
\t       A52_P399_WITNESS_BYTES);
\tmemset((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t       A52_R341_RING_BYTES, 0, A52_P399_WITNESS_BYTES);
\twmb();
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_RING_BYTES,
\t\t\t    A52_P399_WITNESS_BYTES);
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t\t\t    A52_R341_RING_BYTES, A52_P399_WITNESS_BYTES);
"""
    new = """\tmemset((u8 *)a52_r341_sideband + A52_R341_RING_BYTES, 0,
\t       A52_R341_FIXED_BYTES);
\tmemset((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t       A52_R341_RING_BYTES, 0, A52_R341_FIXED_BYTES);
\twmb();
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_RING_BYTES,
\t\t\t    A52_R341_FIXED_BYTES);
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t\t\t    A52_R341_RING_BYTES, A52_R341_FIXED_BYTES);
"""
    text = one(text, old, new, "fixed area clear")

    old = """\ta52_r341_sideband_write(A52_R341_EVT_ARM,
\t\t(u32)smp_processor_id(), mask);
\ta52_ackfr_record("P276 341A map=1 mask=%x int=%u lim=%u",
"""
    new = """\ta52_r341_sideband_write(A52_R341_EVT_ARM,
\t\t(u32)smp_processor_id(), mask);
\ta52_p400_start_busy();
\ta52_ackfr_record("P276 341A map=1 mask=%x int=%u lim=%u",
"""
    text = one(text, old, new, "busy start")

    text = one(text,
        '"BOOT rs=ready phase=399 focus=irq-witness roots=%u copies=2 crc=crc32c"',
        '"BOOT rs=ready phase=400 focus=irq-busy roots=%u copies=2 crc=crc32c"',
        "boot identity")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    hdr = (root / HDR).read_text(errors="replace")
    irq = (root / IRQH).read_text(errors="replace")
    for token in (
        MARK,
        "A52_P400_IRQ_BYTES",
        "A52_P400_BUSY_BYTES",
        "A52_R341_FIXED_BYTES",
        "A52_P400_IRQ_MAGIC",
        "A52_P400_BUSY_MAGIC",
        "a52_ackfr_irq_witness",
        "a52_p400_start_busy();",
        "read_sysreg(cntpct_el0)",
        "A52_P400_BUSY_STRIDE",
        "BOOT rs=ready phase=400 focus=irq-busy roots=%u copies=2 crc=crc32c",
    ):
        if token not in rec:
            raise SystemExit("Phase400 recorder token missing: " + token)
    if "void a52_ackfr_irq_witness(unsigned int irq, unsigned long hwirq);" not in hdr:
        raise SystemExit("Phase400 header declaration missing")
    if "a52_ackfr_irq_witness(irq, (unsigned long)desc->irq_data.hwirq);" not in irq:
        raise SystemExit("Phase400 generic IRQ hook missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root
    for rel in (REC, HDR, IRQH):
        if not (root / rel).is_file():
            raise SystemExit("Phase400 source missing: " + str(rel))
    if not ns.check_only:
        p = root / REC; p.write_text(patch_rec(p.read_text(errors="replace")))
        p = root / HDR; p.write_text(patch_header(p.read_text(errors="replace")))
        p = root / IRQH; p.write_text(patch_irq(p.read_text(errors="replace")))
    validate(root)
    print("Phase400 generic-IRQ + busy CPU witness: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())