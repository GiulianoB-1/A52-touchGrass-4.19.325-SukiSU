#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 81_apply_bpf_x29_frame_restore.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
jit = root / "arch/arm64/net/bpf_jit_comp.c"

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

s = jit.read_text()

marker = "A52_PHASE81_BPF_X29_FRAME_RESTORE_V1"
old = """\t/* We're done with BPF stack */\n\temit(A64_ADD_I(1, A64_SP, A64_SP, ctx->stack_size), ctx);\n\n\t/* Restore fs (x25) and x26 */\n\temit(A64_POP(fp, A64_R(26), A64_SP), ctx);\n"""

new = f"""\t/* {marker}\n\t * Re-anchor the epilogue to the architectural frame pointer instead of\n\t * trusting the live SP plus ctx->stack_size.  x29 is established before\n\t * the BPF callee-saved pushes and remains the stable frame anchor across\n\t * helpers and tail-call handoffs.  In this 4.19 frame layout, the saved\n\t * x25/x26 pair is exactly 48 bytes below x29.\n\t */\n\temit(A64_SUB_I(1, A64_SP, A64_FP, 48), ctx);\n\n\t/* Restore BPF fp (x25) and tail-call counter (x26). */\n\temit(A64_POP(fp, A64_R(26), A64_SP), ctx);\n"""

if marker not in s:
    if s.count(old) != 1:
        raise SystemExit(f"epilogue anchor count={s.count(old)}")
    s = s.replace(old, new, 1)
    jit.write_text(s)

checks = {
    "jit": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "x29_anchor": marker in jit.read_text(),
    "sp_from_x29": "A64_SUB_I(1, A64_SP, A64_FP, 48)" in jit.read_text(),
    "legacy_sp_teardown_removed": old not in jit.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase81 staging audit failed: " + ", ".join(failed))

print("phase81_bpf_x29_frame_restore=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
