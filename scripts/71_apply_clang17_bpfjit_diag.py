#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
cfg = root / "arch/arm64/configs/a52xq_defconfig"
s = cfg.read_text()

old = "CONFIG_BPF_JIT_ALWAYS_ON=y\n"
new = "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n"

if old in s:
    s = s.replace(old, new, 1)
elif new not in s:
    raise SystemExit("BPF_JIT_ALWAYS_ON setting not found")

if "CONFIG_BPF_JIT=y" not in s:
    raise SystemExit("CONFIG_BPF_JIT=y missing")
if "CONFIG_BPF_SYSCALL=y" not in s:
    raise SystemExit("CONFIG_BPF_SYSCALL=y missing")
if "CONFIG_CGROUP_BPF=y" not in s:
    raise SystemExit("CONFIG_CGROUP_BPF=y missing")

cfg.write_text(s)
print("clang17_bpfjit_diag=applied")
print("bpf_jit=y")
print("bpf_jit_always_on=n")
