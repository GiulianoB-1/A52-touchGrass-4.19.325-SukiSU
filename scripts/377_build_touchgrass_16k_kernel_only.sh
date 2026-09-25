#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.206
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
LABEL="a52xq-linux-4.19.206-16k-kernel-only"
OUT="$ARTIFACTS_DIR/phase377-touchgrass-16k-kernel-only"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION source"
[[ -x "$KERNEL_DIR/scripts/config" ]] || fail "scripts/config is missing"
[[ -s "$DEFCONFIG" ]] || fail "a52xq_defconfig is missing"

rm -rf "$OUT"
mkdir -p "$OUT"
cp "$DEFCONFIG" "$OUT/a52xq_defconfig.before"

# Phase 377 changes only the ARM64 MMU page geometry.
# No userspace properties, no KernelSU property spoofing, no boot image repack.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG"   -d ARM64_4K_PAGES   -e ARM64_16K_PAGES   -d ARM64_64K_PAGES   -d ARM64_VA_BITS_36   -d ARM64_VA_BITS_39   -d ARM64_VA_BITS_42   -e ARM64_VA_BITS_47   -d ARM64_VA_BITS_48

cp "$DEFCONFIG" "$OUT/a52xq_defconfig.requested"

# Build from the normal a52xq defconfig. Kconfig must derive:
# PAGE_SHIFT=14, VA_BITS=47, PGTABLE_LEVELS=3.
build_kernel "$LABEL"

FINAL_CONFIG="$ARTIFACTS_DIR/config-$LABEL"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-$LABEL"

cp "$FINAL_CONFIG" "$OUT/final.config"
cp "$FINAL_IMAGE" "$OUT/Image"
cp "$ARTIFACTS_DIR/Image-$LABEL.sha256" "$OUT/Image.sha256"
cp "$LOG_DIR/build-$LABEL.log" "$OUT/build.log"

assert_cfg() {
  local line="$1"
  grep -Fxq "$line" "$FINAL_CONFIG" || fail "Missing expected final config: $line"
}

assert_cfg '# CONFIG_ARM64_4K_PAGES is not set'
assert_cfg 'CONFIG_ARM64_16K_PAGES=y'
assert_cfg '# CONFIG_ARM64_64K_PAGES is not set'
assert_cfg 'CONFIG_ARM64_PAGE_SHIFT=14'
assert_cfg 'CONFIG_ARM64_VA_BITS_47=y'
assert_cfg 'CONFIG_ARM64_VA_BITS=47'
assert_cfg 'CONFIG_PGTABLE_LEVELS=3'

if grep -Fxq 'CONFIG_ARM64_VA_BITS_39=y' "$FINAL_CONFIG"; then
  fail "4K VA_BITS_39 unexpectedly survived"
fi
if grep -Fxq 'CONFIG_ARM64_PAGE_SHIFT=12' "$FINAL_CONFIG"; then
  fail "PAGE_SHIFT=12 unexpectedly survived"
fi

# Record likely page-size assumptions for follow-up review.
# These are findings, not automatic failures because 4K constants can represent
# hardware/IOMMU granules rather than Linux PAGE_SIZE.
{
  echo "=== explicit PAGE_SIZE / PAGE_SHIFT comparisons ==="
  grep -RInE     --exclude-dir=.git --exclude-dir=out     '(PAGE_SIZE[[:space:]]*(==|!=|<=|>=|<|>)[[:space:]]*4096|4096[[:space:]]*(==|!=|<=|>=|<|>)[[:space:]]*PAGE_SIZE|PAGE_SHIFT[[:space:]]*(==|!=)[[:space:]]*12|12[[:space:]]*(==|!=)[[:space:]]*PAGE_SHIFT)'     "$KERNEL_DIR/arch" "$KERNEL_DIR/drivers" "$KERNEL_DIR/fs" "$KERNEL_DIR/mm" "$KERNEL_DIR/include"     2>/dev/null || true

  echo
  echo "=== selected downstream 0x1000 / SZ_4K references ==="
  for d in     "$KERNEL_DIR/drivers/gpu"     "$KERNEL_DIR/drivers/iommu"     "$KERNEL_DIR/drivers/dma-buf"     "$KERNEL_DIR/drivers/scsi"     "$KERNEL_DIR/drivers/media"     "$KERNEL_DIR/drivers/android"; do
    [[ -d "$d" ]] || continue
    grep -RInE       --exclude-dir=.git --exclude-dir=out       '(0x1000|SZ_4K)' "$d" 2>/dev/null || true
  done
} > "$OUT/page-size-source-audit.txt"

{
  echo "phase=377"
  echo "scope=kernel-only"
  echo "kernel_version=$(kernel_version)"
  echo "page_size_bytes=16384"
  echo "page_shift=14"
  echo "va_bits=47"
  echo "pgtable_levels=3"
  echo "userspace_changed=no"
  echo "kernel_property_spoofing=no"
  echo "boot_image_repacked=no"
  echo "flashable=no"
  echo "hardware_validated=no"
  echo "image_sha256=$(sha256sum "$FINAL_IMAGE" | awk '{print $1}')"
} > "$OUT/metadata.txt"

python3 - "$OUT/a52xq_defconfig.before" "$FINAL_CONFIG" "$OUT/config-diff.txt" <<'PY'
from pathlib import Path
import sys

def parse(path):
    out = {}
    for line in Path(path).read_text(errors='replace').splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key = line.split('=', 1)[0]
            out[key] = line
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            key = line[len('# '):].split(' ', 1)[0]
            out[key] = line
    return out

before = parse(sys.argv[1])
after = parse(sys.argv[2])
lines = []
for key in sorted(set(before) | set(after)):
    if before.get(key) != after.get(key):
        lines.append(
            f'{key}\n'
            f'  before: {before.get(key, "<absent>")}\n'
            f'  after:  {after.get(key, "<absent>")}'
        )
Path(sys.argv[3]).write_text('\n'.join(lines) + ('\n' if lines else ''))
PY

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 PHASE 377 - TOUCHGRASS 16K KERNEL-ONLY COMPILE PROBE

This artifact changes only the ARM64 Linux page geometry:
  CONFIG_ARM64_16K_PAGES=y
  CONFIG_ARM64_PAGE_SHIFT=14
  CONFIG_ARM64_VA_BITS_47=y
  CONFIG_ARM64_VA_BITS=47
  CONFIG_PGTABLE_LEVELS=3

It intentionally does NOT modify Android/UN1CA userspace, properties, ELF
alignment, KernelSU module behavior, or the boot image.

This phase is compile/audit-only and is NOT FLASHABLE.
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

echo "Phase 377 16K kernel-only build passed."
