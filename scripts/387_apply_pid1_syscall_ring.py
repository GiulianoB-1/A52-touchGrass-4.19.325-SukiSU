#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE387_PID1_SYSCALL_RING_V1"

HDR=r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_A52_P387_SYSCALL_RING_H
#define _LINUX_A52_P387_SYSCALL_RING_H

void a52_p387_dump_pid1_syscalls(void);

#endif
'''

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
    exitc=root/"kernel/exit.c"
    hdr=root/"include/linux/a52_p387_syscall_ring.h"
    for p in (sysc,exitc):
        if not p.is_file():
            raise SystemExit(f"missing {p}")
    hdr.write_text(HDR)

    one(
        sysc,
        "#include <linux/syscalls.h>\n",
        "#include <linux/syscalls.h>\n#include <linux/atomic.h>\n#include <linux/export.h>\n#include <linux/sched.h>\n#include <linux/a52_p383_dual_recorder.h>\n#include <linux/a52_p387_syscall_ring.h>\n",
        "syscall ring includes",
    )

    marker_anchor="long sys_ni_syscall(void);\n"
    marker=r'''long sys_ni_syscall(void);

/*
 * A52_PHASE387_PID1_SYSCALL_RING_V1
 *
 * Keep the last 32 EL0 syscalls from PID1 in RAM only.  Nothing is written
 * to the Samsung debug partition in the syscall hot path.  If PID1 exits,
 * kernel/exit.c asks us to emit this frozen ring through the Phase383 dual
 * recorder, followed by one critical flush.
 */
#define A52_P387_RING 32U

struct a52_p387_sysrec {
	u64 seq;
	s32 nr;
	s32 valid;
	u64 pc;
	u64 x0;
	u64 x1;
	s64 ret;
};

static struct a52_p387_sysrec a52_p387_ring[A52_P387_RING];
static atomic64_t a52_p387_seq = ATOMIC64_INIT(0);

static int a52_p387_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_p387_sysrec *r;
	u64 seq;
	u32 slot;

	if (unlikely(task_pid_nr(current) != 1 || scno < 0))
		return -1;

	seq = (u64)atomic64_inc_return(&a52_p387_seq);
	slot = (u32)((seq - 1U) & (A52_P387_RING - 1U));
	r = &a52_p387_ring[slot];

	WRITE_ONCE(r->valid, 0);
	WRITE_ONCE(r->seq, seq);
	WRITE_ONCE(r->nr, scno);
	WRITE_ONCE(r->pc, regs->pc);
	WRITE_ONCE(r->x0, regs->regs[0]);
	WRITE_ONCE(r->x1, regs->regs[1]);
	WRITE_ONCE(r->ret, (s64)0x8000000000000000ULL);
	smp_wmb();
	WRITE_ONCE(r->valid, 1);
	return (int)slot;
}

static void a52_p387_sys_exit(int slot, long ret)
{
	if (slot < 0)
		return;
	WRITE_ONCE(a52_p387_ring[slot].ret, (s64)ret);
}

void a52_p387_dump_pid1_syscalls(void)
{
	u64 last = (u64)atomic64_read(&a52_p387_seq);
	u64 first;
	u64 seq;
	u32 dumped = 0;

	if (task_pid_nr(current) != 1)
		return;

	first = last > A52_P387_RING ? last - A52_P387_RING + 1U : 1U;
	a52_p383_record("P387 RING first=%llu last=%llu",
		(unsigned long long)first, (unsigned long long)last);

	for (seq = first; seq <= last; seq++) {
		struct a52_p387_sysrec *r =
			&a52_p387_ring[(seq - 1U) & (A52_P387_RING - 1U)];

		if (!READ_ONCE(r->valid) || READ_ONCE(r->seq) != seq)
			continue;

		a52_p383_record("P387 S q=%llu nr=%d ret=%llx x0=%llx x1=%llx",
			(unsigned long long)seq,
			READ_ONCE(r->nr),
			(unsigned long long)READ_ONCE(r->ret),
			(unsigned long long)READ_ONCE(r->x0),
			(unsigned long long)READ_ONCE(r->x1));
		dumped++;
	}

	a52_p383_record_critical("P387 SDONE n=%u last=%llu",
		dumped, (unsigned long long)last);
}
EXPORT_SYMBOL_GPL(a52_p387_dump_pid1_syscalls);
'''
    one(sysc,marker_anchor,marker,"PID1 syscall ring core")

    common_old='''static void el0_svc_common(struct pt_regs *regs, int scno, int sc_nr,
			   const syscall_fn_t syscall_table[])
{
	unsigned long flags = current_thread_info()->flags;

	regs->orig_x0 = regs->regs[0];
'''
    common_new=r'''static void el0_svc_common(struct pt_regs *regs, int scno, int sc_nr,
			   const syscall_fn_t syscall_table[])
{
	unsigned long flags = current_thread_info()->flags;
	int a52_p387_slot = -1;

	regs->orig_x0 = regs->regs[0];
'''
    one(sysc,common_old,common_new,"ring slot state")

    invoke_old='''	invoke_syscall(regs, scno, sc_nr, syscall_table);

	/*
'''
    invoke_new=r'''	a52_p387_slot = a52_p387_sys_enter(regs, scno);
	invoke_syscall(regs, scno, sc_nr, syscall_table);
	a52_p387_sys_exit(a52_p387_slot, regs->regs[0]);

	/*
'''
    one(sysc,invoke_old,invoke_new,"record syscall entry and return")

    # Add ring header to exit.c and dump before Phase386 exit traces flush.
    one(
        exitc,
        "#include <linux/a52_p383_dual_recorder.h>\n",
        "#include <linux/a52_p383_dual_recorder.h>\n#include <linux/a52_p387_syscall_ring.h>\n",
        "exit ring header",
    )

    exitgrp_old=r'''	if (unlikely(task_pid_nr(current) == 1)) {
		struct pt_regs *a52_regs = current_pt_regs();

		a52_p383_record("P386 EXITGRP code=%d pc=%lx",
'''
    exitgrp_new=r'''	if (unlikely(task_pid_nr(current) == 1)) {
		struct pt_regs *a52_regs = current_pt_regs();

		a52_p387_dump_pid1_syscalls();
		a52_p383_record("P386 EXITGRP code=%d pc=%lx",
'''
    one(exitc,exitgrp_old,exitgrp_new,"dump syscall ring on PID1 exit_group")

    for p,toks in {
        sysc:[MARK,"A52_P387_RING","a52_p387_sys_enter","P387 SDONE"],
        exitc:["a52_p387_dump_pid1_syscalls();"],
        hdr:["a52_p387_dump_pid1_syscalls"],
    }.items():
        txt=p.read_text()
        for tok in toks:
            if tok not in txt:
                raise SystemExit(f"{p}: missing {tok}")

    print("A52 Phase387 PID1 syscall ring applied successfully")

if __name__=="__main__":
    main()
