#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-apps-smmu-qsmmuv500-compat"
OUT="$PWD/artifacts/a52xq-apps-smmu-scm-handoff"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/203_ci_parent_trace.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools,comparison}

cp "$BUILD/.config" "$OUT/config/before-phase204.config"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" "$OUT/stage/arm-smmu-before-phase204.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" "$OUT/stage/arm-smmu-qcom-before-phase204.c"
cp "$ROOT/drivers/firmware/qcom_scm.c" "$OUT/comparison/gki-qcom_scm.c"
cp "$ROOT/include/linux/qcom_scm.h" "$OUT/comparison/gki-qcom_scm.h"
cp "$TG/drivers/iommu/arm-smmu.c" "$OUT/comparison/touchgrass-arm-smmu.c"
cp "$TG/drivers/soc/qcom/scm.c" "$OUT/comparison/touchgrass-direct-scm.c"
cp "$TG/include/soc/qcom/scm.h" "$OUT/comparison/touchgrass-direct-scm.h"
cp "$TG/arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi" "$OUT/comparison/touchgrass-lagoon.dtsi"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-before-phase204.c"

sha256sum \
  "$ROOT/fs/pstore/ram.c" "$ROOT/init/main.c" \
  "$ROOT/drivers/base/dd.c" "$ROOT/drivers/base/core.c" \
  "$ROOT/drivers/base/platform.c" "$ROOT/drivers/of/device.c" \
  "$ROOT/drivers/iommu/of_iommu.c" \
  "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  "$ROOT/drivers/firmware/qcom_scm.c" "$ROOT/include/linux/qcom_scm.h" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/msm_smmu.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  > "$OUT/stage/phase203-invariants-before-phase204.sha256"

python3 - <<'PY' | tee "$OUT/logs/phase204-touchgrass-comparison.log"
import json
from pathlib import Path
root = Path('gki/common')
tg = Path('workspace/touchgrass-a52xq')
out = Path('artifacts/a52xq-apps-smmu-scm-handoff/comparison')
gki_qcom = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c').read_text()
gki_core = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
gki_scm = (root / 'drivers/firmware/qcom_scm.c').read_text()
tg_smmu = (tg / 'drivers/iommu/arm-smmu.c').read_text()
tg_scm = (tg / 'drivers/soc/qcom/scm.c').read_text()
tg_scm_h = (tg / 'include/soc/qcom/scm.h').read_text()
lagoon = (tg / 'arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi').read_text()
assert 'qcom_scm_is_available()' in gki_qcom
assert 'if (!scm)' in gki_qcom
assert 'return !!__scm;' in gki_scm
assert 'platform_driver_register(&qcom_scm_driver)' in gki_scm
assert '{ .compatible = "qcom,qsmmu-v500", .data = &arm_mmu500 },' in gki_core
assert 'qcom,skip-init' in gki_core
assert '#include <soc/qcom/scm.h>' in tg_smmu
assert 'qcom_scm_is_available' not in tg_smmu
assert 'arm_smmu_restore_sec_cfg' in tg_smmu
assert 'scm_restore_sec_cfg' in tg_scm
assert 'scm_call2(SCM_SIP_FNID(SCM_SVC_MP, RESTORE_SEC_CFG)' in tg_scm
assert 'extern int scm_restore_sec_cfg' in tg_scm_h
assert 'qcom,scm' not in lagoon
report = {
    'status': 'phase204-touchgrass-first-comparison-pass',
    'touchgrass_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'gki_smmu_waits_for_platform_scm': True,
    'gki_scm_available_is_global_device_pointer': True,
    'touchgrass_smmu_has_platform_scm_gate': False,
    'touchgrass_smmu_uses_direct_downstream_scm': True,
    'touchgrass_lagoon_dts_has_qcom_scm_node': False,
    'actual_phase203_dtb_expected_compatible': 'qcom,qsmmu-v500',
    'actual_phase203_dtb_expected_skip_init': True,
    'selected_fix': 'allow-only-qsmmuv500-skip-init-handoff-without-platform-scm',
    'generic_qcom_smmu_scm_gate_preserved': True,
    'new_recorder_added': False,
}
(out / 'touchgrass-comparison.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
print(json.dumps(report, indent=2, sort_keys=True))
PY

python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT" --self-test | tee "$OUT/logs/phase204-patcher-self-test.log"
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT" | tee "$OUT/logs/phase204-apply.log"

cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" "$OUT/stage/arm-smmu-after-phase204.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" "$OUT/stage/arm-smmu-qcom-after-phase204.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-after-phase204.c"
cp scripts/204_apply_apps_smmu_scm_handoff.py "$OUT/stage/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase203-invariants-before-phase204.sha256"
cmp "$OUT/config/before-phase204.config" "$BUILD/.config"
cmp "$OUT/stage/arm-smmu-before-phase204.c" "$OUT/stage/arm-smmu-after-phase204.c"
cmp "$OUT/stage/recorder-before-phase204.c" "$OUT/stage/recorder-after-phase204.c"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
core = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
qcom = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c').read_text()
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
for marker in (
    '{ .compatible = "qcom,qsmmu-v500", .data = &arm_mmu500 },',
    'if (!smmu->skip_init)', 'if (smmu->use_3lvl_tables)',
    'SMMU parent-probe enter dev=%s driver=%s',
    'bool downstream_handoff = trace &&',
    'of_property_read_bool(smmu->dev->of_node, "qcom,skip-init")',
    'SMMU parent-qcom scm=%d handoff=%d',
    'if (!scm && !downstream_handoff)',
):
    assert marker in (core + qcom), marker
for marker in ('copies=3 crc=crc32c', '#define A52_R179_PREFIX "R99"',
               'a52_r199_crc32c', '!strncmp(message, "SMMU ", 5)'):
    assert marker in rec, marker
assert '\tif (!scm)\n\t\treturn ERR_PTR(-EPROBE_DEFER);' not in qcom
assert qcom.count('if (!scm && !downstream_handoff)') == 1
assert 'device_attach(' not in qcom
assert 'iommu_bypass' not in (core + qcom).lower()
PY

git -C "$ROOT" diff --binary --no-ext-diff -- drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c > "$OUT/stage/phase204-apps-smmu-scm-handoff.patch"
test -s "$OUT/stage/phase204-apps-smmu-scm-handoff.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 KCFLAGS=-Wno-error=frame-larger-than Image > "$OUT/logs/phase204-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase204-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase204-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase204-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for marker in 'qcom,qsmmu-v500' 'qcom,skip-init' \
  'SMMU parent-probe enter dev=%s driver=%s' \
  'SMMU parent-qcom scm=%d handoff=%d' \
  'DCORE suppliers rc=%d status=%d' \
  'DRMCOMP smmu-match added node=%s' \
  'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source "$BASE_OUT/package/boot.img" --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test | tee "$OUT/logs/phase204-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | tee "$OUT/logs/phase204-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 204 Lagoon SCM-less Apps SMMU handoff

FLASH ONLY:
  package/boot.img -> BOOT partition

TouchGrass-first comparison result:
  - the preserved Lagoon DT has qcom,qsmmu-v500 with qcom,skip-init
  - the Lagoon DT has no qcom,scm platform device
  - upstream GKI qcom_smmu_create waits for qcom_scm_is_available()
  - qcom_scm_is_available() remains false until a qcom_scm platform device probes
  - TouchGrass SMMU has no such readiness gate and uses its separate downstream direct-SMC implementation for secure operations
  - in the GKI Lagoon skip-init path, the Qualcomm reset hook is suppressed and no SCM operation is issued by arm-smmu-qcom

Phase 204 changes only the Qualcomm implementation readiness condition. When the node is exactly qcom,qsmmu-v500 and also declares qcom,skip-init, it may preserve the bootloader handoff and continue without the absent upstream qcom_scm platform device. Every other Qualcomm SMMU configuration retains the original SCM gate.

No new recorder is added. Existing Phase 203 checkpoints remain available. No forced bind, device_attach call, IOMMU bypass, supplier relaxation, DTB change, DTBO change, ramdisk change, panel command change, timing change, or power-policy change is included.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-apps-smmu-scm-handoff')
base = json.loads(Path('artifacts/a52xq-apps-smmu-qsmmuv500-compat/final-audit.json').read_text())
comparison = json.loads((root / 'comparison/touchgrass-comparison.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-apps-smmu-scm-handoff-audited', 'phase': 204,
    'base_phase': 203, 'hardware_validated': False, 'flashable_candidate': True,
    'phase199_crc32c_rs3_preserved': True,
    'phase201_smmu_component_dependency_preserved': True,
    'phase202_driver_core_trace_preserved': True,
    'phase203_parent_probe_trace_preserved': True,
    'touchgrass_compared_before_fix': True,
    'touchgrass_commit': comparison['touchgrass_commit'],
    'functional_change_from_phase203': 'qsmmuv500-skip-init-scm-less-handoff',
    'qsmmuv500_skip_init_handoff_added': True,
    'generic_qcom_scm_gate_preserved': True,
    'new_recorder_added': False, 'forced_bind_added': False,
    'device_attach_added': False, 'iommu_bypass_added': False,
    'supplier_dependency_relaxed': False, 'qcom_scm_driver_modified': False,
    'dtb_changed': False, 'dtbo_changed': False,
    'panel_commands_changed': False, 'display_timing_changed': False,
    'recorder_copy_count': 3, 'recorder_parity_symbols_per_copy': 32,
    'recorder_crc': 'CRC32C',
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in ('phase199_crc32c_rs3_preserved', 'phase201_smmu_component_dependency_preserved',
            'phase202_driver_core_trace_preserved', 'phase203_parent_probe_trace_preserved',
            'touchgrass_compared_before_fix', 'qsmmuv500_skip_init_handoff_added',
            'generic_qcom_scm_gate_preserved', 'dtb_preserved', 'ramdisk_preserved',
            'recovery_dtbo_preserved'):
    assert base[key] is True, key
for key in ('new_recorder_added', 'forced_bind_added', 'device_attach_added',
            'iommu_bypass_added', 'supplier_dependency_relaxed', 'qcom_scm_driver_modified'):
    assert base[key] is False, key
assert base['recorder_copy_count'] == 3
assert base['recorder_parity_symbols_per_copy'] == 32
assert base['recorder_crc'] == 'CRC32C'
(root / 'final-audit.json').write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
