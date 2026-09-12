#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 80_apply_bpf_jit_fp_x24.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
jit = root / "arch/arm64/net/bpf_jit_comp.c"
filt = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"

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

# Keep the diagnostic clean: this phase must not contain either external
# Phase78 call guard. We are fixing the register allocation inside the JIT.
leaks = []
if filt.exists():
    h = filt.read_text()
    if "A52_PHASE78_BPF_ABI_GUARD_V1" in h:
        leaks.append("full ABI guard")
    if "A52_PHASE78_BPF_X25_CALL_GUARD_V1" in h:
        leaks.append("x25 call guard")
if mk.exists():
    m = mk.read_text()
    if "bpf_abi_guard.o" in m:
        leaks.append("bpf_abi_guard.o")
    if "bpf_call_x25_guard.o" in m:
        leaks.append("bpf_call_x25_guard.o")
if (root / "arch/arm64/net/bpf_abi_guard.S").exists():
    leaks.append("bpf_abi_guard.S")
if (root / "arch/arm64/net/bpf_call_x25_guard.S").exists():
    leaks.append("bpf_call_x25_guard.S")
if leaks:
    raise SystemExit("Phase80 must be wrapper-free; leaked: " + ", ".join(leaks))

s = jit.read_text()
marker = "A52_PHASE80_BPF_FP_X24_V1"

old = """\t/* read-only frame pointer to access stack */
\t[BPF_REG_FP] = A64_R(25),
"""
new = f"""\t/* read-only frame pointer to access stack
\t *
\t * {marker}
\t * Avoid x25 on this Android/LLVM17 combination. The LLVM17 kernel caller
\t * keeps current (SP_EL0) live in x25 across BPF_PROG_RUN, while hardware
\t * testing proved that the legacy 4.19 arm64 JIT can return with x25
\t * corrupted. x24 is otherwise unused by this JIT and is AAPCS64
\t * callee-saved, so the existing prologue/epilogue pair preserves it with
\t * no additional instructions or call wrapper.
\t */
\t[BPF_REG_FP] = A64_R(24),
"""

if marker not in s:
    if s.count(old) != 1:
        raise SystemExit(f"BPF_REG_FP x25 anchor count={s.count(old)}")
    if "A64_R(24)" in s:
        raise SystemExit("x24 unexpectedly already used by arm64 BPF JIT")
    s = s.replace(old, new, 1)
    jit.write_text(s)

j = jit.read_text()
checks = {
    "jit_enabled": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "bpf_syscall": "CONFIG_BPF_SYSCALL=y" in cfg.read_text(),
    "cgroup_bpf": "CONFIG_CGROUP_BPF=y" in cfg.read_text(),
    "marker": marker in j,
    "fp_x24": "[BPF_REG_FP] = A64_R(24)," in j,
    "fp_x25_removed": "[BPF_REG_FP] = A64_R(25)," not in j,
    "single_x24_use": j.count("A64_R(24)") == 1,
    "no_full_guard": "A52_PHASE78_BPF_ABI_GUARD_V1" not in filt.read_text(),
    "no_x25_guard": "A52_PHASE78_BPF_X25_CALL_GUARD_V1" not in filt.read_text(),
    "no_guard_objects": "bpf_abi_guard.o" not in mk.read_text() and
                        "bpf_call_x25_guard.o" not in mk.read_text(),
}
failed = [k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("phase80 audit failed: " + ", ".join(failed))

print("phase80_bpf_jit_fp_x24=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
