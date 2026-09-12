#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 82_apply_bpf_x25_telemetry.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
hdr = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"
asm = root / "arch/arm64/net/bpf_x25_telemetry.S"
jit = root / "arch/arm64/net/bpf_jit_comp.c"
rec = root / "drivers/misc/a52_touchgrass_recorder.c"

for p in (cfg, hdr, mk, jit, rec):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

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
 * A52 Phase82 ARM64 BPF x25 telemetry guard.
 *
 * Preserve caller x25 so the device remains bootable, but detect and record
 * any JIT program that returns with x25 changed. This turns the known-good
 * Phase78 guard into a diagnostic rather than hiding the underlying defect.
 */
#include <linux/linkage.h>
#include <asm/assembler.h>

ENTRY(a52_bpf_call_x25_telemetry)
	stp	x29, x30, [sp, #-64]!
	str	x25, [sp, #16]
	str	x0,  [sp, #24]   /* JIT function */
	str	x2,  [sp, #32]   /* insnsi */
	str	x3,  [sp, #40]   /* struct bpf_prog * */
	mov	x29, sp

	/* x0=func, x1=ctx, x2=insnsi, x3=prog */
	mov	x16, x0
	mov	x0, x1
	mov	x1, x2
	blr	x16

	/* Capture the value returned by the generated program, then repair x25. */
	mov	x17, x25
	ldr	x25, [sp, #16]
	cmp	x17, x25
	b.eq	1f

	/* Preserve BPF return value while writing the diagnostic record. */
	str	x0, [sp, #48]
	ldr	x0, [sp, #40]   /* prog */
	ldr	x1, [sp, #24]   /* func */
	ldr	x2, [sp, #16]   /* x25 before */
	mov	x3, x17          /* x25 after */
	bl	a52_tgrec_bpf_x25_mismatch
	ldr	x0, [sp, #48]
1:
	ldp	x29, x30, [sp], #64
	ret
ENDPROC(a52_bpf_call_x25_telemetry)
'''
asm.write_text(ASM)

m = mk.read_text()
entry = "obj-$(CONFIG_BPF_JIT) += bpf_x25_telemetry.o\n"
if entry not in m:
    anchor = "obj-$(CONFIG_BPF_JIT) += bpf_jit_comp.o\n"
    if m.count(anchor) != 1:
        raise SystemExit(f"arm64 net Makefile anchor count={m.count(anchor)}")
    m = m.replace(anchor, anchor + entry, 1)
    mk.write_text(m)

h = hdr.read_text()
marker = "A52_PHASE82_BPF_X25_TELEMETRY_V1"
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
extern unsigned int a52_bpf_call_x25_telemetry(
	unsigned int (*func)(const void *ctx, const struct bpf_insn *insn),
	const void *ctx, const struct bpf_insn *insn,
	const struct bpf_prog *prog);
extern void a52_tgrec_bpf_jit_map(const struct bpf_prog *prog);
#endif

"""
    h = h.replace(anchor, decl, 1)

    cfi_jit = """	/* Call jited function without CFI checking. */
	return prog->bpf_func(ctx, prog->insnsi);
"""
    cfi_new = """	/* Call jited function without CFI checking. */
#ifdef CONFIG_ARM64
	return a52_bpf_call_x25_telemetry(prog->bpf_func, ctx,
					  prog->insnsi, prog);
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
		return a52_bpf_call_x25_telemetry(prog->bpf_func, ctx,
						   prog->insnsi, prog);
#endif
	return prog->bpf_func(ctx, prog->insnsi);
}
"""
    if h.count(noncfi) != 1:
        raise SystemExit(f"non-CFI bpf_call_func anchor count={h.count(noncfi)}")
    h = h.replace(noncfi, noncfi_new, 1)
    hdr.write_text(h)

r = rec.read_text()
if "TG82F1" not in r:
    if r.count('TGREC_BUILD_ID          "TG81C1"') != 1:
        raise SystemExit("unexpected TGREC build-id anchor")
    r = r.replace('TGREC_BUILD_ID          "TG81C1"',
                  'TGREC_BUILD_ID          "TG82F1"', 1)
    old_desc = 'TGREC_BUILD_DESC        "phase81 llvm17 fixed-eevdf real-jit callframe-no-x25"'
    if r.count(old_desc) != 1:
        raise SystemExit("unexpected TGREC build-desc anchor")
    r = r.replace(old_desc,
                  'TGREC_BUILD_DESC        "phase82 llvm17 fixed-eevdf bpf-x25-telemetry"', 1)

if "#include <linux/filter.h>" not in r:
    inc_anchor = "#include <linux/kernel.h>\n"
    if r.count(inc_anchor) != 1:
        raise SystemExit("recorder include anchor mismatch")
    r = r.replace(inc_anchor, inc_anchor + "#include <linux/filter.h>\n", 1)

if "TGREC_TYPE_BPF_X25" not in r:
    type_anchor = "#define TGREC_TYPE_REBOOT       5U\n"
    if r.count(type_anchor) != 1:
        raise SystemExit("recorder type anchor mismatch")
    r = r.replace(type_anchor,
                  type_anchor + "#define TGREC_TYPE_BPF_X25      6U\n", 1)

telemetry_marker = "A52_PHASE82_BPF_TELEMETRY_RECORDERS_V1"
if telemetry_marker not in r:
    insert_anchor = "static int __init tgrec_map_init(void)\n"
    if r.count(insert_anchor) != 1:
        raise SystemExit("recorder function insertion anchor mismatch")
    helpers = f'''/* {telemetry_marker} */
void a52_tgrec_bpf_x25_mismatch(const struct bpf_prog *prog,
				const void *func,
				unsigned long before,
				unsigned long after)
{{
	tgrec_record(TGREC_TYPE_BPF_X25,
		     "BPF_X25_MISMATCH prog=%px func=%px insn=%px before=%016lx after=%016lx",
		     prog, func, prog ? prog->insnsi : NULL, before, after);
}}

void a52_tgrec_bpf_jit_map(const struct bpf_prog *prog)
{{
	static atomic_t map_count = ATOMIC_INIT(0);
	int n;

	if (!prog)
		return;

	n = atomic_inc_return(&map_count);
	if (n > 192)
		return;

	tgrec_record(TGREC_TYPE_BPF_X25,
		     "BPF_JIT_MAP n=%d prog=%px func=%px insn=%px len=%u",
		     n, prog, (void *)prog->bpf_func, prog->insnsi, prog->len);
}}

'''
    r = r.replace(insert_anchor, helpers + insert_anchor, 1)

rec.write_text(r)

j = jit.read_text()
jit_marker = "A52_PHASE82_BPF_JIT_MAP_V1"
if jit_marker not in j:
    anchor = """	prog->bpf_func = (void *)ctx.image;
	prog->jited = 1;
	prog->jited_len = image_size;
"""
    if j.count(anchor) != 1:
        raise SystemExit(f"JIT map insertion anchor count={j.count(anchor)}")
    repl = anchor + f"""	/* {jit_marker} */
	a52_tgrec_bpf_jit_map(prog);
"""
    j = j.replace(anchor, repl, 1)
    jit.write_text(j)

checks = {
    "jit": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "asm_save_x25": "str\tx25, [sp, #16]" in asm.read_text(),
    "asm_compare_x25": "cmp\tx17, x25" in asm.read_text(),
    "asm_logger": "bl\ta52_tgrec_bpf_x25_mismatch" in asm.read_text(),
    "makefile": "bpf_x25_telemetry.o" in mk.read_text(),
    "filter_marker": marker in hdr.read_text(),
    "guard_call": "a52_bpf_call_x25_telemetry(prog->bpf_func" in hdr.read_text(),
    "tgrec_id": 'TGREC_BUILD_ID          "TG82F1"' in rec.read_text(),
    "tgrec_desc": "phase82 llvm17 fixed-eevdf bpf-x25-telemetry" in rec.read_text(),
    "mismatch_logger": "BPF_X25_MISMATCH" in rec.read_text(),
    "jit_map_logger": "BPF_JIT_MAP" in rec.read_text(),
    "jit_map_hook": jit_marker in jit.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase82 staging audit failed: " + ", ".join(failed))

print("phase82_bpf_x25_telemetry=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
