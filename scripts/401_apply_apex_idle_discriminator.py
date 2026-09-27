#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE401_APEX_IDLE_DISCRIMINATOR_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
IRQH = Path("kernel/irq/handle.c")
IDLE = Path("kernel/sched/idle.c")
EXIT = Path("kernel/exit.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase401 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if MARK in text:
        return text
    anchor = "void a52_ackfr_irq_witness(unsigned int irq);\n"
    return one(text, anchor, anchor + (
        f"\n/* {MARK} */\n"
        "void a52_p401_irq_witness(unsigned int irq);\n"
        "void a52_p401_idle_enter(void);\n"
        "struct task_struct;\n"
        "void a52_p401_task_exit(struct task_struct *task);\n"
    ), "header")


def patch_irq(text: str) -> str:
    if MARK in text:
        return text
    anchor = "\ta52_ackfr_irq_witness(irq);\n"
    return one(text, anchor,
               anchor + f"\t/* {MARK} */\n\ta52_p401_irq_witness(irq);\n",
               "generic IRQ hook")


def patch_idle(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        anchor = '#include "sched.h"\n'
        text = one(text, anchor, anchor + inc, "idle include")
    anchor = "\t\tarch_cpu_idle();\n"
    return one(text, anchor,
               f"\t\t/* {MARK} */\n\t\ta52_p401_idle_enter();\n" + anchor,
               "arch idle enter")


def patch_exit(text: str) -> str:
    if MARK in text:
        return text
    anchor = "extern void a52_p357_note_do_exit(long code);\n"
    text = one(text, anchor,
               anchor + f"extern void a52_p401_task_exit(struct task_struct *task); /* {MARK} */\n",
               "exit declaration")
    anchor = "\ta52_p357_note_do_exit(code);\n"
    return one(text, anchor,
               anchor + "\ta52_p401_task_exit(current);\n",
               "do_exit hook")


def patch_ufs(text: str) -> str:
    if MARK in text:
        return text
    text = one(text,
               "static void a52_r380_start_sampler(struct ufs_hba *hba)\n",
               "static void __maybe_unused a52_r380_start_sampler(struct ufs_hba *hba)\n",
               "retired Phase380 function")
    anchor = "\t\ta52_r380_start_sampler(hba);\n"
    return one(text, anchor,
               f"\t\t/* {MARK}: Phase380 raw owner retired; P401 owns 0xB1BF8000..0xB1BFF7FF. */\n",
               "retire Phase380 raw sampler")


P401_BLOCK = r'''
/* A52_PHASE401_APEX_IDLE_DISCRIMINATOR_V1
 *
 * Clean owner of 0xB1BF8000..0xB1BFF7FF. Phase380's UFS raw sampler,
 * Phase393/R341 and Phase400's interventional busy worker are retired before
 * this owner is enabled. 0xB1BFF800.. remains owned by the Phase396 NoC-hit
 * lane and is deliberately not touched.
 */
#define A52_P401_PHYS              0xB1BF8000ULL
#define A52_P401_BYTES             0x7800U
#define A52_P401_COPY_BYTES        0x3c00U
#define A52_P401_SLOT_BYTES        64U
#define A52_P401_FIXED_SLOTS       16U
#define A52_P401_RING_OFF          (A52_P401_FIXED_SLOTS * A52_P401_SLOT_BYTES)
#define A52_P401_RING_SLOTS        ((A52_P401_COPY_BYTES - A52_P401_RING_OFF) / A52_P401_SLOT_BYTES)
#define A52_P401_MAGIC             0x3130345044495841ULL
#define A52_P401_COMMIT            0x401c0de5U
#define A52_P401_IDLE_SAMPLE_HZ    100U
#define A52_P401_TIMER_LIMIT       4000U
#define A52_P401_TIMER_MS          10U
#define A52_P401_IDLE_STOP_NS      25000000000ULL

enum a52_p401_event {
	A52_P401_EVT_META = 1,
	A52_P401_EVT_IRQ = 2,
	A52_P401_EVT_TIMER = 3,
	A52_P401_EVT_IDLE_ENTER = 4,
	A52_P401_EVT_APEX_EXIT = 5,
	A52_P401_EVT_FOCUS = 6,
	A52_P401_EVT_TIMER_ARM = 7,
};

struct a52_p401_slot {
	u64 magic;
	u64 boot_id;
	u64 cntpct;
	u64 ns;
	u32 event;
	u32 cpu;
	u32 value0;
	u32 value1;
	u32 sequence;
	u32 sequence_inv;
	u32 version;
	u32 commit;
} __packed;

struct a52_p401_timer {
	struct hrtimer timer;
	u32 ticks;
};

static void *a52_p401_base;
static u64 a52_p401_boot_id;
static atomic_t a52_p401_sequence = ATOMIC_INIT(0);
static atomic_t a52_p401_ring_index = ATOMIC_INIT(0);
static atomic_t a52_p401_apex_exits = ATOMIC_INIT(0);
static atomic_t a52_p401_first_apex_tgid = ATOMIC_INIT(0);
static atomic_t a52_p401_focus = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_p401_timer, a52_p401_timers);
static DEFINE_PER_CPU(u32, a52_p401_irq_count);
static DEFINE_PER_CPU(u64, a52_p401_irq_last_cntpct);
static DEFINE_PER_CPU(u64, a52_p401_idle_last_cntpct);

static int a52_p401_cpu_slot(u32 cpu)
{
	if (cpu == 0U) return 0;
	if (cpu == 5U) return 1;
	if (cpu == 7U) return 2;
	return -1;
}

static void a52_p401_store(unsigned int off, u32 event, u32 cpu,
			   u32 value0, u32 value1)
{
	struct a52_p401_slot s;
	void *dst0, *dst1;
	u32 seq;

	if (!READ_ONCE(a52_p401_base) || off + sizeof(s) > A52_P401_COPY_BYTES)
		return;
	seq = (u32)atomic_inc_return(&a52_p401_sequence);
	memset(&s, 0, sizeof(s));
	s.magic = A52_P401_MAGIC;
	s.boot_id = READ_ONCE(a52_p401_boot_id);
	s.cntpct = a52_p400_cntpct();
	s.ns = ktime_get_boottime_ns();
	s.event = event;
	s.cpu = cpu;
	s.value0 = value0;
	s.value1 = value1;
	s.sequence = seq;
	s.sequence_inv = ~seq;
	s.version = 1U;
	s.commit = A52_P401_COMMIT;
	BUILD_BUG_ON(sizeof(s) != A52_P401_SLOT_BYTES);

	dst0 = (u8 *)a52_p401_base + off;
	dst1 = (u8 *)a52_p401_base + A52_P401_COPY_BYTES + off;
	memcpy(dst0, &s, sizeof(s));
	memcpy(dst1, &s, sizeof(s));
	wmb();
	__flush_dcache_area(dst0, sizeof(s));
	__flush_dcache_area(dst1, sizeof(s));
}

static void a52_p401_fixed(unsigned int slot, u32 event, u32 cpu,
			   u32 value0, u32 value1)
{
	if (slot >= A52_P401_FIXED_SLOTS)
		return;
	a52_p401_store(slot * A52_P401_SLOT_BYTES, event, cpu, value0, value1);
}

static void a52_p401_ring(u32 event, u32 cpu, u32 value0, u32 value1)
{
	unsigned int idx;
	unsigned int off;

	idx = (unsigned int)atomic_inc_return(&a52_p401_ring_index) - 1U;
	off = A52_P401_RING_OFF +
		(idx % A52_P401_RING_SLOTS) * A52_P401_SLOT_BYTES;
	a52_p401_store(off, event, cpu, value0, value1);
}

void a52_p401_irq_witness(unsigned int irq)
{
	struct irq_desc *desc;
	struct irq_data *d;
	u32 cpu = (u32)raw_smp_processor_id();
	u32 *count;
	u64 *last;
	u64 now, step;
	int slot;

	if (!READ_ONCE(a52_p401_base))
		return;
	slot = a52_p401_cpu_slot(cpu);
	if (slot < 0)
		return;
	count = this_cpu_ptr(&a52_p401_irq_count);
	++*count;
	now = a52_p400_cntpct();
	last = this_cpu_ptr(&a52_p401_irq_last_cntpct);
	step = (u64)a52_p400_cntfrq() / 100ULL;
	if (*last && step && (s64)(now - *last) < (s64)step)
		return;
	*last = now;
	desc = irq_to_desc(irq);
	if (!desc)
		return;
	d = irq_desc_get_irq_data(desc);
	a52_p401_fixed(1U + (unsigned int)slot, A52_P401_EVT_IRQ, cpu,
			  irq, (u32)d->hwirq);
}
EXPORT_SYMBOL_GPL(a52_p401_irq_witness);

void a52_p401_idle_enter(void)
{
	u32 cpu = (u32)raw_smp_processor_id();
	u64 *last;
	u64 now, step;
	int slot;

	if (!atomic_read(&a52_p401_focus) || !READ_ONCE(a52_p401_base))
		return;
	if (ktime_get_boottime_ns() > A52_P401_IDLE_STOP_NS)
		return;
	slot = a52_p401_cpu_slot(cpu);
	if (slot < 0)
		return;
	now = a52_p400_cntpct();
	last = this_cpu_ptr(&a52_p401_idle_last_cntpct);
	step = (u64)a52_p400_cntfrq() / (u64)A52_P401_IDLE_SAMPLE_HZ;
	if (*last && step && (s64)(now - *last) < (s64)step)
		return;
	*last = now;
	a52_p401_fixed(7U + (unsigned int)slot, A52_P401_EVT_IDLE_ENTER,
			  cpu, (u32)atomic_read(&a52_p401_apex_exits), 0U);
}
EXPORT_SYMBOL_GPL(a52_p401_idle_enter);

void a52_p401_task_exit(struct task_struct *task)
{
	u32 n, pid, tgid;
	bool apex = false;

	if (!task || !READ_ONCE(a52_p401_base))
		return;
	if (!strncmp(task->comm, "apexd", TASK_COMM_LEN))
		apex = true;
	else if (task->group_leader &&
		 !strncmp(task->group_leader->comm, "apexd", TASK_COMM_LEN))
		apex = true;
	if (!apex)
		return;

	n = (u32)atomic_inc_return(&a52_p401_apex_exits);
	pid = (u32)READ_ONCE(task->pid);
	tgid = (u32)READ_ONCE(task->tgid);
	a52_p401_fixed(10U, A52_P401_EVT_APEX_EXIT,
			  (u32)raw_smp_processor_id(), n, pid);
	a52_p401_ring(A52_P401_EVT_APEX_EXIT,
			 (u32)raw_smp_processor_id(), n, (tgid << 16) ^ pid);

	/*
	 * The old BOOTPOST n=102 is a global exit ordinal, not an apexd-local
	 * count. Avoid baking that correlation into the experiment. Remember the
	 * first apexd TGID seen after core-init; the first exit from any later
	 * apexd TGID arms the focused idle witness.
	 */
	{
		int first = atomic_cmpxchg(&a52_p401_first_apex_tgid, 0, (int)tgid);

		if (!first)
			first = (int)tgid;
		if ((u32)first != tgid &&
		    atomic_cmpxchg(&a52_p401_focus, 0, 1) == 0) {
			a52_p401_fixed(10U, A52_P401_EVT_FOCUS,
					  (u32)raw_smp_processor_id(), n, tgid);
			a52_p401_ring(A52_P401_EVT_FOCUS,
					 (u32)raw_smp_processor_id(), n, tgid);
		}
	}
}
EXPORT_SYMBOL_GPL(a52_p401_task_exit);

static enum hrtimer_restart a52_p401_timer_fn(struct hrtimer *timer)
{
	struct a52_p401_timer *pt =
		container_of(timer, struct a52_p401_timer, timer);
	u32 cpu = (u32)smp_processor_id();
	int slot = a52_p401_cpu_slot(cpu);

	pt->ticks++;
	if (slot >= 0)
		a52_p401_fixed(4U + (unsigned int)slot, A52_P401_EVT_TIMER,
				  cpu, pt->ticks,
				  (u32)atomic_read(&a52_p401_apex_exits));
	if (pt->ticks >= A52_P401_TIMER_LIMIT)
		return HRTIMER_NORESTART;
	hrtimer_forward_now(timer, ms_to_ktime(A52_P401_TIMER_MS));
	return HRTIMER_RESTART;
}

static void a52_p401_timer_start_cpu(void *unused)
{
	struct a52_p401_timer *pt = this_cpu_ptr(&a52_p401_timers);

	(void)unused;
	memset(pt, 0, sizeof(*pt));
	hrtimer_init(&pt->timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED);
	pt->timer.function = a52_p401_timer_fn;
	hrtimer_start(&pt->timer, ms_to_ktime(A52_P401_TIMER_MS),
		      HRTIMER_MODE_REL_PINNED);
}

static int __init a52_p401_timer_init(void)
{
	static const unsigned int cpus[] = { 0U, 5U, 7U };
	unsigned int i, mask = 0;
	int rc;

	for (i = 0; i < ARRAY_SIZE(cpus); i++) {
		unsigned int cpu = cpus[i];
		if (cpu >= nr_cpu_ids || !cpu_online(cpu))
			continue;
		rc = smp_call_function_single(cpu, a52_p401_timer_start_cpu, NULL, 1);
		if (!rc)
			mask |= BIT(cpu);
	}
	a52_p401_ring(A52_P401_EVT_TIMER_ARM,
			 (u32)raw_smp_processor_id(), mask, A52_P401_TIMER_LIMIT);
	a52_ackfr_record("P401 ARM mask=%x int=%u lim=%u",
			 mask, A52_P401_TIMER_MS, A52_P401_TIMER_LIMIT);
	return 0;
}
late_initcall(a52_p401_timer_init);

static int __init a52_p401_init(void)
{
	BUILD_BUG_ON(2U * A52_P401_COPY_BYTES != A52_P401_BYTES);
	BUILD_BUG_ON(A52_P401_RING_OFF >= A52_P401_COPY_BYTES);
	BUILD_BUG_ON((A52_P401_COPY_BYTES - A52_P401_RING_OFF) %
		     A52_P401_SLOT_BYTES);
	BUILD_BUG_ON(sizeof(struct a52_p401_slot) != A52_P401_SLOT_BYTES);

	a52_p401_base = memremap(A52_P401_PHYS, A52_P401_BYTES, MEMREMAP_WB);
	if (!a52_p401_base)
		return 0;
	memset(a52_p401_base, 0, A52_P401_BYTES);
	wmb();
	__flush_dcache_area(a52_p401_base, A52_P401_BYTES);
	a52_p401_boot_id = a52_p400_cntpct();
	atomic_set(&a52_p401_sequence, 0);
	atomic_set(&a52_p401_ring_index, 0);
	atomic_set(&a52_p401_apex_exits, 0);
	atomic_set(&a52_p401_first_apex_tgid, 0);
	atomic_set(&a52_p401_focus, 0);
	a52_p401_fixed(0U, A52_P401_EVT_META,
			  (u32)raw_smp_processor_id(), A52_P401_BYTES,
			  A52_P401_RING_SLOTS);
	return 0;
}
core_initcall_sync(a52_p401_init);

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE400_IRQ_EXEC_WITNESS_V1" not in text:
        raise SystemExit("Phase401 requires Phase400")

    text = one(text,
               "static int __init a52_p400_exec_init(void)\n",
               "static int __init __maybe_unused a52_p400_exec_init(void)\n",
               "retired Phase400 init function")
    text = one(text,
               "late_initcall(a52_p400_exec_init);\n",
               "/* A52_PHASE401 retires the Phase400 busy worker. */\n",
               "retire Phase400 worker")
    text = one(text,
               "static int __init a52_r393_irq_arm_init(void)\n",
               "static int __init __maybe_unused a52_r393_irq_arm_init(void)\n",
               "retired Phase393 init function")
    text = one(text,
               "late_initcall(a52_r393_irq_arm_init);\n",
               "/* A52_PHASE401 retires the overlapping Phase393/R341 arm worker. */\n",
               "retire Phase393/R341")

    anchor = "static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)\n"
    text = one(text, anchor, P401_BLOCK + anchor, "P401 block")

    text = one(text, "\tu64 next_dense = 16000ULL;\n",
               "\tu64 next_dense = 15000ULL;\n", "N396 start")
    text = one(text,
               "\t\tif (now_ms >= 16000ULL && now_ms <= 17200ULL &&\n",
               "\t\tif (now_ms >= 15000ULL && now_ms <= 22000ULL &&\n",
               "N396 window")
    text = one(text, "\t\t\tnext_dense += 10ULL;\n",
               "\t\t\tnext_dense += 250ULL;\n", "N396 cadence")
    text = one(text,
               "\t\tif (now_ms >= 15800ULL && now_ms <= 17400ULL)\n\t\t\tusleep_range(1000, 2000);\n",
               "\t\tif (now_ms >= 14800ULL && now_ms <= 22200ULL)\n\t\t\tusleep_range(10000, 20000);\n",
               "N396 sleep")

    text = one(text,
               '"BOOT rs=ready phase=400 focus=irq-exec roots=%u copies=2 crc=crc32c"',
               '"BOOT rs=ready phase=401 focus=apex-idle roots=%u copies=2 crc=crc32c"',
               "boot marker")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    hdr = (root / HDR).read_text(errors="replace")
    irq = (root / IRQH).read_text(errors="replace")
    idle = (root / IDLE).read_text(errors="replace")
    ex = (root / EXIT).read_text(errors="replace")
    ufs = (root / UFS).read_text(errors="replace")

    required_rec = (
        MARK,
        "A52_P401_PHYS              0xB1BF8000ULL",
        "A52_P401_BYTES             0x7800U",
        "A52_P401_IDLE_SAMPLE_HZ    100U",
        "A52_P401_TIMER_LIMIT       4000U",
        "core_initcall_sync(a52_p401_init);",
        "late_initcall(a52_p401_timer_init);",
        "a52_p401_task_exit",
        "a52_p401_idle_enter",
        "a52_p401_irq_witness",
        "next_dense += 250ULL;",
        "BOOT rs=ready phase=401 focus=apex-idle roots=%u copies=2 crc=crc32c",
    )
    for token in required_rec:
        if token not in rec:
            raise SystemExit("Phase401 recorder token missing: " + token)
    if "late_initcall(a52_p400_exec_init);" in rec:
        raise SystemExit("Phase401 old P400 worker still active")
    if "late_initcall(a52_r393_irq_arm_init);" in rec:
        raise SystemExit("Phase401 old R341 arm worker still active")
    if "void a52_p401_irq_witness(unsigned int irq);" not in hdr:
        raise SystemExit("Phase401 header IRQ declaration missing")
    if "a52_p401_irq_witness(irq);" not in irq:
        raise SystemExit("Phase401 generic IRQ hook missing")
    if "a52_p401_idle_enter();" not in idle:
        raise SystemExit("Phase401 arch idle hook missing")
    if "a52_p401_task_exit(current);" not in ex:
        raise SystemExit("Phase401 do_exit hook missing")
    if "\t\ta52_r380_start_sampler(hba);\n" in ufs:
        raise SystemExit("Phase401 Phase380 raw sampler still active")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    for p in (REC, HDR, IRQH, IDLE, EXIT, UFS):
        if not (ns.root / p).is_file():
            raise SystemExit("Phase401 source missing: " + str(p))
    if not ns.check_only:
        p = ns.root / REC; p.write_text(patch_rec(p.read_text(errors="replace")))
        p = ns.root / HDR; p.write_text(patch_header(p.read_text(errors="replace")))
        p = ns.root / IRQH; p.write_text(patch_irq(p.read_text(errors="replace")))
        p = ns.root / IDLE; p.write_text(patch_idle(p.read_text(errors="replace")))
        p = ns.root / EXIT; p.write_text(patch_exit(p.read_text(errors="replace")))
        p = ns.root / UFS; p.write_text(patch_ufs(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase401 apexd/idle discriminator applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
