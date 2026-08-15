#!/usr/bin/env python3
"""Layer the precise Composer/DRM golden recorder on the proven TouchGrass final recorder build."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_composer_drm_reference_resukisu_wrapper.py <safe-build-template>')

path = Path(sys.argv[1])
project = Path(__file__).resolve().parent.parent

subprocess.run([
    sys.executable,
    str(project / 'scripts/touchgrass_final_boot_reference_resukisu_wrapper.py'),
    str(path),
], check=True)

text = path.read_text()
anchor = '''info "Building Linux 4.19.200 + ReSukiSU-safe + GPU + final boot reference recorders"\nbuild_kernel "$LABEL"\n'''
block = r'''info "Applying observation-only precise TouchGrass Composer/DRM golden recorder v1"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_overlay.py"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_retention_fix.py"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_payload_fix.py"
python3 "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_overlay.py" --self-test
python3 "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_overlay.py" "$ROOT"
python3 "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_retention_fix.py" "$ROOT"
python3 "$PROJECT_DIR/scripts/touchgrass_composer_drm_reference_payload_fix.py" "$ROOT"

# Guard against accidentally emitting Python escape text into C source.
if grep -Fq '\tretcode = drm_ioctl_kernel' "$ROOT/drivers/gpu/drm/drm_ioctl.c"; then
  fail "literal Python tab escape leaked into drm_ioctl.c"
fi

git -C "$ROOT" diff --check

test -s "$ROOT/include/linux/tg_display_reference.h" || fail "display recorder header missing"
test -s "$ROOT/kernel/tg_display_reference.c" || fail "display recorder implementation missing"
grep -Fq 'obj-y += tg_display_reference.o' "$ROOT/kernel/Makefile" || fail "display recorder Kbuild hook missing"
grep -Fq 'touchgrass_composer_drm_reference_v1' "$ROOT/kernel/tg_display_reference.c" || fail "display recorder marker missing"
grep -Fq 'first-events-retention' "$ROOT/kernel/tg_display_reference.c" || fail "display recorder retention policy missing"
for marker in \
  COMPOSER_EXEC SYS_OPEN_IN SYS_IOCTL_IN SYS_MMAP_IN DRM_OPEN_IN DRM_IOCTL_DESC \
  PROP_CREATE PROP_GET OBJ_PROP ATOMIC_PROP MSM_ATOMIC_IN BINDER_TX_TARGET \
  DRM_CAP DRM_CLIENT_CAP DRM_RES_COUNTS DRM_CONN_A DRM_PLANE_RES DRM_PLANE_A \
  DRM_ENCODER DRM_CRTC_A DRM_PROP_SUM DRM_OBJ_SUM; do
  grep -RqsF "$marker" \
    "$ROOT/kernel/tg_display_reference.c" \
    "$ROOT/fs" "$ROOT/mm/mmap.c" "$ROOT/drivers/gpu/drm" \
    "$ROOT/drivers/android/binder.c" "$ROOT/techpack/display/msm/msm_drv.c" \
    || fail "display recorder hook missing: $marker"
done

# The already hardware-proven golden recorders must remain untouched and present.
grep -Fq 'touchgrass_gpu_reference_v1' "$ROOT/kernel/tg_gpu_reference.c" || fail "GPU reference recorder disappeared"
grep -Fq 'touchgrass_final_boot_reference_v2' "$ROOT/kernel/tg_boot_reference.c" || fail "final boot recorder disappeared"

info "Building Linux 4.19.200 + ReSukiSU-safe + GPU + boot + precise Composer/DRM golden recorders"
build_kernel "$LABEL"
'''

if text.count(anchor) != 1:
    raise SystemExit(f'composer/DRM recorder compile anchor mismatch: expected 1, found {text.count(anchor)}')
path.write_text(text.replace(anchor, block, 1))
print(f'Injected precise TouchGrass Composer/DRM golden recorder into {path}')
