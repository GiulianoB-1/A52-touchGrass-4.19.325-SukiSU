#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE365_APEXD_WRITEV_FUTEX_V1"

BLOCK = r'''
/* A52_PHASE365_APEXD_WRITEV_FUTEX_V1
 *
 * Phase364 hardware caught the second apexd group leader entering
 * futex(FUTEX_WAIT_BITSET_PRIVATE) at ~19.035167649 s and never returning.
 * The immediately preceding group-leader syscall (~27.5 us earlier) is writev.
 *
 * Reuse the Phase364 32 KiB sideband as a 16-entry, 1 KiB circular event ring
 * mirrored into two 16 KiB copies. Trace only the apexd group leader.
 *
 * WRITEV records preserve up to 844 payload bytes. FUTEX records preserve all
 * six syscall arguments. A blocking futex remains as an ENTRY record with the
 * INT64_MIN return sentinel. Returned calls are rewritten in-place and flushed.
 */
#define A52_R365_SIDEBAND_PHYS    0xB1BF0000ULL
#define A52_R365_SIDEBAND_BYTES   0x8000U
#define A52_R365_COPY_BYTES       0x4000U
#define A52_R365_SLOT_BYTES       1024U
#define A52_R365_SLOTS            16U
#define A52_R365_PAYLOAD_BYTES    844U
#define A52_R365_MAGIC            0x3536335746585041ULL
#define A52_R365_COMMIT           0x365c0de5U
#define A52_R365_VERSION          1U

#define A52_R365_EVT_WRITEV_ENTRY  1U
#define A52_R365_EVT_WRITEV_RETURN 2U
#define A52_R365_EVT_FUTEX_ENTRY   3U
#define A52_R365_EVT_FUTEX_RETURN  4U

struct a52_r365_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 x[6];
	s64 ret;
	u32 event;
	u32 syscallno;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ring_index;
	u32 commit;
	u32 version;
	s32 fd;
	u32 iovcnt;
	u32 payload_len;
	char comm[TASK_COMM_LEN];
	u8 payload[A52_R365_PAYLOAD_BYTES];
	u8 reserved[16];
};

static const char a52_r365_marker[] __used =
	"A52_PHASE365_APEXD_WRITEV_FUTEX_V1";
static void *a52_r365_sideband;
static atomic64_t a52_r365_sequence = ATOMIC64_INIT(0);

static bool a52_r365_target(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static void a52_r365_copy_writev(struct a52_r365_record *r,
				 struct pt_regs *regs)
{
	struct iovec iov[8];
	unsigned long iovcnt = regs->regs[2];
	unsigned int nr;
	unsigned int i;
	size_t left = sizeof(r->payload);
	size_t n;
	u8 *dst = r->payload;

	r->fd = (s32)regs->orig_x0;
	r->iovcnt = (u32)iovcnt;
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
}

static void a52_r365_fill(struct a52_r365_record *r,
			  struct pt_regs *regs, int scno, u32 event,
			  u64 sequence, s64 ret)
{
	unsigned int i;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R365_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->pc = regs->pc;
	r->lr = regs->regs[30];
	r->sp = regs->sp;
	r->x[0] = regs->orig_x0;
	for (i = 1; i < ARRAY_SIZE(r->x); i++)
		r->x[i] = regs->regs[i];
	r->ret = ret;
	r->event = event;
	r->syscallno = (u32)scno;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->ring_index = (u32)((sequence - 1ULL) % A52_R365_SLOTS);
	r->commit = A52_R365_COMMIT;
	r->version = A52_R365_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);

	if (scno == __NR_writev)
		a52_r365_copy_writev(r, regs);
}

static void a52_r365_write(unsigned int slot,
			   const struct a52_r365_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r365_sideband) || !r || slot >= A52_R365_SLOTS)
		return;

	pos = slot * A52_R365_SLOT_BYTES;
	dst0 = (u8 *)a52_r365_sideband + pos;
	dst1 = (u8 *)a52_r365_sideband + A52_R365_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static u64 a52_r365_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r365_record r;
	u64 sequence;
	unsigned int slot;
	u32 event;

	if (!READ_ONCE(a52_r365_sideband) || !a52_r365_target())
		return 0;
	if (scno != __NR_writev && scno != __NR_futex)
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r365_sequence);
	slot = (unsigned int)((sequence - 1ULL) % A52_R365_SLOTS);
	event = scno == __NR_writev ?
		A52_R365_EVT_WRITEV_ENTRY : A52_R365_EVT_FUTEX_ENTRY;
	a52_r365_fill(&r, regs, scno, event, sequence,
		      (s64)(1ULL << 63));
	a52_r365_write(slot, &r);
	return sequence;
}

static void a52_r365_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r365_record r;
	unsigned int slot;
	u32 event;

	if (!sequence || !READ_ONCE(a52_r365_sideband) || !a52_r365_target())
		return;

	slot = (unsigned int)((sequence - 1ULL) % A52_R365_SLOTS);
	event = scno == __NR_writev ?
		A52_R365_EVT_WRITEV_RETURN : A52_R365_EVT_FUTEX_RETURN;
	a52_r365_fill(&r, regs, scno, event, sequence, (s64)regs->regs[0]);
	a52_r365_write(slot, &r);
}

static int __init a52_r365_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r365_record) != A52_R365_SLOT_BYTES);
	BUILD_BUG_ON(A52_R365_SLOTS * A52_R365_SLOT_BYTES !=
		     A52_R365_COPY_BYTES);

	a52_r365_sideband = memremap(A52_R365_SIDEBAND_PHYS,
		A52_R365_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r365_sideband)
		return 0;

	memset(a52_r365_sideband, 0, A52_R365_SIDEBAND_BYTES);
	atomic64_set(&a52_r365_sequence, 0);
	wmb();
	__flush_dcache_area(a52_r365_sideband, A52_R365_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r365_init);

'''


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase365 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R365_SIDEBAND_PHYS    0xB1BF0000ULL",
        "A52_R365_SLOT_BYTES       1024U",
        "A52_R365_SLOTS            16U",
        "A52_R365_PAYLOAD_BYTES    844U",
        "A52_R365_COMMIT           0x365c0de5U",
        "a52_r365_sys_enter(regs, scno);",
        "a52_r365_sys_return(regs, scno, a52_r365_sequence_token);",
        "late_initcall(a52_r365_init);",
        "scno != __NR_writev && scno != __NR_futex",
    ):
        if token not in text:
            raise SystemExit("Phase365 required token missing: " + token)
    if "late_initcall(a52_r364_init);" in text:
        raise SystemExit("Phase365 must disable the Phase364 sideband owner")
    if text.count("invoke_syscall(regs, scno, sc_nr, syscall_table);") != 1:
        raise SystemExit("Phase365 invoke_syscall count mismatch")


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE364_APEXD_SYSCALL_RING_V1" not in text:
        raise SystemExit("Phase365 requires Phase364 syscall lineage")

    text = one(
        text,
        "static int __init a52_r364_init(void)\n",
        "static int __init __used a52_r364_init(void)\n",
        "mark Phase364 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r364_init);\n",
        "/* Phase365 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase364 mapper",
    )

    anchor = (
        'static const char a52_r364_marker[] __used =\n'
        '\t"A52_PHASE364_APEXD_SYSCALL_RING_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase365 block insertion")

    text = one(
        text,
        "\tu64 a52_r364_sequence_token = 0;\n",
        "\tu64 a52_r364_sequence_token = 0;\n"
        "\tu64 a52_r365_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r364_sequence_token = a52_r364_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r364_sys_return(regs, scno, a52_r364_sequence_token);\n"
    )
    new = (
        "\ta52_r364_sequence_token = a52_r364_sys_enter(regs, scno);\n"
        "\ta52_r365_sequence_token = a52_r365_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r365_sys_return(regs, scno, a52_r365_sequence_token);\n"
        "\ta52_r364_sys_return(regs, scno, a52_r364_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYS
    if not path.is_file():
        raise SystemExit("Phase365 syscall source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        validate(before)
        print("Phase365 apexd writev/futex audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase365 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase365 apexd writev/futex recorder applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
