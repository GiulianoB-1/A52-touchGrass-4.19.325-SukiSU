#!/usr/bin/env bash
set -Eeuo pipefail
export BUILD_CROSS_COMPILE="${BUILD_CROSS_COMPILE:-1}"
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"

fail_report(){
  set +e
  rm -rf phase277-failure
  mkdir -p phase277-failure/source phase277-failure/logs phase277-failure/audit
  cp phase277-compile.log phase277-failure/logs/ 2>/dev/null || true
  cp "$SMMU" phase277-failure/source/arm-smmu.c 2>/dev/null || true
  cp scripts/277_apply_qsmmuv500_actlr_parity.py phase277-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase276R source and candidate first.
bash scripts/276r_ci_build.sh
test -s phase276r-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p277-base.config
cp "$SMMU" /tmp/p277-arm-smmu-before.c

# Add only the missing Golden QSMMUv500 display ACTLR contract.
python3 -m py_compile scripts/277_apply_qsmmuv500_actlr_parity.py
python3 scripts/277_apply_qsmmuv500_actlr_parity.py --root "$ROOT" --self-test
python3 scripts/277_apply_qsmmuv500_actlr_parity.py --root "$ROOT"

! cmp -s /tmp/p277-arm-smmu-before.c "$SMMU"
grep -Fq 'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1' "$SMMU"
grep -Fq 'sid != 0x800 && sid != 0x801' "$SMMU"
grep -Fq 'ARM_SMMU_CB_ACTLR, chosen_actlr' "$SMMU"
grep -Fq 'smmu_domain->flush_ops->tlb_flush_all(smmu_domain);' "$SMMU"
grep -Fq 'SMMU P277 ACTLR sid=%x cb=%u actlr=%x before=%x after=%x tlb=1' "$SMMU"

# Phase277 must not change the kernel configuration.
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p277-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase277-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase277-out
mkdir -p phase277-out/{compile,config,package,audit,source}
cp "$IMAGE" phase277-out/compile/Image
cp "$OUT/.config" phase277-out/config/final.config
cp /tmp/p277-base.config phase277-out/audit/phase276r-final.config
cp /tmp/p277-arm-smmu-before.c phase277-out/audit/arm-smmu-before.c
cp phase277-compile.log phase277-out/audit/
cp scripts/277_apply_qsmmuv500_actlr_parity.py phase277-out/audit/
cp "$SMMU" phase277-out/source/arm-smmu.c

gzip -n -c "$IMAGE" > phase277-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase276r-out/package/boot.img \
  --kernel phase277-out/package/Image.gz \
  --output phase277-out/package/boot.img \
  --report phase277-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase277-out')
idn = {
    'phase': '277',
    'name': 'QSMMUV500-DISPLAY-ACTLR-PARITY',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'Phase276R deep DSI root-cause recorder V5',
    'golden_contract': 'qcom,actlr SID/mask match -> CB_ACTLR write -> full domain TLB flush',
    'display_sids': ['0x800', '0x801'],
    'expected_display_actlr': '0x103',
    's2cr_changed': False,
    'tbu_driver_added': False,
    'runtime_success': 'DSI DMA_DONE becomes nonzero after kickoff',
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase276r-final.config',
    'audit/arm-smmu-before.c', 'audit/277_apply_qsmmuv500_actlr_parity.py',
    'source/arm-smmu.c',
]
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY

(cd phase277-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r = Path('phase277-out')
s = (r/'source/arm-smmu.c').read_text()
img = (r/'compile/Image').read_bytes()
source_markers = [
    'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1',
    'of_property_count_u32_elems(np, "qcom,actlr")',
    'ARM_SMMU_CB_ACTLR, chosen_actlr',
    'smmu_domain->flush_ops->tlb_flush_all(smmu_domain);',
]
runtime = 'SMMU P277 ACTLR sid=%x cb=%u actlr=%x before=%x after=%x tlb=1'
for marker in source_markers:
    if marker not in s:
        raise SystemExit('Phase277 source marker missing: ' + marker)
if runtime not in s:
    raise SystemExit('Phase277 runtime marker missing from source')
if runtime.encode() not in img:
    raise SystemExit('Phase277 runtime marker missing from Image')
print('Phase277 QSMMUv500 display ACTLR Image audit: PASS')
PY

trap - EXIT
echo 'Phase277 QSMMUv500 display ACTLR parity build/repack: PASS'
