#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 84_apply_bpf_x25_outofframe_probe.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
hdr = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"
asm = root / "arch/arm64/net/bpf_x25_probe.S"
diag = root / "arch/arm64/net/bpf_x25_diag.c"

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
 * Phase84: capture ARM64 BPF JIT x25 before/after without placing the
 * caller's x25 value in the wrapper stack frame.  This prevents a bad JIT
 * stack restore from accidentally reading our saved x25 and masking the bug.
 */
#include <linux/linkage.h>
#include <asm/assembler.h>

ENTRY(a52_bpf_call_x25_probe)
	/* Keep the same 32-byte wrapper frame size as the Phase81 no-x25 control. */
	stp	x29, x30, [sp, #-32]!
	mov	x29, sp

	/* x0=func, x1=ctx, x2=insnsi, x3=result
	 * Keep result pointer in x27.  Save caller x27 in the result buffer,
	 * not in this wrapper frame.
	 */
	str	x27, [x3, #16]
	mov	x27, x3
	str	x25, [x27, #0]

	mov	x16, x0
	mov	x0, x1
	mov	x1, x2
	blr	x16

	/* Capture JIT-returned x25 before repairing it. */
	str	x25, [x27, #8]
	ldr	x25, [x27, #0]
	ldr	x27, [x27, #16]

	ldp	x29, x30, [sp], #32
	ret
ENDPROC(a52_bpf_call_x25_probe)
'''
asm.write_text(ASM)

DIAG = r'''// SPDX-License-Identifier: GPL-2.0
#include <linux/atomic.h>
#include <linux/bpf.h>
#include <linux/filter.h>
#include <linux/kernel.h>
#include <linux/printk.h>
#include <linux/sched.h>

static atomic_t a52_bpf_x25_mismatches = ATOMIC_INIT(0);

extern void a52_tgrec_bpf_x25_diag(unsigned long before,
				    unsigned long after,
				    unsigned long func,
				    u32 id, u32 type, u32 attach,
				    u32 len, u32 jited_len,
				    u32 stack_depth, u32 func_idx,
				    u32 func_cnt, const char *name);

void a52_bpf_x25_report(const struct bpf_prog *prog,
			 unsigned long before, unsigned long after)
{
	const struct bpf_prog_aux *aux = prog ? prog->aux : NULL;
	const char *name = (aux && aux->name[0]) ? aux->name : "-";
	u32 id = aux ? aux->id : 0;
	u32 stack_depth = aux ? aux->stack_depth : 0;
	u32 func_idx = aux ? aux->func_idx : 0;
	u32 func_cnt = aux ? aux->func_cnt : 0;
	u32 type = prog ? (u32)prog->type : 0;
	u32 attach = prog ? (u32)prog->expected_attach_type : 0;
	u32 len = prog ? prog->len : 0;
	u32 jited_len = prog ? prog->jited_len : 0;
	unsigned long func = prog ? (unsigned long)prog->bpf_func : 0;
	int n = atomic_inc_return(&a52_bpf_x25_mismatches);

	if (n > 32)
		return;

	pr_err("A52BPF_X25 n=%d pid=%d comm=%s id=%u name=%s type=%u attach=%u len=%u jlen=%u is_func=%u fidx=%u fcnt=%u stack=%u before=%016lx after=%016lx func=%px\n",
	       n, current->pid, current->comm, id, name, type, attach,
	       len, jited_len, prog ? prog->is_func : 0, func_idx, func_cnt,
	       stack_depth, before, after, prog ? prog->bpf_func : NULL);

	a52_tgrec_bpf_x25_diag(before, after, func, id, type, attach, len,
			       jited_len, stack_depth, func_idx, func_cnt, name);
}
'''
diag.write_text(DIAG)

m = mk.read_text()
anchor = "obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n"
entries = (
    "obj-$(CONFIG_BPF_JIT) += bpf_x25_probe.o\n"
    "obj-$(CONFIG_BPF_JIT) += bpf_x25_diag.o\n"
)
if "bpf_x25_probe.o" not in m:
    if m.count(anchor) != 1:
        raise SystemExit(f"arm64 net Makefile anchor count={m.count(anchor)}")
    m = m.replace(anchor, anchor + entries, 1)
    mk.write_text(m)

h = hdr.read_text()
marker = "A52_PHASE84_BPF_X25_OUTOFFRAME_PROBE_V1"
if marker not in h:
    anchor_h = """struct sk_filter {
	refcount_t	refcnt;
	struct rcu_head	rcu;
	struct bpf_prog	*prog;
};

"""
    if h.count(anchor_h) != 1:
        raise SystemExit("filter.h declaration anchor mismatch")
    decl = anchor_h + f"""#ifdef CONFIG_ARM64
/* {marker} */
struct a52_bpf_x25_probe_result {{
	unsigned long before;
	unsigned long after;
}};

extern unsigned int a52_bpf_call_x25_probe(
	unsigned int (*func)(const void *ctx, const struct bpf_insn *insn),
	const void *ctx, const struct bpf_insn *insn,
	struct a52_bpf_x25_probe_result *result);
extern void a52_bpf_x25_report(const struct bpf_prog *prog,
				unsigned long before, unsigned long after);
#endif

"""
    h = h.replace(anchor_h, decl, 1)

    cfi_jit = """	/* Call jited function without CFI checking. */
	return prog->bpf_func(ctx, prog->insnsi);
"""
    cfi_new = """	/* Call jited function without CFI checking. */
#ifdef CONFIG_ARM64
	{
		struct a52_bpf_x25_probe_result x25;
		unsigned int ret;

		ret = a52_bpf_call_x25_probe(prog->bpf_func, ctx,
					     prog->insnsi, &x25);
		if (unlikely(x25.before != x25.after))
			a52_bpf_x25_report(prog, x25.before, x25.after);
		return ret;
	}
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
	if (likely(prog->jited)) {
		struct a52_bpf_x25_probe_result x25;
		unsigned int ret;

		ret = a52_bpf_call_x25_probe(prog->bpf_func, ctx,
					     prog->insnsi, &x25);
		if (unlikely(x25.before != x25.after))
			a52_bpf_x25_report(prog, x25.before, x25.after);
		return ret;
	}
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
    "probe_marker": marker in hdr.read_text(),
    "asm_frame_32": "stp\tx29, x30, [sp, #-32]!" in asm.read_text(),
    "x25_not_saved_on_wrapper_stack": "str\tx25, [sp" not in asm.read_text(),
    "x25_before_in_result": "str\tx25, [x27, #0]" in asm.read_text(),
    "x25_after_in_result": "str\tx25, [x27, #8]" in asm.read_text(),
    "x25_restore_from_result": "ldr\tx25, [x27, #0]" in asm.read_text(),
    "x27_saved_in_result": "str\tx27, [x3, #16]" in asm.read_text(),
    "reporter": "A52BPF_X25" in diag.read_text(),
    "makefile_probe": "bpf_x25_probe.o" in mk.read_text(),
    "makefile_diag": "bpf_x25_diag.o" in mk.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase82 staging audit failed: " + ", ".join(failed))

print("phase84_bpf_x25_outofframe_probe=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
