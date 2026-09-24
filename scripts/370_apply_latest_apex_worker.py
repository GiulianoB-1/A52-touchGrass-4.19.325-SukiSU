#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE370_LATEST_APEX_WORKER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase370 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE370_LATEST_APEX_WORKER_V1
 *
 * Phase369 hardware result:
 * - later apexd group leader PID 487 repeatedly created async workers
 * - clone() returned child TID 536 immediately before the final blocked future
 * - main then completed a short FUTEX_WAIT_BITSET_PRIVATE and entered another
 *   FUTEX_WAIT_BITSET_PRIVATE on 0xb400007d7b24f690 that never returned
 * - no zygote or SurfaceFlinger direct-init exec appeared, and Phase346 DSI
 *   p0..p6 remained empty
 *
 * Phase370 latches the most recently cloned apexd worker. Each successful
 * group-leader clone/clone3 return replaces the target PID and resets the
 * target-worker sequence. Only syscalls from that exact PID/TGID are then
 * recorded. Four header replicas preserve the latest clone target; slots 4..63
 * are a 60-entry mirrored chronological syscall ring.
 *
 * Observation only: no syscall arguments, return values, scheduling, futex
 * behavior, userspace memory or APEX logic are modified.
 */
#define A52_R370_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R370_SIDEBAND_BYTES     0x8000U
#define A52_R370_COPY_BYTES         0x4000U
#define A52_R370_SLOT_BYTES         256U
#define A52_R370_SLOTS_PER_COPY     64U
#define A52_R370_HEADER_SLOTS       4U
#define A52_R370_RING_FIRST         4U
#define A52_R370_RING_SLOTS         60U
#define A52_R370_MAGIC              0x3037335857504141ULL
#define A52_R370_COMMIT             0x370c0de5U
#define A52_R370_VERSION            1U

#define A52_R370_EVT_TARGET         1U
#define A52_R370_EVT_ENTRY          2U
#define A52_R370_EVT_RETURN         3U
#define A52_R370_EVT_EXIT_ENTRY     4U

#define A52_R370_PAYLOAD_NONE       0U
#define A52_R370_PAYLOAD_PATH_X0    1U
#define A52_R370_PAYLOAD_PATH_X1    2U
#define A52_R370_PAYLOAD_MOUNT      3U
#define A52_R370_PAYLOAD_WRITEV     4U

struct a52_r370_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 target_pid;
	u64 target_tgid;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 mm;
	u64 clear_child_tid;
	u64 x[6];
	s64 ret;
	u32 event;
	u32 syscallno;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ppid;
	u32 ring_index;
	u32 commit;
	u32 version;
	u32 payload_len;
	u32 payload_kind;
	char comm[TASK_COMM_LEN];
	u8 payload[60];
};

static const char a52_r370_marker[] __used =
	"A52_PHASE370_LATEST_APEX_WORKER_V1";
static void *a52_r370_sideband;
static atomic64_t a52_r370_sequence = ATOMIC64_INIT(0);
static u32 a52_r370_target_pid;
static u32 a52_r370_target_tgid;

static bool a52_r370_apexd_group_leader(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static void a52_r370_copy_user_string(u8 *dst, size_t cap,
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

static void a52_r370_copy_writev(struct a52_r370_record *r,
				 struct pt_regs *regs)
{
	struct iovec iov[4];
	unsigned int nr;
	unsigned int i;
	unsigned long iovcnt = regs->regs[2];
	size_t left = sizeof(r->payload);
	size_t n;
	u8 *dst = r->payload;

	if (!iovcnt)
		return;

	nr = min_t(unsigned int, (unsigned int)iovcnt, ARRAY_SIZE(iov));
	if (copy_from_user(iov, (const void __user *)regs->regs[1],
			   nr * sizeof(iov[0])))
		return;

	for (i = 0; i < nr && left; i++) {
		n = min_t(size_t, iov[i].iov_len, left);
		if (!n)
			continue;
		if (copy_from_user(dst, iov[i].iov_base, n))
			break;
		dst += n;
		left -= n;
	}
	r->payload_len = (u32)(sizeof(r->payload) - left);
	r->payload_kind = A52_R370_PAYLOAD_WRITEV;
}

static void a52_r370_capture_payload(struct a52_r370_record *r,
				      struct pt_regs *regs, int scno)
{
	const char __user *p0 =
		(const char __user *)(unsigned long)regs->orig_x0;
	const char __user *p1 =
		(const char __user *)(unsigned long)regs->regs[1];
	const char __user *p2 =
		(const char __user *)(unsigned long)regs->regs[2];

	if (scno == __NR_writev) {
		a52_r370_copy_writev(r, regs);
		return;
	}

	switch (scno) {
	case __NR_openat:
	case __NR_newfstatat:
	case __NR_faccessat:
	case __NR_mkdirat:
	case __NR_unlinkat:
	case __NR_readlinkat:
		r->payload_kind = A52_R370_PAYLOAD_PATH_X1;
		a52_r370_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#ifdef __NR_openat2
	case __NR_openat2:
		r->payload_kind = A52_R370_PAYLOAD_PATH_X1;
		a52_r370_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#endif
#ifdef __NR_statx
	case __NR_statx:
		r->payload_kind = A52_R370_PAYLOAD_PATH_X1;
		a52_r370_copy_user_string(r->payload, sizeof(r->payload), p1);
		break;
#endif
	case __NR_mount:
		r->payload_kind = A52_R370_PAYLOAD_MOUNT;
		a52_r370_copy_user_string(r->payload, 20, p0);
		a52_r370_copy_user_string(r->payload + 20, 28, p1);
		a52_r370_copy_user_string(r->payload + 48, 12, p2);
		break;
	default:
		break;
	}

	if (r->payload_kind && !r->payload_len)
		r->payload_len = sizeof(r->payload);
}

static void a52_r370_fill(struct a52_r370_record *r,
			  struct pt_regs *regs, int scno, u32 event,
			  u64 sequence, s64 ret, unsigned int slot)
{
	unsigned int i;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R370_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->target_pid = READ_ONCE(a52_r370_target_pid);
	r->target_tgid = READ_ONCE(a52_r370_target_tgid);
	r->pc = regs->pc;
	r->lr = regs->regs[30];
	r->sp = regs->sp;
	r->mm = (u64)(unsigned long)current->mm;
	r->clear_child_tid =
		(u64)(unsigned long)READ_ONCE(current->clear_child_tid);
	r->x[0] = regs->orig_x0;
	for (i = 1; i < ARRAY_SIZE(r->x); i++)
		r->x[i] = regs->regs[i];
	r->ret = ret;
	r->event = event;
	r->syscallno = (u32)scno;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->ppid = (u32)task_ppid_nr(current);
	r->ring_index = slot;
	r->commit = A52_R370_COMMIT;
	r->version = A52_R370_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
}

static void a52_r370_write_slot(unsigned int slot,
				const struct a52_r370_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r370_sideband) || !r ||
	    slot >= A52_R370_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R370_SLOT_BYTES;
	dst0 = (u8 *)a52_r370_sideband + pos;
	dst1 = (u8 *)a52_r370_sideband + A52_R370_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r370_write_target_header(struct pt_regs *regs, int scno,
					 s64 child)
{
	struct a52_r370_record r;
	unsigned int i;

	a52_r370_fill(&r, regs, scno, A52_R370_EVT_TARGET, 0, child, 0);
	for (i = 0; i < A52_R370_HEADER_SLOTS; i++) {
		r.ring_index = i;
		a52_r370_write_slot(i, &r);
	}
}

static void a52_r370_note_clone_return(struct pt_regs *regs, int scno)
{
	s64 child = (s64)regs->regs[0];

	if (!READ_ONCE(a52_r370_sideband) || !a52_r370_apexd_group_leader())
		return;
	if (scno != __NR_clone
#ifdef __NR_clone3
	    && scno != __NR_clone3
#endif
	    )
		return;
	if (child <= 0 || child > INT_MAX)
		return;

	WRITE_ONCE(a52_r370_target_tgid, (u32)current->tgid);
	WRITE_ONCE(a52_r370_target_pid, (u32)child);
	atomic64_set(&a52_r370_sequence, 0);
	a52_r370_write_target_header(regs, scno, child);
}

static bool a52_r370_is_target(void)
{
	u32 pid = READ_ONCE(a52_r370_target_pid);
	u32 tgid = READ_ONCE(a52_r370_target_tgid);

	return pid && current->pid == pid && current->tgid == tgid;
}

static u64 a52_r370_worker_enter(struct pt_regs *regs, int scno)
{
	struct a52_r370_record r;
	u64 sequence;
	unsigned int slot;
	u32 event = A52_R370_EVT_ENTRY;

	if (!READ_ONCE(a52_r370_sideband) || !a52_r370_is_target())
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r370_sequence);
	slot = A52_R370_RING_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R370_RING_SLOTS);

	if (scno == __NR_exit || scno == __NR_exit_group)
		event = A52_R370_EVT_EXIT_ENTRY;

	a52_r370_fill(&r, regs, scno, event, sequence,
		      (s64)(1ULL << 63), slot);
	a52_r370_capture_payload(&r, regs, scno);
	a52_r370_write_slot(slot, &r);
	return sequence;
}

static void a52_r370_worker_return(struct pt_regs *regs, int scno, u64 token)
{
	struct a52_r370_record r;
	unsigned int slot;

	if (!token || !READ_ONCE(a52_r370_sideband) || !a52_r370_is_target())
		return;

	slot = A52_R370_RING_FIRST +
	       (unsigned int)((token - 1ULL) % A52_R370_RING_SLOTS);
	a52_r370_fill(&r, regs, scno, A52_R370_EVT_RETURN, token,
		      (s64)regs->regs[0], slot);
	a52_r370_capture_payload(&r, regs, scno);
	a52_r370_write_slot(slot, &r);
}

static int __init a52_r370_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r370_record) != A52_R370_SLOT_BYTES);
	BUILD_BUG_ON(A52_R370_SLOTS_PER_COPY * A52_R370_SLOT_BYTES !=
		     A52_R370_COPY_BYTES);
	BUILD_BUG_ON(A52_R370_HEADER_SLOTS + A52_R370_RING_SLOTS !=
		     A52_R370_SLOTS_PER_COPY);

	a52_r370_sideband = memremap(A52_R370_SIDEBAND_PHYS,
		A52_R370_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r370_sideband)
		return 0;

	memset(a52_r370_sideband, 0, A52_R370_SIDEBAND_BYTES);
	atomic64_set(&a52_r370_sequence, 0);
	WRITE_ONCE(a52_r370_target_pid, 0);
	WRITE_ONCE(a52_r370_target_tgid, 0);
	wmb();
	__flush_dcache_area(a52_r370_sideband, A52_R370_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r370_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE369_POST_APEX_FRONTIER_V1" not in text:
        raise SystemExit("Phase370 requires Phase369 syscall lineage")

    if "static int __init __used a52_r369_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r369_init(void)\n",
            "static int __init __used a52_r369_init(void)\n",
            "mark Phase369 init dormant",
        )
    if "late_initcall(a52_r369_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r369_init);\n",
            "/* Phase370 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase369 mapper",
        )

    anchor = (
        'static const char a52_r369_marker[] __used =\n'
        '\t"A52_PHASE369_POST_APEX_FRONTIER_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase370 block insertion")

    text = one(
        text,
        "\tu64 a52_r369_sequence_token = 0;\n",
        "\tu64 a52_r369_sequence_token = 0;\n"
        "\tu64 a52_r370_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r369_sequence_token = a52_r369_main_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r369_main_return(regs, scno, a52_r369_sequence_token);\n"
    )
    new = (
        "\ta52_r369_sequence_token = a52_r369_main_enter(regs, scno);\n"
        "\ta52_r370_sequence_token = a52_r370_worker_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r370_worker_return(regs, scno, a52_r370_sequence_token);\n"
        "\ta52_r369_main_return(regs, scno, a52_r369_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R370_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R370_HEADER_SLOTS       4U",
        "A52_R370_RING_SLOTS         60U",
        "A52_R370_COMMIT             0x370c0de5U",
        "a52_r370_note_clone_return(regs, scno);",
        "a52_r370_worker_enter(regs, scno);",
        "a52_r370_worker_return(regs, scno, a52_r370_sequence_token);",
        "WRITE_ONCE(a52_r370_target_pid, (u32)child);",
        "late_initcall(a52_r370_init);",
    ):
        if token not in text:
            raise SystemExit("Phase370 required token missing: " + token)

    if "late_initcall(a52_r369_init);" in text:
        raise SystemExit("Phase370 must disable Phase369 sideband owner")
    if "static int __init __used a52_r369_init(void)" not in text:
        raise SystemExit("Phase370 dormant Phase369 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYSCALL
    if not path.is_file():
        raise SystemExit("Phase370 syscall source missing")

    before = path.read_text(encoding="utf-8")
    if MARK in before:
        validate(before)
        print("Phase370 latest apexd worker audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase370 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase370 latest apexd worker recorder applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
