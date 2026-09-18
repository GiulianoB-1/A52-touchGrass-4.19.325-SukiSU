#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE364_APEXD_SYSCALL_RING_V1"

BLOCK = r'''
/* A52_PHASE364_APEXD_SYSCALL_RING_V1
 *
 * Phase363 hardware ran to the 330 s safety window. UFS is no longer the
 * frontier, but the second /system/bin/apexd group leader starts around 18.6 s
 * and never exits; odsign, zygote and SurfaceFlinger never start.
 *
 * Reuse the now-idle Phase359 32 KiB sideband. Trace only an apexd group
 * leader (pid == tgid, comm == "apexd"). Each syscall entry/return is persisted
 * immediately into a 64-slot circular ring mirrored in two 16 KiB copies.
 * An unmatched final ENTRY therefore identifies a blocking syscall directly.
 *
 * No syscall arguments, return values, scheduling or Android behavior are
 * changed.
 */
#define A52_R364_SIDEBAND_PHYS    0xB1BF0000ULL
#define A52_R364_SIDEBAND_BYTES   0x8000U
#define A52_R364_COPY_BYTES       0x4000U
#define A52_R364_SLOT_BYTES       256U
#define A52_R364_SLOTS            64U
#define A52_R364_MAGIC            0x3436335845504141ULL /* "AAPEX364" LE-ish */
#define A52_R364_COMMIT           0x364c0de5U
#define A52_R364_VERSION          1U

#define A52_R364_EVT_ENTRY        1U
#define A52_R364_EVT_RETURN       2U

struct a52_r364_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 pc;
	u64 sp;
	u64 pstate;
	u64 lr;
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
	char comm[TASK_COMM_LEN];
	u8 reserved[96];
};

static const char a52_r364_marker[] __used =
	"A52_PHASE364_APEXD_SYSCALL_RING_V1";
static void *a52_r364_sideband;
static u64 a52_r364_sequence;

static bool a52_r364_target(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static void a52_r364_fill(struct a52_r364_record *r,
			  struct pt_regs *regs, int scno, u32 event,
			  u64 sequence, s64 ret)
{
	memset(r, 0, sizeof(*r));
	r->magic = A52_R364_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->pc = regs->pc;
	r->sp = regs->sp;
	r->pstate = regs->pstate;
	r->lr = regs->regs[30];
	r->x[0] = regs->orig_x0;
	r->x[1] = regs->regs[1];
	r->x[2] = regs->regs[2];
	r->x[3] = regs->regs[3];
	r->x[4] = regs->regs[4];
	r->x[5] = regs->regs[5];
	r->ret = ret;
	r->event = event;
	r->syscallno = (u32)scno;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->ring_index = (u32)((sequence - 1ULL) % A52_R364_SLOTS);
	r->commit = A52_R364_COMMIT;
	r->version = A52_R364_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
}

static void a52_r364_write(unsigned int slot,
			   const struct a52_r364_record *r)
{
	void *dst0;
	void *dst1;
	unsigned int pos;

	if (!READ_ONCE(a52_r364_sideband) || !r || slot >= A52_R364_SLOTS)
		return;

	pos = slot * A52_R364_SLOT_BYTES;
	dst0 = (u8 *)a52_r364_sideband + pos;
	dst1 = (u8 *)a52_r364_sideband + A52_R364_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static u64 a52_r364_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r364_record r;
	unsigned int slot;
	u64 sequence;

	if (!READ_ONCE(a52_r364_sideband) || !a52_r364_target())
		return 0;

	sequence = ++a52_r364_sequence;
	slot = (unsigned int)((sequence - 1ULL) % A52_R364_SLOTS);
	a52_r364_fill(&r, regs, scno, A52_R364_EVT_ENTRY, sequence,
		      (s64)(1ULL << 63));
	a52_r364_write(slot, &r);
	return sequence;
}

static void a52_r364_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r364_record r;
	unsigned int slot;

	if (!sequence || !READ_ONCE(a52_r364_sideband) || !a52_r364_target())
		return;

	slot = (unsigned int)((sequence - 1ULL) % A52_R364_SLOTS);
	a52_r364_fill(&r, regs, scno, A52_R364_EVT_RETURN, sequence,
		      (s64)regs->regs[0]);
	a52_r364_write(slot, &r);
}

static int __init a52_r364_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r364_record) != A52_R364_SLOT_BYTES);
	BUILD_BUG_ON(A52_R364_SLOTS * A52_R364_SLOT_BYTES !=
		     A52_R364_COPY_BYTES);

	a52_r364_sideband = memremap(A52_R364_SIDEBAND_PHYS,
		A52_R364_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r364_sideband)
		return 0;

	memset(a52_r364_sideband, 0, A52_R364_SIDEBAND_BYTES);
	a52_r364_sequence = 0;
	wmb();
	__flush_dcache_area(a52_r364_sideband, A52_R364_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r364_init);

'''


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase364 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R364_SIDEBAND_PHYS    0xB1BF0000ULL",
        "A52_R364_SLOTS            64U",
        "A52_R364_SLOT_BYTES       256U",
        "A52_R364_COMMIT           0x364c0de5U",
        'current->pid == current->tgid',
        '!strncmp(current->comm, "apexd", TASK_COMM_LEN)',
        "a52_r364_sys_enter(regs, scno);",
        "a52_r364_sys_return(regs, scno, a52_r364_sequence_token);",
        "late_initcall(a52_r364_init);",
    ):
        if token not in text:
            raise SystemExit("Phase364 required token missing: " + token)

    if "late_initcall(a52_r359_init);" in text:
        raise SystemExit("Phase364 must disable the Phase359 sideband owner")
    if text.count("invoke_syscall(regs, scno, sc_nr, syscall_table);") != 1:
        raise SystemExit("Phase364 invoke_syscall count mismatch")


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE359_PID1_WRITEV_PAYLOAD_V1" not in text:
        raise SystemExit("Phase364 requires Phase359 syscall lineage")

    # Keep the old Phase359 implementation compiled for lineage but remove its
    # runtime ownership of 0xB1BF0000..0xB1BF7FFF.
    text = one(
        text,
        "static int __init a52_r359_init(void)\n",
        "static int __init __used a52_r359_init(void)\n",
        "mark Phase359 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r359_init);\n",
        "/* Phase364 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase359 mapper",
    )

    anchor = (
        'static const char a52_r359_marker[] __used =\n'
        '\t"A52_PHASE359_PID1_WRITEV_PAYLOAD_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase364 block insertion")

    text = one(
        text,
        "\tu64 a52_r359_sequence_token = 0;\n",
        "\tu64 a52_r359_sequence_token = 0;\n"
        "\tu64 a52_r364_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r359_maybe_persist(scno);\n"
        "\ta52_r359_sequence_token = a52_r359_writev_enter(regs, scno);\n"
        "\ta52_r358_sequence_token = a52_r358_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r358_sys_return(regs, scno, a52_r358_sequence_token);\n"
        "\ta52_r359_writev_return(regs, a52_r359_sequence_token);\n"
    )
    new = (
        "\ta52_r359_maybe_persist(scno);\n"
        "\ta52_r359_sequence_token = a52_r359_writev_enter(regs, scno);\n"
        "\ta52_r358_sequence_token = a52_r358_sys_enter(regs, scno);\n"
        "\ta52_r364_sequence_token = a52_r364_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r364_sys_return(regs, scno, a52_r364_sequence_token);\n"
        "\ta52_r358_sys_return(regs, scno, a52_r358_sequence_token);\n"
        "\ta52_r359_writev_return(regs, a52_r359_sequence_token);\n"
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
        raise SystemExit("Phase364 syscall source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        validate(before)
        print("Phase364 apexd syscall ring audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase364 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase364 apexd syscall ring applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
