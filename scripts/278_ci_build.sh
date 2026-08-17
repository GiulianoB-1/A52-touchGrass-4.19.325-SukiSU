#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"

fail_report(){
  set +e
  rm -rf phase278-failure
  mkdir -p phase278-failure/source phase278-failure/logs phase278-failure/audit
  cp phase278-compile.log phase278-failure/logs/ 2>/dev/null || true
  cp "$SMMU" phase278-failure/source/arm-smmu.c 2>/dev/null || true
  cp scripts/278_apply_qsmmuv500_tbu_children_parity.py phase278-failure/audit/ 2>/dev/null || true
  cp scripts/278_check_qsmmuv500_tbu_children_parity.py phase278-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

bash scripts/277_ci_build.sh
test -s phase277-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p278-base.config
cp "$SMMU" /tmp/p278-arm-smmu-before.c

grep -Fq 'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1' "$SMMU"
grep -Fq 'a52_arm_smmu_apply_display_actlr(smmu_domain, fwspec, dev);' "$SMMU"

python3 -m py_compile \
  scripts/278_apply_qsmmuv500_tbu_children_parity.py \
  scripts/278_check_qsmmuv500_tbu_children_parity.py
python3 scripts/278_apply_qsmmuv500_tbu_children_parity.py \
  --root "$ROOT" --self-test
python3 scripts/278_apply_qsmmuv500_tbu_children_parity.py --root "$ROOT"
python3 scripts/278_check_qsmmuv500_tbu_children_parity.py --root "$ROOT"

! cmp -s /tmp/p278-arm-smmu-before.c "$SMMU"
grep -Fq 'A52_PHASE278_QSMMUV500_TBU_CHILDREN_PARITY_V1' "$SMMU"
grep -Fq '.compatible = "qcom,qsmmuv500-tbu"' "$SMMU"
grep -Fq 'of_platform_populate(smmu->dev->of_node, NULL, NULL, smmu->dev)' "$SMMU"
grep -Fq 'return -EPROBE_DEFER;' "$SMMU"
grep -Fq 'tbu->smmu = ctx->smmu;' "$SMMU"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p278-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase278-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase278-out
mkdir -p phase278-out/{compile,config,package,audit,source}
cp "$IMAGE" phase278-out/compile/Image
cp "$OUT/.config" phase278-out/config/final.config
cp /tmp/p278-base.config phase278-out/audit/phase277-final.config
cp /tmp/p278-arm-smmu-before.c phase278-out/audit/arm-smmu-before.c
cp phase278-compile.log phase278-out/audit/
cp scripts/278_apply_qsmmuv500_tbu_children_parity.py phase278-out/audit/
cp scripts/278_check_qsmmuv500_tbu_children_parity.py phase278-out/audit/
cp "$SMMU" phase278-out/source/arm-smmu.c

gzip -n -c "$IMAGE" > phase278-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase277-out/package/boot.img \
  --kernel phase278-out/package/Image.gz \
  --output phase278-out/package/boot.img \
  --report phase278-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase278-out')
idn = {
    'phase': '278',
    'name': 'QSMMUV500-TBU-CHILDREN-PARITY',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'Phase277 QSMMUv500 display ACTLR parity',
    'golden_contract': 'populate qcom,qsmmu-v500 children -> bind qcom,qsmmuv500-tbu -> validate bound child -> associate TBU with parent',
    'tbu_compatible': 'qcom,qsmmuv500-tbu',
    'tbu_resources': ['base', 'status-reg'],
    'tbu_property': 'qcom,stream-id-range',
    'parent_defer_on_unbound_tbu': True,
    'no_tbu_children_is_safe': True,
    'phase277_actlr_preserved': True,
    'runtime_pm_policy_changed': False,
    'debug_testbus_capturebus_ecats_added': False,
    'runtime_success': 'all instantiated A52 QSMMUv500 TBU children bind before parent SMMU probe completes',
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase277-final.config',
    'audit/arm-smmu-before.c',
    'audit/278_apply_qsmmuv500_tbu_children_parity.py',
    'audit/278_check_qsmmuv500_tbu_children_parity.py',
    'source/arm-smmu.c',
]
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY

(cd phase278-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r = Path('phase278-out')
s = (r/'source/arm-smmu.c').read_text()
img = (r/'compile/Image').read_bytes()
source_markers = [
    'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1',
    'A52_PHASE278_QSMMUV500_TBU_CHILDREN_PARITY_V1',
    '.compatible = "qcom,qsmmuv500-tbu"',
    'of_platform_populate(smmu->dev->of_node, NULL, NULL, smmu->dev)',
    'tbu->smmu = ctx->smmu;',
]
runtime_markers = [
    'SMMU P277 ACTLR sid=%x cb=%u actlr=%x before=%x after=%x tlb=1',
    'SMMU P278 TBU probe dev=%s sid=%x count=%x',
    'SMMU P278 TBU link dev=%s parent=%s sid=%x count=%x',
    'SMMU P278 TBU ready parent=%s bound=%u',
]
for marker in source_markers:
    if marker not in s:
        raise SystemExit('Phase278 source marker missing: ' + marker)
for marker in runtime_markers:
    if marker not in s:
        raise SystemExit('Phase278 runtime marker missing from source: ' + marker)
    if marker.encode() not in img:
        raise SystemExit('Phase278 runtime marker missing from Image: ' + marker)
if b'qsmmuv500-tbu' not in img:
    raise SystemExit('Phase278 TBU compatible/driver string missing from Image')
print('Phase278 QSMMUv500 TBU child lifecycle Image audit: PASS')
PY

trap - EXIT
echo 'Phase278 QSMMUv500 TBU child lifecycle parity build/repack: PASS'
