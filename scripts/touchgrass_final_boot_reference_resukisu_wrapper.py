#!/usr/bin/env python3
"""Layer the final boot-reference recorder onto the proven Run-10 GPU build."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_final_boot_reference_resukisu_wrapper.py <safe-build-template>')

path = Path(sys.argv[1])
project = Path(__file__).resolve().parent.parent

# Reproduce the exact GPU recorder integration already proven to boot on hardware.
subprocess.run([
    sys.executable,
    str(project / 'scripts/touchgrass_gpu_reference_resukisu_wrapper.py'),
    str(path),
], check=True)

text = path.read_text()
anchor = '''info "Building Linux 4.19.153 + safe ReSukiSU + GPU reference recorder"\nbuild_kernel "$LABEL"\n'''
block = r'''info "Applying observation-only final TouchGrass boot-reference recorder v2"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_final_boot_reference_overlay_v2.py"
python3 "$PROJECT_DIR/scripts/touchgrass_final_boot_reference_overlay_v2.py" "$ROOT"

# The v2 overlay originally inserted its header after the textual last #include.
# Several Samsung sources end their include section inside CONFIG_* guards, so
# that can hide TG_BOOT_REF/TG_BOOT_REF0 from the actual build. Normalize every
# recorder-touched source to one include at preprocessor depth zero.
python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
inc = '#include <linux/tg_boot_reference.h>'
targets = (
    'init/main.c',
    'drivers/base/dd.c',
    'drivers/iommu/iommu.c',
    'drivers/android/binder.c',
    'drivers/scsi/ufs/ufshcd.c',
    'drivers/scsi/ufs/ufs-qcom.c',
    'init/do_mounts.c',
    'techpack/display/msm/msm_drv.c',
    'techpack/display/msm/dsi/dsi_display.c',
    'techpack/display/msm/dsi/dsi_panel.c',
    'techpack/display/msm/sde/sde_kms.c',
)

if_re = re.compile(r'^#\s*(?:if|ifdef|ifndef)\b')
endif_re = re.compile(r'^#\s*endif\b')
include_re = re.compile(r'^#\s*include\b')


def include_depth(text: str) -> int:
    depth = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped == inc:
            return depth
        if if_re.match(stripped):
            depth += 1
        elif endif_re.match(stripped):
            depth -= 1
            if depth < 0:
                raise SystemExit('preprocessor nesting underflow while auditing recorder include')
    raise SystemExit('recorder include disappeared during audit')


def insert_before_first_top_level_include(text: str, label: str) -> str:
    lines = text.splitlines(keepends=True)
    depth = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if depth == 0 and include_re.match(stripped):
            lines.insert(i, inc + '\n')
            return ''.join(lines)
        if if_re.match(stripped):
            depth += 1
        elif endif_re.match(stripped):
            depth -= 1
            if depth < 0:
                raise SystemExit(f'{label}: preprocessor nesting underflow')
    raise SystemExit(f'{label}: no unconditional include anchor found')


for rel in targets:
    p = root / rel
    if not p.is_file():
        continue
    s = p.read_text()
    uses_recorder = 'TG_BOOT_REF(' in s or 'TG_BOOT_REF0(' in s
    if not uses_recorder:
        continue
    count = s.count(inc)
    if count != 1:
        raise SystemExit(f'{rel}: final recorder include count mismatch: {count}')
    s = s.replace(inc, '', 1)
    s = insert_before_first_top_level_include(s, rel)
    if s.count(inc) != 1 or include_depth(s) != 0:
        raise SystemExit(f'{rel}: final recorder include is not unconditional')
    p.write_text(s)
    print(f'FINAL_RECORDER_INCLUDE_OK {rel}')
PY

# Keep the old audit token as an explicit compatibility line, while the first
# proc header remains the authoritative v2 format marker.
python3 - "$ROOT/kernel/tg_boot_reference.c" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
needle = 'seq_puts(m, "# touchgrass_final_boot_reference_v2\\n");'
compat = 'seq_puts(m, "# touchgrass_final_boot_reference_v1 compatibility\\n");'
if compat not in s:
    if s.count(needle) != 1:
        raise SystemExit('final boot recorder v2 proc-header anchor mismatch')
    s = s.replace(needle, needle + '\n\t' + compat, 1)
p.write_text(s)
PY

# Package the exact overlay used by this build under the stable artifact name.
cp "$PROJECT_DIR/scripts/touchgrass_final_boot_reference_overlay_v2.py" \
   "$PROJECT_DIR/scripts/touchgrass_final_boot_reference_overlay.py"

git -C "$ROOT" diff --check

test -s "$ROOT/include/linux/tg_boot_reference.h" || fail "final boot recorder header missing"
test -s "$ROOT/kernel/tg_boot_reference.c" || fail "final boot recorder implementation missing"
grep -Fq 'obj-y += tg_boot_reference.o' "$ROOT/kernel/Makefile" || fail "final boot recorder Kbuild hook missing"
grep -Fq 'touchgrass_final_boot_reference_v2' "$ROOT/kernel/tg_boot_reference.c" || fail "final boot recorder v2 marker missing"
grep -Fq 'INITCALL:POST' "$ROOT/init/main.c" || fail "initcall result recorder missing"
grep -Fq 'PROBE:BUS_POST' "$ROOT/drivers/base/dd.c" || fail "bus probe result recorder missing"
grep -Fq 'PROBE:DRV_POST' "$ROOT/drivers/base/dd.c" || fail "driver probe result recorder missing"
grep -Fq 'IOMMU:GROUP_ADD' "$ROOT/drivers/iommu/iommu.c" || fail "generic IOMMU recorder missing"
grep -Fq 'USER:RUN_INIT' "$ROOT/init/main.c" || fail "userspace handoff recorder missing"

# Confirm the independent, hardware-proven GPU recorder remains intact.
grep -Fq 'touchgrass_gpu_reference_v1' "$ROOT/kernel/tg_gpu_reference.c" || fail "GPU reference recorder disappeared"
grep -Fq 'SMMU:CEN_POST' "$ROOT/drivers/iommu/arm-smmu.c" || fail "GPU SMMU reference hook disappeared"
grep -Fq 'GMU:ATT_POST' "$ROOT/drivers/gpu/msm/kgsl_gmu.c" || fail "GPU GMU reference hook disappeared"

info "Building Linux 4.19.200 + ReSukiSU-safe + GPU + final boot reference recorders"
build_kernel "$LABEL"
'''

if text.count(anchor) != 1:
    raise SystemExit(f'final recorder compile anchor mismatch: expected 1, found {text.count(anchor)}')
path.write_text(text.replace(anchor, block, 1))
print(f'Injected final TouchGrass boot-reference recorder v2 into {path}')
