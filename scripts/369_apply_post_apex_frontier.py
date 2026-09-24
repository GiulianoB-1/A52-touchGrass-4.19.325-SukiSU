#!/usr/bin/env python3
# Phase369 workflow trigger v1
# Phase369 rebuild trigger v2
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE369_POST_APEX_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase369 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE369_POST_APEX_FRONTIER_V1
 *
 * Phase368 hardware result:
 * - bootstrap apexd exited normally
 * - the later apexd activation workers captured by the per-thread map exited
 * - the later apexd group leader remained alive at the Phase339 safety cutoff
 *   in syscall 101 (__NR_nanosleep)
 * - inherited Phase346 DSI raw slots p0..p6 were still empty
 *
 * Phase369 answers two questions in one boot:
 * 1) Why is the main apexd sleeping after activation?  Record a chronological
 *    group-leader syscall ring, including nanosleep duration and writev/path
 *    payloads.
 * 2) How far did init continue after apexd activation?  Record execve/execveat
 *    attempts for direct PID1 children in a second ring.
 *
 * 32 KiB sideband, mirrored 16 KiB copies.  Each copy contains:
 * slots  0..31: apexd group-leader syscall chronology
 * slots 32..63: direct-init-child exec chronology
 *
 * Observation only.
 */
#define A52_R369_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R369_SIDEBAND_BYTES     0x8000U
#define A52_R369_COPY_BYTES         0x4000U
#define A52_R369_SLOT_BYTES         256U
#define A52_R369_SLOTS_PER_COPY     64U
#define A52_R369_MAIN_FIRST         0U
#define A52_R369_MAIN_SLOTS         32U
#define A52_R369_EXEC_FIRST         32U
#define A52_R369_EXEC_SLOTS         32U
#define A52_R369_MAGIC              0x3936335846504141ULL
#define A52_R369_COMMIT             0x369c0de5U
#define A52_R369_VERSION            1U

#define A52_R369_EVT_MAIN_ENTRY     1U
#define A52_R369_EVT_MAIN_RETURN    2U
#define A52_R369_EVT_INIT_EXEC      10U

#define A52_R369_PAYLOAD_NONE       0U
#define A52_R369_PAYLOAD_WRITEV     1U
#define A52_R369_PAYLOAD_PATH       2U

struct a52_r369_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 mm;
	u64 x[6];
	s64 ret;
	s64 sleep_sec;
	s64 sleep_nsec;
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
	u8 payload[68];
};

static const char a52_r369_marker[] __used =
	"A52_PHASE369_POST_APEX_FRONTIER_V1";
static void *a52_r369_sideband;
static atomic64_t a52_r369_main_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_r369_exec_sequence = ATOMIC64_INIT(0);

static bool a52_r369_apexd_group_leader(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static void a52_r369_copy_user_string(u8 *dst, size_t cap,
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

static void a52_r369_copy_writev(struct a52_r369_record *r,
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
	r->payload_kind = A52_R369_PAYLOAD_WRITEV;
}

static void a52_r369_capture_sleep(struct a52_r369_record *r,
				   struct pt_regs *regs, int scno)
{
	struct {
		s64 tv_sec;
		s64 tv_nsec;
	} ts;
	const void __user *p = NULL;

	if (scno == __NR_nanosleep)
		p = (const void __user *)(unsigned long)regs->orig_x0;
#ifdef __NR_clock_nanosleep
	else if (scno == __NR_clock_nanosleep)
		p = (const void __user *)(unsigned long)regs->regs[2];
#endif
	if (!p)
		return;
	if (copy_from_user(&ts, p, sizeof(ts)))
		return;
	r->sleep_sec = ts.tv_sec;
	r->sleep_nsec = ts.tv_nsec;
}

static void a52_r369_capture_main_payload(struct a52_r369_record *r,
					  struct pt_regs *regs, int scno)
{
	const char __user *path = NULL;

	a52_r369_capture_sleep(r, regs, scno);

	if (scno == __NR_writev) {
		a52_r369_copy_writev(r, regs);
		return;
	}

	switch (scno) {
	case __NR_openat:
	case __NR_newfstatat:
	case __NR_faccessat:
	case __NR_mkdirat:
	case __NR_unlinkat:
	case __NR_readlinkat:
		path = (const char __user *)(unsigned long)regs->regs[1];
		break;
#ifdef __NR_openat2
	case __NR_openat2:
		path = (const char __user *)(unsigned long)regs->regs[1];
		break;
#endif
#ifdef __NR_faccessat2
	case __NR_faccessat2:
		path = (const char __user *)(unsigned long)regs->regs[1];
		break;
#endif
#ifdef __NR_statx
	case __NR_statx:
		path = (const char __user *)(unsigned long)regs->regs[1];
		break;
#endif
	default:
		break;
	}

	if (path) {
		r->payload_kind = A52_R369_PAYLOAD_PATH;
		a52_r369_copy_user_string(r->payload, sizeof(r->payload), path);
		r->payload_len = strnlen((const char *)r->payload,
					sizeof(r->payload));
	}
}

static void a52_r369_fill(struct a52_r369_record *r,
			  struct pt_regs *regs, int scno, u32 event,
			  u64 sequence, s64 ret, unsigned int slot)
{
	unsigned int i;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R369_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->pc = regs->pc;
	r->lr = regs->regs[30];
	r->sp = regs->sp;
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
	r->ppid = (u32)task_ppid_nr(current);
	r->ring_index = slot;
	r->commit = A52_R369_COMMIT;
	r->version = A52_R369_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
}

static void a52_r369_write_slot(unsigned int slot,
				const struct a52_r369_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r369_sideband) || !r ||
	    slot >= A52_R369_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R369_SLOT_BYTES;
	dst0 = (u8 *)a52_r369_sideband + pos;
	dst1 = (u8 *)a52_r369_sideband + A52_R369_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static u64 a52_r369_main_enter(struct pt_regs *regs, int scno)
{
	struct a52_r369_record r;
	u64 sequence;
	unsigned int slot;

	if (!READ_ONCE(a52_r369_sideband) || !a52_r369_apexd_group_leader())
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r369_main_sequence);
	slot = A52_R369_MAIN_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R369_MAIN_SLOTS);
	a52_r369_fill(&r, regs, scno, A52_R369_EVT_MAIN_ENTRY, sequence,
		      (s64)(1ULL << 63), slot);
	a52_r369_capture_main_payload(&r, regs, scno);
	a52_r369_write_slot(slot, &r);
	return sequence;
}

static void a52_r369_main_return(struct pt_regs *regs, int scno, u64 token)
{
	struct a52_r369_record r;
	u64 sequence;
	unsigned int slot;

	if (!token || !READ_ONCE(a52_r369_sideband) ||
	    !a52_r369_apexd_group_leader())
		return;

	sequence = (u64)atomic64_inc_return(&a52_r369_main_sequence);
	slot = A52_R369_MAIN_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R369_MAIN_SLOTS);
	a52_r369_fill(&r, regs, scno, A52_R369_EVT_MAIN_RETURN, sequence,
		      (s64)regs->regs[0], slot);
	a52_r369_capture_main_payload(&r, regs, scno);
	a52_r369_write_slot(slot, &r);
}

static void a52_r369_maybe_init_exec(struct pt_regs *regs, int scno)
{
	struct a52_r369_record r;
	const char __user *path;
	u64 sequence;
	unsigned int slot;

	if (!READ_ONCE(a52_r369_sideband) || task_ppid_nr(current) != 1)
		return;
	if (scno != __NR_execve && scno != __NR_execveat)
		return;

	if (scno == __NR_execve)
		path = (const char __user *)(unsigned long)regs->orig_x0;
	else
		path = (const char __user *)(unsigned long)regs->regs[1];

	sequence = (u64)atomic64_inc_return(&a52_r369_exec_sequence);
	slot = A52_R369_EXEC_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R369_EXEC_SLOTS);
	a52_r369_fill(&r, regs, scno, A52_R369_EVT_INIT_EXEC, sequence,
		      (s64)(1ULL << 63), slot);
	r.payload_kind = A52_R369_PAYLOAD_PATH;
	a52_r369_copy_user_string(r.payload, sizeof(r.payload), path);
	r.payload_len = strnlen((const char *)r.payload, sizeof(r.payload));
	a52_r369_write_slot(slot, &r);
}

static int __init a52_r369_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r369_record) != A52_R369_SLOT_BYTES);
	BUILD_BUG_ON(A52_R369_SLOTS_PER_COPY * A52_R369_SLOT_BYTES !=
		     A52_R369_COPY_BYTES);
	BUILD_BUG_ON(A52_R369_MAIN_SLOTS + A52_R369_EXEC_SLOTS !=
		     A52_R369_SLOTS_PER_COPY);

	a52_r369_sideband = memremap(A52_R369_SIDEBAND_PHYS,
		A52_R369_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r369_sideband)
		return 0;

	memset(a52_r369_sideband, 0, A52_R369_SIDEBAND_BYTES);
	atomic64_set(&a52_r369_main_sequence, 0);
	atomic64_set(&a52_r369_exec_sequence, 0);
	wmb();
	__flush_dcache_area(a52_r369_sideband, A52_R369_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r369_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE368_APEXD_WORKER_SYSCALL_MAP_V1" not in text:
        raise SystemExit("Phase369 requires Phase368 syscall lineage")

    if "#include <linux/uaccess.h>\n" not in text:
        text = "#include <linux/uaccess.h>\n" + text
    if "#include <linux/uio.h>\n" not in text:
        text = "#include <linux/uio.h>\n" + text

    # Retire Phase368 runtime ownership of the shared 32 KiB sideband.
    if "static int __init __used a52_r368_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r368_init(void)\n",
            "static int __init __used a52_r368_init(void)\n",
            "mark Phase368 init dormant",
        )
    if "late_initcall(a52_r368_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r368_init);\n",
            "/* Phase369 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase368 mapper",
        )

    anchor = (
        'static const char a52_r368_marker[] __used =\n'
        '\t"A52_PHASE368_APEXD_WORKER_SYSCALL_MAP_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase369 block insertion")

    text = one(
        text,
        "\tu64 a52_r368_sequence_token = 0;\n",
        "\tu64 a52_r368_sequence_token = 0;\n"
        "\tu64 a52_r369_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r368_sequence_token = a52_r368_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r368_sys_return(regs, scno, a52_r368_sequence_token);\n"
    )
    new = (
        "\ta52_r368_sequence_token = a52_r368_sys_enter(regs, scno);\n"
        "\ta52_r369_maybe_init_exec(regs, scno);\n"
        "\ta52_r369_sequence_token = a52_r369_main_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r369_main_return(regs, scno, a52_r369_sequence_token);\n"
        "\ta52_r368_sys_return(regs, scno, a52_r368_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R369_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R369_MAIN_SLOTS         32U",
        "A52_R369_EXEC_SLOTS         32U",
        "A52_R369_COMMIT             0x369c0de5U",
        "A52_R369_EVT_INIT_EXEC",
        "a52_r369_capture_sleep",
        "task_ppid_nr(current) != 1",
        "a52_r369_maybe_init_exec(regs, scno);",
        "a52_r369_main_enter(regs, scno);",
        "a52_r369_main_return(regs, scno, a52_r369_sequence_token);",
        "late_initcall(a52_r369_init);",
    ):
        if token not in text:
            raise SystemExit("Phase369 required token missing: " + token)

    if "late_initcall(a52_r368_init);" in text:
        raise SystemExit("Phase369 must disable Phase368 sideband owner")
    if "static int __init __used a52_r368_init(void)" not in text:
        raise SystemExit("Phase369 dormant Phase368 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYSCALL
    if not path.is_file():
        raise SystemExit("Phase369 syscall source missing")

    before = path.read_text(encoding="utf-8")
    if MARK in before:
        validate(before)
        print("Phase369 post-apex frontier audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase369 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase369 post-apex frontier applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
