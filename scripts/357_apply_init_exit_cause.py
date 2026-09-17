#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
EXIT = Path("kernel/exit.c")
MARK = "A52_PHASE357_INIT_EXIT_CAUSE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase357 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE357_INIT_EXIT_CAUSE_V1
 *
 * Phase355/356 resolved the apparent ~12 s system freeze: CPU6 is executing
 * Linux panic()'s final mdelay(PANIC_TIMER_STEP) loop in PID1/init context.
 * Phase356's saved LR resolves exactly to panic+0x300, immediately after the
 * __const_udelay(1000 * 0x10c7) call used by mdelay(100).
 *
 * Phase357 records how PID1 reached do_exit and the exact values used by the
 * global-init panic branch. The final state is replicated across the whole
 * 32 KiB sideband so pmsg clear-bit damage cannot erase the diagnosis.
 */
#define A52_R357_SIDEBAND_PHYS   0xB1BF0000ULL
#define A52_R357_SIDEBAND_BYTES  0x8000U
#define A52_R357_COPY_BYTES      0x4000U
#define A52_R357_SLOT_BYTES      256U
#define A52_R357_SLOTS_PER_COPY  64U
#define A52_R357_MAGIC           0x3735335449584549ULL /* "IEXIT357" */
#define A52_R357_COMMIT          0x357c0de5U

#define A52_R357_N_GROUP_EXIT    BIT(0)
#define A52_R357_N_DO_EXIT       BIT(1)
#define A52_R357_N_MAKE_DEAD     BIT(2)
#define A52_R357_N_INIT_PANIC    BIT(3)

struct a52_r357_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 runtime_text;
	u64 do_exit_code;
	u64 panic_code;
	u64 jobctl;
	u64 mm;
	u64 task_flags;
	u64 reserved64;
	u32 note_mask;
	u32 cpu;
	u32 pid;
	u32 tgid;
	s32 group_exit_code;
	s32 task_exit_code;
	s32 do_group_exit_code;
	s32 make_task_dead_signr;
	u32 signal_flags;
	s32 exit_signal;
	u32 pending_signal;
	u32 fatal_signal;
	s32 signal_live;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[96];
};

extern char _text[];
static void *a52_r357_sideband;
static u32 a52_r357_note_mask;
static s64 a52_r357_do_exit_code;
static s32 a52_r357_group_exit_code;
static s32 a52_r357_make_dead_signr;

void a52_p357_note_group_exit(int exit_code)
{
	if (current->pid != 1)
		return;
	WRITE_ONCE(a52_r357_group_exit_code, (s32)exit_code);
	WRITE_ONCE(a52_r357_note_mask,
		READ_ONCE(a52_r357_note_mask) | A52_R357_N_GROUP_EXIT);
}

void a52_p357_note_do_exit(long code)
{
	if (current->pid != 1)
		return;
	WRITE_ONCE(a52_r357_do_exit_code, (s64)code);
	WRITE_ONCE(a52_r357_note_mask,
		READ_ONCE(a52_r357_note_mask) | A52_R357_N_DO_EXIT);
}

void a52_p357_note_make_task_dead(int signr)
{
	if (current->pid != 1)
		return;
	WRITE_ONCE(a52_r357_make_dead_signr, (s32)signr);
	WRITE_ONCE(a52_r357_note_mask,
		READ_ONCE(a52_r357_note_mask) | A52_R357_N_MAKE_DEAD);
}

void a52_p357_persist_init_panic(long code, int group_exit_code)
{
	struct a52_r357_record r;
	unsigned int i;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r357_sideband) || current->pid != 1)
		return;

	BUILD_BUG_ON(sizeof(struct a52_r357_record) != A52_R357_SLOT_BYTES);
	memset(&r, 0, sizeof(r));
	r.magic = A52_R357_MAGIC;
	r.ns = ktime_get_ns();
	r.sequence = (u64)atomic64_read(&a52_r179_sequence);
	r.runtime_text = (u64)(unsigned long)_text;
	r.do_exit_code = (u64)READ_ONCE(a52_r357_do_exit_code);
	r.panic_code = (u64)(s64)code;
	r.jobctl = (u64)READ_ONCE(current->jobctl);
	r.mm = (u64)(unsigned long)READ_ONCE(current->mm);
	r.task_flags = (u64)READ_ONCE(current->flags);
	r.note_mask = READ_ONCE(a52_r357_note_mask) | A52_R357_N_INIT_PANIC;
	r.cpu = (u32)raw_smp_processor_id();
	r.pid = (u32)current->pid;
	r.tgid = (u32)current->tgid;
	r.group_exit_code = (s32)group_exit_code;
	r.task_exit_code = (s32)READ_ONCE(current->exit_code);
	r.do_group_exit_code = READ_ONCE(a52_r357_group_exit_code);
	r.make_task_dead_signr = READ_ONCE(a52_r357_make_dead_signr);
	r.signal_flags = READ_ONCE(current->signal->flags);
	r.exit_signal = (s32)READ_ONCE(current->exit_signal);
	r.pending_signal = signal_pending(current) ? 1U : 0U;
	r.fatal_signal = fatal_signal_pending(current) ? 1U : 0U;
	r.signal_live = (s32)atomic_read(&current->signal->live);
	r.commit = A52_R357_COMMIT;
	r.version = 1U;
	memcpy(r.comm, current->comm, TASK_COMM_LEN);

	for (i = 0; i < A52_R357_SLOTS_PER_COPY; i++) {
		dst0 = (u8 *)a52_r357_sideband + i * A52_R357_SLOT_BYTES;
		dst1 = (u8 *)a52_r357_sideband + A52_R357_COPY_BYTES +
			i * A52_R357_SLOT_BYTES;
		memcpy(dst0, &r, sizeof(r));
		memcpy(dst1, &r, sizeof(r));
	}
	wmb();
	__flush_dcache_area(a52_r357_sideband, A52_R357_SIDEBAND_BYTES);
}

static void a52_r357_start(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r357_record) != A52_R357_SLOT_BYTES);
	BUILD_BUG_ON(A52_R357_SLOTS_PER_COPY * A52_R357_SLOT_BYTES !=
		A52_R357_COPY_BYTES);

	a52_r357_sideband = memremap(A52_R357_SIDEBAND_PHYS,
		A52_R357_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r357_sideband) {
		a52_ackfr_record("P276 357A map=0");
		return;
	}

	memset(a52_r357_sideband, 0, A52_R357_SIDEBAND_BYTES);
	a52_r357_note_mask = 0U;
	a52_r357_do_exit_code = 0;
	a52_r357_group_exit_code = 0;
	a52_r357_make_dead_signr = 0;
	wmb();
	__flush_dcache_area(a52_r357_sideband, A52_R357_SIDEBAND_BYTES);
	a52_ackfr_record("P276 357A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase357 requires Phase343 recorder lineage")
    anchor = "static void *a52_r343_sideband;\n"
    text = one(text, anchor, RAW_BLOCK + anchor, "recorder insertion")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r357_start();\n\ta52_r343_start();\n",
               "late-init mapping")
    return text


def patch_exit(text: str) -> str:
    if MARK in text:
        return text

    inc = "#include <trace/hooks/dtask.h>\n"
    decl = (
        inc +
        "\nextern void a52_p357_note_group_exit(int exit_code); /* " + MARK + " */\n"
        "extern void a52_p357_note_do_exit(long code);\n"
        "extern void a52_p357_note_make_task_dead(int signr);\n"
        "extern void a52_p357_persist_init_panic(long code, int group_exit_code);\n"
    )
    text = one(text, inc, decl, "exit externs")

    text = one(text,
               "\tint group_dead;\n\n",
               "\tint group_dead;\n\n\ta52_p357_note_do_exit(code);\n\n",
               "do_exit note")

    old = (
        "\t\t\tpanic(\"Attempted to kill init! exitcode=0x%08x\\n\",\n"
        "\t\t\t\ttsk->signal->group_exit_code ?: (int)code);\n"
    )
    new = (
        "\t\t\ta52_p357_persist_init_panic(code,\n"
        "\t\t\t\ttsk->signal->group_exit_code);\n"
        "\t\t\tpanic(\"Attempted to kill init! exitcode=0x%08x\\n\",\n"
        "\t\t\t\ttsk->signal->group_exit_code ?: (int)code);\n"
    )
    text = one(text, old, new, "global-init panic")

    text = one(text,
               "\tunsigned int limit;\n\n",
               "\tunsigned int limit;\n\n\ta52_p357_note_make_task_dead(signr);\n\n",
               "make_task_dead note")

    text = one(text,
               "\tstruct signal_struct *sig = current->signal;\n\n",
               "\tstruct signal_struct *sig = current->signal;\n\n"
               "\ta52_p357_note_group_exit(exit_code);\n\n",
               "do_group_exit note")
    return text


def validate(br: str, ar: str, be: str, ae: str) -> None:
    joined = ar + ae
    for token in (
        MARK,
        "A52_R357_SIDEBAND_PHYS   0xB1BF0000ULL",
        "A52_R357_COMMIT          0x357c0de5U",
        "a52_r357_start();",
        "a52_p357_note_do_exit(code);",
        "a52_p357_note_group_exit(exit_code);",
        "a52_p357_note_make_task_dead(signr);",
        "a52_p357_persist_init_panic(code,",
        "Attempted to kill init! exitcode=0x%08x",
    ):
        if token not in joined:
            raise SystemExit("Phase357 required token missing: " + token)

    if ae.count('panic("Attempted to kill init! exitcode=0x%08x') !=             be.count('panic("Attempted to kill init! exitcode=0x%08x'):
        raise SystemExit("Phase357 changed global-init panic call count")
    if ar.count("a52_r357_start();") != 1:
        raise SystemExit("Phase357 recorder start count mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    files = [ns.root / REC, ns.root / EXIT]
    for p in files:
        if not p.is_file():
            raise SystemExit("Phase357 missing source: " + str(p))

    br, be = (p.read_text() for p in files)
    ar = patch_rec(br)
    ae = patch_exit(be)
    validate(br, ar, be, ae)

    if ns.check_only:
        print("Phase357 init-exit cause audit: PASS")
        return 0

    files[0].write_text(ar)
    files[1].write_text(ae)
    print("Phase357 init-exit cause probe applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
