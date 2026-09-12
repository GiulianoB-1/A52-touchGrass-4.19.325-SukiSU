#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 79_apply_real_bpf_jit_unguarded_control.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
filt = root / "include/linux/filter.h"
mk = root / "arch/arm64/net/Makefile"

c = cfg.read_text()

if "# CONFIG_BPF_JIT is not set\n" in c:
    c = c.replace("# CONFIG_BPF_JIT is not set\n", "CONFIG_BPF_JIT=y\n", 1)
if "CONFIG_BPF_JIT=y\n" not in c:
    raise SystemExit("CONFIG_BPF_JIT=y missing")

if "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n" in c:
    c = c.replace("# CONFIG_BPF_JIT_ALWAYS_ON is not set\n",
                  "CONFIG_BPF_JIT_ALWAYS_ON=y\n", 1)
elif "CONFIG_BPF_JIT_ALWAYS_ON=y\n" not in c:
    raise SystemExit("CONFIG_BPF_JIT_ALWAYS_ON state missing")

if "CONFIG_PSTORE_RAM=y\n" in c:
    c = c.replace("CONFIG_PSTORE_RAM=y\n",
                  "# CONFIG_PSTORE_RAM is not set\n", 1)
elif "# CONFIG_PSTORE_RAM is not set\n" not in c:
    raise SystemExit("CONFIG_PSTORE_RAM state missing")

cfg.write_text(c)

# This phase is intentionally unguarded. Fail the build if Phase78's ABI
# wrapper or assembly object somehow leaked into the reconstructed source.
leaks = []
if filt.exists() and "A52_PHASE78_BPF_ABI_GUARD_V1" in filt.read_text():
    leaks.append("filter.h ABI guard")
if mk.exists() and "bpf_abi_guard.o" in mk.read_text():
    leaks.append("bpf_abi_guard.o")
if (root / "arch/arm64/net/bpf_abi_guard.S").exists():
    leaks.append("bpf_abi_guard.S")
if leaks:
    raise SystemExit("Phase79 must be unguarded; leaked: " + ", ".join(leaks))

checks = {
    "jit_enabled": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on": "CONFIG_BPF_JIT_ALWAYS_ON=y" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "bpf_syscall": "CONFIG_BPF_SYSCALL=y" in cfg.read_text(),
    "cgroup_bpf": "CONFIG_CGROUP_BPF=y" in cfg.read_text(),
    "no_phase78_filter_guard": "A52_PHASE78_BPF_ABI_GUARD_V1" not in filt.read_text(),
    "no_guard_object": "bpf_abi_guard.o" not in mk.read_text(),
}
failed = [k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("phase79 audit failed: " + ", ".join(failed))

print("phase79_real_bpf_jit_unguarded_control=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
