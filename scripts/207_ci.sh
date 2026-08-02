#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-smmu-display-contracts"
OUT="$PWD/artifacts/a52xq-secure-tbu-dependency-audit"
ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/206_ci.sh
rm -rf "$OUT"
mkdir -p "$OUT"/{gki,touchgrass,comparison,config,logs}

copy_file() {
  local base="$1" rel="$2" dst="$3"
  test -f "$base/$rel"
  mkdir -p "$OUT/$dst/$(dirname "$rel")"
  cp "$base/$rel" "$OUT/$dst/$rel"
}

for rel in \
  include/linux/io-pgtable.h \
  drivers/iommu/io-pgtable.c \
  drivers/iommu/io-pgtable-arm.c \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  a52-compat/include/soc/qcom/secure_buffer.h \
  drivers/a52_secure/secure_buffer.c \
  drivers/a52_display/msm/msm_smmu.c \
  drivers/a52_display/msm/sde/sde_kms.c; do
  copy_file "$ROOT" "$rel" gki
done

for rel in \
  include/linux/io-pgtable.h \
  drivers/iommu/io-pgtable.c \
  drivers/iommu/io-pgtable-arm.c \
  drivers/iommu/arm-smmu.c \
  include/soc/qcom/secure_buffer.h \
  drivers/soc/qcom/secure_buffer.c \
  arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi; do
  copy_file "$TG" "$rel" touchgrass
done

cp "$BASE_OUT/config/final.config" "$OUT/config/phase206-final.config"
cp "$BASE_OUT/package/boot.img" "$OUT/comparison/phase206-boot.img"
cp "$BASE_OUT/comparison/phase206-active-display-dt.json" "$OUT/comparison/"
cp "$BASE_OUT/comparison/phase206-touchgrass-comparison.json" "$OUT/comparison/"

python3 - <<'PY' | tee "$OUT/logs/phase207-dependency-audit.log"
import importlib.util
import json
import re
from pathlib import Path

out = Path('artifacts/a52xq-secure-tbu-dependency-audit')
gki = out / 'gki'
tg = out / 'touchgrass'
config = (out / 'config/phase206-final.config').read_text()

def read(base, rel):
    return (base / rel).read_text(errors='replace')

G = {
    'iopg_h': read(gki, 'include/linux/io-pgtable.h'),
    'iopg_c': read(gki, 'drivers/iommu/io-pgtable.c'),
    'armpt': read(gki, 'drivers/iommu/io-pgtable-arm.c'),
    'smmu_h': read(gki, 'drivers/iommu/arm/arm-smmu/arm-smmu.h'),
    'smmu': read(gki, 'drivers/iommu/arm/arm-smmu/arm-smmu.c'),
    'sec_h': read(gki, 'a52-compat/include/soc/qcom/secure_buffer.h'),
    'sec': read(gki, 'drivers/a52_secure/secure_buffer.c'),
    'msm_smmu': read(gki, 'drivers/a52_display/msm/msm_smmu.c'),
    'kms': read(gki, 'drivers/a52_display/msm/sde/sde_kms.c'),
}
T = {
    'iopg_h': read(tg, 'include/linux/io-pgtable.h'),
    'iopg_c': read(tg, 'drivers/iommu/io-pgtable.c'),
    'armpt': read(tg, 'drivers/iommu/io-pgtable-arm.c'),
    'smmu': read(tg, 'drivers/iommu/arm-smmu.c'),
    'sec_h': read(tg, 'include/soc/qcom/secure_buffer.h'),
    'sec': read(tg, 'drivers/soc/qcom/secure_buffer.c'),
    'lagoon': read(tg, 'arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi'),
}

spec = importlib.util.spec_from_file_location(
    'phase205_dt', 'scripts/205_compare_post_smmu_touchgrass.py')
phase205_dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase205_dt)
boot = out / 'comparison/phase206-boot.img'
dtb = phase205_dt.boot_dtb(boot)
nodes = phase205_dt.parse_fdt(dtb)
tbus = phase205_dt.find_compat(nodes, 'qcom,qsmmuv500-tbu')
secure = phase205_dt.find_compat(nodes, 'qcom,smmu_sde_sec')
assert secure and phase205_dt.u32s(secure[0][1].get('qcom,iommu-vmid')) == [10]

secure_checks = {
    'target_hyp_assign_phys_declared': 'hyp_assign_phys' in G['sec_h'],
    'target_hyp_assign_phys_implemented': 'int hyp_assign_phys(' in G['sec'],
    'target_secure_buffer_configured': 'CONFIG_QCOM_SECURE_BUFFER=y' in config or 'CONFIG_QCOM_SECURE_BUFFER=' in config or 'secure_buffer.o' in G['sec'],
    'touchgrass_secure_vmid_attr': 'DOMAIN_ATTR_SECURE_VMID' in T['smmu'],
    'touchgrass_assign_table': 'arm_smmu_assign_table' in T['smmu'],
    'touchgrass_unassign_table': 'arm_smmu_unassign_table' in T['smmu'],
    'touchgrass_pgtable_prepare': 'arm_smmu_prepare_pgtable' in T['smmu'],
    'touchgrass_custom_pgtable_alloc': 'arm_smmu_alloc_pages_exact' in T['smmu'],
    'target_secure_vmid_field': 'secure_vmid' in G['smmu_h'],
    'target_assign_table': 'arm_smmu_assign_table' in G['smmu'],
    'target_pgtable_alloc_hook': ('alloc_pages_exact' in G['iopg_h'] or
                                  'alloc_pages' in G['iopg_h']),
    'target_pgtable_free_hook': ('free_pages_exact' in G['iopg_h'] or
                                 'free_pages' in G['iopg_h']),
    'active_secure_vmid': 10,
}

tbu_props = []
for path, props in tbus:
    tbu_props.append({
        'path': path,
        'properties': phase205_dt.compact(props),
    })

tbu_checks = {
    'active_tbu_count': len(tbus),
    'touchgrass_tbu_probe': 'qsmmuv500_tbu_probe' in T['smmu'],
    'touchgrass_tbu_remove': 'qsmmuv500_tbu_remove' in T['smmu'],
    'touchgrass_tbu_halt': 'qsmmuv500_tbu_halt' in T['smmu'],
    'touchgrass_tbu_resume': 'qsmmuv500_tbu_resume' in T['smmu'],
    'touchgrass_hard_translate': 'qsmmuv500_iova_to_phys_hard' in T['smmu'],
    'touchgrass_power_resources': 'msm_bus_scale_register_client' in T['smmu'] or 'devm_regulator_get' in T['smmu'],
    'target_tbu_probe': 'qsmmuv500_tbu_probe' in G['smmu'],
    'target_tbu_halt': 'qsmmuv500_tbu_halt' in G['smmu'],
    'target_tbu_resume': 'qsmmuv500_tbu_resume' in G['smmu'],
    'target_tbu_containment': 'a52_apps_smmu_has_unmanaged_tbus' in G['smmu'],
}

assert secure_checks['target_hyp_assign_phys_declared']
assert secure_checks['target_hyp_assign_phys_implemented']
assert secure_checks['touchgrass_secure_vmid_attr']
assert secure_checks['touchgrass_assign_table']
assert not secure_checks['target_secure_vmid_field']
assert not secure_checks['target_assign_table']
assert tbu_checks['active_tbu_count'] > 0
assert tbu_checks['touchgrass_tbu_probe']
assert not tbu_checks['target_tbu_probe']
assert tbu_checks['target_tbu_containment']

report = {
    'status': 'phase207-exact-dependency-audit-pass',
    'phase206_base': True,
    'touchgrass_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'secure_vmid': secure_checks,
    'tbu': tbu_checks,
    'active_tbus': tbu_props,
    'secure_next_phase': {
        'requires_io_pgtable_allocator_extension': not secure_checks['target_pgtable_alloc_hook'],
        'requires_domain_secure_vmid_state': not secure_checks['target_secure_vmid_field'],
        'requires_assign_unassign_lifecycle': not secure_checks['target_assign_table'],
        'secure_buffer_backend_already_present': True,
    },
    'tbu_next_phase': {
        'requires_platform_driver': not tbu_checks['target_tbu_probe'],
        'requires_power_resource_port': True,
        'requires_sid_range_binding': True,
        'requires_halt_resume': True,
        'requires_suspend_resume_validation': True,
    },
    'new_recorder_added': False,
    'kernel_behavior_changed': False,
}
(out / 'comparison/phase207-dependency-report.json').write_text(
    json.dumps(report, indent=2, sort_keys=True) + '\n')
print(json.dumps(report, indent=2, sort_keys=True))
PY

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 Phase 207 exact secure-VMID and QSMMUv500-TBU dependency audit

This artifact is non-flashable. It contains the exact pinned Phase 206 GKI files, exact TouchGrass comparison files, active Samsung DT data, and a machine-readable dependency report.

No kernel behavior, recorder, DTB, DTBO, panel, clock, regulator, or power policy is changed by this phase.
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
