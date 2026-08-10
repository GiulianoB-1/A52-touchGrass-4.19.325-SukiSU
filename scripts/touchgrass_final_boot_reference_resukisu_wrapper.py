#!/usr/bin/env python3
"""Layer the final boot-reference recorder onto the proven Run-10 GPU build.

We intentionally invoke the already hardware-proven GPU wrapper first. The final
recorder is then injected immediately before build_kernel(), preserving the exact
TouchGrass 4.19.200 + ReSukiSU-safe configuration and the GPU recorder behavior.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_final_boot_reference_resukisu_wrapper.py <safe-build-template>')

path = Path(sys.argv[1])
project = Path(__file__).resolve().parent.parent

# First reproduce the exact recorder integration that already booted on hardware.
subprocess.run([
    sys.executable,
    str(project / 'scripts/touchgrass_gpu_reference_resukisu_wrapper.py'),
    str(path),
], check=True)

text = path.read_text()
anchor = '''info "Building Linux 4.19.153 + safe ReSukiSU + GPU reference recorder"\nbuild_kernel "$LABEL"\n'''
block = r'''info "Applying observation-only final TouchGrass boot-reference recorder"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_final_boot_reference_overlay.py"
python3 "$PROJECT_DIR/scripts/touchgrass_final_boot_reference_overlay.py" "$ROOT"

git -C "$ROOT" diff --check

test -s "$ROOT/include/linux/tg_boot_reference.h" || fail "final boot recorder header missing"
test -s "$ROOT/kernel/tg_boot_reference.c" || fail "final boot recorder implementation missing"
grep -Fq 'obj-y += tg_boot_reference.o' "$ROOT/kernel/Makefile" || fail "final boot recorder Kbuild hook missing"
grep -Fq 'INITCALL:POST' "$ROOT/init/main.c" || fail "initcall result recorder missing"
grep -Fq 'PROBE:BUS_POST' "$ROOT/drivers/base/dd.c" || fail "bus probe result recorder missing"
grep -Fq 'PROBE:DRV_POST' "$ROOT/drivers/base/dd.c" || fail "driver probe result recorder missing"
grep -Fq 'IOMMU:GROUP_ADD' "$ROOT/drivers/iommu/iommu.c" || fail "generic IOMMU recorder missing"
grep -Fq 'USER:KERNEL_INIT' "$ROOT/init/main.c" || fail "userspace handoff recorder missing"

# Confirm the independent GPU recorder is still present too.
grep -Fq 'touchgrass_gpu_reference_v1' "$ROOT/kernel/tg_gpu_reference.c" || fail "GPU reference recorder disappeared"
grep -Fq 'SMMU:CEN_POST' "$ROOT/drivers/iommu/arm-smmu.c" || fail "GPU SMMU reference hook disappeared"
grep -Fq 'GMU:ATT_POST' "$ROOT/drivers/gpu/msm/kgsl_gmu.c" || fail "GPU GMU reference hook disappeared"

info "Building Linux 4.19.200 + ReSukiSU-safe + GPU + final boot reference recorders"
build_kernel "$LABEL"
'''

if text.count(anchor) != 1:
    raise SystemExit(f'final recorder compile anchor mismatch: expected 1, found {text.count(anchor)}')
path.write_text(text.replace(anchor, block, 1))
print(f'Injected final TouchGrass boot-reference recorder into {path}')
