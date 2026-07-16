#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
LABEL="a52xq-p1-full-hardware-diag-$TARGET_VERSION"
OUT="$ARTIFACTS_DIR/p1-full-hardware-diag"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION source"
[[ -x "$KERNEL_DIR/scripts/config" ]] || fail "scripts/config is missing"
[[ -s "$DEFCONFIG" ]] || fail "a52xq_defconfig is missing"
mkdir -p "$OUT"
cp "$DEFCONFIG" "$OUT/a52xq_defconfig.before"

# Keep the complete A52/Lagoon hardware configuration. The previous P0 build
# removed display, KGSL, audio, media, WLAN, Bluetooth and the entire
# touchscreen subsystem. This diagnostic build intentionally does not repeat
# those reductions. Only the active STMicroelectronics FTS implementation is
# disabled.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \
  -d TOUCHSCREEN_STM_FTS5CU56A \
  -e PRINTK -e PRINTK_TIME -e IKCONFIG -e IKCONFIG_PROC \
  -e DEBUG_KERNEL -e FRAME_POINTER -e PANIC_ON_OOPS \
  -e PSTORE -e PSTORE_RAM -e PSTORE_CONSOLE -e PSTORE_PMSG \
  -e SERIAL_MSM_GENI -e SERIAL_MSM_GENI_CONSOLE \
  -e ARCH_QCOM -e ARCH_LAGOON \
  -e QCOM_SCM -e QCOM_RPMH -e QCOM_SMEM -e QCOM_SMP2P \
  -e QCOM_COMMAND_DB -e COMMON_CLK_QCOM -e SDM_GCC_LAGOON \
  -e PINCTRL -e PINCTRL_MSM -e PINCTRL_LAGOON \
  -e REGULATOR -e REGULATOR_QCOM_RPMH \
  -e IOMMU_SUPPORT -e ARM_SMMU -e QTI_IOMMU_SUPPORT \
  -e SCSI -e SCSI_UFSHCD -e SCSI_UFSHCD_PLATFORM -e SCSI_UFS_QCOM \
  -e PHY_QCOM_UFS \
  -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT \
  -e EXT4_FS -e F2FS_FS -e FS_ENCRYPTION \
  -e SECURITY -e SECURITY_SELINUX -e ANDROID_BINDER_IPC \
  -e INPUT -e INPUT_TOUCHSCREEN -e INPUT_MISC -e INPUT_QPNP_POWER_ON \
  -e DRM -e QCOM_KGSL -e QCOM_KGSL_IOMMU \
  -e MEDIA_SUPPORT -e SOUND -e SND \
  -e WLAN -e CFG80211 -e MAC80211 -e BT

# PANIC_TIMEOUT is numeric and scripts/config accepts --set-val for it.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --set-val PANIC_TIMEOUT 10

cp "$DEFCONFIG" "$OUT/a52xq_defconfig.requested"
build_kernel "$LABEL"

FINAL_CONFIG="$ARTIFACTS_DIR/config-$LABEL"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-$LABEL"
IMAGE_GZ="$OUT/Image.gz"
cp "$FINAL_CONFIG" "$OUT/final.config"
cp "$FINAL_IMAGE" "$OUT/Image"
gzip -n -9 -c "$FINAL_IMAGE" > "$IMAGE_GZ"

# Export symbolized diagnostics for any future panic trace.
cp "$KERNEL_DIR/out/System.map" "$OUT/System.map" 2>/dev/null || true
cp "$KERNEL_DIR/out/vmlinux" "$OUT/vmlinux" 2>/dev/null || true
cp "$KERNEL_DIR/out/Module.symvers" "$OUT/Module.symvers" 2>/dev/null || true

required_y=(
  ARCH_QCOM ARCH_LAGOON QCOM_SCM QCOM_RPMH QCOM_SMEM QCOM_SMP2P
  QCOM_COMMAND_DB COMMON_CLK_QCOM SDM_GCC_LAGOON PINCTRL_LAGOON
  REGULATOR_QCOM_RPMH ARM_SMMU SCSI_UFSHCD SCSI_UFSHCD_PLATFORM
  SCSI_UFS_QCOM PHY_QCOM_UFS BLK_DEV_INITRD DEVTMPFS EXT4_FS F2FS_FS
  FS_ENCRYPTION SECURITY_SELINUX ANDROID_BINDER_IPC INPUT INPUT_TOUCHSCREEN
  INPUT_QPNP_POWER_ON DRM QCOM_KGSL MEDIA_SUPPORT SOUND SND WLAN CFG80211
  MAC80211 BT PRINTK PRINTK_TIME DEBUG_KERNEL FRAME_POINTER PANIC_ON_OOPS
  PSTORE PSTORE_RAM PSTORE_CONSOLE PSTORE_PMSG SERIAL_MSM_GENI
  SERIAL_MSM_GENI_CONSOLE
)

missing=0
: > "$OUT/required-builtins.txt"
for symbol in "${required_y[@]}"; do
  if grep -Fxq "CONFIG_${symbol}=y" "$FINAL_CONFIG"; then
    printf 'CONFIG_%s=y\n' "$symbol" >> "$OUT/required-builtins.txt"
  else
    printf 'MISSING CONFIG_%s=y\n' "$symbol" | tee -a "$OUT/required-builtins.txt" >&2
    missing=1
  fi
done
[[ "$missing" == 0 ]] || fail "One or more required full-hardware built-ins were not retained"

# The narrow experiment: retain the touchscreen framework and all unrelated
# touch drivers, but prove that the active FTS implementation is disabled.
grep -Fxq '# CONFIG_TOUCHSCREEN_STM_FTS5CU56A is not set' "$FINAL_CONFIG" \
  || fail "FTS touchscreen driver remained enabled"
grep -Fxq 'CONFIG_INPUT_TOUCHSCREEN=y' "$FINAL_CONFIG" \
  || fail "Touchscreen framework was unexpectedly disabled"

{
  echo "active_touch_driver=stm,fts_touch"
  echo "disabled_symbol=CONFIG_TOUCHSCREEN_STM_FTS5CU56A"
  grep -E '^(CONFIG_INPUT_TOUCHSCREEN=|CONFIG_TOUCHSCREEN_STM_FTS5CU56A=|# CONFIG_TOUCHSCREEN_STM_FTS5CU56A is not set)' "$FINAL_CONFIG"
} > "$OUT/touchscreen-audit.txt"

# Record all differences from the source tree's original device defconfig.
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
        lines.append(f'{key}\n  before: {before.get(key, "<absent>")}\n  after:  {after.get(key, "<absent>")}')
Path(sys.argv[3]).write_text('\n'.join(lines) + ('\n' if lines else ''))
PY

raw_bytes=$(stat -c %s "$FINAL_IMAGE")
gz_bytes=$(stat -c %s "$IMAGE_GZ")
(( gz_bytes < 80 * 1024 * 1024 )) || fail "Compressed kernel exceeds conservative boot payload cap"

{
  echo "status=diagnostic-build-passed"
  echo "flashable=no"
  echo "hardware_validated=no"
  echo "kernel_release=$(make -s -C "$KERNEL_DIR" O="$KERNEL_DIR/out" ARCH=arm64 kernelrelease)"
  echo "raw_image_bytes=$raw_bytes"
  echo "image_gz_bytes=$gz_bytes"
  echo "image_sha256=$(sha256sum "$FINAL_IMAGE" | awk '{print $1}')"
  echo "image_gz_sha256=$(sha256sum "$IMAGE_GZ" | awk '{print $1}')"
  echo "touch_policy=disable-only-stm-fts"
  echo "display_stack=retained"
  echo "kgsl_stack=retained"
  echo "audio_media_wireless=retained"
  echo "panic_timeout=10"
  echo "boot_ramdisk_policy=preserve-original-during-later-repack"
  echo "boot_dtb_policy=preserve-original-during-later-repack"
} | tee "$OUT/metadata.txt"

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

cat > "$OUT/NOTICE.txt" <<'EOF'
NON-FLASHABLE DIAGNOSTIC KERNEL ARTIFACT.

This build keeps the full A52/Lagoon hardware stack and disables only the
active STMicroelectronics FTS touchscreen driver. It must still be repacked
into the checksum-locked original boot image and audited before hardware use.
EOF
