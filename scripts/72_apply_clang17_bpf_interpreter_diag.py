#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
cfg = root / "arch/arm64/configs/a52xq_defconfig"
s = cfg.read_text()

s = s.replace("CONFIG_BPF_JIT=y\n", "# CONFIG_BPF_JIT is not set\n", 1)
s = s.replace("CONFIG_BPF_JIT_ALWAYS_ON=y\n", "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n", 1)

if "# CONFIG_BPF_JIT is not set" not in s:
    raise SystemExit("failed to disable CONFIG_BPF_JIT")
if "CONFIG_BPF_SYSCALL=y" not in s:
    raise SystemExit("CONFIG_BPF_SYSCALL=y missing")
if "CONFIG_CGROUP_BPF=y" not in s:
    raise SystemExit("CONFIG_CGROUP_BPF=y missing")

cfg.write_text(s)
print("clang17_bpf_interpreter_diag=applied")
print("bpf_jit=n")
print("bpf_syscall=y")
print("cgroup_bpf=y")
