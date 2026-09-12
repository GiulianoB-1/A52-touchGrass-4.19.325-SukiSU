#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 83_apply_bpf_subprog_call_target_fix.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
hdr = root / "include/linux/filter.h"
core = root / "kernel/bpf/core.c"
jit = root / "arch/arm64/net/bpf_jit_comp.c"

# Keep the same real-JIT/no-pstore experimental configuration as Phase79.
c = cfg.read_text()
def set_line(text, yes, no, want_yes):
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

# Backport generic helper from upstream e2c95a61656d.
h = hdr.read_text()
proto = """int bpf_jit_get_func_addr(const struct bpf_prog *prog,
			  const struct bpf_insn *insn, bool extra_pass,
			  u64 *func_addr, bool *func_addr_fixed);

"""
anchor = "struct bpf_prog *bpf_jit_blind_constants(struct bpf_prog *fp);\n"
if "bpf_jit_get_func_addr(" not in h:
    if h.count(anchor) != 1:
        raise SystemExit(f"filter.h prototype anchor count={h.count(anchor)}")
    h = h.replace(anchor, proto + anchor, 1)
    hdr.write_text(h)

s = core.read_text()
marker = "A52_PHASE83_BPF_SUBPROG_CALL_TARGET_FIX_V1"
helper = f"""/* {marker}
 * Backport of upstream e2c95a61656d: resolve BPF helper calls from
 * __bpf_call_base, but resolve BPF_PSEUDO_CALL targets through
 * prog->aux->func[off]->bpf_func once the extra JIT pass has populated them.
 */
int bpf_jit_get_func_addr(const struct bpf_prog *prog,
			  const struct bpf_insn *insn, bool extra_pass,
			  u64 *func_addr, bool *func_addr_fixed)
{{
	s16 off = insn->off;
	s32 imm = insn->imm;
	u8 *addr;

	*func_addr_fixed = insn->src_reg != BPF_PSEUDO_CALL;
	if (!*func_addr_fixed) {{
		if (!extra_pass)
			addr = NULL;
		else if (prog->aux->func &&
			 off >= 0 && off < prog->aux->func_cnt)
			addr = (u8 *)prog->aux->func[off]->bpf_func;
		else
			return -EINVAL;
	}} else {{
		addr = (u8 *)__bpf_call_base + imm;
	}}

	*func_addr = (unsigned long)addr;
	return 0;
}}

"""
core_anchor = "static int bpf_jit_blind_insn(const struct bpf_insn *from,\n"
if marker not in s:
    if s.count(core_anchor) != 1:
        raise SystemExit(f"core helper anchor count={s.count(core_anchor)}")
    s = s.replace(core_anchor, helper + core_anchor, 1)
    core.write_text(s)

j = jit.read_text()

# Backport upstream 8c11ea5ce13d to ARM64.
if marker not in j:
    old_sig = "static int build_insn(const struct bpf_insn *insn, struct jit_ctx *ctx)\n"
    new_sig = "static int build_insn(const struct bpf_insn *insn, struct jit_ctx *ctx,\n\t\t      bool extra_pass)\n"
    if j.count(old_sig) != 1:
        raise SystemExit(f"build_insn signature count={j.count(old_sig)}")
    j = j.replace(old_sig, new_sig, 1)

    old_call = """	case BPF_JMP | BPF_CALL:
	{
		const u8 r0 = bpf2a64[BPF_REG_0];
		const u64 func = (u64)__bpf_call_base + imm;

		if (ctx->prog->is_func)
			emit_addr_mov_i64(tmp, func, ctx);
		else
			emit_a64_mov_i64(tmp, func, ctx);
		emit(A64_BLR(tmp), ctx);
		emit(A64_MOV(1, r0, A64_R(0)), ctx);
		break;
	}
"""
    new_call = f"""	case BPF_JMP | BPF_CALL:
	{{
		const u8 r0 = bpf2a64[BPF_REG_0];
		bool func_addr_fixed;
		u64 func_addr;
		int ret;

		/* {marker}: use the real aux->func target for BPF_PSEUDO_CALL. */
		ret = bpf_jit_get_func_addr(ctx->prog, insn, extra_pass,
					    &func_addr, &func_addr_fixed);
		if (ret < 0)
			return ret;

		if (func_addr_fixed)
			emit_a64_mov_i64(tmp, func_addr, ctx);
		else
			emit_addr_mov_i64(tmp, func_addr, ctx);
		emit(A64_BLR(tmp), ctx);
		emit(A64_MOV(1, r0, A64_R(0)), ctx);
		break;
	}}
"""
    if j.count(old_call) != 1:
        raise SystemExit(f"BPF_CALL block count={j.count(old_call)}")
    j = j.replace(old_call, new_call, 1)

    old_body_sig = "static int build_body(struct jit_ctx *ctx)\n"
    new_body_sig = "static int build_body(struct jit_ctx *ctx, bool extra_pass)\n"
    if j.count(old_body_sig) != 1:
        raise SystemExit(f"build_body signature count={j.count(old_body_sig)}")
    j = j.replace(old_body_sig, new_body_sig, 1)

    old_insn_call = "\t\tret = build_insn(insn, ctx);\n"
    new_insn_call = "\t\tret = build_insn(insn, ctx, extra_pass);\n"
    if j.count(old_insn_call) != 1:
        raise SystemExit(f"build_insn invocation count={j.count(old_insn_call)}")
    j = j.replace(old_insn_call, new_insn_call, 1)

    # Two build_body calls: fake/real pass. Pass current extra_pass state to both.
    if j.count("build_body(&ctx)") != 2:
        raise SystemExit(f"build_body invocation count={j.count('build_body(&ctx)')}")
    j = j.replace("build_body(&ctx)", "build_body(&ctx, extra_pass)")

    jit.write_text(j)

checks = {
    "jit_enabled": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "generic_proto": "bpf_jit_get_func_addr(" in hdr.read_text(),
    "generic_helper": marker in core.read_text(),
    "arm64_marker": marker in jit.read_text(),
    "pseudo_call_resolution": "prog->aux->func[off]->bpf_func" in core.read_text(),
    "arm64_uses_helper": "bpf_jit_get_func_addr(ctx->prog, insn, extra_pass" in jit.read_text(),
    "extra_pass_threaded": "build_body(&ctx, extra_pass)" in jit.read_text(),
    "legacy_direct_formula_removed": "const u64 func = (u64)__bpf_call_base + imm;" not in jit.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase83 staging audit failed: " + ", ".join(failed))

print("phase83_bpf_subprog_call_target_fix=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
