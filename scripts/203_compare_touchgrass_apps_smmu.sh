#!/usr/bin/env bash
set -Eeuo pipefail

TG="${1:?TouchGrass source path required}"
P202="${2:?Phase 202 artifact path required}"
OUT="${3:?Output path required}"

rm -rf "$OUT"
mkdir -p "$OUT"/{touchgrass-files,phase202,logs}

git -C "$TG" show -s --format=fuller HEAD > "$OUT/TOUCHGRASS-COMMIT.txt"
cp "$P202/config/final.config" "$OUT/phase202/final.config"
cp "$P202/final-audit.json" "$OUT/phase202/final-audit.json"

git -C "$TG" grep -n -I -E \
  '15000000|apps[-_]?smmu|apps_smmu|qcom,[A-Za-z0-9_-]*smmu|arm,smmu|ARM_SMMU|QCOM_IOMMU' \
  -- arch/arm64/boot/dts arch/arm64/configs drivers/iommu drivers/firmware drivers/soc \
  > "$OUT/logs/touchgrass-all-hits.txt" || true

git -C "$TG" grep -n -I -E \
  '15000000|apps[-_]?smmu|apps_smmu' \
  -- arch/arm64/boot/dts \
  > "$OUT/logs/touchgrass-apps-smmu-dts-hits.txt" || true

git -C "$TG" grep -n -I -E \
  'arm_smmu_of_match|qcom.*smmu|smmu.*of_match|platform_driver.*smmu' \
  -- drivers/iommu \
  > "$OUT/logs/touchgrass-driver-match-hits.txt" || true

grep -nE '^(CONFIG_(ARM_SMMU|ARM_SMMU_V3|IOMMU_SUPPORT|OF_IOMMU|QCOM_IOMMU|QCOM_SMMU|IOMMU_IO_PGTABLE|IOMMU_DEFAULT_PASSTHROUGH|IOMMU_DEFAULT_DMA_STRICT|IOMMU_DEFAULT_DMA_LAZY|IOMMU_DEBUGFS|IOMMU_IOVA))=' \
  "$P202/config/final.config" > "$OUT/phase202/iommu-config.txt" || true

find "$TG/arch/arm64/configs" -type f -print0 | sort -z | \
  xargs -0 grep -HnE '^(CONFIG_(ARM_SMMU|ARM_SMMU_V3|IOMMU_SUPPORT|OF_IOMMU|QCOM_IOMMU|QCOM_SMMU|IOMMU_IO_PGTABLE|IOMMU_DEFAULT_PASSTHROUGH|IOMMU_DEFAULT_DMA_STRICT|IOMMU_DEFAULT_DMA_LAZY|IOMMU_DEBUGFS|IOMMU_IOVA))=' \
  > "$OUT/logs/touchgrass-iommu-config-hits.txt" || true

python3 - "$TG" "$OUT" <<'PY'
from pathlib import Path
import re, shutil, sys

tg = Path(sys.argv[1])
out = Path(sys.argv[2])
hit_file = out / 'logs/touchgrass-apps-smmu-dts-hits.txt'
driver_file = out / 'logs/touchgrass-driver-match-hits.txt'
lines = hit_file.read_text(errors='replace').splitlines() if hit_file.exists() else []
paths = []
for line in lines:
    m = re.match(r'([^:]+):(\d+):(.*)', line)
    if m and m.group(1) not in paths:
        paths.append(m.group(1))

contexts = []
for rel in paths:
    src = tg / rel
    if not src.is_file():
        continue
    dst = out / 'touchgrass-files' / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    text = src.read_text(errors='replace').splitlines()
    hit_nums = []
    for line in lines:
        m = re.match(rf'{re.escape(rel)}:(\d+):', line)
        if m:
            hit_nums.append(int(m.group(1)))
    for n in hit_nums:
        start = max(1, n - 24)
        end = min(len(text), n + 45)
        contexts.append(f'===== {rel}:{start}-{end} (hit {n}) =====')
        contexts.extend(f'{i:6d}: {text[i-1]}' for i in range(start, end + 1))
        contexts.append('')
(out / 'TOUCHGRASS-APPS-SMMU-DTS-CONTEXT.txt').write_text('\n'.join(contexts) + '\n')

driver_lines = driver_file.read_text(errors='replace').splitlines() if driver_file.exists() else []
driver_paths = []
for line in driver_lines:
    m = re.match(r'([^:]+):(\d+):(.*)', line)
    if m and m.group(1) not in driver_paths:
        driver_paths.append(m.group(1))
for rel in driver_paths:
    src = tg / rel
    if src.is_file():
        dst = out / 'touchgrass-files' / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

p202_cfg = (out / 'phase202/iommu-config.txt').read_text(errors='replace')
tg_cfg = (out / 'logs/touchgrass-iommu-config-hits.txt').read_text(errors='replace')
dts_hits = hit_file.read_text(errors='replace') if hit_file.exists() else ''
drv_hits = driver_file.read_text(errors='replace') if driver_file.exists() else ''

report = [
    'Phase 203 exact TouchGrass vs Phase 202 Apps SMMU comparison',
    '',
    'TouchGrass commit:',
    '  6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    '',
    'Phase 202 artifact:',
    '  8823289525',
    '',
    'TouchGrass Apps SMMU DTS hits:',
    dts_hits or '  NONE',
    '',
    'TouchGrass ARM/Qualcomm SMMU driver-match hits:',
    drv_hits or '  NONE',
    '',
    'Phase 202 final IOMMU config:',
    p202_cfg or '  NONE',
    '',
    'TouchGrass defconfig IOMMU options:',
    tg_cfg or '  NONE',
]
(out / 'COMPARISON-REPORT.txt').write_text('\n'.join(report) + '\n')

comment = [
    '## Automated exact comparison completed',
    '',
    'The workflow compared pinned TouchGrass commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7` with audited Phase 202 artifact `8823289525`.',
    '',
    '```text',
]
comment.extend((out / 'COMPARISON-REPORT.txt').read_text(errors='replace').splitlines()[:220])
comment.extend(['```', '', 'The complete artifact includes copied DTS and SMMU driver source files plus full grep results and checksums.'])
(out / 'PR-COMMENT.md').write_text('\n'.join(comment) + '\n')
PY

test -s "$OUT/COMPARISON-REPORT.txt"
test -s "$OUT/TOUCHGRASS-APPS-SMMU-DTS-CONTEXT.txt"
test -s "$OUT/logs/touchgrass-driver-match-hits.txt"
test -s "$OUT/PR-COMMENT.md"

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
