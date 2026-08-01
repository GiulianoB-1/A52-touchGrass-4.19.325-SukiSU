#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-apps-smmu-qsmmuv500-compat"
OUT="$PWD/artifacts/a52xq-apps-smmu-registration-audit"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/203_ci_parent_trace.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase204.config"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  "$OUT/stage/arm-smmu-before-phase204.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" \
  "$OUT/stage/arm-smmu-qcom-before-phase204.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase204.c"
sha256sum \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/base/dd.c" \
  "$ROOT/drivers/base/core.c" \
  "$ROOT/drivers/base/platform.c" \
  "$ROOT/drivers/of/device.c" \
  "$ROOT/drivers/iommu/of_iommu.c" \
  "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/msm_smmu.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  > "$OUT/stage/phase203-invariants-before-phase204.sha256"

python3 scripts/204_apply_apps_smmu_registration_audit.py \
  --root "$ROOT" --self-test | tee "$OUT/logs/phase204-patcher-self-test.log"
python3 scripts/204_apply_apps_smmu_registration_audit.py \
  --root "$ROOT" | tee "$OUT/logs/phase204-apply.log"

cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  "$OUT/stage/arm-smmu-after-phase204.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" \
  "$OUT/stage/arm-smmu-qcom-after-phase204.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase204.c"
cp scripts/204_apply_apps_smmu_registration_audit.py "$OUT/stage/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase203-invariants-before-phase204.sha256"
cmp "$OUT/config/before-phase204.config" "$BUILD/.config"
cmp "$OUT/stage/arm-smmu-qcom-before-phase204.c" \
  "$OUT/stage/arm-smmu-qcom-after-phase204.c"
cmp "$OUT/stage/recorder-before-phase204.c" \
  "$OUT/stage/recorder-after-phase204.c"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
core = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
qcom = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c').read_text()
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
for marker in (
    '{ .compatible = "qcom,qsmmu-v500", .data = &arm_mmu500 },',
    'if (!smmu->skip_init)',
    'if (smmu->use_3lvl_tables)',
    'SMMU parent-probe enter dev=%s driver=%s',
    'SMMU parent-qcom scm=%d',
    '#define A52_APPS_SMMU_NAME "15000000.apps-smmu"',
    'SMMU arm-init enter',
    'SMMU arm-init register rc=%d',
    'SMMU audit %s present=%d bound=%s match=%d ofmatch=%d',
    'SMMU audit %s compat%d=%s',
    'bus_find_device_by_name(&platform_bus_type',
    'driver_match_device(&arm_smmu_driver.driver, dev)',
    'of_match_device(arm_smmu_of_match, dev)',
    'late_initcall_sync(a52_arm_smmu_late_audit);',
    'module_init(arm_smmu_init);',
    'module_exit(arm_smmu_exit);',
):
    assert marker in core, marker
for marker in (
    'copies=3 crc=crc32c',
    '#define A52_R179_PREFIX "R99"',
    'a52_r199_crc32c',
    '!strncmp(message, "SMMU ", 5)',
):
    assert marker in rec, marker
assert 'module_platform_driver(arm_smmu_driver);' not in core
assert core.count('platform_driver_register(&arm_smmu_driver)') == 1
assert 'iommu_bypass' not in (core + qcom).lower()
PY

git -C "$ROOT" diff --binary --no-ext-diff -- \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  > "$OUT/stage/phase204-apps-smmu-registration-audit.patch"
test -s "$OUT/stage/phase204-apps-smmu-registration-audit.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase204-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase204-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase204-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase204-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for marker in \
  'qcom,qsmmu-v500' \
  '15000000.apps-smmu' \
  'SMMU parent-probe enter dev=%s driver=%s' \
  'SMMU parent-qcom scm=%d' \
  'SMMU arm-init enter' \
  'SMMU arm-init register rc=%d' \
  'SMMU audit %s present=%d bound=%s match=%d ofmatch=%d' \
  'SMMU audit %s compat%d=%s' \
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
  tee "$OUT/logs/phase204-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | \
  tee "$OUT/logs/phase204-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 204 Apps SMMU registration and match audit

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 203 hardware evidence, reconstructed from three CRC32C-protected recorder
copies, shows that the display context-bank child matches msmdrm_smmu but is
deferred by supplier 15000000.apps-smmu. The ARM SMMU driver registers with
return code zero, yet no parent arm_smmu_device_probe checkpoint is reached.

Phase 204 is diagnostic only. It records:
  - ARM SMMU module initialization and platform-driver registration result
  - whether 15000000.apps-smmu exists before registration
  - its bound driver, actual platform-bus match result and OF-table match result
    immediately after registration and again at late init
  - up to four compatible strings from the parent device
  - parent probe checkpoints even if the device name matches but its compatible
    string differs from qcom,qsmmu-v500

Phase 204 does not force a bind, call device_attach, alter deferred-probe rules,
relax supplier dependencies or add an IOMMU bypass. DTB, DTBO, ramdisk, panel
commands, display timing and display power policy remain unchanged.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-apps-smmu-registration-audit')
base = json.loads(Path('artifacts/a52xq-apps-smmu-qsmmuv500-compat/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-apps-smmu-registration-audit-audited',
    'phase': 204,
    'base_phase': 203,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase199_crc32c_rs3_preserved': True,
    'phase201_smmu_component_dependency_preserved': True,
    'phase202_driver_core_trace_preserved': True,
    'phase203_parent_probe_trace_preserved': True,
    'functional_change_from_phase203': 'none-diagnostic-registration-match-audit-only',
    'apps_smmu_registration_audit_added': True,
    'apps_smmu_actual_driver_match_audit_added': True,
    'apps_smmu_of_match_audit_added': True,
    'apps_smmu_compatible_string_audit_added': True,
    'apps_smmu_late_init_audit_added': True,
    'forced_bind_added': False,
    'device_attach_added': False,
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
    'phase203_parent_probe_trace_preserved',
    'apps_smmu_registration_audit_added',
    'apps_smmu_actual_driver_match_audit_added',
    'apps_smmu_of_match_audit_added',
    'apps_smmu_compatible_string_audit_added',
    'apps_smmu_late_init_audit_added',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert base[key] is True, key
for key in ('forced_bind_added', 'device_attach_added', 'iommu_bypass_added',
            'supplier_dependency_relaxed'):
    assert base[key] is False, key
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
