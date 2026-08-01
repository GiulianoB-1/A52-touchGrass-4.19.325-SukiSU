#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-smmu-driver-core-trace"
OUT="$PWD/artifacts/a52xq-apps-smmu-qsmmuv500-compat"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/202_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase203.config"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  "$OUT/stage/arm-smmu-before-phase203.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" \
  "$OUT/stage/arm-smmu-qcom-before-phase203.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase203.c"
sha256sum \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/base/dd.c" \
  "$ROOT/drivers/base/core.c" \
  "$ROOT/drivers/base/platform.c" \
  "$ROOT/drivers/of/device.c" \
  "$ROOT/drivers/iommu/of_iommu.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/msm_smmu.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  > "$OUT/stage/phase202-invariants-before-phase203.sha256"

python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py \
  --root "$ROOT" --self-test | tee "$OUT/logs/phase203-patcher-self-test.log"
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py \
  --root "$ROOT" | tee "$OUT/logs/phase203-apply.log"

cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  "$OUT/stage/arm-smmu-after-phase203.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" \
  "$OUT/stage/arm-smmu-qcom-after-phase203.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase203.c"
cp scripts/203_apply_apps_smmu_qsmmuv500_compat.py "$OUT/stage/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase202-invariants-before-phase203.sha256"
cmp "$OUT/config/before-phase203.config" "$BUILD/.config"
cmp "$OUT/stage/recorder-before-phase203.c" "$OUT/stage/recorder-after-phase203.c"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
core = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
qcom = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c').read_text()
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
for marker in (
    '{ .compatible = "qcom,qsmmu-v500", .data = &qcom_qsmmuv500 },',
    'ARM_SMMU_MATCH_DATA(qcom_qsmmuv500, ARM_SMMU_V2, QCOM_SMMUV2);',
    'arm_smmu_qsmmuv500_skip_init',
    'if (!skip_init)',
    'ias = min(ias, 39UL);',
    'SMMU parent-probe enter dev=%s driver=%s',
    'SMMU parent-dt version=%d model=%d girq=%u skip=%d lvl3=%d',
    'SMMU parent-reset enter skip=%d groups=%u cbs=%u',
    'SMMU parent-register rc=%d',
    'SMMU parent-probe exit rc=%d legacy=0',
):
    assert marker in core, marker
for marker in (
    'SMMU parent-qcom-create scm=%d',
    'SMMU parent-qcom-cfg enter groups=%u',
    'SMMU parent-qcom-cfg exit rc=0',
    'SMMU parent-qcom-create rc=0',
):
    assert marker in qcom, marker
for marker in (
    'copies=3 crc=crc32c',
    '#define A52_R179_PREFIX "R99"',
    'a52_r199_crc32c',
    '!strncmp(message, "SMMU ", 5)',
):
    assert marker in rec, marker
assert core.count('.compatible = "qcom,qsmmu-v500"') == 1
assert 'iommu_bypass' not in (core + qcom).lower()
assert 'qcom,skip-init' in core
assert 'qcom,use-3-lvl-tables' in core
PY

git -C "$ROOT" diff --binary --no-ext-diff -- \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c \
  > "$OUT/stage/phase203-apps-smmu-qsmmuv500-compat.patch"
test -s "$OUT/stage/phase203-apps-smmu-qsmmuv500-compat.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase203-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase203-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase203-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase203-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for marker in \
  'qcom,qsmmu-v500' \
  'SMMU parent-probe enter dev=%s driver=%s' \
  'SMMU parent-dt version=%d model=%d girq=%u skip=%d lvl3=%d' \
  'SMMU parent-qcom-create scm=%d' \
  'SMMU parent-qcom-cfg enter groups=%u' \
  'SMMU parent-reset enter skip=%d groups=%u cbs=%u' \
  'SMMU parent-domain 3lvl ias=%lu' \
  'SMMU parent-register rc=%d' \
  'SMMU parent-probe exit rc=%d legacy=0' \
  'DCORE suppliers rc=%d status=%d' \
  'DRMCOMP smmu-match added node=%s' \
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
  tee "$OUT/logs/phase203-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | \
  tee "$OUT/logs/phase203-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 203 Lagoon Apps SMMU qsmmuv500 compatibility

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 202 hardware result:
  - qcom,smmu_sde_unsec matched msmdrm_smmu and entered really_probe()
  - its managed supplier link to 15000000.apps-smmu stayed dormant
  - the parent Apps SMMU had no driver, causing repeated -EPROBE_DEFER

Exact TouchGrass comparison:
  - Lagoon Apps SMMU DT compatible is qcom,qsmmu-v500
  - TouchGrass downstream arm-smmu explicitly matches qcom,qsmmu-v500
  - pinned GKI arm-smmu did not match qcom,qsmmu-v500
  - both kernels already have CONFIG_ARM_SMMU=y

Phase 203 changes:
  - add the missing qcom,qsmmu-v500 OF match to GKI's Qualcomm SMMUv2 path
  - preserve TouchGrass qcom,skip-init semantics so live bootloader stream and
    context-bank mappings are not cleared during reset
  - preserve TouchGrass qcom,use-3-lvl-tables semantics by limiting stage-1
    AArch64 IAS to 39 bits
  - trace the parent probe, SCM readiness, hardware configuration, reset,
    IOMMU registration and bus initialization
  - preserve the Phase 202 child/device-link trace and Phase 201 dependency
  - preserve three recorder copies, 32 RS symbols and CRC32C

This is a narrow compatibility port, not a wholesale import of the downstream
TouchGrass SMMU/TBU driver. No IOMMU bypass is added. DTB, DTBO, ramdisk, panel
commands, display timing and display power policy remain unchanged.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-apps-smmu-qsmmuv500-compat')
base = json.loads(Path('artifacts/a52xq-smmu-driver-core-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-apps-smmu-qsmmuv500-compat-audited',
    'phase': 203,
    'base_phase': 202,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase199_crc32c_rs3_preserved': True,
    'phase201_smmu_component_dependency_preserved': True,
    'phase202_driver_core_trace_preserved': True,
    'functional_change_from_phase202': 'lagoon-apps-smmu-qsmmuv500-minimal-compatibility',
    'qcom_qsmmuv500_match_added': True,
    'touchgrass_skip_init_semantics_preserved': True,
    'touchgrass_3lvl_ias_semantics_preserved': True,
    'full_touchgrass_tbu_driver_imported': False,
    'iommu_bypass_added': False,
    'supplier_dependency_relaxed': False,
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
    'phase201_smmu_component_dependency_preserved',
    'phase202_driver_core_trace_preserved',
    'qcom_qsmmuv500_match_added',
    'touchgrass_skip_init_semantics_preserved',
    'touchgrass_3lvl_ias_semantics_preserved',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert base[key] is True, key
assert base['iommu_bypass_added'] is False
assert base['supplier_dependency_relaxed'] is False
assert base['full_touchgrass_tbu_driver_imported'] is False
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
