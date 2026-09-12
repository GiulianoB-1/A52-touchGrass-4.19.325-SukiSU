#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 86_apply_arm64_bpf_jit_frame_restore.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
jit = root / "arch/arm64/net/bpf_jit_comp.c"

s = jit.read_text()
marker = "A52_PHASE86_BPF_JIT_X29_FRAME_RESTORE_V1"

old = """	/* We're done with BPF stack */
	emit(A64_ADD_I(1, A64_SP, A64_SP, ctx->stack_size), ctx);

	/* Restore fs (x25) and x26 */
	emit(A64_POP(fp, A64_R(26), A64_SP), ctx);
"""

new = f"""	/* {marker}
	 * Re-anchor the epilogue to the architectural frame pointer instead of
	 * trusting the live SP plus ctx->stack_size.
	 *
	 * The A52 LLVM17 hardware crash proved that a JITed cgroup BPF program
	 * can return with the caller's x25 clobbered. The JIT prologue saves the
	 * x25/x26 pair at FP-48, so restoring SP from x29 makes the callee-saved
	 * restore independent of any BPF-stack/tail-call SP drift.
	 */
	emit(A64_SUB_I(1, A64_SP, A64_FP, 48), ctx);

	/* Restore BPF fp (x25) and tail-call counter (x26). */
	emit(A64_POP(fp, A64_R(26), A64_SP), ctx);
"""

if marker not in s:
    if s.count(old) != 1:
        raise SystemExit(f"epilogue anchor count={s.count(old)}")
    s = s.replace(old, new, 1)
    jit.write_text(s)

t = jit.read_text()
checks = {
    "marker": marker in t,
    "sp_reanchored_from_x29": "A64_SUB_I(1, A64_SP, A64_FP, 48)" in t,
    "x25_x26_restore_retained": "A64_POP(fp, A64_R(26), A64_SP)" in t,
    "old_stack_size_epilogue_removed": old not in t,
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase86 staging audit failed: " + ", ".join(failed))

print("phase86_arm64_bpf_jit_frame_restore=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
