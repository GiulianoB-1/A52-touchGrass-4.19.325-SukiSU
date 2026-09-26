#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE396_NOC_IRQ_EVIDENCE_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
IRQH = Path("kernel/irq/handle.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase396 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if MARK in text:
        return text
    anchor = "void a52_ackfr_record(const char *fmt, ...) __printf(1, 2);\n"
    return one(text, anchor,
               anchor + "\n/* " + MARK + " */\n"
               "void a52_ackfr_noc_irq_hit(unsigned int irq);\n",
               "header declaration")


BLOCK = r'''

/* A52_PHASE396_NOC_IRQ_EVIDENCE_V1
 * Samsung's preserved debug partition reports NOCERR IRQ identifiers
 * 404,226,290,108,198,186,323 before TZBSP_ERR_FATAL_NOC_ERROR.  Resolve
 * those identifiers against both Linux IRQ numbers and GIC hwirq values
 * (including the common SPI +32 translation), then preserve two forms of
 * evidence:
 *   1. hardirq hit records in the unused 0x600-byte ramoops tail gap;
 *   2. process-context descriptor/chip-state snapshots around 17.6-18.6 s.
 *
 * The hit lane is two-copy, non-wrapping and does not depend on R48, pstore,
 * workqueues, or scheduler progress once the IRQ itself is being handled.
 */
#define A52_P396_HIT_PHYS        0xB1BFF800ULL
#define A52_P396_HIT_BYTES       0x600U
#define A52_P396_HIT_COPY_BYTES  (A52_P396_HIT_BYTES / 2U)
#define A52_P396_HIT_SLOT_BYTES  64U
#define A52_P396_HIT_SLOTS       (A52_P396_HIT_COPY_BYTES / A52_P396_HIT_SLOT_BYTES)
#define A52_P396_HIT_MAGIC       0x363933434f4e4951ULL
#define A52_P396_HIT_COMMIT      0x396c0de5U
#define A52_P396_MAX_WATCH       32U
#define A52_P396_CPU_PACK        8U

struct a52_p396_hit_slot {
	u64 magic;
	u64 monotonic_ns;
	u64 r48_seq;
	u64 hwirq;
	u32 index;
	u32 irq;
	u32 cpu;
	u32 flags;
	u32 cpu_count;
	u32 commit;
	u8 reserved[8];
} __packed;

static const unsigned int a52_p396_targets[] = {
	404U, 226U, 290U, 108U, 198U, 186U, 323U,
};
static void *a52_p396_hit_base;
static atomic_t a52_p396_hit_index = ATOMIC_INIT(0);
static unsigned int a52_p396_watch_irq[A52_P396_MAX_WATCH];
static unsigned int a52_p396_watch_target[A52_P396_MAX_WATCH];
static unsigned int a52_p396_watch_mode[A52_P396_MAX_WATCH];
static u32 a52_p396_watch_base[A52_P396_MAX_WATCH][A52_P396_CPU_PACK];
static atomic_t a52_p396_watch_count = ATOMIC_INIT(0);
static u8 *a52_p396_fast_irq;
static unsigned int a52_p396_fast_nr;
static struct task_struct *a52_p396_task;

static unsigned int a52_p396_match(unsigned int irq, irq_hw_number_t hwirq,
				    unsigned int *mode)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(a52_p396_targets); i++) {
		unsigned int target = a52_p396_targets[i];

		if (irq == target) {
			*mode = 1U;
			return target;
		}
		if ((u64)hwirq == (u64)target) {
			*mode = 2U;
			return target;
		}
		if ((u64)hwirq == (u64)target + 32ULL) {
			*mode = 3U;
			return target;
		}
	}
	return 0U;
}

static int a52_p396_find_watch(unsigned int irq)
{
	unsigned int i;
	unsigned int n = (unsigned int)atomic_read(&a52_p396_watch_count);

	if (n > A52_P396_MAX_WATCH)
		n = A52_P396_MAX_WATCH;
	for (i = 0; i < n; i++)
		if (READ_ONCE(a52_p396_watch_irq[i]) == irq)
			return (int)i;
	return -1;
}

static void a52_p396_add_watch(unsigned int irq, struct irq_desc *desc,
			       unsigned int target, unsigned int mode)
{
	unsigned int idx, cpu;
	struct irq_chip *chip;
	const char *chip_name = "-";
	const char *action_name = "-";

	if (!desc || a52_p396_find_watch(irq) >= 0)
		return;
	idx = (unsigned int)atomic_read(&a52_p396_watch_count);
	if (idx >= A52_P396_MAX_WATCH)
		return;

	chip = irq_desc_get_chip(desc);
	if (chip && chip->name)
		chip_name = chip->name;
	if (READ_ONCE(desc->action) && READ_ONCE(desc->action->name))
		action_name = READ_ONCE(desc->action->name);

	WRITE_ONCE(a52_p396_watch_irq[idx], irq);
	WRITE_ONCE(a52_p396_watch_target[idx], target);
	WRITE_ONCE(a52_p396_watch_mode[idx], mode);
	if (READ_ONCE(a52_p396_fast_irq) && irq < READ_ONCE(a52_p396_fast_nr))
		WRITE_ONCE(a52_p396_fast_irq[irq], 1U);
	for (cpu = 0; cpu < A52_P396_CPU_PACK && cpu < nr_cpu_ids; cpu++)
		a52_p396_watch_base[idx][cpu] = kstat_irqs_cpu(irq, (int)cpu);
	smp_wmb();
	atomic_set(&a52_p396_watch_count, (int)(idx + 1U));

	a52_ackfr_record("N396 MAP q=%u hw=%llu t=%u md=%u chip=%.12s act=%.16s",
		irq, (unsigned long long)irq_desc_get_irq_data(desc)->hwirq,
		target, mode, chip_name, action_name);
}

static void a52_p396_resolve(void)
{
	struct irq_desc *desc;
	unsigned int irq;

	for_each_irq_desc(irq, desc) {
		unsigned int mode = 0U;
		unsigned int target;

		if (!desc)
			continue;
		target = a52_p396_match(irq,
			irq_desc_get_irq_data(desc)->hwirq, &mode);
		if (target)
			a52_p396_add_watch(irq, desc, target, mode);
	}
}

static u32 a52_p396_sw_state(struct irq_data *d)
{
	u32 state = 0U;

	if (irqd_irq_disabled(d))
		state |= BIT(0);
	if (irqd_irq_masked(d))
		state |= BIT(1);
	if (irqd_irq_inprogress(d))
		state |= BIT(2);
	if (irqd_is_activated(d))
		state |= BIT(3);
	if (irqd_is_started(d))
		state |= BIT(4);
	return state;
}

static void a52_p396_chip_state(unsigned int irq, u32 *state, u32 *valid)
{
	bool bit;
	int ret;

	*state = 0U;
	*valid = 0U;
	ret = irq_get_irqchip_state(irq, IRQCHIP_STATE_PENDING, &bit);
	if (!ret) { *valid |= BIT(0); if (bit) *state |= BIT(0); }
	ret = irq_get_irqchip_state(irq, IRQCHIP_STATE_ACTIVE, &bit);
	if (!ret) { *valid |= BIT(1); if (bit) *state |= BIT(1); }
	ret = irq_get_irqchip_state(irq, IRQCHIP_STATE_MASKED, &bit);
	if (!ret) { *valid |= BIT(2); if (bit) *state |= BIT(2); }
	ret = irq_get_irqchip_state(irq, IRQCHIP_STATE_LINE_LEVEL, &bit);
	if (!ret) { *valid |= BIT(3); if (bit) *state |= BIT(3); }
}

static u64 a52_p396_cpu_delta_pack(unsigned int idx, unsigned int irq,
				   u32 *total)
{
	u64 packed = 0ULL;
	unsigned int cpu;

	*total = 0U;
	for (cpu = 0; cpu < nr_cpu_ids; cpu++) {
		u32 now = kstat_irqs_cpu(irq, (int)cpu);
		u32 delta;

		*total += now;
		if (cpu >= A52_P396_CPU_PACK)
			continue;
		delta = now - a52_p396_watch_base[idx][cpu];
		if (delta > 0xffU)
			delta = 0xffU;
		packed |= (u64)delta << (cpu * 8U);
	}
	return packed;
}

static void a52_p396_sample(u64 now_ms)
{
	unsigned int idx;
	unsigned int n = (unsigned int)atomic_read(&a52_p396_watch_count);

	if (n > A52_P396_MAX_WATCH)
		n = A52_P396_MAX_WATCH;
	for (idx = 0; idx < n; idx++) {
		unsigned int irq = READ_ONCE(a52_p396_watch_irq[idx]);
		struct irq_desc *desc = irq_to_desc(irq);
		struct irq_data *d;
		u32 sw, hw, ok, total;
		u64 pc;

		if (!desc)
			continue;
		d = irq_desc_get_irq_data(desc);
		sw = a52_p396_sw_state(d);
		a52_p396_chip_state(irq, &hw, &ok);
		pc = a52_p396_cpu_delta_pack(idx, irq, &total);
		a52_ackfr_record("N396 S ms=%llu q=%u t=%u sw=%x hw=%x ok=%x dp=%u pc=%016llx",
			(unsigned long long)now_ms, irq, total, sw, hw, ok,
			READ_ONCE(desc->depth), (unsigned long long)pc);
	}
}

void a52_ackfr_noc_irq_hit(unsigned int irq)
{
	struct a52_p396_hit_slot slot;
	struct irq_desc *desc;
	struct irq_data *d;
	unsigned int idx;
	unsigned int pos;
	unsigned int cpu;
	int watch;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_p396_hit_base) || !READ_ONCE(a52_p396_fast_irq) ||
	    irq >= READ_ONCE(a52_p396_fast_nr) ||
	    !READ_ONCE(a52_p396_fast_irq[irq]))
		return;
	watch = a52_p396_find_watch(irq);
	if (watch < 0)
		return;
	idx = (unsigned int)atomic_inc_return(&a52_p396_hit_index) - 1U;
	if (idx >= A52_P396_HIT_SLOTS)
		return;
	desc = irq_to_desc(irq);
	if (!desc)
		return;
	d = irq_desc_get_irq_data(desc);
	cpu = (unsigned int)raw_smp_processor_id();

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P396_HIT_MAGIC;
	slot.monotonic_ns = ktime_get_ns();
	slot.r48_seq = (u64)atomic64_read(&a52_r179_sequence);
	slot.hwirq = (u64)d->hwirq;
	slot.index = idx;
	slot.irq = irq;
	slot.cpu = cpu;
	slot.flags = a52_p396_sw_state(d);
	slot.cpu_count = kstat_irqs_cpu(irq, (int)cpu);
	slot.commit = A52_P396_HIT_COMMIT;

	BUILD_BUG_ON(sizeof(struct a52_p396_hit_slot) != A52_P396_HIT_SLOT_BYTES);
	pos = idx * A52_P396_HIT_SLOT_BYTES;
	dst0 = (u8 *)a52_p396_hit_base + pos;
	dst1 = (u8 *)a52_p396_hit_base + A52_P396_HIT_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}
EXPORT_SYMBOL_GPL(a52_ackfr_noc_irq_hit);

static int a52_p396_thread_fn(void *unused)
{
	u64 next_dense = 17600ULL;
	u64 next_sparse = 14000ULL;
	u64 next_resolve = 12000ULL;

	(void)unused;
	while (!kthread_should_stop()) {
		u64 now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);

		if (now_ms >= 45000ULL)
			break;
		if (now_ms >= next_resolve) {
			a52_p396_resolve();
			next_resolve += 1000ULL;
		}
		if (now_ms >= 17600ULL && now_ms <= 18600ULL &&
		    now_ms >= next_dense) {
			a52_p396_sample(now_ms);
			next_dense += 20ULL;
			continue;
		}
		if (now_ms >= next_sparse) {
			a52_p396_sample(now_ms);
			next_sparse += 250ULL;
			continue;
		}
		if (now_ms >= 17400ULL && now_ms <= 18800ULL)
			usleep_range(2000, 3000);
		else
			msleep(20);
	}
	a52_ackfr_record("N396 DONE ms=%llu wc=%u hits=%u",
		(unsigned long long)div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC),
		(unsigned int)atomic_read(&a52_p396_watch_count),
		(unsigned int)atomic_read(&a52_p396_hit_index));
	return 0;
}

static int __init a52_p396_init(void)
{
	long kt = 0;

	BUILD_BUG_ON(A52_P396_HIT_COPY_BYTES % A52_P396_HIT_SLOT_BYTES);
	BUILD_BUG_ON(A52_P396_HIT_SLOTS != 12U);
	a52_p396_fast_nr = READ_ONCE(nr_irqs);
	a52_p396_fast_irq = kcalloc(a52_p396_fast_nr, sizeof(*a52_p396_fast_irq), GFP_KERNEL);
	a52_p396_hit_base = memremap(A52_P396_HIT_PHYS,
		A52_P396_HIT_BYTES, MEMREMAP_WB);
	if (a52_p396_hit_base) {
		memset(a52_p396_hit_base, 0, A52_P396_HIT_BYTES);
		wmb();
		__flush_dcache_area(a52_p396_hit_base, A52_P396_HIT_BYTES);
	}
	a52_p396_resolve();
	a52_p396_task = kthread_run(a52_p396_thread_fn, NULL, "a52_p396_noc");
	if (IS_ERR(a52_p396_task)) {
		kt = PTR_ERR(a52_p396_task);
		a52_p396_task = NULL;
	}
	a52_ackfr_record("N396 ARM map=%u fast=%u wc=%u kt=%ld",
		a52_p396_hit_base ? 1U : 0U,
		a52_p396_fast_irq ? a52_p396_fast_nr : 0U,
		(unsigned int)atomic_read(&a52_p396_watch_count), kt);
	return 0;
}
late_initcall(a52_p396_init);
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE395_NOC_WALL_V1" not in text:
        raise SystemExit("Phase396 requires Phase395 recorder lineage")

    text = one(text,
        "#include <linux/workqueue.h>\n",
        "#include <linux/workqueue.h>\n"
        "#include <linux/interrupt.h>\n"
        "#include <linux/irq.h>\n"
        "#include <linux/irqdesc.h>\n"
        "#include <linux/kernel_stat.h>\n"
        "#include <linux/slab.h>\n",
        "IRQ includes")

    text = one(text,
        "#define A52_R179_BANK_ALL (A52_R179_BANK_CONSOLE | \\\n\t\t\t   A52_R179_BANK_FTRACE | A52_R179_BANK_RECORD)\n",
        "#define A52_R179_BANK_ALL (A52_R179_BANK_FTRACE | A52_R179_BANK_RECORD)\n"
        "/* A52_PHASE396_R48_TWO_COPY_V1: console bank belongs to PSTORE_CONSOLE. */\n",
        "R48 two-copy mask")

    text = one(text,
        'a52_ackfr_record("BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers roots=%u copies=3 crc=crc32c",\n',
        'a52_ackfr_record("BOOT rs=ready phase=396 focus=noc-irq roots=%u copies=2 crc=crc32c",\n',
        "R48 copy marker")

    text = one(text,
        '\t       !strncmp(fmt, "N395", 4); /* A52_PHASE395_NOC_WALL_V1 */\n',
        '\t       !strncmp(fmt, "N395", 4) || /* A52_PHASE395_NOC_WALL_V1 */\n'
        '\t       !strncmp(fmt, "N396", 4); /* A52_PHASE396_NOC_IRQ_EVIDENCE_V1 */\n',
        "diag admission")
    text = one(text,
        '\t    strncmp(fmt, "N395", 4))\n',
        '\t    strncmp(fmt, "N395", 4) &&\n'
        '\t    strncmp(fmt, "N396", 4))\n',
        "focused admission")

    # Ensure N396 survives the original 896-event in-memory capacity and keeps
    # feeding the two surviving R48 banks near the fatal window.
    anchor = 'return !strncmp(message, "P276 ", 5) ||\n'
    text = one(text, anchor,
               'return !strncmp(message, "N396 ", 5) ||\n       !strncmp(message, "P276 ", 5) ||\n',
               "critical N396 admission")

    anchor = 'static const char a52_p395_noc_wall_marker[] __used = "A52_PHASE395_NOC_WALL_V1";\n'
    text = one(text, anchor, anchor + BLOCK,
               "Phase396 block")
    return text


def patch_irq_handle(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <linux/kernel_stat.h>\n"
    if inc not in text:
        raise SystemExit("Phase396 handle.c include anchor missing")
    text = one(text, inc,
               inc + '#include <linux/a52_ack_secure_flight_recorder.h>\n',
               "handle include")

    anchor = "\trecord_irq_time(desc);\n"
    if anchor not in text:
        raise SystemExit("Phase396 __handle_irq_event_percpu anchor missing")
    text = one(text, anchor,
               "\t/* " + MARK + " */\n"
               "\ta52_ackfr_noc_irq_hit(irq);\n\n" + anchor,
               "generic IRQ hook")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text()
    hdr = (root / HDR).read_text()
    irqh = (root / IRQH).read_text()
    for token in (
        MARK,
        "A52_PHASE396_R48_TWO_COPY_V1",
        "A52_P396_HIT_PHYS        0xB1BFF800ULL",
        "404U, 226U, 290U, 108U, 198U, 186U, 323U",
        "N396 MAP q=%u hw=%llu t=%u md=%u",
        "N396 S ms=%llu q=%u t=%u sw=%x hw=%x ok=%x dp=%u pc=%016llx",
        "N396 ARM map=%u fast=%u wc=%u kt=%ld",
        "a52_ackfr_noc_irq_hit(unsigned int irq)",
        '#define A52_R179_BANK_ALL (A52_R179_BANK_FTRACE | A52_R179_BANK_RECORD)',
        "copies=2 crc=crc32c",
        '!strncmp(fmt, "N396", 4)',
    ):
        if token not in rec:
            raise SystemExit("Phase396 recorder token missing: " + token)
    if "a52_ackfr_noc_irq_hit(unsigned int irq);" not in hdr:
        raise SystemExit("Phase396 header declaration missing")
    for token in (MARK, "a52_ackfr_noc_irq_hit(irq);"):
        if token not in irqh:
            raise SystemExit("Phase396 generic IRQ hook missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root
    for rel in (REC, HDR, IRQH):
        if not (root / rel).is_file():
            raise SystemExit("Phase396 source missing: " + str(rel))
    if not ns.check_only:
        p = root / REC; p.write_text(patch_rec(p.read_text()))
        p = root / HDR; p.write_text(patch_header(p.read_text()))
        p = root / IRQH; p.write_text(patch_irq_handle(p.read_text()))
    validate(root)
    print("Phase396 NoC IRQ evidence + two-copy R48 console preservation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())