#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 78_apply_bpf_abi_guard.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
filt = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"
asm = root / "arch/arm64/net/bpf_abi_guard.S"

c = cfg.read_text()

# Restore the real JIT configuration used by the original LLVM17 build.
if "# CONFIG_BPF_JIT is not set\n" in c:
    c = c.replace("# CONFIG_BPF_JIT is not set\n", "CONFIG_BPF_JIT=y\n", 1)
if "CONFIG_BPF_JIT=y\n" not in c:
    raise SystemExit("CONFIG_BPF_JIT=y missing")

if "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n" in c:
    c = c.replace("# CONFIG_BPF_JIT_ALWAYS_ON is not set\n",
                  "CONFIG_BPF_JIT_ALWAYS_ON=y\n", 1)
elif "CONFIG_BPF_JIT_ALWAYS_ON=y\n" not in c:
    raise SystemExit("CONFIG_BPF_JIT_ALWAYS_ON state missing")

# TGREC exclusively owns 0xB1B00000..0xB1BFFFFF in diagnostics.
if "CONFIG_PSTORE_RAM=y\n" in c:
    c = c.replace("CONFIG_PSTORE_RAM=y\n",
                  "# CONFIG_PSTORE_RAM is not set\n", 1)
elif "# CONFIG_PSTORE_RAM is not set\n" not in c:
    raise SystemExit("CONFIG_PSTORE_RAM state missing")

cfg.write_text(c)

ASM = r'''/* SPDX-License-Identifier: GPL-2.0 */
#include <linux/linkage.h>

/*
 * A52 Phase78 BPF ABI guard.
 *
 * Signature:
 *   unsigned int a52_bpf_call_abi_guard(
 *       unsigned int (*fn)(const void *ctx, const struct bpf_insn *insn),
 *       const void *ctx,
 *       const struct bpf_insn *insn);
 *
 * The legacy arm64 BPF JIT uses x19-x22, x25 and x26 internally and is
 * expected to preserve every AAPCS64 callee-saved GPR.  Clang17 exposed a
 * return path where x25 came back corrupted.  Save and restore all x19-x28
 * explicitly around the dynamic BPF call so the kernel caller never observes
 * a JIT ABI violation.
 */
ENTRY(a52_bpf_call_abi_guard)
	stp	x29, x30, [sp, #-16]!
	mov	x29, sp
	stp	x19, x20, [sp, #-16]!
	stp	x21, x22, [sp, #-16]!
	stp	x23, x24, [sp, #-16]!
	stp	x25, x26, [sp, #-16]!
	stp	x27, x28, [sp, #-16]!

	mov	x16, x0
	mov	x0, x1
	mov	x1, x2
	blr	x16

	ldp	x27, x28, [sp], #16
	ldp	x25, x26, [sp], #16
	ldp	x23, x24, [sp], #16
	ldp	x21, x22, [sp], #16
	ldp	x19, x20, [sp], #16
	ldp	x29, x30, [sp], #16
	ret
ENDPROC(a52_bpf_call_abi_guard)
'''
asm.write_text(ASM)

m = mk.read_text()
entry = "obj-$(CONFIG_BPF_JIT) += bpf_abi_guard.o\n"
if entry not in m:
    if "obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n" not in m:
        raise SystemExit("arm64 net BPF Makefile anchor missing")
    m = m.replace("obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n",
                  "obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n" + entry, 1)
    mk.write_text(m)

s = filt.read_text()
marker = "A52_PHASE78_BPF_ABI_GUARD_V1"
if marker not in s:
    proto_anchor = """struct sk_filter {
	refcount_t	refcnt;
	struct rcu_head	rcu;
	struct bpf_prog	*prog;
};

"""
    if s.count(proto_anchor) != 1:
        raise SystemExit("filter.h sk_filter anchor mismatch")
    proto = proto_anchor + f"""/* {marker} */
extern unsigned int a52_bpf_call_abi_guard(
	unsigned int (*fn)(const void *ctx, const struct bpf_insn *insn),
	const void *ctx, const struct bpf_insn *insn);

"""
    s = s.replace(proto_anchor, proto, 1)

    # CFI-enabled JIT path: after target validation, wrap the generated-code call.
    old_cfi = """	/* Call jited function without CFI checking. */
	return prog->bpf_func(ctx, prog->insnsi);
}
"""
    new_cfi = """	/* Call jited function through the arm64 callee-saved ABI guard. */
	return a52_bpf_call_abi_guard(prog->bpf_func, ctx, prog->insnsi);
}
"""
    if s.count(old_cfi) != 1:
        raise SystemExit(f"CFI JIT call anchor count={s.count(old_cfi)}")
    s = s.replace(old_cfi, new_cfi, 1)

    # Non-CFI build: guard only JITed programs; keep interpreter direct.
    old_plain = """static inline unsigned int bpf_call_func(const struct bpf_prog *prog,
					 const void *ctx)
{
	return prog->bpf_func(ctx, prog->insnsi);
}
"""
    new_plain = """static inline unsigned int bpf_call_func(const struct bpf_prog *prog,
					 const void *ctx)
{
	if (likely(prog->jited))
		return a52_bpf_call_abi_guard(prog->bpf_func, ctx, prog->insnsi);
	return prog->bpf_func(ctx, prog->insnsi);
}
"""
    if s.count(old_plain) != 1:
        raise SystemExit(f"plain bpf_call_func anchor count={s.count(old_plain)}")
    s = s.replace(old_plain, new_plain, 1)
    filt.write_text(s)

checks = {
    "jit_enabled": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "guard_source": "a52_bpf_call_abi_guard" in asm.read_text(),
    "save_x25_x26": "stp\tx25, x26" in asm.read_text(),
    "restore_x25_x26": "ldp\tx25, x26" in asm.read_text(),
    "makefile": "bpf_abi_guard.o" in mk.read_text(),
    "filter_marker": marker in filt.read_text(),
    "guard_call": "a52_bpf_call_abi_guard(prog->bpf_func" in filt.read_text(),
}
failed = [k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("phase78 audit failed: " + ", ".join(failed))

print("phase78_bpf_abi_guard=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
