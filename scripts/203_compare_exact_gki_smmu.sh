#!/usr/bin/env bash
set -Eeuo pipefail

GKI="${1:?GKI source path required}"
TG="${2:?TouchGrass source path required}"
P202="${3:?Phase 202 artifact path required}"
OUT="${4:?Output path required}"
EXPECTED_GKI_SHA=f960ed27302b1ff8e61e152fc202554d778deccd

actual_sha="$(git -C "$GKI" rev-parse HEAD)"
test "$actual_sha" = "$EXPECTED_GKI_SHA"

mkdir -p "$OUT"/{gki-files,logs,phase202}
git -C "$GKI" show -s --format=fuller HEAD > "$OUT/GKI-COMMIT.txt"

for rel in \
  drivers/iommu/arm/Kconfig \
  drivers/iommu/arm/Makefile \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c \
  drivers/iommu/arm/arm-smmu/arm-smmu-qcom.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.h; do
  src="$GKI/$rel"
  if [ -f "$src" ]; then
    dst="$OUT/gki-files/$rel"
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
  fi
done

git -C "$GKI" grep -n -I -E \
  'qcom,qsmmu-v500|qcom,[A-Za-z0-9_-]*smmu|arm_smmu_of_match|qcom_smmu_impl_init|ARM_SMMU_QCOM|platform_driver' \
  -- drivers/iommu/arm \
  > "$OUT/logs/gki-smmu-driver-hits.txt" || true

git -C "$TG" grep -n -I -E \
  'qcom,qsmmu-v500|qcom,[A-Za-z0-9_-]*smmu|arm_smmu_of_match|qsmmuv500_arch_ops|platform_driver' \
  -- drivers/iommu/arm-smmu.c \
  > "$OUT/logs/touchgrass-exact-smmu-driver-hits.txt" || true

grep -nE 'CONFIG_.*(SMMU|IOMMU)' "$P202/config/final.config" \
  > "$OUT/phase202/all-smmu-iommu-config.txt" || true

grep -nE 'CONFIG_.*(SMMU|IOMMU)' "$TG/arch/arm64/configs/a52xq_defconfig" \
  > "$OUT/logs/touchgrass-a52xq-all-smmu-iommu-config.txt" || true

grep -nE 'CONFIG_.*(SMMU|IOMMU)' "$TG/arch/arm64/configs/vendor/a52xq_eur_open_defconfig" \
  > "$OUT/logs/touchgrass-a52xq-eur-all-smmu-iommu-config.txt" || true

python3 - "$GKI" "$TG" "$P202" "$OUT" <<'PY'
from pathlib import Path
import re, sys

gki, tg, p202, out = map(Path, sys.argv[1:])

def read(path):
    return path.read_text(errors='replace') if path.is_file() else ''

tg_dts = read(tg / 'arch/arm64/boot/dts/vendor/qcom/msm-arm-smmu-lagoon.dtsi')
tg_drv = read(tg / 'drivers/iommu/arm-smmu.c')
gki_core = read(gki / 'drivers/iommu/arm/arm-smmu/arm-smmu.c')
gki_qcom = read(gki / 'drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c')
gki_kconfig = read(gki / 'drivers/iommu/arm/Kconfig')
p202_cfg = read(p202 / 'config/final.config')
tg_cfg = read(tg / 'arch/arm64/configs/a52xq_defconfig')

compat_m = re.search(r'apps_smmu:\s*apps-smmu@15000000\s*\{.*?compatible\s*=\s*"([^"]+)"', tg_dts, re.S)
compat = compat_m.group(1) if compat_m else 'NOT FOUND'

def enabled(text, name):
    if f'{name}=y' in text:
        return 'y'
    if f'{name}=m' in text:
        return 'm'
    if f'# {name} is not set' in text:
        return 'not set'
    return 'absent'

gki_all = gki_core + '\n' + gki_qcom
strings = sorted(set(re.findall(r'\.compatible\s*=\s*"([^"]*smmu[^"]*)"', gki_all, re.I)))
key = [
    'Phase 203 key findings: exact TouchGrass versus pinned GKI Apps SMMU',
    '',
    f'TouchGrass Apps SMMU DT compatible: {compat}',
    f'TouchGrass driver matches qcom,qsmmu-v500: {"yes" if "qcom,qsmmu-v500" in tg_drv else "no"}',
    f'Pinned GKI driver matches qcom,qsmmu-v500: {"yes" if "qcom,qsmmu-v500" in gki_all else "no"}',
    f'Pinned GKI contains Qualcomm SMMU implementation source: {"yes" if gki_qcom else "no"}',
    '',
    'Configuration:',
    f'  TouchGrass CONFIG_ARM_SMMU: {enabled(tg_cfg, "CONFIG_ARM_SMMU")}',
    f'  Phase 202 CONFIG_ARM_SMMU: {enabled(p202_cfg, "CONFIG_ARM_SMMU")}',
    f'  Phase 202 CONFIG_ARM_SMMU_QCOM: {enabled(p202_cfg, "CONFIG_ARM_SMMU_QCOM")}',
    f'  Phase 202 CONFIG_ARM_SMMU_LEGACY_DT_BINDINGS: {enabled(p202_cfg, "CONFIG_ARM_SMMU_LEGACY_DT_BINDINGS")}',
    f'  Phase 202 CONFIG_QCOM_IOMMU: {enabled(p202_cfg, "CONFIG_QCOM_IOMMU")}',
    '',
    'Pinned GKI SMMU compatible strings:',
]
key.extend(f'  {s}' for s in strings)
key.extend([
    '',
    'Interpretation:',
    '  If the pinned GKI list does not contain qcom,qsmmu-v500, the existing',
    '  TouchGrass DT node cannot bind to the GKI ARM SMMU platform driver.',
    '  CONFIG_ARM_SMMU=y alone is therefore insufficient.',
])
(out / 'KEY-FINDINGS.txt').write_text('\n'.join(key) + '\n')

# Extract compact match/config evidence for review.
def selected_lines(text, patterns):
    result=[]
    for i,line in enumerate(text.splitlines(),1):
        if any(re.search(p,line,re.I) for p in patterns):
            result.append(f'{i}: {line}')
    return result

ev = ['Exact evidence', '', 'TouchGrass driver:']
ev += selected_lines(tg_drv, [r'qcom,qsmmu-v500', r'qcom_smmuv500', r'arm_smmu_of_match'])
ev += ['', 'Pinned GKI core driver:']
ev += selected_lines(gki_core, [r'qcom,qsmmu-v500', r'arm_smmu_of_match', r'qcom_smmu'])
ev += ['', 'Pinned GKI Qualcomm implementation:']
ev += selected_lines(gki_qcom, [r'\.compatible', r'qcom_smmu_impl_init', r'qsmmu'])
ev += ['', 'Pinned GKI Kconfig:']
ev += selected_lines(gki_kconfig, [r'ARM_SMMU', r'QCOM'])
(out / 'EXACT-MATCH-EVIDENCE.txt').write_text('\n'.join(ev) + '\n')

comment = [
    '## Exact GKI source comparison',
    '',
    '```text',
    *(out / 'KEY-FINDINGS.txt').read_text(errors='replace').splitlines(),
    '```',
    '',
    'The artifact includes the pinned GKI ARM SMMU core, Qualcomm implementation, Kconfig, TouchGrass downstream driver, Lagoon DTS context, final Phase 202 config, and checksums.',
]
(out / 'PR-COMMENT-V2.md').write_text('\n'.join(comment) + '\n')
PY

test -s "$OUT/KEY-FINDINGS.txt"
test -s "$OUT/EXACT-MATCH-EVIDENCE.txt"
test -s "$OUT/PR-COMMENT-V2.md"

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
