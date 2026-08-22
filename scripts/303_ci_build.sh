#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report() {
  set +e
  rm -rf phase303-failure
  mkdir -p phase303-failure/{source,logs,audit}
  cp phase303-compile.log phase303-failure/logs/ 2>/dev/null || true
  for f in "$CTRL" "$HW" "$REC"; do [ -f "$f" ] && cp "$f" phase303-failure/source/ || true; done
  cp scripts/303_apply_gdm_visible_exact_f05a5a.py phase303-failure/audit/ 2>/dev/null || true
  cp /tmp/p303-base.config /tmp/p303-ctrl-before.c /tmp/p303-hw-before.c /tmp/p303-rec-before.c phase303-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-proven Phase296 lineage. Phase303 deliberately
# drops the now-obsolete Phase302 odsign wait-channel observer: this boot proved
# Android reaches composer and a real atomic commit. Keep the Phase280 timeout
# retention latch and Phase293 passive command-DMA reference intact.
bash scripts/296_ci_build.sh
test -s phase296-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$CTRL" "$HW" "$REC"; do test -s "$f"; done
test "$(stat -c '%s' phase296-out/package/boot.img)" -eq 100663296

grep -Fq 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1' "$CTRL"
grep -Fq 'A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1' "$HW"
grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$CTRL"
grep -Fq 'P276 280Z q=2' "$CTRL"

cp "$OUT/.config" /tmp/p303-base.config
cp "$CTRL" /tmp/p303-ctrl-before.c
cp "$HW" /tmp/p303-hw-before.c
cp "$REC" /tmp/p303-rec-before.c

python3 -m py_compile scripts/303_apply_gdm_visible_exact_f05a5a.py
python3 scripts/303_apply_gdm_visible_exact_f05a5a.py --root "$ROOT"
python3 scripts/303_apply_gdm_visible_exact_f05a5a.py --root "$ROOT" --check-only

# Phase303 changes only diagnostic trace selection/formatting in the existing
# Phase293 DSI reference. Recorder transport/admission and all other subsystems
# stay byte-for-byte unchanged.
cmp -s /tmp/p303-rec-before.c "$REC"
! cmp -s /tmp/p303-ctrl-before.c "$CTRL"
! cmp -s /tmp/p303-hw-before.c "$HW"

# No behavior-changing DSI primitives may be added by the patch. Counts of the
# relevant write/wait/clock/recovery primitives must be identical pre/post.
python3 - <<'PY'
from pathlib import Path
pairs=[('/tmp/p303-ctrl-before.c', Path('gki/common/drivers/a52_display/msm/dsi/dsi_ctrl.c')),
       ('/tmp/p303-hw-before.c', Path('gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c'))]
tokens=['DSI_W32(', 'writel(', 'writel_relaxed(', 'clk_set_rate(',
        'wait_for_completion_timeout(', 'msleep(', 'usleep_range(',
        'DSI_CTRL_CMD_FIFO_STORE']
for before, after in pairs:
    b=Path(before).read_text(); a=after.read_text()
    for t in tokens:
        if b.count(t) != a.count(t):
            raise SystemExit(f'Phase303 behavior primitive count changed: {after} {t} {b.count(t)}->{a.count(t)}')
print('Phase303 behavior primitive count audit: PASS')
PY

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p303-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase303-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 303 S00 c=0 in=%x mf=%x t=%x l=%u' \
  'P276 303 S00p p=%02x%02x%02x' \
  'P276 303 S03 irq=%d in=%x st=%x' \
  'P276 303 S04 irq=%d in=%x st=%x' \
  'P276 303 S05 dc=%x off=%x len=%x fc=%x' \
  'P276 303 S06 st=%x fs=%x ln=%x ck=%x' \
  'P276 303 S07 seen=1 st=%x in=%x irq0=%d' \
  'P276 303 S08 ret=%d irq=%d in=%x st=%x' \
  'P276 303 S09 st=%x fs=%x ln=%x ck=%x' \
  'P276 303 DONE success=0 target=0/8/20/29/3' \
  'P276 280Z q=2'; do
  grep -aFq "$marker" "$IMAGE"
done

grep -Fq 'p[0] != 0xF0 || p[1] != 0x5A || p[2] != 0x5A' "$CTRL"
! grep -Fq 'a52_ackfr_record("GDM ' "$CTRL"
! grep -Fq 'a52_ackfr_record("GDM ' "$HW"

rm -rf phase303-out
mkdir -p phase303-out/{compile,config,package,audit,source}
cp "$IMAGE" phase303-out/compile/Image
cp "$OUT/.config" phase303-out/config/final.config
cp /tmp/p303-base.config phase303-out/audit/phase296-final.config
cp /tmp/p303-ctrl-before.c phase303-out/audit/dsi-ctrl-before.c
cp /tmp/p303-hw-before.c phase303-out/audit/dsi-ctrl-hw-before.c
cp /tmp/p303-rec-before.c phase303-out/audit/recorder-before.c
cp phase303-compile.log phase303-out/audit/
cp scripts/303_apply_gdm_visible_exact_f05a5a.py phase303-out/audit/
cp "$CTRL" phase303-out/source/dsi_ctrl.c
cp "$HW" phase303-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase303-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase303-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase296-out/package/boot.img \
  --kernel phase303-out/package/Image.gz \
  --output phase303-out/package/boot.img \
  --report phase303-out/package/repack-report.json

test "$(stat -c '%s' phase303-out/package/boot.img)" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase303-out')
idn={
  'phase':'303',
  'name':'GDM-VISIBLE-EXACT-F05A5A-V1',
  'git_sha':os.getenv('GITHUB_SHA'),
  'hardware_validated':False,
  'base':'exact Phase296 reconstruction (Phase293 retained-timeout DSI lineage + userspace DRM frontier)',
  'phase302_hardware_capture':'A52_RAW_RAMOOPS_20260823_004829.zip',
  'phase302_hardware_result':[
    'Android progressed through system_server/composer to DRM admission and a real atomic commit',
    'TX_LEVEL1_KEY_ENABLE reached MIPI host transfer',
    'memory-fetch command DMA waited about 200 ms and completion value remained zero',
    'Phase280 q=2 retention latch froze the recorder immediately after the timeout snapshot',
    'SMMU state remained unchanged from pre-kickoff through timeout and global fault registers stayed zero',
    'Phase293 GDM strings were not persistent-visible because they used the non-admitted GDM prefix'
  ],
  'functional_change':'diagnostic-only: exact F0 5A 5A trace arm plus persistent-visible formatting',
  'recorder_change':False,
  'dsi_control_flow_change':False,
  'wait_or_timeout_change':False,
  'fifo_reroute':False,
  'clock_recovery':False,
  'rpmh_change':False,
  'target':{'ctrl':0,'flags':0x20,'msg_flags':0x8,'type':0x29,'tx_len':3,'payload':'F05A5A'},
  'markers':{
    '303 S00/S00p':'target identity and payload',
    '303 S03/S04':'pre/post DMA_DONE IRQ arm raw interrupt/controller state',
    '303 S05':'programmed DMA registers immediately before production trigger',
    '303 S06':'immediately after production SW trigger',
    '303 S07':'DMA_DONE ISR only if hardware asserts completion',
    '303 S08':'completion wait result plus raw interrupt/status',
    '303 S09':'timeout-final FIFO/lane/clock/error/controller state before Phase280 retention latch'
  },
  'hardware_question':'For the exact Samsung F0 5A 5A level-1 key command, what raw DSI register/IRQ state first diverges between kickoff and the missing DMA_DONE completion?'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=['compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json',
       'audit/phase296-final.config','audit/dsi-ctrl-before.c','audit/dsi-ctrl-hw-before.c','audit/recorder-before.c',
       'audit/phase303-compile.log','audit/303_apply_gdm_visible_exact_f05a5a.py',
       'source/dsi_ctrl.c','source/dsi_ctrl_hw_cmn.c','source/a52_ack_secure_flight_recorder.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase303-out && sha256sum -c SHA256SUMS)
python3 scripts/303_apply_gdm_visible_exact_f05a5a.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase303 exact F0 5A 5A persistent GDM reference build/repack: PASS'
