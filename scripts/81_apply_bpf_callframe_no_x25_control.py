#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 81_apply_bpf_callframe_no_x25_control.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
hdr = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"
asm = root / "arch/arm64/net/bpf_callframe_control.S"

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

# This control deliberately reproduces the working Phase78 x25 guard's
# call-frame shape, but DOES NOT save or restore x25. If it crashes, x25
# preservation itself is causal. If it boots, the extra call frame is causal.
ASM = r'''/* SPDX-License-Identifier: GPL-2.0 */
/*
 * A52 Phase81 ARM64 BPF call-frame control.
 *
 * Same indirect-call trampoline shape as the Phase78 x25-only guard,
 * intentionally without saving/restoring x25.
 */
#include <linux/linkage.h>
#include <asm/assembler.h>

ENTRY(a52_bpf_callframe_control)
	stp	x29, x30, [sp, #-32]!
	mov	x29, sp

	/* x0=func, x1=ctx, x2=insnsi */
	mov	x16, x0
	mov	x0, x1
	mov	x1, x2
	blr	x16

	ldp	x29, x30, [sp], #32
	ret
ENDPROC(a52_bpf_callframe_control)
'''
asm.write_text(ASM)

m = mk.read_text()
entry = "obj-$(CONFIG_BPF_JIT) += bpf_callframe_control.o\n"
if entry not in m:
    anchor = "obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n"
    if m.count(anchor) != 1:
        raise SystemExit(f"arm64 net Makefile anchor count={m.count(anchor)}")
    m = m.replace(anchor, anchor + entry, 1)
    mk.write_text(m)

h = hdr.read_text()
marker = "A52_PHASE81_BPF_CALLFRAME_NO_X25_CONTROL_V1"
if marker not in h:
    anchor = """struct sk_filter {
	refcount_t	refcnt;
	struct rcu_head	rcu;
	struct bpf_prog	*prog;
};

"""
    if h.count(anchor) != 1:
        raise SystemExit("filter.h declaration anchor mismatch")
    decl = anchor + f"""#ifdef CONFIG_ARM64
/* {marker} */
extern unsigned int a52_bpf_callframe_control(
	unsigned int (*func)(const void *ctx, const struct bpf_insn *insn),
	const void *ctx, const struct bpf_insn *insn);
#endif

"""
    h = h.replace(anchor, decl, 1)

    cfi_jit = """	/* Call jited function without CFI checking. */
	return prog->bpf_func(ctx, prog->insnsi);
"""
    cfi_new = """	/* Call jited function without CFI checking. */
#ifdef CONFIG_ARM64
	return a52_bpf_callframe_control(prog->bpf_func, ctx, prog->insnsi);
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
		return a52_bpf_callframe_control(prog->bpf_func, ctx,
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
    "asm_frame": "stp\tx29, x30, [sp, #-32]!" in asm.read_text(),
    "asm_call": "blr\tx16" in asm.read_text(),
    "no_x25_save": "str\tx25" not in asm.read_text() and "stp\tx25" not in asm.read_text(),
    "no_x25_restore": "ldr\tx25" not in asm.read_text() and "ldp\tx25" not in asm.read_text(),
    "makefile": "bpf_callframe_control.o" in mk.read_text(),
    "filter_marker": marker in hdr.read_text(),
    "control_call": "a52_bpf_callframe_control(prog->bpf_func" in hdr.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase81 staging audit failed: " + ", ".join(failed))

print("phase81_bpf_callframe_no_x25_control=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
