#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE368_APEXD_WORKER_SYSCALL_MAP_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase368 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def locate_futex(root: Path) -> Path:
    hits = []
    for rel in (Path("kernel/futex/core.c"), Path("kernel/futex.c")):
        p = root / rel
        if p.is_file():
            t = p.read_text(encoding="utf-8")
            if "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1" in t:
                hits.append(p)
    if len(hits) != 1:
        raise SystemExit(f"Phase368 expected one Phase367 futex source, found {hits}")
    return hits[0]


BLOCK = r'''
/* A52_PHASE368_APEXD_WORKER_SYSCALL_MAP_V1
 *
 * Phase367 proved the apexd group leader was successfully woken from one
 * FUTEX_WAIT_BITSET_PRIVATE, then immediately blocked on a second futex that
 * was never targeted by a later wake. That strongly deprioritizes a generic
 * futex-wake failure and instead points to an async APEX activation worker
 * that never reaches completion.
 *
 * Phase368 persists the latest syscall state independently for every thread
 * belonging to the apexd process. Each thread owns one of 64 mirrored slots.
 * Syscall ENTRY overwrites the slot with ret=LONG_MIN; normal RETURN rewrites
 * the same slot with the actual result. A thread stuck inside the kernel
 * therefore survives as an ENTRY record, while a thread that exited normally
 * is marked explicitly as EXIT_ENTRY.
 *
 * Observation only: syscall arguments, return values, userspace memory,
 * scheduling and kernel behavior are not modified.
 */
#define A52_R368_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R368_SIDEBAND_BYTES     0x8000U
#define A52_R368_COPY_BYTES         0x4000U
#define A52_R368_SLOT_BYTES         256U
#define A52_R368_SLOTS              64U
#define A52_R368_MAGIC              0x3836335853504141ULL
#define A52_R368_COMMIT             0x368c0de5U
#define A52_R368_VERSION            1U

#define A52_R368_EVT_ENTRY          1U
#define A52_R368_EVT_RETURN         2U
#define A52_R368_EVT_EXIT_ENTRY     3U

#define A52_R368_PAYLOAD_NONE       0U
#define A52_R368_PAYLOAD_PATH_X0    1U
#define A52_R368_PAYLOAD_PATH_X1    2U
#define A52_R368_PAYLOAD_MOUNT      3U
#define A52_R368_PAYLOAD_RENAME     4U

struct a52_r368_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 clear_child_tid;
	u64 mm;
	u64 x[6];
	s64 ret;
	u32 event;
	u32 syscallno;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 slot_index;
	u32 commit;
	u32 version;
	u32 payload_len;
	u32 payload_kind;
	char comm[TASK_COMM_LEN];
	u8 payload[80];
};

static const char a52_r368_marker[] __used =
	"A52_PHASE368_APEXD_WORKER_SYSCALL_MAP_V1";
static void *a52_r368_sideband;
static atomic64_t a52_r368_sequence = ATOMIC64_INIT(0);
static atomic_t a52_r368_owner[A52_R368_SLOTS];

static bool a52_r368_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static int a52_r368_slot_for_pid(u32 pid)
{
	unsigned int start = pid & (A52_R368_SLOTS - 1U);
	unsigned int i;

	for (i = 0; i < A52_R368_SLOTS; i++) {
		unsigned int slot = (start + i) & (A52_R368_SLOTS - 1U);
		int owner = atomic_read(&a52_r368_owner[slot]);

		if (owner == (int)pid)
			return (int)slot;
		if (!owner &&
		    atomic_cmpxchg(&a52_r368_owner[slot], 0, (int)pid) == 0)
			return (int)slot;
	}
	return -1;
}

static void a52_r368_copy_user_string(u8 *dst, size_t cap,
				      const char __user *src)
{
	long n;

	if (!dst || !cap || !src)
		return;
	n = strncpy_from_user((char *)dst, src, cap);
	if (n < 0)
		return;
	dst[cap - 1] = '\0';
}

static void a52_r368_capture_payload(struct a52_r368_record *r, int scno)
{
	const char __user *p0 = (const char __user *)(unsigned long)r->x[0];
	const char __user *p1 = (const char __user *)(unsigned long)r->x[1];
	const char __user *p2 = (const char __user *)(unsigned long)r->x[2];
	const char __user *p3 = (const char __user *)(unsigned long)r->x[3];

	switch (scno) {
	case __NR_openat:
	case __NR_newfstatat:
	case __NR_faccessat:
	case __NR_mkdirat:
	case __NR_unlinkat:
	case __NR_readlinkat:
		r->payload_kind = A52_R368_PAYLOAD_PATH_X1;
		a52_r368_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#ifdef __NR_openat2
	case __NR_openat2:
		r->payload_kind = A52_R368_PAYLOAD_PATH_X1;
		a52_r368_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#endif
#ifdef __NR_faccessat2
	case __NR_faccessat2:
		r->payload_kind = A52_R368_PAYLOAD_PATH_X1;
		a52_r368_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#endif
#ifdef __NR_statx
	case __NR_statx:
		r->payload_kind = A52_R368_PAYLOAD_PATH_X1;
		a52_r368_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#endif
	case __NR_mount:
		r->payload_kind = A52_R368_PAYLOAD_MOUNT;
		a52_r368_copy_user_string(r->payload, 24, p0);
		a52_r368_copy_user_string(r->payload + 24, 44, p1);
		a52_r368_copy_user_string(r->payload + 68, 12, p2);
		break;
#ifdef __NR_renameat
	case __NR_renameat:
		r->payload_kind = A52_R368_PAYLOAD_RENAME;
		a52_r368_copy_user_string(r->payload, 40, p1);
		a52_r368_copy_user_string(r->payload + 40, 40, p3);
		break;
#endif
#ifdef __NR_renameat2
	case __NR_renameat2:
		r->payload_kind = A52_R368_PAYLOAD_RENAME;
		a52_r368_copy_user_string(r->payload, 40, p1);
		a52_r368_copy_user_string(r->payload + 40, 40, p3);
		break;
#endif
	default:
		break;
	}

	if (r->payload_kind)
		r->payload_len = sizeof(r->payload);
}

static void a52_r368_fill(struct a52_r368_record *r,
			  struct pt_regs *regs, int scno, u32 event,
			  u64 sequence, s64 ret, unsigned int slot)
{
	unsigned int i;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R368_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->pc = regs->pc;
	r->lr = regs->regs[30];
	r->sp = regs->sp;
	r->clear_child_tid =
		(u64)(unsigned long)READ_ONCE(current->clear_child_tid);
	r->mm = (u64)(unsigned long)current->mm;
	r->x[0] = regs->orig_x0;
	for (i = 1; i < ARRAY_SIZE(r->x); i++)
		r->x[i] = regs->regs[i];
	r->ret = ret;
	r->event = event;
	r->syscallno = (u32)scno;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->slot_index = slot;
	r->commit = A52_R368_COMMIT;
	r->version = A52_R368_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
	a52_r368_capture_payload(r, scno);
}

static void a52_r368_write_slot(unsigned int slot,
				const struct a52_r368_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r368_sideband) || !r || slot >= A52_R368_SLOTS)
		return;

	pos = slot * A52_R368_SLOT_BYTES;
	dst0 = (u8 *)a52_r368_sideband + pos;
	dst1 = (u8 *)a52_r368_sideband + A52_R368_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static u64 a52_r368_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r368_record r;
	u64 sequence;
	u32 event = A52_R368_EVT_ENTRY;
	int slot;

	if (!READ_ONCE(a52_r368_sideband) || !a52_r368_apexd_thread())
		return 0;

	slot = a52_r368_slot_for_pid((u32)current->pid);
	if (slot < 0)
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r368_sequence);
	if (scno == __NR_exit || scno == __NR_exit_group)
		event = A52_R368_EVT_EXIT_ENTRY;

	a52_r368_fill(&r, regs, scno, event, sequence, LONG_MIN,
		      (unsigned int)slot);
	a52_r368_write_slot((unsigned int)slot, &r);
	return sequence;
}

static void a52_r368_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r368_record r;
	int slot;

	if (!sequence || !READ_ONCE(a52_r368_sideband) ||
	    !a52_r368_apexd_thread())
		return;

	slot = a52_r368_slot_for_pid((u32)current->pid);
	if (slot < 0)
		return;

	a52_r368_fill(&r, regs, scno, A52_R368_EVT_RETURN, sequence,
		      (s64)regs->regs[0], (unsigned int)slot);
	a52_r368_write_slot((unsigned int)slot, &r);
}

static int __init a52_r368_init(void)
{
	unsigned int i;

	BUILD_BUG_ON(sizeof(struct a52_r368_record) != A52_R368_SLOT_BYTES);
	BUILD_BUG_ON(A52_R368_SLOTS * A52_R368_SLOT_BYTES !=
		     A52_R368_COPY_BYTES);
	BUILD_BUG_ON((A52_R368_SLOTS & (A52_R368_SLOTS - 1U)) != 0);

	a52_r368_sideband = memremap(A52_R368_SIDEBAND_PHYS,
		A52_R368_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r368_sideband)
		return 0;

	memset(a52_r368_sideband, 0, A52_R368_SIDEBAND_BYTES);
	atomic64_set(&a52_r368_sequence, 0);
	for (i = 0; i < A52_R368_SLOTS; i++)
		atomic_set(&a52_r368_owner[i], 0);
	wmb();
	__flush_dcache_area(a52_r368_sideband, A52_R368_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r368_init);

'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE366_APEXD_FUTEX_THREADS_V1" not in text:
        raise SystemExit("Phase368 requires Phase366 syscall lineage")

    if "#include <linux/uaccess.h>\n" not in text:
        text = "#include <linux/uaccess.h>\n" + text

    anchor = (
        'static const char a52_r366_marker[] __used =\n'
        '\t"A52_PHASE366_APEXD_FUTEX_THREADS_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase368 block insertion")

    text = one(
        text,
        "\tu64 a52_r366_sequence_token = 0;\n",
        "\tu64 a52_r366_sequence_token = 0;\n"
        "\tu64 a52_r368_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r366_sequence_token = a52_r366_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r366_sys_return(regs, scno, a52_r366_sequence_token);\n"
    )
    new = (
        "\ta52_r366_sequence_token = a52_r366_sys_enter(regs, scno);\n"
        "\ta52_r368_sequence_token = a52_r368_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r368_sys_return(regs, scno, a52_r368_sequence_token);\n"
        "\ta52_r366_sys_return(regs, scno, a52_r366_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def patch_futex(text: str) -> str:
    if "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1" not in text:
        raise SystemExit("Phase368 requires Phase367 futex lineage")

    if "static int __init __used a52_r367_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r367_init(void)\n",
            "static int __init __used a52_r367_init(void)\n",
            "mark Phase367 init dormant",
        )
    if "late_initcall(a52_r367_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r367_init);\n",
            "/* Phase368 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase367 mapper",
        )
    return text


def validate(syscall: str, futex: str) -> None:
    for token in (
        MARK,
        "A52_R368_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R368_SLOT_BYTES         256U",
        "A52_R368_SLOTS              64U",
        "A52_R368_COMMIT             0x368c0de5U",
        "A52_R368_EVT_EXIT_ENTRY",
        "atomic_cmpxchg(&a52_r368_owner[slot], 0, (int)pid)",
        "a52_r368_sys_enter(regs, scno);",
        "a52_r368_sys_return(regs, scno, a52_r368_sequence_token);",
        "late_initcall(a52_r368_init);",
    ):
        if token not in syscall:
            raise SystemExit("Phase368 syscall token missing: " + token)

    if "late_initcall(a52_r367_init);" in futex:
        raise SystemExit("Phase368 must disable Phase367 sideband owner")
    if "static int __init __used a52_r367_init(void)" not in futex:
        raise SystemExit("Phase368 dormant Phase367 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    root = ns.root.resolve()
    syscall_path = root / SYSCALL
    futex_path = locate_futex(root)
    if not syscall_path.is_file():
        raise SystemExit("Phase368 syscall source missing")

    syscall = syscall_path.read_text(encoding="utf-8")
    futex = futex_path.read_text(encoding="utf-8")

    if MARK in syscall:
        validate(syscall, futex)
        print("Phase368 apexd worker syscall map audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase368 marker missing in check-only mode")

    syscall2 = patch_syscall(syscall)
    futex2 = patch_futex(futex)
    validate(syscall2, futex2)

    syscall_path.write_text(syscall2, encoding="utf-8")
    futex_path.write_text(futex2, encoding="utf-8")
    print(f"Phase368 apexd worker syscall map applied: {syscall_path}")
    print(f"Phase368 disabled Phase367 sideband mapper: {futex_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
