#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
PANEL="$ROOT/drivers/a52_display/msm/dsi/dsi_panel.c"
DISPLAY="$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"

fail_report(){
  set +e
  rm -rf phase276r-failure
  mkdir -p phase276r-failure/source phase276r-failure/audit phase276r-failure/logs
  cp phase276r-compile.log phase276r-failure/logs/ 2>/dev/null || true
  cp phase276r-deep-path-parity-before.txt phase276r-failure/audit/ 2>/dev/null || true
  for p in "$PANEL" "$DISPLAY" "$CTRL"; do
    [ -f "$p" ] && cp "$p" phase276r-failure/source/ || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase276 base. This is not the final artifact.
bash scripts/276_ci_build.sh
test -s phase276-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p276r-base.config
cp "$PANEL" /tmp/p276r-panel.c
cp "$DISPLAY" /tmp/p276r-display.c
cp "$CTRL" /tmp/p276r-ctrl.c

# Golden gate before any Phase276R instrumentation. In addition to the already
# proven host/controller/message path, V4 requires exact parity for the common
# memory-DMA kickoff, error-status reader, and catalog op wiring.
python3 -m py_compile \
  scripts/276r_deep_dsi_parity_probe.py \
  scripts/276r_deep_dsi_frontier.py \
  scripts/276r_precise_dma_probe.py \
  scripts/276r_audit_candidate.py
python3 scripts/276r_deep_dsi_parity_probe.py "$ROOT" "$TG"
grep -Fq 'path_exact_match=1' phase276r-deep-path-parity-before.txt
grep -Fq 'low_level_hw_exact_match=1' phase276r-deep-path-parity-before.txt
grep -Fq 'all_exact_match=1' phase276r-deep-path-parity-before.txt

# Preserve the previously hardware-proven Phase276R target correlation and deep
# call-path checkpoints.
python3 scripts/276r_deep_dsi_frontier.py "$ROOT"

# The deep patcher declares the target helper before dsi_ctrl_cmd_transfer(), but
# kickoff/DMA-wait checkpoints live earlier in dsi_ctrl.c. Move the declaration
# to file scope after the first recorder include. Declaration order only.
python3 - "$CTRL" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
s = p.read_text()
decl = 'extern bool a52_p276r_deep_active(void);\n'
inc = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
decl_count = s.count(decl)
inc_count = s.count(inc)
if decl_count != 1:
    raise SystemExit(f'deep-active declaration count {decl_count}, expected 1')
if inc_count < 1:
    raise SystemExit('recorder include missing')
s = s.replace(decl, '', 1)
s = s.replace(inc, inc + decl, 1)
decl_pos = s.find(decl)
first_call = s.find('a52_p276r_deep_active()', decl_pos + len(decl))
if decl_pos < 0 or first_call < 0 or decl_pos > first_call:
    raise SystemExit('deep-active declaration still follows first call')
p.write_text(s)
print(f'Phase276R deep-active declaration relocated before first use (recorder includes={inc_count})')
PY

# Hardware V3 proved: wait timeout, raw status=0, DMA_DONE=0, dma_irq_trig=0,
# IRQ mask=0x401, DMA_DONE refcount=1, then Samsung panic path. V4 therefore adds
# only two new target-only discriminators around that exact failure:
#   1) memory-DMA IOVA/length/hw_flags immediately before kickoff_command();
#   2) read-only DSI hardware error bitmap after the existing timeout status read.
# It keeps the V3 status/panic records for correlation. No control flow, register
# write, lock, command, timeout, payload, flag, or return value is changed.
python3 scripts/276r_precise_dma_probe.py "$CTRL"

! cmp -s /tmp/p276r-panel.c "$PANEL"
! cmp -s /tmp/p276r-display.c "$DISPLAY"
! cmp -s /tmp/p276r-ctrl.c "$CTRL"
grep -Fq 'A52_PHASE276R_DMA_PROGRAM_ERROR_DISCRIMINATOR_V4' "$CTRL"
grep -Fq 'P276 H K o=%llx l=%u h=%x' "$CTRL"
grep -Fq 'P276 H E e=%llx' "$CTRL"
grep -Fq 'P276 P S st=%x dn=%u a=%d im=%x ir=%u' "$CTRL"
grep -Fq 'P276 P P e=%u d=%u' "$CTRL"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p276r-base.config "$OUT/.config"
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase276r-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase276r-out
mkdir -p phase276r-out/{compile,config,package,audit,source}
cp "$IMAGE" phase276r-out/compile/Image
cp "$OUT/.config" phase276r-out/config/final.config
cp /tmp/p276r-base.config phase276r-out/audit/phase276-final.config
cp phase276r-deep-path-parity-before.txt phase276r-out/audit/
cp phase276r-compile.log phase276r-out/audit/
cp scripts/276r_*.py phase276r-out/audit/
cp "$PANEL" phase276r-out/source/dsi_panel.c
cp "$DISPLAY" phase276r-out/source/dsi_display.c
cp "$CTRL" phase276r-out/source/dsi_ctrl.c

gzip -n -c "$IMAGE" > phase276r-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase276-out/package/boot.img \
  --kernel phase276r-out/package/Image.gz \
  --output phase276r-out/package/boot.img \
  --report phase276r-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase276r-out')
idn = {
    'phase': '276R',
    'name': 'DEEP-DSI-DMA-PROGRAM-ERROR-DISCRIMINATOR-V4',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'supersedes_phase276_for_hardware': True,
    'hardware_question': (
        'For the proven TX_LEVEL1_KEY_ENABLE memory-DMA failure with DMA_DONE=0, '
        'capture the programmed DMA IOVA/length/hw_flags and the read-only DSI '
        'hardware error bitmap to distinguish DMA mapping/programming failure from '
        'controller/PHY/FIFO/timeout/clock failure.'
    ),
    'previous_hardware_result': 'status=0 DMA_DONE=0 dma_irq_trig=0 irq_mask=0x401 dma_done_refcount=1',
    'functional_change': (
        'none; observation only; adds exactly two target-only runtime records beyond V3 '
        'and preserves the V3 timeout/panic discriminators'
    ),
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase276-final.config',
    'audit/phase276r-deep-path-parity-before.txt', 'source/dsi_panel.c',
    'source/dsi_display.c', 'source/dsi_ctrl.c',
]
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY

(cd phase276r-out && sha256sum -c SHA256SUMS)
python3 scripts/276r_audit_candidate.py phase276r-out

# Final source/Image marker guard. The source-only comment must not be required in
# Image; only runtime strings are expected to survive compilation.
python3 - <<'PY'
from pathlib import Path
r = Path('phase276r-out')
s = (r/'source/dsi_ctrl.c').read_text()
img = (r/'compile/Image').read_bytes()
source_only = ['A52_PHASE276R_DMA_PROGRAM_ERROR_DISCRIMINATOR_V4']
runtime = [
    'P276 P S st=%x dn=%u a=%d im=%x ir=%u',
    'P276 P P e=%u d=%u',
    'P276 H K o=%llx l=%u h=%x',
    'P276 H E e=%llx',
]
for t in source_only:
    if t not in s:
        raise SystemExit('source V4 marker missing ' + t)
for t in runtime:
    if t not in s:
        raise SystemExit('source V4 runtime marker missing ' + t)
    if t.encode() not in img:
        raise SystemExit('Image V4 runtime marker missing ' + t)
print('Phase276R precise DMA programming/error V4 marker audit: PASS')
PY

trap - EXIT
echo 'Phase276R precise DSI DMA programming/error V4 build/repack: PASS'
