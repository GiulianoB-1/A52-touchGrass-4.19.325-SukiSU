#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase296-failure
  mkdir -p phase296-failure/{source,logs,audit}
  cp phase296-compile.log phase296-failure/logs/ 2>/dev/null || true
  for f in "$DRV" "$ATOMIC" "$KMS" "$REC"; do
    [ -f "$f" ] && cp "$f" phase296-failure/source/ || true
  done
  cp scripts/296_apply_userspace_drm_atomic_frontier.py phase296-failure/audit/ 2>/dev/null || true
  cp /tmp/p296-base.config phase296-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase293 reference lineage. Phase296 deliberately does
# not inherit the disproven Phase295 ENODATA interpretation or its extra probes.
bash scripts/293_ci_build.sh
test -s phase293-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DRV" "$ATOMIC" "$KMS" "$REC"; do test -s "$f"; done

test "$(stat -c '%s' phase293-out/package/boot.img)" -eq 100663296
cp "$OUT/.config" /tmp/p296-base.config
cp "$DRV" /tmp/p296-msm-drv-before.c
cp "$ATOMIC" /tmp/p296-msm-atomic-before.c
cp "$KMS" /tmp/p296-sde-kms-before.c
cp "$REC" /tmp/p296-rec-before.c

# Hardware capture proved the normal DSI probe/bind succeeds. Also lock down
# the exact build fact that eliminates fbdev as the source of a first modeset.
grep -Fxq '# CONFIG_DRM_FBDEV_EMULATION is not set' /tmp/p296-base.config
grep -Fxq '# CONFIG_FB is not set' /tmp/p296-base.config

python3 -m py_compile scripts/296_apply_userspace_drm_atomic_frontier.py
python3 scripts/296_apply_userspace_drm_atomic_frontier.py \
  --root "$ROOT" --report phase296-patch-report.json
python3 scripts/296_apply_userspace_drm_atomic_frontier.py \
  --root "$ROOT" --check-only

# The retained recorder transport itself must not change. Phase296 reuses the
# already-admitted P276 critical prefix so short-tail captures remain useful.
cmp -s /tmp/p296-rec-before.c "$REC"

# Preserve the exact Phase293 kernel configuration.
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p296-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase296-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase296-out
mkdir -p phase296-out/{compile,config,package,audit,source}
cp "$IMAGE" phase296-out/compile/Image
cp "$OUT/.config" phase296-out/config/final.config
cp /tmp/p296-base.config phase296-out/audit/phase293-final.config
cp /tmp/p296-msm-drv-before.c phase296-out/audit/msm-drv-before.c
cp /tmp/p296-msm-atomic-before.c phase296-out/audit/msm-atomic-before.c
cp /tmp/p296-sde-kms-before.c phase296-out/audit/sde-kms-before.c
cp /tmp/p296-rec-before.c phase296-out/audit/recorder-before.c
cp phase296-compile.log phase296-out/audit/
cp phase296-patch-report.json phase296-out/audit/
cp scripts/296_apply_userspace_drm_atomic_frontier.py phase296-out/audit/
cp "$DRV" phase296-out/source/msm_drv.c
cp "$ATOMIC" phase296-out/source/msm_atomic.c
cp "$KMS" phase296-out/source/sde_kms.c
cp "$REC" phase296-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase296-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase293-out/package/boot.img \
  --kernel phase296-out/package/Image.gz \
  --output phase296-out/package/boot.img \
  --report phase296-out/package/repack-report.json

test "$(stat -c '%s' phase296-out/package/boot.img)" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase296-out')
idn={
 'phase':'296',
 'name':'USERSPACE-DRM-ATOMIC-FRONTIER',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase293 reconstruction from Phase280 retained-timeout lineage',
 'phase295_correction':'hardware ramoops proved dsi_display_dev_probe, dsi_panel_get and DRM/KMS bind succeed; later -61 was diagnostic data, not probe return',
 'fbdev_runtime_source':False,
 'fbdev_evidence':['# CONFIG_DRM_FBDEV_EMULATION is not set','# CONFIG_FB is not set'],
 'functional_change':'instrumentation-only',
 'recorder_change':False,
 'recorder_transport':'inherited P276 critical-after-capacity R48/RS48 path',
 'markers':{
   '296R':'drm_dev_register return',
   '296O':'msm DRM file open entry/exit; recorder metadata carries pid/tgid/comm',
   '296A':'driver atomic_check entry/return',
   '296C':'msm_atomic_commit entry/return/error stage',
   '296W':'complete_commit worker/synchronous completion entry',
   '296K':'SDE prepare_commit / commit / complete_commit entry'
 },
 'interpretation':{
   'R0_no_O':'DRM device registered but Android userspace never opens this DRM device before capture ends',
   'O_no_A':'userspace opens DRM but never reaches an atomic validation request; inspect capability/resource/ioctl/userspace admission',
   'A_error':'atomic validation is reached and rejected; next phase must isolate exact KMS atomic_check rejection',
   'A0_no_C':'validation succeeds but no commit is submitted',
   'C_no_W':'atomic commit entered but completion dispatch/worker is not reached',
   'W_no_K':'completion began but KMS prepare_commit callback is not reached',
   'K':'normal SDE commit path exists; move frontier downstream toward bridge/DSI enable and target F0 5A 5A'
 },
 'hardware_question':'After successful DRM/KMS bind, does Android userspace open the DRM card and submit a valid atomic commit, and what is the last persistent boundary reached?'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[
 'compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json',
 'audit/phase293-final.config','audit/msm-drv-before.c','audit/msm-atomic-before.c','audit/sde-kms-before.c',
 'audit/recorder-before.c','audit/phase296-compile.log','audit/phase296-patch-report.json',
 'audit/296_apply_userspace_drm_atomic_frontier.py','source/msm_drv.c','source/msm_atomic.c','source/sde_kms.c',
 'source/a52_ack_secure_flight_recorder.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files:
  f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase296-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase296-out')
img=(r/'compile/Image').read_bytes()
d=(r/'source/msm_drv.c').read_text()
a=(r/'source/msm_atomic.c').read_text()
k=(r/'source/sde_kms.c').read_text()
markers=[
 'P276 296R r=%d','P276 296O e','P276 296O x r=%d',
 'P276 296A e','P276 296A x r=%d','P276 296C e n=%d',
 'P276 296C x r=%d q=1','P276 296C x r=0 q=0','P276 296C x r=%d q=2',
 'P276 296W e','P276 296K p','P276 296K c','P276 296K x']
for m in markers:
 if m not in (d+a+k): raise SystemExit('Phase296 source marker missing: '+m)
 if m.encode() not in img: raise SystemExit('Phase296 runtime marker missing from Image: '+m)
if (r/'source/a52_ack_secure_flight_recorder.c').read_bytes() != (r/'audit/recorder-before.c').read_bytes():
 raise SystemExit('Phase296 recorder changed unexpectedly')
print('Phase296 passive userspace DRM atomic frontier audit: PASS')
PY

python3 scripts/296_apply_userspace_drm_atomic_frontier.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase296 userspace DRM atomic frontier build/repack: PASS'
