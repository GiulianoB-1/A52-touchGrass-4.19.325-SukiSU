#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE386_PID1_ICACHE_EXIT_TRACE_V1"

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
    elf=root/"fs/binfmt_elf.c"
    exitc=root/"kernel/exit.c"
    for p in (elf,exitc):
        if not p.is_file():
            raise SystemExit(f"missing {p}")

    one(
        elf,
        "#include <asm/param.h>\n#include <asm/page.h>\n",
        "#include <asm/param.h>\n#include <asm/page.h>\n#include <asm/cacheflush.h>\n",
        "arm64 user icache helper include",
    )

    old=r'''	}

	kfree(bounce);
	a52_p383_record_critical("P385 READY RWX anon=%lx-%lx", map_start, map_end);
	return 0;

fail:
'''
    new=r'''	}

	/*
	 * A52_PHASE386_PID1_ICACHE_EXIT_TRACE_V1
	 *
	 * Phase385 writes executable instructions through copy_to_user() into
	 * anonymous RWX pages.  ARM64 requires explicit D/I-cache coherency before
	 * those bytes are executed.  Use the user-range helper because map_start
	 * and map_end are userspace virtual addresses.
	 */
	rc = (int)__flush_cache_user_range(map_start, map_end);
	if (rc) {
		a52_p383_record_critical("P386 ICACHE_FAIL rc=%d %lx-%lx",
			rc, map_start, map_end);
		goto fail;
	}
	a52_p383_record("P386 ICACHE_OK %lx-%lx", map_start, map_end);

	kfree(bounce);
	a52_p383_record_critical("P385 READY RWX anon=%lx-%lx", map_start, map_end);
	return 0;

fail:
'''
    one(elf,old,new,"flush copied PID1 image before execution")

    # Recorder + pt_regs access in exit path.
    one(
        exitc,
        "#include <linux/writeback.h>\n#include <linux/shm.h>\n",
        "#include <linux/writeback.h>\n#include <linux/shm.h>\n#include <linux/a52_p383_dual_recorder.h>\n#include <asm/ptrace.h>\n",
        "PID1 exit recorder includes",
    )

    doexit_old='''	set_fs(USER_DS);

	if (unlikely(in_atomic())) {
'''
    doexit_new=r'''	set_fs(USER_DS);

	if (unlikely(task_pid_nr(current) == 1)) {
		struct pt_regs *a52_regs = current_pt_regs();

		a52_p383_record("P386 DOEXIT code=%lx pc=%lx sp=%lx",
			(unsigned long)code,
			a52_regs ? a52_regs->pc : 0UL,
			a52_regs ? a52_regs->sp : 0UL);
		a52_p383_record_critical("P386 DOREGS lr=%lx x0=%lx x8=%lx",
			a52_regs ? a52_regs->regs[30] : 0UL,
			a52_regs ? a52_regs->regs[0] : 0UL,
			a52_regs ? a52_regs->regs[8] : 0UL);
	}

	if (unlikely(in_atomic())) {
'''
    one(exitc,doexit_old,doexit_new,"record all PID1 exits")

    exitgrp_old='''SYSCALL_DEFINE1(exit_group, int, error_code)
{
	do_group_exit((error_code & 0xff) << 8);
'''
    exitgrp_new=r'''SYSCALL_DEFINE1(exit_group, int, error_code)
{
	if (unlikely(task_pid_nr(current) == 1)) {
		struct pt_regs *a52_regs = current_pt_regs();

		a52_p383_record("P386 EXITGRP code=%d pc=%lx",
			error_code, a52_regs ? a52_regs->pc : 0UL);
		a52_p383_record_critical("P386 EXITREG lr=%lx sp=%lx",
			a52_regs ? a52_regs->regs[30] : 0UL,
			a52_regs ? a52_regs->sp : 0UL);
	}
	do_group_exit((error_code & 0xff) << 8);
'''
    one(exitc,exitgrp_old,exitgrp_new,"record PID1 exit_group syscall")

    for p,toks in {
        elf:[MARK,"__flush_cache_user_range","P386 ICACHE_OK","P386 ICACHE_FAIL"],
        exitc:["P386 EXITGRP","P386 EXITREG","P386 DOEXIT","P386 DOREGS"],
    }.items():
        txt=p.read_text()
        for tok in toks:
            if tok not in txt:
                raise SystemExit(f"{p}: missing {tok}")

    print("A52 Phase386 PID1 icache synchronization + exit trace applied successfully")

if __name__=="__main__":
    main()
