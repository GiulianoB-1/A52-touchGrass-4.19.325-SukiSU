#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
LABEL="a52xq-p0-boot-probe-$TARGET_VERSION"
OUT="$ARTIFACTS_DIR/p0-boot-probe"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION source"
[[ -x "$KERNEL_DIR/scripts/config" ]] || fail "scripts/config is missing"
[[ -s "$DEFCONFIG" ]] || fail "a52xq_defconfig is missing"
mkdir -p "$OUT"
cp "$DEFCONFIG" "$OUT/a52xq_defconfig.before"

# Keep the complete Lagoon/A52 platform chain and the minimum storage,
# initramfs, Android, security and persistent-log facilities needed to produce
# useful evidence. These remain built-in because no early module-loading path
# has been proven on this device.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \
  -d KSU -d KSU_SUSFS -d KPROBES \
  -e ARCH_QCOM -e ARCH_LAGOON \
  -e QCOM_SCM -e QCOM_RPMH -e QCOM_SMEM -e QCOM_SMP2P \
  -e QCOM_GLINK -e QRTR -e QCOM_COMMAND_DB \
  -e COMMON_CLK_QCOM -e SDM_GCC_LAGOON -e QCOM_GDSC \
  -e QCOM_LLCC -e QCOM_LAGOON_LLCC \
  -e PINCTRL -e PINCTRL_MSM -e PINCTRL_LAGOON -e PINCTRL_QCOM_SPMI_PMIC \
  -e REGULATOR -e REGULATOR_QCOM_RPMH -e REGULATOR_QCOM_SPMI \
  -e IOMMU_SUPPORT -e ARM_SMMU -e QTI_IOMMU_SUPPORT \
  -e SCSI -e SCSI_UFSHCD -e SCSI_UFSHCD_PLATFORM -e SCSI_UFS_QCOM \
  -e PHY_QCOM_UFS \
  -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT \
  -e EXT4_FS -e EROFS_FS -e F2FS_FS -e FS_ENCRYPTION \
  -e SECURITY -e SECURITY_SELINUX -e ANDROID_BINDER_IPC \
  -e PSTORE -e PSTORE_RAM -e PSTORE_PMSG \
  -e PRINTK -e PRINTK_TIME -e IKCONFIG -e IKCONFIG_PROC \
  -d MEDIA_SUPPORT -d VIDEO_V4L2 -d DRM \
  -d SOUND -d SND -d WLAN -d CFG80211 -d MAC80211 \
  -d BT -d NFC -d INPUT_TOUCHSCREEN -d INPUT_MISC

cp "$DEFCONFIG" "$OUT/a52xq_defconfig.requested"
build_kernel "$LABEL"

FINAL_CONFIG="$ARTIFACTS_DIR/config-$LABEL"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-$LABEL"
IMAGE_GZ="$OUT/Image.gz"
cp "$FINAL_CONFIG" "$OUT/final.config"
cp "$FINAL_IMAGE" "$OUT/Image"
gzip -n -9 -c "$FINAL_IMAGE" > "$IMAGE_GZ"

required_y=(
  ARCH_QCOM ARCH_LAGOON QCOM_SCM QCOM_RPMH QCOM_SMEM QCOM_SMP2P
  COMMON_CLK_QCOM SDM_GCC_LAGOON QCOM_GDSC PINCTRL_LAGOON
  REGULATOR_QCOM_RPMH ARM_SMMU SCSI_UFSHCD SCSI_UFSHCD_PLATFORM
  SCSI_UFS_QCOM PHY_QCOM_UFS BLK_DEV_INITRD DEVTMPFS EXT4_FS
  SECURITY_SELINUX ANDROID_BINDER_IPC PSTORE PSTORE_RAM PSTORE_PMSG
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
[[ "$missing" == 0 ]] || fail "One or more P0 built-ins were not retained by Kconfig"

# These are intentionally excluded from the first probe. Record, but do not
# fail, if dependency selection turns any of them back on.
optional_off=(KSU KSU_SUSFS MEDIA_SUPPORT DRM SOUND SND WLAN CFG80211 MAC80211 BT NFC INPUT_TOUCHSCREEN)
: > "$OUT/late-stack-audit.txt"
for symbol in "${optional_off[@]}"; do
  grep -E "^(CONFIG_${symbol}=|# CONFIG_${symbol} is not set)" "$FINAL_CONFIG" \
    >> "$OUT/late-stack-audit.txt" || printf 'CONFIG_%s absent\n' "$symbol" >> "$OUT/late-stack-audit.txt"
done

raw_bytes=$(stat -c %s "$FINAL_IMAGE")
gz_bytes=$(stat -c %s "$IMAGE_GZ")
# Actual boot partition is 96 MiB. Keep a conservative 80 MiB compressed cap.
(( gz_bytes < 80 * 1024 * 1024 )) || fail "Compressed kernel exceeds conservative boot payload cap"

{
  echo "status=build-passed"
  echo "flashable=no"
  echo "kernel_release=$(make -s -C "$KERNEL_DIR" O="$KERNEL_DIR/out" ARCH=arm64 kernelrelease)"
  echo "raw_image_bytes=$raw_bytes"
  echo "image_gz_bytes=$gz_bytes"
  echo "image_sha256=$(sha256sum "$FINAL_IMAGE" | awk '{print $1}')"
  echo "image_gz_sha256=$(sha256sum "$IMAGE_GZ" | awk '{print $1}')"
  echo "boot_partition_bytes=100663296"
  echo "boot_header_version=2"
  echo "boot_page_size=4096"
  echo "existing_kernel_compression=gzip"
  echo "embedded_dtb_bytes=401068"
  echo "dtbo_selected_index=1"
  echo "dtbo_asset_policy=preserve-original"
  echo "boot_ramdisk_policy=preserve-original"
  echo "boot_cmdline_policy=preserve-original"
} | tee "$OUT/metadata.txt"

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

cat > "$OUT/NOTICE.txt" <<'EOF'
NON-FLASHABLE SOURCE CHECKPOINT.

Image.gz is a build result for compatibility analysis. It has not been inserted
into the uploaded UN1CA boot.img and has not been tested on hardware. Do not
flash it directly.
EOF
