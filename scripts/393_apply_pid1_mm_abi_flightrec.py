#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE393_PID1_MM_ABI_FLIGHTREC_V1"

def one(path, old, new, label):
    t=path.read_text()
    if new in t:
        print(f"{path}: {label} already applied")
        return
    n=t.count(old)
    if n!=1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(t.replace(old,new,1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv)!=2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    sysc=root/"arch/arm64/kernel/syscall.c"
    if not sysc.is_file():
        raise SystemExit(f"missing {sysc}")

    one(
        sysc,
        "#include <linux/syscalls.h>\n",
        "#include <linux/syscalls.h>\n#include <linux/mm.h>\n#include <linux/sizes.h>\n",
        "MM ABI recorder includes",
    )

    old_struct=r'''struct a52_p387_sysrec {
	u64 seq;
	s32 nr;
	s32 valid;
	u64 pc;
	u64 x0;
	u64 x1;
	u64 x2;
	u64 x3;
	s64 ret;
};
'''
    new_struct=r'''struct a52_p387_sysrec {
	u64 seq;
	s32 nr;
	s32 valid;
	u64 pc;
	u64 x0;
	u64 x1;
	u64 x2;
	u64 x3;
	u64 x4;
	u64 x5;
	s64 ret;
};

/*
 * A52_PHASE393_PID1_MM_ABI_FLIGHTREC_V1
 *
 * Phase392 successfully translates Scudo's misaligned fixed mmap commits.
 * Do not guess the next compatibility fix.  Persist only rare PID1 memory-ABI
 * anomalies: negative returns or addresses that are 4 KiB aligned but not
 * native-16KiB aligned for mmap/munmap/mprotect/madvise/mremap.
 *
 * The normal syscall hot path stays RAM-only.  Only a selected anomaly emits
 * recorder records; negative-return events use one critical record so the
 * Samsung-debug copy survives a subsequent panic/reboot.
 */
#define A52_P393_MAX_EVENTS 32

static atomic_t a52_p393_events = ATOMIC_INIT(0);

static bool a52_p393_is_mm_syscall(int nr)
{
	return nr == __NR_mmap ||
	       nr == __NR_munmap ||
	       nr == __NR_mprotect ||
	       nr == __NR_madvise ||
	       nr == __NR_mremap;
}

static void a52_p393_mm_event(struct a52_p387_sysrec *r, long ret)
{
	unsigned long addr;
	bool legacy4k;
	bool native_misaligned;
	bool failed;
	int event;

	if (!r || !a52_p393_is_mm_syscall(READ_ONCE(r->nr)))
		return;

	addr = (unsigned long)READ_ONCE(r->x0);
	legacy4k = !(addr & (SZ_4K - 1));
	native_misaligned = !!(addr & (PAGE_SIZE - 1));
	failed = ret < 0;

	if (!failed && !(legacy4k && native_misaligned))
		return;

	event = atomic_inc_return(&a52_p393_events);
	if (event > A52_P393_MAX_EVENTS)
		return;

	a52_p383_record("P393 MM e=%d q=%llu nr=%d ret=%lx a=%llx l=%llx",
		event,
		(unsigned long long)READ_ONCE(r->seq),
		READ_ONCE(r->nr),
		(unsigned long)ret,
		(unsigned long long)READ_ONCE(r->x0),
		(unsigned long long)READ_ONCE(r->x1));
	a52_p383_record("P393 MX e=%d x2=%llx x3=%llx x4=%llx x5=%llx",
		event,
		(unsigned long long)READ_ONCE(r->x2),
		(unsigned long long)READ_ONCE(r->x3),
		(unsigned long long)READ_ONCE(r->x4),
		(unsigned long long)READ_ONCE(r->x5));

	if (failed)
		a52_p383_record_critical("P393 ERR e=%d nr=%d ret=%lx pc=%llx",
			event, READ_ONCE(r->nr), (unsigned long)ret,
			(unsigned long long)READ_ONCE(r->pc));
}
'''
    one(sysc,old_struct,new_struct,"extend PID1 ring for MM ABI flight recorder")

    old_enter=r'''	WRITE_ONCE(r->x2, regs->regs[2]);
	WRITE_ONCE(r->x3, regs->regs[3]);
	WRITE_ONCE(r->ret, (s64)0x8000000000000000ULL);
'''
    new_enter=r'''	WRITE_ONCE(r->x2, regs->regs[2]);
	WRITE_ONCE(r->x3, regs->regs[3]);
	WRITE_ONCE(r->x4, regs->regs[4]);
	WRITE_ONCE(r->x5, regs->regs[5]);
	WRITE_ONCE(r->ret, (s64)0x8000000000000000ULL);
'''
    one(sysc,old_enter,new_enter,"capture x4/x5 for memory syscalls")

    old_exit=r'''static void a52_p387_sys_exit(int slot, long ret)
{
	if (slot < 0)
		return;
	WRITE_ONCE(a52_p387_ring[slot].ret, (s64)ret);
}
'''
    new_exit=r'''static void a52_p387_sys_exit(int slot, long ret)
{
	struct a52_p387_sysrec *r;

	if (slot < 0)
		return;

	r = &a52_p387_ring[slot];
	WRITE_ONCE(r->ret, (s64)ret);
	a52_p393_mm_event(r, ret);
}
'''
    one(sysc,old_exit,new_exit,"persist rare PID1 memory ABI anomalies")

    txt=sysc.read_text()
    for tok in [
        MARK,
        "A52_P393_MAX_EVENTS 32",
        "a52_p393_mm_event",
        "P393 MM e=%d",
        "P393 MX e=%d",
        "P393 ERR e=%d",
        "WRITE_ONCE(r->x5, regs->regs[5]);",
    ]:
        if tok not in txt:
            raise SystemExit(f"Phase393 postcondition missing: {tok}")

    print("A52 Phase393 PID1 MM ABI flight recorder applied successfully")

if __name__=="__main__":
    main()
