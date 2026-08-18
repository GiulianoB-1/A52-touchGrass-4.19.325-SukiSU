#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"

fail_report(){
  set +e
  rm -rf phase279-failure
  mkdir -p phase279-failure/source phase279-failure/logs phase279-failure/audit
  cp phase279-compile.log phase279-failure/logs/ 2>/dev/null || true
  cp "$SMMU" phase279-failure/source/arm-smmu.c 2>/dev/null || true
  cp "$DSI" phase279-failure/source/dsi_ctrl.c 2>/dev/null || true
  cp scripts/279_apply_live_dsi_iova_translation.py phase279-failure/audit/ 2>/dev/null || true
  cp scripts/279_check_live_dsi_iova_translation.py phase279-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase278 live-SMMU recorder first. Phase279 broadens the
# same proven DSI DMA boundary without changing translation or DSI control flow.
bash scripts/278_ci_build.sh
test -s phase278-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p279-base.config
cp "$SMMU" /tmp/p279-arm-smmu-before.c
cp "$DSI" /tmp/p279-dsi-ctrl-before.c

python3 -m py_compile \
  scripts/279_apply_live_dsi_iova_translation.py \
  scripts/279_check_live_dsi_iova_translation.py
python3 scripts/279_apply_live_dsi_iova_translation.py --root "$ROOT" --self-test
python3 scripts/279_apply_live_dsi_iova_translation.py --root "$ROOT"
python3 scripts/279_check_live_dsi_iova_translation.py --root "$ROOT"

! cmp -s /tmp/p279-arm-smmu-before.c "$SMMU"
! cmp -s /tmp/p279-dsi-ctrl-before.c "$DSI"
grep -Fq 'A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1' "$SMMU"
grep -Fq 'A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1' "$DSI"
grep -Fq 'ops->iova_to_phys(ops, iova)' "$SMMU"
grep -Fq 'ops->iova_to_phys(ops, end)' "$SMMU"
grep -Fq 'ARM_SMMU_CB_FSYNR0' "$SMMU"
grep -Fq 'ARM_SMMU_GR0_sGFSR' "$SMMU"
grep -Fq 'SMMU P279 I p=%u sid=%x cb=%u i=%llx l=%u e=%llx p0=%llx p1=%llx' "$SMMU"
grep -Fq 'SMMU P279 F p=%u sid=%x cb=%u fs=%x sy=%x far=%llx cfr=%x' "$SMMU"
grep -Fq 'SMMU P279 G p=%u gf=%x g0=%x g1=%x g2=%x' "$SMMU"
grep -Fq 'a52_p279_display_iova_snapshot(0, cmd_mem->offset,' "$DSI"
grep -Fq 'a52_p279_display_fault_snapshot(2);' "$DSI"

# No config, mapping, TLB, stream-routing, ACTLR or DSI functional change is
# allowed. The checker additionally rejects ATS1PR and SMMU write primitives.
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p279-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase279-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase279-out
mkdir -p phase279-out/{compile,config,package,audit,source}
cp "$IMAGE" phase279-out/compile/Image
cp "$OUT/.config" phase279-out/config/final.config
cp /tmp/p279-base.config phase279-out/audit/phase278-final.config
cp /tmp/p279-arm-smmu-before.c phase279-out/audit/arm-smmu-before.c
cp /tmp/p279-dsi-ctrl-before.c phase279-out/audit/dsi-ctrl-before.c
cp phase279-compile.log phase279-out/audit/
cp scripts/279_apply_live_dsi_iova_translation.py phase279-out/audit/
cp scripts/279_check_live_dsi_iova_translation.py phase279-out/audit/
cp "$SMMU" phase279-out/source/arm-smmu.c
cp "$DSI" phase279-out/source/dsi_ctrl.c

gzip -n -c "$IMAGE" > phase279-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase278-out/package/boot.img \
  --kernel phase279-out/package/Image.gz \
  --output phase279-out/package/boot.img \
  --report phase279-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase279-out')
idn = {
    'phase': '279',
    'name': 'BROAD-DISPLAY-FAILURE-CROSS-SECTION',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'Phase278 live SMMU state + Phase277 ACTLR + Phase276R DMA recorder V5',
    'functional_change': 'none; diagnostic-only broad read-only cross-section',
    'translation_state_changed': False,
    'hardware_translation_request_issued': False,
    'fault_status_cleared': False,
    'tbu_driver_added': False,
    'snapshot_points': {
        '0': 'pre-kickoff: live Phase278 state + software IOVA translation/root + SMMU faults',
        '1': 'post-kickoff: live Phase278 state + SMMU faults',
        '2': 'DMA timeout: live Phase278 state + SMMU faults before DSI status handling',
    },
    'hardware_question': (
        'For the exact DSI command DMA that times out with DMA_DONE=0, correlate in '
        'one boot: software first/last-byte IOVA translation, cached versus live '
        'TTBR0/TCR, context fault FSR/FSYNR0/FAR/CBFRSYNRA, global fault '
        'sGFSR/sGFSYNR0/1/2, existing live ACTLR/SCTLR/S2CR/SMR/CBAR, and existing '
        'DSI DMA/controller/clock/error readback.'
    ),
    'interpretation': {
        'mapping': 'p0/p1 zero or inconsistent => software mapping defect',
        'root': 'live TTBR0/TCR differs from cached => context programming/root drift',
        'context_fault': 'new FSR/FSYNR0/FAR after kickoff => translating CB rejected/faulted DMA',
        'global_fault': 'new sGFSR/sGFSYNR values => stream/global SMMU fault path',
        'downstream': 'mapping/root/fault state clean while DMA_DONE stays zero => move beyond software SMMU mapping/root into fetch/coherency/interconnect/controller path',
    },
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase278-final.config',
    'audit/arm-smmu-before.c', 'audit/dsi-ctrl-before.c',
    'audit/279_apply_live_dsi_iova_translation.py',
    'audit/279_check_live_dsi_iova_translation.py',
    'source/arm-smmu.c', 'source/dsi_ctrl.c',
]
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY

(cd phase279-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r = Path('phase279-out')
smmu = (r/'source/arm-smmu.c').read_text()
dsi = (r/'source/dsi_ctrl.c').read_text()
img = (r/'compile/Image').read_bytes()
required_source = [
    'A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1',
    'ops->iova_to_phys(ops, iova)',
    'ops->iova_to_phys(ops, end)',
    'ARM_SMMU_CB_FSYNR0', 'ARM_SMMU_CB_FAR', 'ARM_SMMU_GR0_sGFSR',
    'a52_p279_display_iova_snapshot(0, cmd_mem->offset,',
    'a52_p279_display_fault_snapshot(0);',
    'a52_p279_display_fault_snapshot(1);',
    'a52_p279_display_fault_snapshot(2);',
    'a52_p278_display_smmu_snapshot(0);',
    'a52_p278_display_smmu_snapshot(1);',
    'a52_p278_display_smmu_snapshot(2);',
]
for marker in required_source:
    if marker not in smmu + dsi:
        raise SystemExit('Phase279 source marker missing: ' + marker)
runtime = [
    'SMMU P279 I p=%u sid=%x cb=%u i=%llx l=%u e=%llx p0=%llx p1=%llx',
    'SMMU P279 T p=%u sid=%x r=%d ht=%llx ct=%llx hr=%x cr=%x',
    'SMMU P279 F p=%u sid=%x cb=%u fs=%x sy=%x far=%llx cfr=%x',
    'SMMU P279 G p=%u gf=%x g0=%x g1=%x g2=%x',
    'SMMU P278 C p=%u sid=%x sme=%d cb=%u a=%x sc=%x m=%u f=%x',
    'P276 H K o=%llx l=%u h=%x',
    'P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x',
]
for marker in runtime:
    if marker.encode() not in img:
        raise SystemExit('Phase279 Image runtime marker missing: ' + marker)
print('Phase279 broad display failure snapshot Image audit: PASS')
PY

python3 scripts/279_check_live_dsi_iova_translation.py --root "$ROOT"

trap - EXIT
echo 'Phase279 broad display failure cross-section build/repack: PASS'
