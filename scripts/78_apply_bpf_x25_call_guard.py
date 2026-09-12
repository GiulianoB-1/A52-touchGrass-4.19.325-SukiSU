#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 78_apply_bpf_x25_call_guard.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
hdr = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"
asm = root / "arch/arm64/net/bpf_call_x25_guard.S"

c = cfg.read_text()

def set_line(text: str, yes: str, no: str, want_yes: bool) -> str:
    if want_yes:
        if yes in text:
            return text
        if no in text:
            return text.replace(no, yes, 1)
    else:
        if no in text:
            return text
        if yes in text:
            return text.replace(yes, no, 1)
    raise SystemExit(f"config state not found: {yes.strip()} / {no.strip()}")

c = set_line(c, "CONFIG_BPF_JIT=y\n", "# CONFIG_BPF_JIT is not set\n", True)
c = set_line(c, "CONFIG_BPF_JIT_ALWAYS_ON=y\n",
             "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n", True)
c = set_line(c, "CONFIG_PSTORE_RAM=y\n",
             "# CONFIG_PSTORE_RAM is not set\n", False)
cfg.write_text(c)

ASM = r'''/* SPDX-License-Identifier: GPL-2.0 */
/*
 * A52 Phase78 ARM64 BPF call ABI guard.
 *
 * The LLVM17 caller of BPF_PROG_RUN was observed keeping current (SP_EL0)
 * in x25 across the indirect BPF call. A failing JIT return corrupted x25
 * to 0x02000000, and the caller then faulted at [x25 + 0x50].
 *
 * Preserve the caller's x25 explicitly across the dynamically generated
 * BPF program. x25 remains freely usable by the BPF JIT as BPF_REG_FP.
 */
#include <linux/linkage.h>
#include <asm/assembler.h>

ENTRY(a52_bpf_call_x25_guard)
	stp	x29, x30, [sp, #-32]!
	str	x25, [sp, #16]
	mov	x29, sp

	/* x0=func, x1=ctx, x2=insnsi */
	mov	x16, x0
	mov	x0, x1
	mov	x1, x2
	blr	x16

	ldr	x25, [sp, #16]
	ldp	x29, x30, [sp], #32
	ret
ENDPROC(a52_bpf_call_x25_guard)
'''
asm.write_text(ASM)

m = mk.read_text()
entry = "obj-$(CONFIG_BPF_JIT) += bpf_call_x25_guard.o\n"
if entry not in m:
    anchor = "obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n"
    if m.count(anchor) != 1:
        raise SystemExit(f"arm64 net Makefile anchor count={m.count(anchor)}")
    m = m.replace(anchor, anchor + entry, 1)
    mk.write_text(m)

h = hdr.read_text()
decl_marker = "A52_PHASE78_BPF_X25_CALL_GUARD_V1"
if decl_marker not in h:
    anchor = """struct sk_filter {
	refcount_t	refcnt;
	struct rcu_head	rcu;
	struct bpf_prog	*prog;
};

"""
    if h.count(anchor) != 1:
        raise SystemExit("filter.h declaration anchor mismatch")
    decl = anchor + f"""#ifdef CONFIG_ARM64
/* {decl_marker} */
extern unsigned int a52_bpf_call_x25_guard(
	unsigned int (*func)(const void *ctx, const struct bpf_insn *insn),
	const void *ctx, const struct bpf_insn *insn);
#endif

"""
    h = h.replace(anchor, decl, 1)

    # CFI interpreter path remains a normal C call. Only the validated JIT
    # call is routed through the assembly guard.
    cfi_jit = """	/* Call jited function without CFI checking. */
	return prog->bpf_func(ctx, prog->insnsi);
"""
    cfi_new = """	/* Call jited function without CFI checking. */
#ifdef CONFIG_ARM64
	return a52_bpf_call_x25_guard(prog->bpf_func, ctx, prog->insnsi);
#else
	return prog->bpf_func(ctx, prog->insnsi);
#endif
"""
    if h.count(cfi_jit) != 1:
        raise SystemExit(f"CFI JIT call anchor count={h.count(cfi_jit)}")
    h = h.replace(cfi_jit, cfi_new, 1)

    noncfi = """static inline unsigned int bpf_call_func(const struct bpf_prog *prog,
					 const void *ctx)
{
	return prog->bpf_func(ctx, prog->insnsi);
}
"""
    noncfi_new = """static inline unsigned int bpf_call_func(const struct bpf_prog *prog,
					 const void *ctx)
{
#ifdef CONFIG_ARM64
	if (likely(prog->jited))
		return a52_bpf_call_x25_guard(prog->bpf_func, ctx,
					      prog->insnsi);
#endif
	return prog->bpf_func(ctx, prog->insnsi);
}
"""
    if h.count(noncfi) != 1:
        raise SystemExit(f"non-CFI bpf_call_func anchor count={h.count(noncfi)}")
    h = h.replace(noncfi, noncfi_new, 1)
    hdr.write_text(h)

checks = {
    "jit": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "bpf_syscall": "CONFIG_BPF_SYSCALL=y" in cfg.read_text(),
    "cgroup_bpf": "CONFIG_CGROUP_BPF=y" in cfg.read_text(),
    "asm_save_x25": "str\tx25, [sp, #16]" in asm.read_text(),
    "asm_restore_x25": "ldr\tx25, [sp, #16]" in asm.read_text(),
    "asm_call": "blr\tx16" in asm.read_text(),
    "makefile": "bpf_call_x25_guard.o" in mk.read_text(),
    "filter_guard": decl_marker in hdr.read_text(),
    "guard_call": "a52_bpf_call_x25_guard(prog->bpf_func" in hdr.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase78 staging audit failed: " + ", ".join(failed))

print("phase78_bpf_x25_call_guard=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
