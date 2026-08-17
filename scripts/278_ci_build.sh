#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"

fail_report(){
  set +e
  rm -rf phase278-failure
  mkdir -p phase278-failure/source phase278-failure/logs phase278-failure/audit
  cp phase278-compile.log phase278-failure/logs/ 2>/dev/null || true
  cp "$SMMU" phase278-failure/source/arm-smmu.c 2>/dev/null || true
  cp "$DSI" phase278-failure/source/dsi_ctrl.c 2>/dev/null || true
  cp scripts/278_apply_live_display_smmu_snapshot.py phase278-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase277 candidate first. Phase277 already preserves the
# hardware-proven Phase276R DSI recorder and adds the display ACTLR experiment.
bash scripts/277_ci_build.sh
test -s phase277-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p278-base.config
cp "$SMMU" /tmp/p278-arm-smmu-before.c
cp "$DSI" /tmp/p278-dsi-ctrl-before.c

# Phase278 is diagnostic-only. It remembers the exact Phase277 display SME/CB
# and snapshots live hardware SMMU state at the proven DSI DMA boundary.
python3 -m py_compile scripts/278_apply_live_display_smmu_snapshot.py
python3 scripts/278_apply_live_display_smmu_snapshot.py --root "$ROOT" --self-test
python3 scripts/278_apply_live_display_smmu_snapshot.py --root "$ROOT"

! cmp -s /tmp/p278-arm-smmu-before.c "$SMMU"
! cmp -s /tmp/p278-dsi-ctrl-before.c "$DSI"
grep -Fq 'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1' "$SMMU"
grep -Fq 'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1' "$DSI"
grep -Fq 'a52_p278_display_smmu_snapshot(0);' "$DSI"
grep -Fq 'a52_p278_display_smmu_snapshot(1);' "$DSI"
grep -Fq 'a52_p278_display_smmu_snapshot(2);' "$DSI"
grep -Fq 'ARM_SMMU_GR0_S2CR(ctx->sme)' "$SMMU"
grep -Fq 'ARM_SMMU_CB_ACTLR' "$SMMU"
grep -Fq 'SMMU P278 C p=%u sid=%x sme=%d cb=%u a=%x sc=%x m=%u f=%x' "$SMMU"
grep -Fq 'SMMU P278 S p=%u s2=%x ty=%u xcb=%u smr=%x cbar=%x' "$SMMU"

# No config or translation-policy change is allowed in this phase.
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
cp /tmp/p278-dsi-ctrl-before.c phase278-out/audit/dsi-ctrl-before.c
cp phase278-compile.log phase278-out/audit/
cp scripts/278_apply_live_display_smmu_snapshot.py phase278-out/audit/
cp "$SMMU" phase278-out/source/arm-smmu.c
cp "$DSI" phase278-out/source/dsi_ctrl.c

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
    'name': 'LIVE-DISPLAY-SMMU-STATE-CORRELATION',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'Phase277 QSMMUv500 display ACTLR parity + Phase276R DSI DMA recorder V5',
    'functional_change': 'none; diagnostic-only live register snapshots',
    'translation_state_changed': False,
    'tbu_driver_added': False,
    'snapshot_points': {
        '0': 'immediately before embedded memory-DMA kickoff',
        '1': 'immediately after kickoff returns',
        '2': 'after the proven DMA completion timeout, before DSI status handling',
    },
    'hardware_question': (
        'At the exact failing display DMA transaction, is the Phase277 ACTLR 0x103 '
        'still active, is SCTLR.M enabled, and does the hardware S2CR route the '
        'display SID to the expected translating context bank?'
    ),
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase277-final.config',
    'audit/arm-smmu-before.c', 'audit/dsi-ctrl-before.c',
    'audit/278_apply_live_display_smmu_snapshot.py',
    'source/arm-smmu.c', 'source/dsi_ctrl.c',
]
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY

(cd phase278-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r = Path('phase278-out')
smmu = (r/'source/arm-smmu.c').read_text()
dsi = (r/'source/dsi_ctrl.c').read_text()
img = (r/'compile/Image').read_bytes()
required_source = [
    'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1',
    'a52_p278_remember_display_context(smmu_domain, fwspec, dev);',
    'a52_p278_display_smmu_snapshot(0);',
    'a52_p278_display_smmu_snapshot(1);',
    'a52_p278_display_smmu_snapshot(2);',
]
for marker in required_source:
    if marker not in smmu + dsi:
        raise SystemExit('Phase278 source marker missing: ' + marker)
runtime = [
    'SMMU P278 C p=%u sid=%x sme=%d cb=%u a=%x sc=%x m=%u f=%x',
    'SMMU P278 S p=%u s2=%x ty=%u xcb=%u smr=%x cbar=%x',
    'P276 H K o=%llx l=%u h=%x',
    'P276 D M w=1 v=%d',
]
for marker in runtime:
    if marker.encode() not in img:
        raise SystemExit('Phase278 Image runtime marker missing: ' + marker)
print('Phase278 live display SMMU state Image audit: PASS')
PY

trap - EXIT
echo 'Phase278 live display SMMU state correlation build/repack: PASS'
