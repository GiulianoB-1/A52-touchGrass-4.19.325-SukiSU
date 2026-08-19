#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase280-failure
  mkdir -p phase280-failure/{source,logs,audit}
  cp phase280-compile.log phase280-failure/logs/ 2>/dev/null || true
  cp "$SMMU" phase280-failure/source/arm-smmu.c 2>/dev/null || true
  cp "$DSI" phase280-failure/source/dsi_ctrl.c 2>/dev/null || true
  cp "$REC" phase280-failure/source/a52_ack_secure_flight_recorder.c 2>/dev/null || true
  cp scripts/280_apply_phase279_evidence_visible.py phase280-failure/audit/ 2>/dev/null || true
  cp scripts/280_check_phase279_evidence_visible.py phase280-failure/audit/ 2>/dev/null || true
  cp scripts/280_apply_timeout_retention_latch.py phase280-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct exact Phase279. Phase280 keeps the same SMMU/DSI hardware
# question. The first patch makes Phase278/279 records admissible; the second
# stops only diagnostic persistence after the complete timeout snapshot so
# later graphics traffic cannot overwrite the evidence.
bash scripts/279_ci_build.sh
test -s phase279-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p280-base.config
cp "$SMMU" /tmp/p280-smmu-before.c
cp "$DSI" /tmp/p280-dsi-before.c
cp "$REC" /tmp/p280-rec-before.c

python3 -m py_compile \
  scripts/280_apply_phase279_evidence_visible.py \
  scripts/280_check_phase279_evidence_visible.py \
  scripts/280_apply_timeout_retention_latch.py

python3 scripts/280_apply_phase279_evidence_visible.py --root "$ROOT"
python3 scripts/280_apply_phase279_evidence_visible.py --root "$ROOT" --check-only
python3 scripts/280_check_phase279_evidence_visible.py \
  --before-smmu /tmp/p280-smmu-before.c --after-smmu "$SMMU" \
  --before-dsi /tmp/p280-dsi-before.c --after-dsi "$DSI" \
  --before-recorder /tmp/p280-rec-before.c --after-recorder "$REC"

# Preserve the evidence-visible state for the original non-perturbation audit.
cp "$DSI" /tmp/p280-dsi-visible.c
cp "$REC" /tmp/p280-rec-visible.c
python3 scripts/280_apply_timeout_retention_latch.py --root "$ROOT"
python3 scripts/280_apply_timeout_retention_latch.py --root "$ROOT" --check-only

grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$DSI"
grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$REC"
grep -Fq 'P276 280Z q=2' "$DSI"
grep -Fq 'a52_ackfr_retain_timeout_snapshot();' "$DSI"
grep -Fq 'atomic_set(&a52_r280_retained, 1);' "$REC"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p280-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase280-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase280-out
mkdir -p phase280-out/{compile,config,package,audit,source}
cp "$IMAGE" phase280-out/compile/Image
cp "$OUT/.config" phase280-out/config/final.config
cp /tmp/p280-base.config phase280-out/audit/phase279-final.config
cp /tmp/p280-smmu-before.c phase280-out/audit/arm-smmu-before.c
cp /tmp/p280-dsi-before.c phase280-out/audit/dsi-ctrl-before.c
cp /tmp/p280-rec-before.c phase280-out/audit/recorder-before.c
cp /tmp/p280-dsi-visible.c phase280-out/audit/dsi-ctrl-evidence-visible-before-retention.c
cp /tmp/p280-rec-visible.c phase280-out/audit/recorder-evidence-visible-before-retention.c
cp phase280-compile.log phase280-out/audit/
cp scripts/280_apply_phase279_evidence_visible.py phase280-out/audit/
cp scripts/280_check_phase279_evidence_visible.py phase280-out/audit/
cp scripts/280_apply_timeout_retention_latch.py phase280-out/audit/
cp "$SMMU" phase280-out/source/arm-smmu.c
cp "$DSI" phase280-out/source/dsi_ctrl.c
cp "$REC" phase280-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase280-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase279-out/package/boot.img \
  --kernel phase280-out/package/Image.gz \
  --output phase280-out/package/boot.img \
  --report phase280-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase280-out')
idn={
 'phase':'280',
 'name':'PHASE279-EVIDENCE-VISIBLE-RETAINED',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase279 broad display failure cross-section',
 'hardware_evidence_20260819':'framework reached SurfaceFlinger/system_server; late critical traffic filled R48 and overwrote the earlier DSI/SMMU window',
 'functional_change':'diagnostic-only: Phase279 record formatting plus persistent-recorder retention latch after complete DSI timeout snapshot',
 'display_control_flow_changed':False,
 'smmu_state_changed':False,
 'mapping_or_tlb_changed':False,
 'timeout_recovery_changed':False,
 'recorder_persistence_stops_after':'point2 SMMU snapshots + DSI interrupt status + DSI error status + P276 280Z',
 'hardware_question':'At the proven DSI command-DMA timeout, are command IOVA translation, live/cached context roots, stream/context state and SMMU fault syndromes clean or failing?',
 'interpretation':{
   'mapping':'279I1/279I2 zero or inconsistent => software IOVA mapping defect',
   'root':'279T1/279T2 live differs from cached => context programming/root drift',
   'context_fault':'279F0/279F1 becomes nonzero after kickoff => context-bank translation fault',
   'global_fault':'279G0/279G1 becomes nonzero after kickoff => stream/global SMMU fault',
   'downstream':'mapping/root/fault state clean while P276 H E/DMA_DONE remain non-completing => move downstream of SMMU translation toward DSI DMA fetch/coherency/interconnect/controller'
 }
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[
 'compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json',
 'audit/phase279-final.config','audit/arm-smmu-before.c','audit/dsi-ctrl-before.c','audit/recorder-before.c',
 'audit/dsi-ctrl-evidence-visible-before-retention.c','audit/recorder-evidence-visible-before-retention.c',
 'audit/280_apply_phase279_evidence_visible.py','audit/280_check_phase279_evidence_visible.py',
 'audit/280_apply_timeout_retention_latch.py','source/arm-smmu.c','source/dsi_ctrl.c',
 'source/a52_ack_secure_flight_recorder.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase280-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase280-out')
s=(r/'source/arm-smmu.c').read_text(); d=(r/'source/dsi_ctrl.c').read_text(); rec=(r/'source/a52_ack_secure_flight_recorder.c').read_text(); img=(r/'compile/Image').read_bytes()
for m in [
 'P276 278C0 q=%u s=%x e=%d c=%u a=%x','P276 279I0 q=%u s=%x c=%u i=%llx n=%u',
 'P276 279T1 q=%u s=%x h=%llx c=%llx','P276 279F0 q=%u s=%x c=%u f=%x y=%x',
 'P276 279G0 q=%u f=%x a=%x b=%x']:
 if m not in s or m.encode() not in img: raise SystemExit('Phase280 evidence marker missing: '+m)
for m in ['A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1','P276 280Z q=2','a52_ackfr_retain_timeout_snapshot']:
 if m not in d+rec: raise SystemExit('Phase280 retention source marker missing: '+m)
# Only runtime string literals are required to survive as raw Image bytes.
# C comments are compiled out, and symbol names may be kallsyms-compressed.
if b'P276 280Z q=2' not in img:
 raise SystemExit('Phase280 retention runtime marker missing from Image: P276 280Z q=2')
print('Phase280 compiled evidence + retention audit: PASS')
PY

# Re-run both source-level audits after compilation. The original visibility
# audit compares against the saved pre-retention DSI/recorder state; the latch
# audit validates the actual compiled source and strict point-2 ordering.
python3 scripts/280_check_phase279_evidence_visible.py \
  --before-smmu /tmp/p280-smmu-before.c --after-smmu "$SMMU" \
  --before-dsi /tmp/p280-dsi-before.c --after-dsi /tmp/p280-dsi-visible.c \
  --before-recorder /tmp/p280-rec-before.c --after-recorder /tmp/p280-rec-visible.c
python3 scripts/280_apply_timeout_retention_latch.py --root "$ROOT" --check-only

trap - EXIT
echo 'Phase280 evidence-visible retained-timeout build/repack: PASS'
