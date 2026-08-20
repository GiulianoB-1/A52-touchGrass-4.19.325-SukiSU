#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
CLK="$ROOT/drivers/a52_display/msm/dsi/dsi_clk_manager.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"

fail_report() {
  set +e
  rm -rf phase291-failure
  mkdir -p phase291-failure/{source,logs,audit,config}
  cp phase291-compile.log phase291-failure/logs/ 2>/dev/null || true
  for f in "$CLK" "$DSI" "$HW"; do
    [ -f "$f" ] && cp "$f" phase291-failure/source/ || true
  done
  cp scripts/291*.py scripts/291*.sh phase291-failure/audit/ 2>/dev/null || true
  cp /tmp/p291-*.config /tmp/p291-*.diff /tmp/p291-*.c phase291-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase289 lineage first.
bash scripts/289_ci_build.sh
test -s phase289-out/package/boot.img
test -s "$CLK"
grep -Fq 'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1' "$CLK"
grep -Fq 'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx' "$CLK"
grep -Fq 'A52_PHASE289_TARGET_TIMEOUT_RETENTION_V1' "$DSI"
grep -Fq 'A52_PHASE289_FIFO_CAUSAL_SLOTS_V1' "$HW"

cp "$OUT/.config" /tmp/p291-base.config
cp "$CLK" /tmp/p291-clk-before.c
cp "$DSI" /tmp/p291-dsi-before.c
cp "$HW" /tmp/p291-hw-before.c

python3 -m py_compile scripts/291_apply_cont_splash_zero_rate_recovery.py
python3 scripts/291_apply_cont_splash_zero_rate_recovery.py --self-test
python3 scripts/291_apply_cont_splash_zero_rate_recovery.py --root "$ROOT"
python3 scripts/291_apply_cont_splash_zero_rate_recovery.py --root "$ROOT" --check-only

! cmp -s /tmp/p291-clk-before.c "$CLK"
cmp -s /tmp/p291-dsi-before.c "$DSI"
cmp -s /tmp/p291-hw-before.c "$HW"
cmp -s /tmp/p291-base.config "$OUT/.config"

for token in \
  'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1' \
  'bool a52_targets_valid = l_clks->freq.byte_clk_rate &&' \
  'bool a52_zero_handoff = !a52_byte_now || !a52_pixel_now ||' \
  'if (!a52_targets_valid || !a52_zero_handoff)' \
  'P291 C0 c=%d b=%llx p=%llx i=%llx ab=%lx ap=%lx ai=%lx'; do
  grep -Fq "$token" "$CLK"
done

python3 - "$CLK" /tmp/p291-clk-before.c <<'PY'
from pathlib import Path
import sys
new = Path(sys.argv[1]).read_text()
old = Path(sys.argv[2]).read_text()

# Phase291 must reuse the existing Golden clock programming sequence rather
# than adding a parallel path or changing power/trigger primitives.
for needle in [
    'clk_set_rate(', 'clk_prepare(', 'clk_unprepare(', 'clk_enable(',
    'clk_disable(', 'clk_prepare_enable(', 'clk_disable_unprepare(',
    'dsi_link_hs_clk_start(', 'dsi_link_hs_clk_stop(',
]:
    if old.count(needle) != new.count(needle):
        raise SystemExit(f'Phase291 changed production clock primitive count for {needle}: '
                         f'{old.count(needle)} -> {new.count(needle)}')

fn0 = new.index('static int dsi_link_hs_clk_set_rate(')
fn1 = new.index('\nstatic int dsi_link_hs_clk_prepare(', fn0)
fn = new[fn0:fn1]
mark = fn.index('A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1')
valid = fn.index('if (!a52_targets_valid || !a52_zero_handoff)', mark)
byte_set = fn.index('clk_set_rate(link_hs_clks->byte_clk,', valid)
pixel_set = fn.index('clk_set_rate(link_hs_clks->pixel_clk,', byte_set)
if not (mark < valid < byte_set < pixel_set):
    raise SystemExit('Phase291 guard is not immediately upstream of the existing HS set-rate sequence')

# The original unconditional splash return must be gone from this function,
# while a guarded return remains to preserve Golden behavior for sane handoff.
if '\tif (mngr->is_cont_splash_enabled)\n\t\treturn 0;' in fn:
    raise SystemExit('Phase291 left the original unconditional splash early return')
if 'if (!a52_targets_valid || !a52_zero_handoff)\n\t\t\treturn 0;' not in fn:
    raise SystemExit('Phase291 guarded preservation return missing')

# Zero targets can never fall through to clk_set_rate.
for required in [
    'l_clks->freq.byte_clk_rate &&',
    'l_clks->freq.pix_clk_rate &&',
    'l_clks->freq.byte_intf_clk_rate',
]:
    if required not in fn[:byte_set]:
        raise SystemExit('Phase291 non-zero target guard incomplete: ' + required)

print('Phase291 source safety/ordering audit: PASS')
PY

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
if ! cmp -s /tmp/p291-base.config "$OUT/.config"; then
  diff -u /tmp/p291-base.config "$OUT/.config" > /tmp/p291-config.diff || true
  echo '::error::Phase291 changed kernel config'
  cat /tmp/p291-config.diff
  exit 1
fi

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase291-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase291-out
mkdir -p phase291-out/{compile,config,package,audit,source}
cp "$IMAGE" phase291-out/compile/Image
cp "$OUT/.config" phase291-out/config/final.config
cp /tmp/p291-base.config phase291-out/audit/phase289-final.config
cp /tmp/p291-clk-before.c phase291-out/audit/dsi-clk-manager-before.c
cp "$CLK" phase291-out/source/dsi_clk_manager.c
cp "$DSI" phase291-out/source/dsi_ctrl.c
cp "$HW" phase291-out/source/dsi_ctrl_hw_cmn.c
cp phase291-compile.log phase291-out/audit/
cp scripts/291_apply_cont_splash_zero_rate_recovery.py phase291-out/audit/
cp scripts/291_ci_build.sh phase291-out/audit/

gzip -n -c "$IMAGE" > phase291-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase289-out/package/boot.img \
  --kernel phase291-out/package/Image.gz \
  --output phase291-out/package/boot.img \
  --report phase291-out/package/repack-report.json

test -s phase291-out/package/boot.img
test "$(stat -c '%s' phase291-out/package/boot.img)" -gt 1000000
file phase291-out/package/boot.img | tee phase291-out/package/boot-file.txt
sha256sum phase291-out/package/boot.img | tee phase291-out/package/boot-sha256.txt

cat > phase291-out/PHASE291-CHANGE.txt <<'EOF'
Phase291 continuous-splash zero-rate recovery
=============================================
Hardware basis:
- Phase285 observed valid non-zero calculated DSI HS target rates while the
  Linux clock framework reported byte/pixel/byte-interface clocks as 0 Hz.
- TouchGrass dsi_link_hs_clk_set_rate() normally returns without clk_set_rate()
  whenever continuous splash is active.
- Phase289 later proved the failing FIFO command reaches a real SW_TRIGGER and
  command/DMA BUSY but never reaches DMA_DONE.

Behavioral change:
- Preserve the TouchGrass continuous-splash early return when inherited HS
  clock rates are non-zero/sane.
- Also preserve the early return when any cached target rate is zero.
- Only when cached byte/pixel/(optional byte-interface) targets are all non-zero
  AND at least one corresponding Linux clock reports 0 Hz, fall through to the
  already-existing TouchGrass clk_set_rate() sequence.
- No new clk_set_rate call site is added.
- No DSI packet, SW_TRIGGER, timeout, recovery, PHY, panel command, or brightness
  behavior is changed by Phase291.

Expected discriminator:
- If the bad splash handoff is causal, Phase284 M6/M7/M8 should finally appear
  for the first affected HS setup and the Phase289 command should reach DMA_DONE
  instead of remaining DSI_STATUS=3.
- If rates are successfully applied but DMA still hangs, PHY/REFGEN/lane-launch
  becomes the next isolated boundary.
EOF

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase291-out')
identity = {
    'phase': 291,
    'name': 'CONT-SPLASH-ZERO-RATE-RECOVERY',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'hardware-tested Phase289 lineage reconstructed by scripts/289_ci_build.sh',
    'behavior_change': True,
    'change_scope': 'DSI HS continuous-splash zero-rate handoff guard only',
    'zero_target_programming_allowed': False,
    'new_clk_set_rate_call_sites': 0,
    'dsi_trigger_policy_changed': False,
    'dsi_packet_behavior_changed': False,
    'recovery_behavior_changed': False,
    'brightness_changed_from_base': False,
    'golden_repack_source': 'phase289-out/package/boot.img',
    'repacker': 'scripts/38_repack_a52_p1_boot.py',
    'question': 'Does conditionally applying the already-derived non-zero HS rates during a demonstrably zero-rate continuous-splash handoff restore DSI DMA completion?'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
files = [p.relative_to(r) for p in sorted(r.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for rel in files:
        p = r/rel
        f.write(hashlib.sha256(p.read_bytes()).hexdigest() + '  ./' + str(rel) + '\n')
PY
(cd phase291-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
img = Path('phase291-out/compile/Image').read_bytes()
for marker in [
    b'P291 C0 c=%d b=%llx p=%llx i=%llx ab=%lx ap=%lx ai=%lx',
    b'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx',
    b'P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx',
    b'P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx',
    b'P276 282A m=fifo f=%x',
    b'P289 F4 c=%x sw=%x st=%x fs=%x in=%x',
]:
    if marker not in img:
        raise SystemExit('Phase291 compiled marker missing: ' + marker.decode())
print('Phase291 compiled lineage/recovery marker audit: PASS')
PY

python3 scripts/291_apply_cont_splash_zero_rate_recovery.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase291 continuous-splash zero-rate recovery build/Golden-FDR repack: PASS'
