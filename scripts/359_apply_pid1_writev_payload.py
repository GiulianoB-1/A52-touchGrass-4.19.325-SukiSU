#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE359_PID1_WRITEV_PAYLOAD_V1"

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase359 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)

BLOCK = r'''
/* A52_PHASE359_PID1_WRITEV_PAYLOAD_V1
 *
 * Phase358 proved PID1 reaches Bionic abort() after a first-stage-mount
 * timeout. Phase359 captures the last four PID1 writev payloads so the
 * exact Android init error text and missing partition/device names survive.
 *
 * Normal writev calls update only a four-entry RAM ring. On PID1
 * exit/exit_group, each of the four records is replicated four times in each
 * 16 KiB mirror of the 32 KiB Phase358 sideband.
 */
#define A52_R359_SIDEBAND_PHYS    0xB1BF0000ULL
#define A52_R359_SIDEBAND_BYTES   0x8000U
#define A52_R359_COPY_BYTES       0x4000U
#define A52_R359_SLOT_BYTES       1024U
#define A52_R359_RING_SLOTS       4U
#define A52_R359_REPLICAS         4U
#define A52_R359_SLOTS_PER_COPY   16U
#define A52_R359_MAGIC            0x3935335654495257ULL /* "WRITV359" */
#define A52_R359_COMMIT           0x359c0de5U
#define A52_R359_VERSION          1U

#define A52_R359_EVT_ENTRY        1U
#define A52_R359_EVT_RETURN       2U

struct a52_r359_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 pc;
	u64 lr;
	u64 sp;
	s64 ret;
	u32 event;
	s32 fd;
	u32 iovcnt;
	u32 payload_len;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ring_index;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 payload[912];
};

static const char a52_r359_marker[] __used =
	"A52_PHASE359_PID1_WRITEV_PAYLOAD_V1";
static void *a52_r359_sideband;
static struct a52_r359_record a52_r359_ring[A52_R359_RING_SLOTS];
static u64 a52_r359_sequence;
static u32 a52_r359_count;

static void a52_r359_copy_payload(struct a52_r359_record *r,
				  struct pt_regs *regs)
{
	struct iovec iov[8];
	unsigned long iovcnt = regs->regs[2];
	unsigned int nr;
	unsigned int i;
	size_t left = sizeof(r->payload);
	size_t n;
	u8 *dst = r->payload;

	if (!iovcnt)
		return;
	nr = iovcnt > ARRAY_SIZE(iov) ? ARRAY_SIZE(iov) : (unsigned int)iovcnt;
	if (copy_from_user(iov, (const void __user *)regs->regs[1],
			   nr * sizeof(iov[0])))
		return;

	r->iovcnt = (u32)iovcnt;
	for (i = 0; i < nr && left; i++) {
		n = iov[i].iov_len;
		if (n > left)
			n = left;
		if (!n)
			continue;
		if (copy_from_user(dst, iov[i].iov_base, n))
			break;
		dst += n;
		left -= n;
	}
	r->payload_len = (u32)(sizeof(r->payload) - left);
}

static u64 a52_r359_writev_enter(struct pt_regs *regs, int scno)
{
	struct a52_r359_record r;
	u64 sequence;
	unsigned int pos;

	if (current->pid != 1 || scno != __NR_writev ||
	    !READ_ONCE(a52_r359_sideband))
		return 0;

	sequence = ++a52_r359_sequence;
	pos = (unsigned int)((sequence - 1ULL) % A52_R359_RING_SLOTS);
	memset(&r, 0, sizeof(r));
	r.magic = A52_R359_MAGIC;
	r.ns = ktime_get_ns();
	r.sequence = sequence;
	r.pc = regs->pc;
	r.lr = regs->regs[30];
	r.sp = regs->sp;
	r.ret = (s64)(1ULL << 63);
	r.event = A52_R359_EVT_ENTRY;
	r.fd = (s32)regs->orig_x0;
	r.cpu = (u32)raw_smp_processor_id();
	r.pid = (u32)current->pid;
	r.tgid = (u32)current->tgid;
	r.ring_index = pos;
	r.commit = A52_R359_COMMIT;
	r.version = A52_R359_VERSION;
	memcpy(r.comm, current->comm, TASK_COMM_LEN);
	a52_r359_copy_payload(&r, regs);
	memcpy(&a52_r359_ring[pos], &r, sizeof(r));
	if (a52_r359_count < A52_R359_RING_SLOTS)
		a52_r359_count++;
	return sequence;
}

static void a52_r359_writev_return(struct pt_regs *regs, u64 sequence)
{
	unsigned int pos;
	struct a52_r359_record *r;

	if (!sequence || current->pid != 1)
		return;
	pos = (unsigned int)((sequence - 1ULL) % A52_R359_RING_SLOTS);
	r = &a52_r359_ring[pos];
	if (READ_ONCE(r->sequence) != sequence ||
	    READ_ONCE(r->commit) != A52_R359_COMMIT)
		return;
	WRITE_ONCE(r->ret, (s64)regs->regs[0]);
	WRITE_ONCE(r->event, A52_R359_EVT_RETURN);
}

static void a52_r359_write_slot(unsigned int slot,
				const struct a52_r359_record *r)
{
	unsigned int pos = slot * A52_R359_SLOT_BYTES;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r359_sideband) || !r ||
	    slot >= A52_R359_SLOTS_PER_COPY)
		return;
	dst0 = (u8 *)a52_r359_sideband + pos;
	dst1 = (u8 *)a52_r359_sideband + A52_R359_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
}

static void a52_r359_persist(void)
{
	u64 last;
	u64 first;
	u64 seq;
	unsigned int count;
	unsigned int i;
	unsigned int rep;
	unsigned int pos;
	const struct a52_r359_record *r;

	if (!READ_ONCE(a52_r359_sideband))
		return;
	count = READ_ONCE(a52_r359_count);
	if (!count)
		return;
	if (count > A52_R359_RING_SLOTS)
		count = A52_R359_RING_SLOTS;

	last = READ_ONCE(a52_r359_sequence);
	first = last - count + 1ULL;
	for (i = 0; i < count; i++) {
		seq = first + i;
		pos = (unsigned int)((seq - 1ULL) % A52_R359_RING_SLOTS);
		r = &a52_r359_ring[pos];
		if (READ_ONCE(r->sequence) != seq ||
		    READ_ONCE(r->commit) != A52_R359_COMMIT)
			continue;
		for (rep = 0; rep < A52_R359_REPLICAS; rep++)
			a52_r359_write_slot(i * A52_R359_REPLICAS + rep, r);
	}
	wmb();
	__flush_dcache_area(a52_r359_sideband, A52_R359_SIDEBAND_BYTES);
}

static void a52_r359_maybe_persist(int scno)
{
	if (current->pid == 1 &&
	    (scno == __NR_exit_group || scno == __NR_exit))
		a52_r359_persist();
}

static int __init a52_r359_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r359_record) != A52_R359_SLOT_BYTES);
	BUILD_BUG_ON(A52_R359_SLOTS_PER_COPY * A52_R359_SLOT_BYTES !=
		     A52_R359_COPY_BYTES);
	BUILD_BUG_ON(A52_R359_RING_SLOTS * A52_R359_REPLICAS !=
		     A52_R359_SLOTS_PER_COPY);

	a52_r359_sideband = memremap(A52_R359_SIDEBAND_PHYS,
		A52_R359_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r359_sideband)
		return 0;
	memset(a52_r359_sideband, 0, A52_R359_SIDEBAND_BYTES);
	memset(a52_r359_ring, 0, sizeof(a52_r359_ring));
	a52_r359_sequence = 0;
	a52_r359_count = 0;
	wmb();
	__flush_dcache_area(a52_r359_sideband, A52_R359_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r359_init);

'''

def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE358_PID1_SYSCALL_TRACE_V1" not in text:
        raise SystemExit("Phase359 requires Phase358 syscall lineage")

    text = one(text,
               "#include <linux/string.h>\n",
               "#include <linux/string.h>\n#include <linux/uaccess.h>\n#include <linux/uio.h>\n",
               "writev includes")
    text = one(text,
               'static const char a52_r358_marker[] __used = "A52_PHASE358_PID1_SYSCALL_TRACE_V1";\n',
               BLOCK + 'static const char a52_r358_marker[] __used = "A52_PHASE358_PID1_SYSCALL_TRACE_V1";\n',
               "recorder insertion")
    text = one(text,
               "static int __init a52_r358_init(void)\n",
               "static int __init __used a52_r358_init(void)\n",
               "mark Phase358 init dormant")
    text = one(text,
               "late_initcall(a52_r358_init);\n",
               "/* Phase359 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
               "disable Phase358 sideband mapper")
    text = one(text,
               "\tu64 a52_r358_sequence_token = 0;\n",
               "\tu64 a52_r358_sequence_token = 0;\n\tu64 a52_r359_sequence_token = 0;\n",
               "Phase359 sequence token")
    old = (
        "\ta52_r358_sequence_token = a52_r358_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r358_sys_return(regs, scno, a52_r358_sequence_token);\n"
    )
    new = (
        "\ta52_r359_maybe_persist(scno);\n"
        "\ta52_r359_sequence_token = a52_r359_writev_enter(regs, scno);\n"
        "\ta52_r358_sequence_token = a52_r358_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r358_sys_return(regs, scno, a52_r358_sequence_token);\n"
        "\ta52_r359_writev_return(regs, a52_r359_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text

def validate(before: str, after: str) -> None:
    for token in (
        MARK,
        "A52_R359_SIDEBAND_PHYS    0xB1BF0000ULL",
        "A52_R359_SLOT_BYTES       1024U",
        "A52_R359_RING_SLOTS       4U",
        "A52_R359_REPLICAS         4U",
        "A52_R359_COMMIT           0x359c0de5U",
        "a52_r359_maybe_persist(scno);",
        "a52_r359_writev_enter(regs, scno);",
        "a52_r359_writev_return(regs, a52_r359_sequence_token);",
        "late_initcall(a52_r359_init);",
    ):
        if token not in after:
            raise SystemExit("Phase359 required token missing: " + token)
    if "late_initcall(a52_r358_init);" in after:
        raise SystemExit("Phase359 failed to disable Phase358 mapper")
    if after.count("invoke_syscall(regs, scno, sc_nr, syscall_table);") != before.count("invoke_syscall(regs, scno, sc_nr, syscall_table);"):
        raise SystemExit("Phase359 changed invoke_syscall count")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    path = ns.root / SYS
    if not path.is_file():
        raise SystemExit("Phase359 missing syscall source: " + str(path))
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        validate(before, before)
        print("Phase359 PID1 writev payload audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase359 marker missing in check-only mode")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase359 PID1 writev payload recorder applied")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
