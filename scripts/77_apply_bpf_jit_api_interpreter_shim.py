#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 77_apply_bpf_jit_api_interpreter_shim.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
jit = root / "arch/arm64/net/bpf_jit_comp.c"

c = cfg.read_text()

# Android 12 bpfloader expects the JIT sysctl interface to exist. Keep
# CONFIG_BPF_JIT=y, but turn off ALWAYS_ON so interpreter fallback is legal.
if "# CONFIG_BPF_JIT is not set\n" in c:
    c = c.replace("# CONFIG_BPF_JIT is not set\n", "CONFIG_BPF_JIT=y\n", 1)
elif "CONFIG_BPF_JIT=y\n" not in c:
    raise SystemExit("CONFIG_BPF_JIT state not found")

if "CONFIG_BPF_JIT_ALWAYS_ON=y\n" in c:
    c = c.replace("CONFIG_BPF_JIT_ALWAYS_ON=y\n",
                  "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n", 1)
elif "# CONFIG_BPF_JIT_ALWAYS_ON is not set\n" not in c:
    raise SystemExit("CONFIG_BPF_JIT_ALWAYS_ON state not found")

# TGREC owns the fixed 0xB1B00000 1 MiB region in this diagnostic. The
# normal ramoops backend otherwise writes DBGC data over the mirrored TGREC
# records, which is exactly what Phase76 hardware showed.
if "CONFIG_PSTORE_RAM=y\n" in c:
    c = c.replace("CONFIG_PSTORE_RAM=y\n",
                  "# CONFIG_PSTORE_RAM is not set\n", 1)
elif "# CONFIG_PSTORE_RAM is not set\n" not in c:
    raise SystemExit("CONFIG_PSTORE_RAM state not found")

cfg.write_text(c)

s = jit.read_text()
marker = "A52_PHASE77_BPF_JIT_API_INTERPRETER_SHIM_V1"
if marker not in s:
    anchor = """struct bpf_prog *bpf_int_jit_compile(struct bpf_prog *prog)
{
"""
    if s.count(anchor) != 1:
        raise SystemExit(f"bpf_int_jit_compile anchor count={s.count(anchor)}")
    replacement = anchor + f"""\t/*
\t * {marker}
\t *
\t * Keep CONFIG_BPF_JIT and its Android-visible sysctls present, but do
\t * not execute this legacy arm64 JIT backend while diagnosing the
\t * Clang17-only register corruption. Because BPF_JIT_ALWAYS_ON is off,
\t * returning the unchanged program here makes core BPF retain the
\t * interpreter selected before the JIT attempt.
\t */
\tif (prog->jit_requested) {{
\t\tpr_info_once("A52: arm64 BPF JIT API present, execution forced to interpreter\\n");
\t\treturn prog;
\t}}

"""
    s = s.replace(anchor, replacement, 1)
    jit.write_text(s)

checks = {
    "bpf_jit_present": "CONFIG_BPF_JIT=y" in cfg.read_text(),
    "jit_always_on_off": "# CONFIG_BPF_JIT_ALWAYS_ON is not set" in cfg.read_text(),
    "pstore_ram_off": "# CONFIG_PSTORE_RAM is not set" in cfg.read_text(),
    "bpf_syscall": "CONFIG_BPF_SYSCALL=y" in cfg.read_text(),
    "cgroup_bpf": "CONFIG_CGROUP_BPF=y" in cfg.read_text(),
    "jit_backend_shim": marker in jit.read_text(),
    "interpreter_return": "if (prog->jit_requested)" in jit.read_text() and "return prog;" in jit.read_text(),
}
failed = [k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("phase77 audit failed: " + ", ".join(failed))

print("phase77_bpf_jit_api_interpreter_shim=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
