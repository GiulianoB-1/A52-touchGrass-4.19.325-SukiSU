#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK="A52_PHASE400_IRQ_EXEC_WITNESS_V1"
REC=Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
HDR=Path("include/linux/a52_ack_secure_flight_recorder.h")
IRQH=Path("kernel/irq/handle.c")

def one(t,o,n,l):
    c=t.count(o)
    if c!=1: raise SystemExit(f"Phase400 {l}: expected 1 anchor, found {c}")
    return t.replace(o,n,1)

def patch_header(t):
    if MARK in t: return t
    a="void a52_ackfr_noc_irq_hit(unsigned int irq);\n"
    return one(t,a,a+"\n/* "+MARK+" */\nvoid a52_ackfr_irq_witness(unsigned int irq);\n","header")

def patch_irq(t):
    if MARK in t: return t
    a="\t/* A52_PHASE396_NOC_IRQ_EVIDENCE_V1 */\n\ta52_ackfr_noc_irq_hit(irq);\n\n\trecord_irq_time(desc);\n"
    n="\t/* A52_PHASE396_NOC_IRQ_EVIDENCE_V1 */\n\ta52_ackfr_noc_irq_hit(irq);\n\t/* "+MARK+" */\n\ta52_ackfr_irq_witness(irq);\n\n\trecord_irq_time(desc);\n"
    return one(t,a,n,"generic IRQ hook")

def patch_rec(t):
    if MARK in t: return t
    if "A52_PHASE399_FIXED_HARDIRQ_WITNESS_V1" not in t:
        raise SystemExit("Phase400 requires Phase399")

    old="""#define A52_P399_WITNESS_SLOT_BYTES 64U
#define A52_P399_WITNESS_SLOTS 3U
#define A52_P399_WITNESS_BYTES (A52_P399_WITNESS_SLOT_BYTES * A52_P399_WITNESS_SLOTS)
#define A52_R341_RING_BYTES (A52_R341_COPY_BYTES - A52_P399_WITNESS_BYTES)
#define A52_R341_SLOTS_PER_COPY (A52_R341_RING_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
#define A52_P399_WITNESS_MAGIC 0x3939335749525148ULL
#define A52_P399_WITNESS_COMMIT 0x399c0de5U
"""
    new="""#define A52_P399_WITNESS_SLOT_BYTES 64U
#define A52_P399_WITNESS_SLOTS 3U
#define A52_P399_WITNESS_BYTES (A52_P399_WITNESS_SLOT_BYTES * A52_P399_WITNESS_SLOTS)
/* A52_PHASE400_IRQ_EXEC_WITNESS_V1
 * Slots 0..2: last generic IRQ on CPU0/5/7.
 * Slot 3: CPU7 non-interrupt busy-poll execution witness.
 */
#define A52_P400_WITNESS_SLOT_BYTES 64U
#define A52_P400_WITNESS_SLOTS 4U
#define A52_P400_WITNESS_BYTES (A52_P400_WITNESS_SLOT_BYTES * A52_P400_WITNESS_SLOTS)
#define A52_R341_RING_BYTES (A52_R341_COPY_BYTES - A52_P400_WITNESS_BYTES - A52_P399_WITNESS_BYTES)
#define A52_R341_SLOTS_PER_COPY (A52_R341_RING_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
#define A52_P399_WITNESS_MAGIC 0x3939335749525148ULL
#define A52_P399_WITNESS_COMMIT 0x399c0de5U
#define A52_P400_IRQ_MAGIC 0x3030345152495741ULL
#define A52_P400_IRQ_COMMIT 0x4001c0deU
#define A52_P400_EXEC_MAGIC 0x3030344345585741ULL
#define A52_P400_EXEC_COMMIT 0x4002c0deU
"""
    t=one(t,old,new,"geometry")

    # Keep Phase399 fixed witnesses pinned to the final 192 bytes of each copy.
    old="""\tdst0 = (u8 *)a52_r341_sideband + A52_R341_RING_BYTES +
\t\t(unsigned int)slot * A52_P399_WITNESS_SLOT_BYTES;
\tdst1 = (u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t\tA52_R341_RING_BYTES +
\t\t(unsigned int)slot * A52_P399_WITNESS_SLOT_BYTES;
"""
    new="""\tdst0 = (u8 *)a52_r341_sideband + A52_R341_COPY_BYTES -
\t\tA52_P399_WITNESS_BYTES +
\t\t(unsigned int)slot * A52_P399_WITNESS_SLOT_BYTES;
\tdst1 = (u8 *)a52_r341_sideband + 2U * A52_R341_COPY_BYTES -
\t\tA52_P399_WITNESS_BYTES +
\t\t(unsigned int)slot * A52_P399_WITNESS_SLOT_BYTES;
"""
    t=one(t,old,new,"P399 address")

    a="""struct a52_p399_witness {
\tu64 magic;
\tu64 monotonic_ns;
\tu64 jiffies64;
\tu64 r48_sequence;
\tu32 cpu;
\tu32 tick;
\tu32 tick_inv;
\tu32 slot;
\tu32 version;
\tu32 commit;
\tu32 preempt_count;
\tu32 irqs_disabled;
} __packed;

static void *a52_r341_sideband;
"""
    n="""struct a52_p399_witness {
\tu64 magic;
\tu64 monotonic_ns;
\tu64 jiffies64;
\tu64 r48_sequence;
\tu32 cpu;
\tu32 tick;
\tu32 tick_inv;
\tu32 slot;
\tu32 version;
\tu32 commit;
\tu32 preempt_count;
\tu32 irqs_disabled;
} __packed;

struct a52_p400_irq_witness {
\tu64 magic;
\tu64 cntpct;
\tu64 r48_sequence;
\tu32 cpu;
\tu32 irq;
\tu32 hwirq;
\tu32 count;
\tu32 cntfrq;
\tu32 preempt_count;
\tu32 irqs_disabled;
\tu32 version;
\tu32 commit;
\tu32 reserved;
} __packed;

struct a52_p400_exec_witness {
\tu64 magic;
\tu64 cntpct;
\tu64 loops;
\tu64 start_cntpct;
\tu32 cpu;
\tu32 sequence;
\tu32 sequence_inv;
\tu32 cntfrq;
\tu32 preempt_count;
\tu32 irqs_disabled;
\tu32 version;
\tu32 commit;
} __packed;

static void *a52_r341_sideband;
"""
    t=one(t,a,n,"structs")

    a="""static atomic_t a52_r341_sideband_index = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_r341_cpu_timer, a52_r341_timers);

static int a52_p399_witness_slot(u32 cpu)
"""
    n="""static atomic_t a52_r341_sideband_index = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_r341_cpu_timer, a52_r341_timers);
static DEFINE_PER_CPU(u32, a52_p400_irq_count);
static atomic_t a52_p400_irq_armed = ATOMIC_INIT(0);
static struct task_struct *a52_p400_exec_task;

static __always_inline u64 a52_p400_cntpct(void)
{
\tu64 v;
\tasm volatile("isb; mrs %0, cntpct_el0" : "=r" (v));
\treturn v;
}

static __always_inline u32 a52_p400_cntfrq(void)
{
\tu64 v;
\tasm volatile("mrs %0, cntfrq_el0" : "=r" (v));
\treturn (u32)v;
}

static int a52_p400_cpu_slot(u32 cpu)
{
\tif (cpu == 0U) return 0;
\tif (cpu == 5U) return 1;
\tif (cpu == 7U) return 2;
\treturn -1;
}

static int a52_p399_witness_slot(u32 cpu)
"""
    t=one(t,a,n,"state")

    a="static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)\n"
    block=r'''static void a52_p400_fixed_write(unsigned int slot, const void *src)
{
	void *dst0, *dst1;

	if (!READ_ONCE(a52_r341_sideband) || slot >= A52_P400_WITNESS_SLOTS)
		return;
	dst0 = (u8 *)a52_r341_sideband + A52_R341_RING_BYTES +
		slot * A52_P400_WITNESS_SLOT_BYTES;
	dst1 = (u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
		A52_R341_RING_BYTES + slot * A52_P400_WITNESS_SLOT_BYTES;
	memcpy(dst0, src, A52_P400_WITNESS_SLOT_BYTES);
	memcpy(dst1, src, A52_P400_WITNESS_SLOT_BYTES);
	wmb();
	__flush_dcache_area(dst0, A52_P400_WITNESS_SLOT_BYTES);
	__flush_dcache_area(dst1, A52_P400_WITNESS_SLOT_BYTES);
}

void a52_ackfr_irq_witness(unsigned int irq)
{
	struct a52_p400_irq_witness w;
	struct irq_desc *desc;
	struct irq_data *d;
	u32 *counter;
	u32 cpu = (u32)raw_smp_processor_id();
	int slot;

	if (!atomic_read(&a52_p400_irq_armed))
		return;
	slot = a52_p400_cpu_slot(cpu);
	if (slot < 0)
		return;
	desc = irq_to_desc(irq);
	if (!desc)
		return;
	d = irq_desc_get_irq_data(desc);
	counter = this_cpu_ptr(&a52_p400_irq_count);
	++*counter;

	memset(&w, 0, sizeof(w));
	w.magic = A52_P400_IRQ_MAGIC;
	w.cntpct = a52_p400_cntpct();
	w.r48_sequence = (u64)atomic64_read(&a52_r179_sequence);
	w.cpu = cpu;
	w.irq = irq;
	w.hwirq = (u32)d->hwirq;
	w.count = *counter;
	w.cntfrq = a52_p400_cntfrq();
	w.preempt_count = (u32)preempt_count();
	w.irqs_disabled = irqs_disabled() ? 1U : 0U;
	w.version = 1U;
	w.commit = A52_P400_IRQ_COMMIT;
	BUILD_BUG_ON(sizeof(w) != A52_P400_WITNESS_SLOT_BYTES);
	a52_p400_fixed_write((unsigned int)slot, &w);
}
EXPORT_SYMBOL_GPL(a52_ackfr_irq_witness);

static void a52_p400_exec_write(u64 start, u64 loops, u32 sequence)
{
	struct a52_p400_exec_witness w;

	memset(&w, 0, sizeof(w));
	w.magic = A52_P400_EXEC_MAGIC;
	w.cntpct = a52_p400_cntpct();
	w.loops = loops;
	w.start_cntpct = start;
	w.cpu = (u32)raw_smp_processor_id();
	w.sequence = sequence;
	w.sequence_inv = ~sequence;
	w.cntfrq = a52_p400_cntfrq();
	w.preempt_count = (u32)preempt_count();
	w.irqs_disabled = irqs_disabled() ? 1U : 0U;
	w.version = 1U;
	w.commit = A52_P400_EXEC_COMMIT;
	BUILD_BUG_ON(sizeof(w) != A52_P400_WITNESS_SLOT_BYTES);
	a52_p400_fixed_write(3U, &w);
}

static int a52_p400_exec_fn(void *unused)
{
	u64 start, end, cnt, next, loops = 0;
	u64 freq, step;
	u32 sequence = 0;
	int rc;

	(void)unused;
	rc = set_cpus_allowed_ptr(current, cpumask_of(7));
	a52_ackfr_record("P400 EXEC affinity cpu=7 rc=%d", rc);
	if (rc)
		return 0;

	while (div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC) < 16350ULL) {
		if (kthread_should_stop())
			return 0;
		msleep(5);
	}

	atomic_set(&a52_p400_irq_armed, 1);
	while (div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC) < 16400ULL)
		cpu_relax();

	freq = (u64)a52_p400_cntfrq();
	if (!freq)
		freq = 19200000ULL;
	step = div_u64(freq, 1000ULL);
	if (!step)
		step = 1ULL;

	/*
	 * Intentional interventional discriminator.  CPU7 executes continuously
	 * with preemption disabled and hard IRQs enabled.  The witness is driven
	 * by raw instruction execution and CNTPCT, not by a timer interrupt.
	 */
	preempt_disable();
	start = a52_p400_cntpct();
	end = start + div_u64(freq * 600ULL, 1000ULL);
	next = start;
	for (;;) {
		loops++;
		cnt = a52_p400_cntpct();
		if ((s64)(cnt - next) >= 0 ||
		    !(loops & ((1ULL << 20) - 1ULL))) {
			sequence++;
			a52_p400_exec_write(start, loops, sequence);
			if ((s64)(cnt - next) >= 0)
				next = cnt + step;
		}
		if ((s64)(cnt - end) >= 0)
			break;
		cpu_relax();
	}
	sequence++;
	a52_p400_exec_write(start, loops, sequence);
	preempt_enable();

	atomic_set(&a52_p400_irq_armed, 0);
	a52_ackfr_record("P400 EXEC done loops=%llu seq=%u",
		(unsigned long long)loops, sequence);
	return 0;
}

static int __init a52_p400_exec_init(void)
{
	a52_p400_exec_task =
		kthread_run(a52_p400_exec_fn, NULL, "a52_p400_exec");
	if (IS_ERR(a52_p400_exec_task)) {
		a52_ackfr_record("P400 EXEC err=%ld",
			PTR_ERR(a52_p400_exec_task));
		a52_p400_exec_task = NULL;
	}
	return 0;
}
late_initcall(a52_p400_exec_init);

'''
    t=one(t,a,block+a,"helpers")

    old="""\tBUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES % A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES + A52_P399_WITNESS_BYTES !=
\t\t     A52_R341_COPY_BYTES);
"""
    new="""\tBUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES % A52_R341_SLOT_BYTES);
\tBUILD_BUG_ON(A52_R341_RING_BYTES + A52_P400_WITNESS_BYTES +
\t\t     A52_P399_WITNESS_BYTES != A52_R341_COPY_BYTES);
"""
    t=one(t,old,new,"geometry assert")

    old="""\tmemset((u8 *)a52_r341_sideband + A52_R341_RING_BYTES, 0,
\t       A52_P399_WITNESS_BYTES);
\tmemset((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t       A52_R341_RING_BYTES, 0, A52_P399_WITNESS_BYTES);
\twmb();
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_RING_BYTES,
\t\t\t    A52_P399_WITNESS_BYTES);
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t\t\t    A52_R341_RING_BYTES, A52_P399_WITNESS_BYTES);
"""
    new="""\tmemset((u8 *)a52_r341_sideband + A52_R341_RING_BYTES, 0,
\t       A52_P400_WITNESS_BYTES + A52_P399_WITNESS_BYTES);
\tmemset((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t       A52_R341_RING_BYTES, 0,
\t       A52_P400_WITNESS_BYTES + A52_P399_WITNESS_BYTES);
\twmb();
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_RING_BYTES,
\t\t\t    A52_P400_WITNESS_BYTES + A52_P399_WITNESS_BYTES);
\t__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
\t\t\t    A52_R341_RING_BYTES,
\t\t\t    A52_P400_WITNESS_BYTES + A52_P399_WITNESS_BYTES);
"""
    t=one(t,old,new,"clear")

    t=one(t,
      '"BOOT rs=ready phase=399 focus=irq-witness roots=%u copies=2 crc=crc32c"',
      '"BOOT rs=ready phase=400 focus=irq-exec roots=%u copies=2 crc=crc32c"',
      "boot marker")
    return t

def validate(root):
    r=(root/REC).read_text(errors="replace")
    h=(root/HDR).read_text(errors="replace")
    i=(root/IRQH).read_text(errors="replace")
    for x in (MARK,"A52_P400_IRQ_MAGIC","A52_P400_EXEC_MAGIC",
              "a52_p400_exec_fn","preempt_disable();",
              "set_cpus_allowed_ptr(current, cpumask_of(7))",
              'mrs %0, cntpct_el0',
              "BOOT rs=ready phase=400 focus=irq-exec roots=%u copies=2 crc=crc32c"):
        if x not in r: raise SystemExit("Phase400 recorder token missing: "+x)
    if "void a52_ackfr_irq_witness(unsigned int irq);" not in h:
        raise SystemExit("Phase400 header declaration missing")
    if "a52_ackfr_irq_witness(irq);" not in i:
        raise SystemExit("Phase400 IRQ hook missing")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    for p in (REC,HDR,IRQH):
        if not (a.root/p).is_file(): raise SystemExit("Phase400 source missing: "+str(p))
    if not a.check_only:
        p=a.root/REC; p.write_text(patch_rec(p.read_text(errors="replace")))
        p=a.root/HDR; p.write_text(patch_header(p.read_text(errors="replace")))
        p=a.root/IRQH; p.write_text(patch_irq(p.read_text(errors="replace")))
    validate(a.root)
    print("Phase400 generic-IRQ + busy-execution witness: PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
