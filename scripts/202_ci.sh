#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-smmu-component-dependency"
OUT="$PWD/artifacts/a52xq-smmu-driver-core-trace"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/201_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase202.config"
for file in \
  drivers/base/dd.c \
  drivers/base/core.c \
  drivers/base/platform.c \
  drivers/of/device.c \
  drivers/iommu/of_iommu.c \
  drivers/a52_secure/a52_ack_secure_flight_recorder.c; do
  name="${file//\//-}"
  cp "$ROOT/$file" "$OUT/stage/${name%.c}-before-phase202.c"
done
sha256sum \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/msm_smmu.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  > "$OUT/stage/phase201-invariants-before-phase202.sha256"

python3 scripts/202_apply_driver_core_trace.py --root "$ROOT" --self-test | \
  tee "$OUT/logs/phase202-patcher-self-test.log"
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT" | \
  tee "$OUT/logs/phase202-apply.log"

for file in \
  drivers/base/dd.c \
  drivers/base/core.c \
  drivers/base/platform.c \
  drivers/of/device.c \
  drivers/iommu/of_iommu.c \
  drivers/a52_secure/a52_ack_secure_flight_recorder.c; do
  name="${file//\//-}"
  cp "$ROOT/$file" "$OUT/stage/${name%.c}-after-phase202.c"
done
cp scripts/202_apply_driver_core_trace.py "$OUT/stage/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase201-invariants-before-phase202.sha256"
cmp "$OUT/config/before-phase202.config" "$BUILD/.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
files = {
    'dd': root / 'drivers/base/dd.c',
    'core': root / 'drivers/base/core.c',
    'platform': root / 'drivers/base/platform.c',
    'ofdev': root / 'drivers/of/device.c',
    'ofiommu': root / 'drivers/iommu/of_iommu.c',
    'rec': root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c',
}
text = {key: path.read_text() for key, path in files.items()}
for marker in (
    'DCORE attach enter dev=%s async=%d driver=%d',
    'DCORE platform-match drv=%s rc=%d',
    'DCORE really enter dev=%s drv=%s all=%d',
    'DCORE suppliers rc=%d status=%d',
    'DCORE dma exit rc=%d mapped=%d',
    'DCORE bus-probe enter drv=%s',
    'DCORE defer add dev=%s',
    'DCORE defer retry dev=%s',
):
    assert marker in text['dd'] or marker in text['platform'], marker
for marker in (
    'DLINK check enter dev=%s status=%d permissive=%d',
    'DLINK fwnode wait supplier=%pfwP',
    'DLINK supplier=%s st=%d flags=%x supst=%d',
):
    assert marker in text['core'], marker
for marker in (
    'DMA platform enter dev=%s',
    'DMA platform exit rc=%d mapped=%d',
):
    assert marker in text['platform'], marker
for marker in (
    'DMA of enter dev=%s force=%d',
    'DMA iommu result err=%ld ptr=%d mapped=%d',
):
    assert marker in text['ofdev'], marker
for marker in (
    'IOMMU of enter dev=%s fwspec=%d ops=%d',
    'IOMMU configure-device err=%d',
    'IOMMU probe-device exit err=%d mapped=%d',
):
    assert marker in text['ofiommu'], marker
for marker in (
    'copies=3 crc=crc32c',
    '#define A52_R179_PREFIX "R99"',
    'a52_r199_crc32c',
    '!strncmp(message, "DCORE ", 6)',
    '!strncmp(message, "DLINK ", 6)',
    '!strncmp(message, "DMA ", 4)',
    '!strncmp(message, "IOMMU ", 6)',
):
    assert marker in text['rec'], marker
for key in ('dd', 'core', 'platform', 'ofdev', 'ofiommu'):
    assert 'qcom,smmu_sde_unsec' in text[key], key
assert 'iommu_bypass' not in ''.join(text.values()).lower()
PY

git -C "$ROOT" diff --binary --no-ext-diff -- \
  drivers/base/dd.c \
  drivers/base/core.c \
  drivers/base/platform.c \
  drivers/of/device.c \
  drivers/iommu/of_iommu.c \
  drivers/a52_secure/a52_ack_secure_flight_recorder.c \
  > "$OUT/stage/phase202-smmu-driver-core-trace.patch"
test -s "$OUT/stage/phase202-smmu-driver-core-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase202-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase202-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase202-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase202-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in \
  'drivers/base/dd.o' \
  'drivers/base/core.o' \
  'drivers/base/platform.o' \
  'drivers/of/device.o' \
  'drivers/iommu/of_iommu.o' \
  'drivers/a52_secure/a52_ack_secure_flight_recorder.o'; do
  grep -Fq "$object" "$OUT/logs/phase202-compile.log"
done
for marker in \
  'DCORE attach enter dev=%s async=%d driver=%d' \
  'DCORE platform-match drv=%s rc=%d' \
  'DCORE suppliers rc=%d status=%d' \
  'DLINK fwnode wait supplier=%pfwP' \
  'DLINK supplier=%s st=%d flags=%x supst=%d' \
  'DMA iommu result err=%ld ptr=%d mapped=%d' \
  'IOMMU configure-device err=%d' \
  'IOMMU probe-device exit err=%d mapped=%d' \
  'DRMCOMP smmu-match added node=%s' \
  'SMMU component-add exit rc=%d' \
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
  tee "$OUT/logs/phase202-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | \
  tee "$OUT/logs/phase202-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 202 filtered SMMU driver-core trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 201 hardware result:
  - the unsecure SMMU platform device was created successfully
  - the msmdrm_smmu driver was registered
  - the device never entered msm_smmu_probe()
  - the fourth DRM component remained missing, so DRM correctly stayed unbound
  - Linux and Android userspace remained alive while the inherited splash later
    went black

Phase 202 changes:
  - observation-only trace for qcom,smmu_sde_unsec
  - trace platform-bus match and device attach
  - trace supplier/device-link readiness before really_probe()
  - trace deferred-probe enqueue and retry
  - trace platform DMA configuration and OF IOMMU configuration
  - trace transition into the actual platform-driver probe callback
  - preserve the Phase 201 component dependency and error propagation
  - preserve three recorder copies, 32 RS symbols and CRC32C

No IOMMU bypass is added. No supplier dependency is relaxed. DTB, DTBO, ramdisk,
panel commands, display timing and display power policy remain unchanged.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-smmu-driver-core-trace')
base = json.loads(Path('artifacts/a52xq-smmu-component-dependency/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-smmu-driver-core-trace-audited',
    'phase': 202,
    'base_phase': 201,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase199_crc32c_rs3_preserved': True,
    'phase200_smmu_defer_trace_preserved': True,
    'phase201_smmu_component_dependency_preserved': True,
    'functional_change_from_phase201': 'observation-only-filtered-smmu-driver-core-trace',
    'iommu_bypass_added': False,
    'supplier_dependency_relaxed': False,
    'driver_core_trace_filtered_to_unsecure_sde_smmu': True,
    'platform_match_trace_added': True,
    'supplier_link_trace_added': True,
    'deferred_probe_trace_added': True,
    'dma_iommu_config_trace_added': True,
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
    'phase201_smmu_component_dependency_preserved',
    'driver_core_trace_filtered_to_unsecure_sde_smmu',
    'platform_match_trace_added',
    'supplier_link_trace_added',
    'deferred_probe_trace_added',
    'dma_iommu_config_trace_added',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert base[key] is True, key
assert base['iommu_bypass_added'] is False
assert base['supplier_dependency_relaxed'] is False
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
