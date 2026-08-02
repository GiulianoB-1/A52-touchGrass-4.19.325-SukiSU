#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-apps-smmu-scm-handoff"
OUT="$PWD/artifacts/a52xq-smmu-display-contracts"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/204_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools,comparison}

cp "$BUILD/.config" "$OUT/config/before-phase206.config"
for rel in \
  include/linux/iommu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/iommu/dma-iommu.c \
  drivers/a52_display/msm/msm_smmu.c; do
  safe="${rel//\//-}"
  cp "$ROOT/$rel" "$OUT/stage/${safe}-before-phase206"
done

cp "$TG/drivers/iommu/arm-smmu.c" "$OUT/comparison/touchgrass-arm-smmu.c"
cp "$TG/arch/arm64/mm/dma-mapping.c" "$OUT/comparison/touchgrass-dma-mapping.c"
cp "$TG/arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi" "$OUT/comparison/touchgrass-lagoon.dtsi"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-before-phase206.c"

python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase206-patcher-self-test.log"
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT" \
  | tee "$OUT/logs/phase206-apply.log"
cp scripts/206_apply_smmu_display_contracts.py "$OUT/stage/"

for rel in \
  include/linux/iommu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/iommu/dma-iommu.c \
  drivers/a52_display/msm/msm_smmu.c; do
  safe="${rel//\//-}"
  cp "$ROOT/$rel" "$OUT/stage/${safe}-after-phase206"
done
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-after-phase206.c"

git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase206.config" "$BUILD/.config"
cmp "$OUT/stage/recorder-before-phase206.c" "$OUT/stage/recorder-after-phase206.c"

python3 - <<'PY' | tee "$OUT/logs/phase206-touchgrass-comparison.log"
import hashlib
import importlib.util
import json
from pathlib import Path
root = Path('gki/common')
tg = Path('workspace/touchgrass-a52xq')
out = Path('artifacts/a52xq-smmu-display-contracts/comparison')
core = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
hdr = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.h').read_text()
iommu_h = (root / 'include/linux/iommu.h').read_text()
dma = (root / 'drivers/iommu/dma-iommu.c').read_text()
msm = (root / 'drivers/a52_display/msm/msm_smmu.c').read_text()
tg_smmu = (tg / 'drivers/iommu/arm-smmu.c').read_text()
tg_dma = (tg / 'arch/arm64/mm/dma-mapping.c').read_text()
lagoon = (tg / 'arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi').read_text()

spec = importlib.util.spec_from_file_location(
    'phase205_dt', 'scripts/205_compare_post_smmu_touchgrass.py')
phase205_dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase205_dt)
boot = Path('artifacts/a52xq-apps-smmu-scm-handoff/package/boot.img')
dtb = phase205_dt.boot_dtb(boot)
nodes = phase205_dt.parse_fdt(dtb)
unsecure_nodes = phase205_dt.find_compat(nodes, 'qcom,smmu_sde_unsec')
secure_nodes = phase205_dt.find_compat(nodes, 'qcom,smmu_sde_sec')
assert len(unsecure_nodes) == 1, unsecure_nodes
assert len(secure_nodes) == 1, secure_nodes
unsecure_path, unsecure_props = unsecure_nodes[0]
secure_path, secure_props = secure_nodes[0]
unsecure_pool = phase205_dt.u32s(
    unsecure_props.get('qcom,iommu-dma-addr-pool'))
secure_pool = phase205_dt.u32s(
    secure_props.get('qcom,iommu-dma-addr-pool'))

for marker in ('DOMAIN_ATTR_EARLY_MAP', 'arm_smmu_enable_s1_translations',
               'qcom,iommu-earlymap', '~BIT(DOMAIN_ATTR_EARLY_MAP)',
               'cfg_to_smmu_domain(cfg)->attributes'):
    assert marker in core, marker
assert 'ARM_SMMU_SCTLR_TRE | ARM_SMMU_SCTLR_M' not in core
assert 'arm_smmu_enable_s1_translations' in tg_smmu
assert 'qcom,iommu-earlymap' in tg_smmu

for marker in ('a52_iommu_get_dma_window', 'qcom,iommu-dma-addr-pool',
               'of_read_number(ranges, naddr)',
               'iommu_dma_init_domain(domain, dma_base, size, dev)'):
    assert marker in dma, marker
assert 'arm_iommu_get_dma_window' in tg_dma
assert 'qcom,iommu-dma-addr-pool' in tg_dma
assert unsecure_pool == [0x00020000, 0xfffe0000], unsecure_pool
assert secure_pool == [0x00020000, 0xfffe0000], secure_pool
assert 'qcom,iommu-earlymap' in unsecure_props
assert 'non-fatal' in phase205_dt.strings(
    unsecure_props.get('qcom,iommu-faults'))
assert phase205_dt.u32s(secure_props.get('qcom,iommu-vmid')) == [10]
active_dt = {
    'dtb_sha256': hashlib.sha256(dtb).hexdigest(),
    'unsecure': {
        'path': unsecure_path,
        'properties': phase205_dt.compact(unsecure_props),
    },
    'secure': {
        'path': secure_path,
        'properties': phase205_dt.compact(secure_props),
    },
}
(out / 'phase206-active-display-dt.json').write_text(
    json.dumps(active_dt, indent=2, sort_keys=True) + '\n')

assert 'DOMAIN_ATTR_NON_FATAL_FAULTS' in iommu_h
assert 'BIT(DOMAIN_ATTR_NON_FATAL_FAULTS)' in core
assert 'qcom,iommu-faults' in core
assert 'panic(' not in core[core.find('static irqreturn_t arm_smmu_context_fault'):core.find('static irqreturn_t arm_smmu_global_fault')]

assert 'secure display SMMU is fail-closed' in msm
assert 'return ERR_PTR(-EOPNOTSUPP);' in msm
assert 'a52_unported_secure_display' in core
assert 'a52_arm_smmu_attach_fault' in core
assert 'S2CR_TYPE_FAULT' in core
assert 'SMMU secure-streams faulted dev=%s' in core
assert 'ret = a52_arm_smmu_attach_fault(dev, cfg, fwspec);' in core
assert core.find('ret = arm_smmu_init_domain_context(domain, smmu, dev);') < core.find('ret = a52_arm_smmu_attach_fault(dev, cfg, fwspec);')
assert 'DOMAIN_ATTR_SECURE_VMID' in tg_smmu
assert 'arm_smmu_assign_table' in tg_smmu

for marker in ('a52_apps_smmu_has_unmanaged_tbus', 'qcom,qsmmuv500-tbu',
               'runtime PM disabled until qsmmuv500 TBU support is ported',
               'system suspend blocked until qsmmuv500 TBU support is ported',
               'return -EBUSY;'):
    assert marker in core, marker
assert 'qsmmuv500_tbu_probe' in tg_smmu

report = {
    'status': 'phase206-touchgrass-contracts-pass',
    'touchgrass_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'early_map_semantics_ported': True,
    'early_map_sctlr_m_transition_ported': True,
    'display_iova_pool_parser_ported': True,
    'display_iova_base': f'0x{unsecure_pool[0]:08x}',
    'display_iova_size': f'0x{unsecure_pool[1]:08x}',
    'active_dtb_sha256': hashlib.sha256(dtb).hexdigest(),
    'non_fatal_fault_contract_ported': True,
    'upstream_fault_handler_already_nonfatal': True,
    'secure_vmid_backend_ported': False,
    'secure_display_fail_closed': True,
    'secure_display_default_domain_faulted': True,
    'secure_display_attach_returns_success': True,
    'tbu_backend_ported': False,
    'tbu_power_collapse_fail_closed': True,
    'runtime_pm_blocked_for_unmanaged_tbus': True,
    'system_suspend_blocked_for_unmanaged_tbus': True,
    'new_recorder_added': False,
}
(out / 'phase206-touchgrass-comparison.json').write_text(
    json.dumps(report, indent=2, sort_keys=True) + '\n')
print(json.dumps(report, indent=2, sort_keys=True))
PY

git -C "$ROOT" diff --binary --no-ext-diff -- \
  include/linux/iommu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/iommu/dma-iommu.c \
  drivers/a52_display/msm/msm_smmu.c \
  > "$OUT/stage/phase206-smmu-display-contracts.patch"
test -s "$OUT/stage/phase206-smmu-display-contracts.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase206-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase206.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase206-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase206-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase206-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase206-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for marker in \
  'qcom,iommu-earlymap' \
  'qcom,iommu-dma-addr-pool' \
  'SMMU secure-domain unavailable domain=%d' \
  'SMMU secure-streams faulted dev=%s' \
  'runtime PM disabled until qsmmuv500 TBU support is ported' \
  'system suspend blocked until qsmmuv500 TBU support is ported' \
  'SMMU parent-qcom scm=%d handoff=%d' \
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
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test \
  | tee "$OUT/logs/phase206-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test \
  | tee "$OUT/logs/phase206-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 206 display SMMU contracts

FLASH ONLY:
  package/boot.img -> BOOT partition

Implemented from the exact TouchGrass comparison:
  - qcom,iommu-earlymap state is applied before context-bank creation
  - SCTLR.M remains clear during bootloader handoff and is enabled when KMS clears EARLY_MAP
  - qcom,iommu-dma-addr-pool initializes the DMA IOVA aperture
  - qcom,iommu-faults = "non-fatal" is represented by the IOMMU attribute API
  - secure display default-domain streams are programmed to S2CR_TYPE_FAULT, while KMS secure domain creation fails closed because secure VMID/page-table ownership is not yet ported
  - Apps SMMU runtime PM and system suspend fail closed while qsmmuv500 TBU power support is absent

Normal unsecure display is the target of this candidate. Protected display and system suspend are intentionally unavailable rather than silently unsafe.

No new recorder, forced bind, IOMMU bypass, supplier relaxation, DTB change, DTBO change, ramdisk change, panel command change, timing change, regulator change, or clock-rate change is included.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-smmu-display-contracts')
base = json.loads(Path('artifacts/a52xq-apps-smmu-scm-handoff/final-audit.json').read_text())
comparison = json.loads((root / 'comparison/phase206-touchgrass-comparison.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-smmu-display-contracts-audited',
    'phase': 206,
    'base_phase': 204,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase204': 'display-smmu-contracts',
    'early_map_semantics_ported': comparison['early_map_semantics_ported'],
    'display_iova_pool_parser_ported': comparison['display_iova_pool_parser_ported'],
    'non_fatal_fault_contract_ported': comparison['non_fatal_fault_contract_ported'],
    'secure_display_fail_closed': comparison['secure_display_fail_closed'],
    'secure_display_default_domain_faulted': comparison['secure_display_default_domain_faulted'],
    'secure_display_attach_returns_success': comparison['secure_display_attach_returns_success'],
    'secure_vmid_backend_ported': comparison['secure_vmid_backend_ported'],
    'tbu_power_collapse_fail_closed': comparison['tbu_power_collapse_fail_closed'],
    'tbu_backend_ported': comparison['tbu_backend_ported'],
    'runtime_pm_blocked_for_unmanaged_tbus': True,
    'system_suspend_blocked_for_unmanaged_tbus': True,
    'normal_unsecure_display_target': True,
    'new_recorder_added': False,
    'forced_bind_added': False,
    'iommu_bypass_added': False,
    'supplier_dependency_relaxed': False,
    'dtb_changed': False,
    'dtbo_changed': False,
    'ramdisk_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'clock_rates_changed': False,
    'regulator_policy_changed': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in ('early_map_semantics_ported', 'display_iova_pool_parser_ported',
            'non_fatal_fault_contract_ported', 'secure_display_fail_closed',
            'secure_display_default_domain_faulted',
            'secure_display_attach_returns_success',
            'tbu_power_collapse_fail_closed', 'dtb_preserved',
            'ramdisk_preserved', 'recovery_dtbo_preserved'):
    assert base[key] is True, key
for key in ('secure_vmid_backend_ported', 'tbu_backend_ported',
            'new_recorder_added', 'forced_bind_added', 'iommu_bypass_added',
            'supplier_dependency_relaxed', 'dtb_changed', 'dtbo_changed',
            'ramdisk_changed', 'panel_commands_changed',
            'display_timing_changed', 'clock_rates_changed',
            'regulator_policy_changed'):
    assert base[key] is False, key
(root / 'final-audit.json').write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
