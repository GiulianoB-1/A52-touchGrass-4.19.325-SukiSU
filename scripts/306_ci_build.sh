#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase306-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
SDE="$ROOT/drivers/a52_display/msm/sde_rsc.c"
BUS="$ROOT/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
RSC="$ROOT/drivers/soc/qcom/rpmh-rsc.c"
RPMH="$ROOT/drivers/soc/qcom/rpmh.c"
COMPAT="$ROOT/a52-port-compat.h"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
GOLD_RSC="$PWD/workspace/touchgrass-a52xq/drivers/soc/qcom/rpmh-rsc.c"
GOLD_RPMH="$PWD/workspace/touchgrass-a52xq/drivers/soc/qcom/rpmh.c"

fail_report() {
  set +e
  rm -rf phase306-failure
  mkdir -p phase306-failure/{logs,audit,source}
  cp phase306-compile.log phase306-olddefconfig.log phase306-failure/logs/ 2>/dev/null || true
  for f in "$CTRL" "$HW" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do
    [ -f "$f" ] && cp "$f" phase306-failure/source/ || true
  done
  cp /tmp/p306-* phase306-failure/audit/ 2>/dev/null || true
  cp scripts/306_apply_display_solver_compat.py phase306-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase305 candidate first. Hardware subsequently proved
# its flush bridge executes successfully but does not restore DSI DMA_DONE.
bash scripts/305_ci_build.sh

test -s phase305-out/package/boot.img
test -s phase305-out/compile/Image
test -s phase305-out/config/final.config
test "$(stat -c '%s' phase305-out/package/boot.img)" -eq 100663296
for f in "$CTRL" "$HW" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC" "$GOLD_RSC" "$GOLD_RPMH"; do test -s "$f"; done

# Lock the proven Phase305 repair and the still-erased solver contract.
grep -Fq 'A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1' "$RSC"
grep -Fq 'P276 305F e' "$RSC"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"
grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"

# Lock exact Golden semantics used for this compatibility repair:
# - wait for ACTIVE, or borrowed WAKE, TCS to be free before changing ownership
# - reject ACTIVE requests with -EBUSY while solver-owned.
grep -Fq 'void rpmh_rsc_mode_solver_set(struct rsc_drv *drv, bool enable)' "$GOLD_RSC"
grep -Fq 'if (!tcs->num_tcs)' "$GOLD_RSC"
grep -Fq 'tcs = get_tcs_of_type(drv, WAKE_TCS);' "$GOLD_RSC"
grep -Fq 'drv->in_solver_mode = enable;' "$GOLD_RSC"
grep -Fq 'if (ctrlr->in_solver_mode && state == RPMH_ACTIVE_ONLY_STATE)' "$GOLD_RPMH"
grep -Fq 'ret = -EBUSY;' "$GOLD_RPMH"

# The imported Samsung display bus explicitly accepts this solver-owned result.
grep -Fq 'ret && ret != -EBUSY' "$BUS"
grep -Fq 'the display RSC is in solver mode' "$BUS"

cp phase305-out/config/final.config /tmp/p306-phase305.config
cp "$CTRL" /tmp/p306-ctrl-before.c
cp "$HW" /tmp/p306-hw-before.c
cp "$SDE" /tmp/p306-sde-before.c
cp "$BUS" /tmp/p306-bus-before.c
cp "$RSC" /tmp/p306-rsc-before.c
cp "$RPMH" /tmp/p306-rpmh-before.c
cp "$COMPAT" /tmp/p306-compat-before.h
cp "$REC" /tmp/p306-rec-before.c

python3 -m py_compile scripts/306_apply_display_solver_compat.py
python3 scripts/306_apply_display_solver_compat.py --root "$ROOT"
python3 scripts/306_apply_display_solver_compat.py --root "$ROOT" --check-only

# Solver-only delta on top of Phase305. Flush, DSI, SDE, bus, native rpmh.c,
# recorder and config remain unchanged.
cmp -s /tmp/p306-ctrl-before.c "$CTRL"
cmp -s /tmp/p306-hw-before.c "$HW"
cmp -s /tmp/p306-sde-before.c "$SDE"
cmp -s /tmp/p306-bus-before.c "$BUS"
cmp -s /tmp/p306-rpmh-before.c "$RPMH"
cmp -s /tmp/p306-rec-before.c "$REC"
! cmp -s /tmp/p306-rsc-before.c "$RSC"
! cmp -s /tmp/p306-compat-before.h "$COMPAT"

grep -Fxq '#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"
! grep -Fq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"

python3 - <<'PY'
from pathlib import Path
r0 = Path('/tmp/p306-rsc-before.c').read_text()
r1 = Path('gki/common/drivers/soc/qcom/rpmh-rsc.c').read_text()
c0 = Path('/tmp/p306-compat-before.h').read_text()
c1 = Path('gki/common/a52-port-compat.h').read_text()

expected = {
    'rpmh_rsc_ctrlr_is_busy(drv)': 1,
    'local_irq_save(': 1,
    'local_irq_restore(': 2,
    'cpu_relax();': 1,
    'P276 306M e=%u o=%u w=%u r=%d': 1,
    'P276 306G b st=%d ty=%d': 1,
}
for token, delta in expected.items():
    got = r1.count(token) - r0.count(token)
    if got != delta:
        raise SystemExit(f'Phase306 intended RSC delta mismatch {token}: {got} != {delta}')

for token in [
    'write_tcs_reg(', 'write_tcs_reg_sync(', 'write_tcs_cmd(',
    '__tcs_buffer_write(', '__tcs_set_trigger(', 'wait_event_lock_irq(',
    'msleep(', 'usleep_range(', 'udelay(', 'rpmh_flush(&drv->client)',
]:
    if r1.count(token) != r0.count(token):
        raise SystemExit(f'Phase306 unexpected low-level primitive drift: {token}')

if c0.count('#define rpmh_mode_solver_set(d,e) do{}while(0)') != 1:
    raise SystemExit('Phase306 baseline solver-erasure count changed')
if c1.count('#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))') != 1:
    raise SystemExit('Phase306 solver compatibility macro missing')
if c0.count('#define rpmh_flush(d) a52_rpmh_flush_compat((d))') != 1 or c1.count('#define rpmh_flush(d) a52_rpmh_flush_compat((d))') != 1:
    raise SystemExit('Phase306 Phase305 flush bridge drifted')
print('Phase306 solver-only behavior delta audit: PASS')
PY

cp /tmp/p306-phase305.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase306-olddefconfig.log 2>&1
cmp -s /tmp/p306-phase305.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase306-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase306-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 306M e=%u o=%u w=%u r=%d' \
  'P276 306G b st=%d ty=%d' \
  'P276 305F e' \
  'P276 305F x r=%d l=%u b=%u' \
  'P276 301S t=%d r=%d en=1 irq=%d' \
  'P276 301B A r=%d st=%d mb=%s' \
  'P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d' \
  'P276 301C e st=%d ty=%d n=%d slots=%u' \
  'P276 303 S00p p=%02x%02x%02x' \
  'P276 303 S06 st=%x fs=%x ln=%x ck=%x' \
  'P276 303 S07 seen=1 st=%x in=%x irq0=%d' \
  'P276 303 S08 ret=%d irq=%d in=%x st=%x' \
  'P276 280Z q=2'; do
  grep -aFq "$marker" "$IMAGE"
done

if [ -f "$BUILD/vmlinux" ]; then
  nm "$BUILD/vmlinux" | grep -Eq ' [Tt] a52_rpmh_mode_solver_set_compat$'
  nm "$BUILD/vmlinux" | grep -Eq ' [Tt] a52_rpmh_flush_compat$'
fi

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase306-compile.log phase306-olddefconfig.log "$OUT/audit/"
cp scripts/306_apply_display_solver_compat.py "$OUT/audit/"
for f in /tmp/p306-*; do [ -f "$f" ] && cp "$f" "$OUT/audit/"; done
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
  --source phase305-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase306-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
    'phase': '306',
    'name': 'DISPLAY-SOLVER-COMPAT-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'base': 'Phase305 flush-repair lineage plus hardware result A52_RAW_RAMOOPS_20260823_145905.zip',
    'hardware_validated': False,
    'functional_change': 'restore display-only rpmh_mode_solver_set ownership and block ACTIVE sends with -EBUSY while owned',
    'phase305_hardware_result': [
        '305F bridge executed with r=0 l=1 b=0',
        '301I invalidate and WAKE/SLEEP 301C programming appeared',
        'exact F0 5A 5A still produced CLK_STATUS=0x8037C3, LANE=0x1F1F and no DMA_DONE',
        'flush defect is real but insufficient by itself',
    ],
    'phase305_flush_repair_retained': True,
    'native_rpmh_core_changed': False,
    'sde_source_changed': False,
    'msm_bus_source_changed': False,
    'dsi_control_flow_changed': False,
    'smmu_or_gem_changed': False,
    'wait_or_timeout_changed': False,
    'hardware_question': 'Does restoring display solver ownership prevent borrowed WAKE_TCS ACTIVE triggers and restore DSI DMA_DONE for exact F0 5A 5A?',
    'boot_bytes': (r/'package/boot.img').stat().st_size,
    'boot_sha256': hashlib.sha256((r/'package/boot.img').read_bytes()).hexdigest(),
    'image_sha256': hashlib.sha256((r/'compile/Image').read_bytes()).hexdigest(),
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
for key in ('dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    if not identity[key]:
        raise SystemExit('Phase306 repack invariant failed: ' + key)
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [p for p in r.rglob('*') if p.is_file() and p.name != 'SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for p in sorted(files):
        f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
print('Phase306 solver compatibility audit: PASS')
PY

(cd "$OUT" && sha256sum -c SHA256SUMS)
python3 scripts/306_apply_display_solver_compat.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase306 display solver compatibility build/repack: PASS'
