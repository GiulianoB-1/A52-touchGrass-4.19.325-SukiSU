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
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct exact Phase279 first. Phase280 does not add new SMMU reads or
# touch DSI/recorder logic; it only makes Phase278/279 evidence persistable.
bash scripts/279_ci_build.sh
test -s phase279-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p280-base.config
cp "$SMMU" /tmp/p280-smmu-before.c
cp "$DSI" /tmp/p280-dsi-before.c
cp "$REC" /tmp/p280-rec-before.c

python3 -m py_compile scripts/280_apply_phase279_evidence_visible.py scripts/280_check_phase279_evidence_visible.py
python3 scripts/280_apply_phase279_evidence_visible.py --root "$ROOT"
python3 scripts/280_apply_phase279_evidence_visible.py --root "$ROOT" --check-only
python3 scripts/280_check_phase279_evidence_visible.py \
  --before-smmu /tmp/p280-smmu-before.c --after-smmu "$SMMU" \
  --before-dsi /tmp/p280-dsi-before.c --after-dsi "$DSI" \
  --before-recorder /tmp/p280-rec-before.c --after-recorder "$REC"

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
cp phase280-compile.log phase280-out/audit/
cp scripts/280_apply_phase279_evidence_visible.py phase280-out/audit/
cp scripts/280_check_phase279_evidence_visible.py phase280-out/audit/
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
 'name':'PHASE279-EVIDENCE-VISIBLE',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase279 broad display failure cross-section',
 'root_issue':'Phase278/279 SMMU diagnostics used SMMU-prefixed formats rejected by the pre-sequence recorder admission gate',
 'functional_change':'diagnostic record formatting only; no recorder, DSI, SMMU state, mapping, TLB, ATS, fault-clear or control-flow change',
 'recorder_implementation_changed':False,
 'dsi_implementation_changed':False,
 'phase279_measurements_changed':False,
 'record_namespace':'existing critical P276 namespace',
 'hardware_question':'At the proven DSI command-DMA timeout, are command IOVA translation, live/cached context roots, stream/context state and SMMU fault syndromes clean or failing?',
 'interpretation':{
   'mapping':'279I1/279I2 zero or inconsistent => software IOVA mapping defect',
   'root':'279T1/279T2 live differs from cached => context programming/root drift',
   'context_fault':'279F0/279F1 becomes nonzero after kickoff => context-bank translation fault',
   'global_fault':'279G0/279G1 becomes nonzero after kickoff => stream/global SMMU fault',
   'downstream':'mapping/root/fault state clean while P276 H E remains zero => move downstream of SMMU translation toward DSI DMA fetch/coherency/interconnect/controller'
 }
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=['compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json','audit/phase279-final.config','audit/arm-smmu-before.c','audit/dsi-ctrl-before.c','audit/recorder-before.c','audit/280_apply_phase279_evidence_visible.py','audit/280_check_phase279_evidence_visible.py','source/arm-smmu.c','source/dsi_ctrl.c','source/a52_ack_secure_flight_recorder.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY

(cd phase280-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase280-out')
s=(r/'source/arm-smmu.c').read_text()
d=(r/'source/dsi_ctrl.c').read_text()
rec=(r/'source/a52_ack_secure_flight_recorder.c').read_text()
img=(r/'compile/Image').read_bytes()
required=[
 'A52_PHASE280_PHASE279_EVIDENCE_VISIBLE_V1',
 'P276 278C0 q=%u s=%x e=%d c=%u a=%x',
 'P276 278S0 q=%u s=%x z=%x t=%u x=%u',
 'P276 279I0 q=%u s=%x c=%u i=%llx n=%u',
 'P276 279I1 q=%u s=%x e=%llx a=%llx',
 'P276 279I2 q=%u s=%x b=%llx',
 'P276 279T1 q=%u s=%x h=%llx c=%llx',
 'P276 279F0 q=%u s=%x c=%u f=%x y=%x',
 'P276 279F1 q=%u s=%x a=%llx r=%x',
 'P276 279G0 q=%u f=%x a=%x b=%x',
 'P276 279G1 q=%u c=%x',
]
for m in required:
 if m not in s: raise SystemExit('Phase280 source marker missing: '+m)
 if m.startswith('P276 ') and m.encode() not in img: raise SystemExit('Phase280 Image marker missing: '+m)
if 'a52_p279_display_iova_snapshot(0, cmd_mem->offset,' not in d: raise SystemExit('Phase279 DSI call site missing')
if 'strncmp(fmt, "P276", 4)' not in rec: raise SystemExit('P276 admission missing')
if 'return !strncmp(message, "P276 ", 5)' not in rec: raise SystemExit('P276 critical retention missing')
print('Phase280 compiled evidence visibility audit: PASS')
PY

python3 scripts/280_check_phase279_evidence_visible.py \
  --before-smmu /tmp/p280-smmu-before.c --after-smmu "$SMMU" \
  --before-dsi /tmp/p280-dsi-before.c --after-dsi "$DSI" \
  --before-recorder /tmp/p280-rec-before.c --after-recorder "$REC"

trap - EXIT
echo 'Phase280 Phase279 evidence-visible build/repack: PASS'
