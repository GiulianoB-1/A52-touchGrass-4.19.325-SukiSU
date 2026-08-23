#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase305-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
SDE="$ROOT/drivers/a52_display/msm/sde_rsc.c"
BUS="$ROOT/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
RSC="$ROOT/drivers/soc/qcom/rpmh-rsc.c"
RPMH="$ROOT/drivers/soc/qcom/rpmh.c"
COMPAT="$ROOT/a52-port-compat.h"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report() {
  set +e
  rm -rf phase305-failure
  mkdir -p phase305-failure/{logs,audit,source}
  cp phase305-compile.log phase305-olddefconfig.log phase305-failure/logs/ 2>/dev/null || true
  for f in "$CTRL" "$HW" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do
    [ -f "$f" ] && cp "$f" phase305-failure/source/ || true
  done
  cp /tmp/p305-* phase305-failure/audit/ 2>/dev/null || true
  cp scripts/305_apply_display_rpmh_flush_compat.py phase305-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-validated Phase304 observer lineage first.
bash scripts/304_ci_build.sh

test -s phase304-out/package/boot.img
test -s phase304-out/compile/Image
test -s phase304-out/config/final.config
test "$(stat -c '%s' phase304-out/package/boot.img)" -eq 100663296
for f in "$CTRL" "$HW" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do test -s "$f"; done

# Phase304 hardware evidence requires these observers and the original erasure.
grep -Fq 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' "$RSC"
grep -Fq 'A52_PHASE304_EXACT_F05A5A_VISIBILITY_V1' "$CTRL"
grep -Fq 'P276 303 S00p p=%02x%02x%02x' "$CTRL"
grep -Fq 'P276 280Z q=2' "$CTRL"
grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) do{}while(0)' "$COMPAT"

# Lock the exact native 5.10 flush implementation used by the compatibility bridge.
grep -Fq 'int rpmh_flush(struct rpmh_ctrlr *ctrlr)' "$RPMH"
grep -Fq 'lockdep_assert_irqs_disabled();' "$RPMH"
grep -Fq 'if (!spin_trylock(&ctrlr->cache_lock))' "$RPMH"
grep -Fq 'rpmh_rsc_invalidate(ctrlr_to_drv(ctrlr));' "$RPMH"
grep -Fq 'ctrlr->dirty = false;' "$RPMH"

cp phase304-out/config/final.config /tmp/p305-phase304.config
cp "$CTRL" /tmp/p305-ctrl-before.c
cp "$HW" /tmp/p305-hw-before.c
cp "$SDE" /tmp/p305-sde-before.c
cp "$BUS" /tmp/p305-bus-before.c
cp "$RSC" /tmp/p305-rsc-before.c
cp "$RPMH" /tmp/p305-rpmh-before.c
cp "$COMPAT" /tmp/p305-compat-before.h
cp "$REC" /tmp/p305-rec-before.c

python3 -m py_compile scripts/305_apply_display_rpmh_flush_compat.py
python3 scripts/305_apply_display_rpmh_flush_compat.py --root "$ROOT"
python3 scripts/305_apply_display_rpmh_flush_compat.py --root "$ROOT" --check-only

# Flush-only scope: DSI, SDE, bus logic, native rpmh.c and recorder remain exact.
cmp -s /tmp/p305-ctrl-before.c "$CTRL"
cmp -s /tmp/p305-hw-before.c "$HW"
cmp -s /tmp/p305-sde-before.c "$SDE"
cmp -s /tmp/p305-bus-before.c "$BUS"
cmp -s /tmp/p305-rpmh-before.c "$RPMH"
cmp -s /tmp/p305-rec-before.c "$REC"
! cmp -s /tmp/p305-rsc-before.c "$RSC"
! cmp -s /tmp/p305-compat-before.h "$COMPAT"

# Solver remains intentionally erased. Only the legacy display flush stub changes.
grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"
! grep -Fq '#define rpmh_flush(d) do{}while(0)' "$COMPAT"

# Audit the actual intended behavior change and reject unrelated primitives.
python3 - <<'PY'
from pathlib import Path
r0 = Path('/tmp/p305-rsc-before.c').read_text()
r1 = Path('gki/common/drivers/soc/qcom/rpmh-rsc.c').read_text()
c0 = Path('/tmp/p305-compat-before.h').read_text()
c1 = Path('gki/common/a52-port-compat.h').read_text()

expected_rsc_delta = {
    'local_irq_save(': 1,
    'local_irq_restore(': 1,
    'spin_trylock(&drv->lock)': 1,
    'rpmh_rsc_ctrlr_is_busy(drv)': 1,
    'rpmh_flush(&drv->client)': 1,
}
for token, delta in expected_rsc_delta.items():
    got = r1.count(token) - r0.count(token)
    if got != delta:
        raise SystemExit(f'Phase305 intended RSC delta mismatch {token}: {got} != {delta}')

for token in [
    'write_tcs_reg(', 'write_tcs_reg_sync(', 'write_tcs_cmd(',
    '__tcs_buffer_write(', '__tcs_set_trigger(', 'wait_event_lock_irq(',
    'msleep(', 'usleep_range(', 'udelay(',
]:
    if r1.count(token) != r0.count(token):
        raise SystemExit(f'Phase305 unexpected low-level RSC primitive drift: {token}')

if c0.count('#define rpmh_flush(d) do{}while(0)') != 1:
    raise SystemExit('Phase305 baseline flush-erasure count changed')
if c1.count('#define rpmh_flush(d) a52_rpmh_flush_compat((d))') != 1:
    raise SystemExit('Phase305 compatibility macro missing')
if c0.count('#define rpmh_mode_solver_set(d,e) do{}while(0)') != c1.count('#define rpmh_mode_solver_set(d,e) do{}while(0)'):
    raise SystemExit('Phase305 solver-erasure behavior changed unexpectedly')
print('Phase305 flush-only behavior delta audit: PASS')
PY

# Preserve Phase304/Phase296 configuration byte-for-byte.
cp /tmp/p305-phase304.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase305-olddefconfig.log 2>&1
cmp -s /tmp/p305-phase304.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase305-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase305-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

# Require repair evidence plus the exact hardware frontier observers in final Image.
for marker in \
  'P276 305F e' \
  'P276 305F x r=%d l=%u b=%u' \
  'P276 301I s=%u w=%u use=%u' \
  'P276 301C e st=%d ty=%d n=%d slots=%u' \
  'P276 301F would dev=%s irq=%d' \
  'P276 303 S00p p=%02x%02x%02x' \
  'P276 303 S06 st=%x fs=%x ln=%x ck=%x' \
  'P276 303 S07 seen=1 st=%x in=%x irq0=%d' \
  'P276 303 S08 ret=%d irq=%d in=%x st=%x' \
  'P276 280Z q=2'; do
  grep -aFq "$marker" "$IMAGE"
done

# Make sure the new bridge linked into the final kernel.
if [ -f "$BUILD/vmlinux" ]; then
  nm "$BUILD/vmlinux" | grep -Eq ' [Tt] a52_rpmh_flush_compat$'
fi

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase305-compile.log phase305-olddefconfig.log "$OUT/audit/"
cp scripts/305_apply_display_rpmh_flush_compat.py "$OUT/audit/"
for f in /tmp/p305-*; do [ -f "$f" ] && cp "$f" "$OUT/audit/"; done
cp "$CTRL" "$OUT/source/dsi_ctrl.c"
cp "$HW" "$OUT/source/dsi_ctrl_hw_cmn.c"
cp "$SDE" "$OUT/source/sde_rsc.c"
cp "$BUS" "$OUT/source/msm_bus_fabric_rpmh.c"
cp "$RSC" "$OUT/source/rpmh-rsc.c"
cp "$RPMH" "$OUT/source/rpmh.c"
cp "$COMPAT" "$OUT/source/a52-port-compat.h"
cp "$REC" "$OUT/source/a52_ack_secure_flight_recorder.c"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase304-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase305-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
    'phase': '305',
    'name': 'DISPLAY-RPMH-FLUSH-COMPAT-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'base': 'hardware-validated Phase304 observer lineage',
    'hardware_validated': False,
    'functional_change': 'display-scoped legacy rpmh_flush compatibility bridge only',
    'phase304_hardware_capture': 'A52_RAW_RAMOOPS_20260823_134725.zip',
    'phase304_result': [
        'display ACTIVE_ONLY traffic selected borrowed WAKE_TCS and completed successfully',
        'SDE reached the Golden would-flush boundary',
        'no low-level 301I invalidate or 301C control-data programming followed before DSI',
        'exact F0 5A 5A then entered DSI STATUS=3 without DMA_DONE and timed out',
    ],
    'solver_stub_preserved': True,
    'solver_behavior_restored': False,
    'flush_stub_replaced': True,
    'native_rpmh_core_changed': False,
    'dsi_control_flow_changed': False,
    'smmu_or_gem_changed': False,
    'wait_or_timeout_changed': False,
    'repair_contract': 'local IRQ disable -> try drv lock -> reject busy ACTIVE TCS -> native 5.10 rpmh_flush(ctrlr)',
    'hardware_question': 'Does restoring only the missing display rpmh_flush contract make exact F0 5A 5A assert DSI DMA_DONE?',
    'boot_bytes': (r/'package/boot.img').stat().st_size,
    'boot_sha256': hashlib.sha256((r/'package/boot.img').read_bytes()).hexdigest(),
    'image_sha256': hashlib.sha256((r/'compile/Image').read_bytes()).hexdigest(),
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
for key in ('dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    if not identity[key]:
        raise SystemExit('Phase305 repack invariant failed: ' + key)
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [p for p in r.rglob('*') if p.is_file() and p.name != 'SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for p in sorted(files):
        f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
print('Phase305 flush-only repair audit: PASS')
PY

(cd "$OUT" && sha256sum -c SHA256SUMS)
python3 scripts/305_apply_display_rpmh_flush_compat.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase305 display RPMh flush compatibility build/repack: PASS'
