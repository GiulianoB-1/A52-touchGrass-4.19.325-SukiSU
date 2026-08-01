#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-smmu-defer-trace"
OUT="$PWD/artifacts/a52xq-smmu-component-dependency"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/200_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase201.config"
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" "$OUT/stage/msm-drv-before-phase201.c"
cp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$OUT/stage/msm-smmu-before-phase201.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-before-phase201.c"
sha256sum \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  > "$OUT/stage/phase200-invariants-before-phase201.sha256"

python3 scripts/201_apply_smmu_component_dependency.py --self-test | \
  tee "$OUT/logs/phase201-patcher-self-test.log"
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT" | \
  tee "$OUT/logs/phase201-apply.log"

cp "$ROOT/drivers/a52_display/msm/msm_drv.c" "$OUT/stage/msm-drv-after-phase201.c"
cp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$OUT/stage/msm-smmu-after-phase201.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-after-phase201.c"
cp scripts/201_apply_smmu_component_dependency.py "$OUT/stage/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase200-invariants-before-phase201.sha256"
cmp "$OUT/config/before-phase201.config" "$BUILD/.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
drv = (root / 'drivers/a52_display/msm/msm_drv.c').read_text()
smmu = (root / 'drivers/a52_display/msm/msm_smmu.c').read_text()
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
for marker in (
    'DRMPOST helper propagate rc=%d',
    'ret = IS_ERR(kms) ? PTR_ERR(kms) : -ENODEV;',
    'DRMCOMP smmu-match ready=%d existing=%d driver=%d client=%d',
    'DRMCOMP smmu-match added node=%s',
    'component_match_add(dev, matchptr, compare_of, smmu_node);',
):
    assert marker in drv, marker
for marker in (
    '#include <linux/component.h>',
    'bool client_dev_owned;',
    'SMMU component-add enter compat=%s',
    'SMMU component-add exit rc=%d',
    'SMMU component-bind dev=%s master=%s',
    'component_del(&pdev->dev, &msm_smmu_component_ops);',
    'pdev = of_find_device_by_node(child);',
    'SMMU create state domain=%d existing=%d driver=%d client=%d',
):
    assert marker in smmu, marker
assert 'if (smmu->client_dev && smmu->client_dev_owned)' in smmu
for marker in (
    'copies=3 crc=crc32c',
    '#define A52_R179_PREFIX "R99"',
    'a52_r199_crc32c',
    '!strncmp(message, "SMMU ", 5)',
):
    assert marker in rec, marker
assert 'iommu_bypass' not in drv.lower()
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase201-smmu-component-dependency.patch"
test -s "$OUT/stage/phase201-smmu-component-dependency.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase201-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase201-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase201-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase201-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in \
  'drivers/a52_display/msm/msm_drv.o' \
  'drivers/a52_display/msm/msm_smmu.o'; do
  grep -Fq "$object" "$OUT/logs/phase201-compile.log"
done
for marker in \
  'DRMPOST helper propagate rc=%d' \
  'DRMCOMP smmu-match ready=%d existing=%d driver=%d client=%d' \
  'DRMCOMP smmu-match added node=%s' \
  'SMMU component-add exit rc=%d' \
  'SMMU component-bind dev=%s master=%s' \
  'SMMU create state domain=%d existing=%d driver=%d client=%d' \
  'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$BASE_OUT/package/boot.img" \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test | \
  tee "$OUT/logs/phase201-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | \
  tee "$OUT/logs/phase201-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 201 SMMU component dependency

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 200 capture result:
  - the unsecure SMMU child platform device was created
  - its driver and client were not ready
  - Phase 200 correctly returned -EPROBE_DEFER
  - msm_drm_init() then accidentally returned stale rc=0, so the component
    framework considered the failed bind successful and did not retry it

Phase 201 changes:
  - propagate PTR_ERR(kms), including -EPROBE_DEFER, from msm_drm_init()
  - pre-create the unsecure SDE SMMU context-bank device before master binding
  - register the ready SMMU context bank as a component
  - add that component to the DRM master match list, so DRM bind cannot begin
    until the context-bank probe has a real IOMMU domain
  - reuse the pre-created device during msm_smmu_new()
  - preserve three recorder copies, 32 RS symbols and CRC32C

No IOMMU bypass is added. DTB, DTBO, ramdisk, panel commands and display timing
remain unchanged.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-smmu-component-dependency')
base = json.loads(Path('artifacts/a52xq-smmu-defer-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-smmu-component-dependency-audited',
    'phase': 201,
    'base_phase': 200,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase199_crc32c_rs3_preserved': True,
    'phase200_smmu_defer_trace_preserved': True,
    'functional_change_from_phase200': 'smmu-component-readiness-dependency-and-helper-error-propagation',
    'iommu_bypass_added': False,
    'drm_helper_error_propagated': True,
    'unsecure_smmu_precreated': True,
    'unsecure_smmu_component_dependency': True,
    'precreated_smmu_reused': True,
    'dtb_changed': False,
    'dtbo_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'recorder_copy_count': 3,
    'recorder_parity_symbols_per_copy': 32,
    'recorder_crc': 'CRC32C',
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in (
    'phase199_crc32c_rs3_preserved',
    'phase200_smmu_defer_trace_preserved',
    'drm_helper_error_propagated',
    'unsecure_smmu_precreated',
    'unsecure_smmu_component_dependency',
    'precreated_smmu_reused',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert base[key] is True, key
assert base['recorder_copy_count'] == 3
assert base['recorder_parity_symbols_per_copy'] == 32
assert base['recorder_crc'] == 'CRC32C'
(root / 'final-audit.json').write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
