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
  -e INPUT -e INPUT_MISC -e INPUT_QPNP_POWER_ON \
  -d MEDIA_SUPPORT -d VIDEO_V4L2 -d DRM \
  -d SOUND -d SND -d WLAN -d CFG80211 -d MAC80211 \
  -d BT -d NFC -d INPUT_TOUCHSCREEN \
  -d INPUT_TOUCHSCREEN_TCLMV2 -d TOUCHSCREEN_DUMP_MODE \
  -d TOUCHSCREEN_STM_FTS5CU56A -d TOUCHSCREEN_ZINITIX_ZT7650 \
  -d INPUT_SEC_SECURE_TOUCH \
  -d ICNSS -d ICNSS_QMI -d CNSS_UTILS \
  -d QCOM_KGSL -d QCOM_KGSL_IOMMU \
  -d LEDS_SM5714 -d SEC_PERF_MANAGER -d SEC_PERF_MANAGER_QC \
  -d SEC_DEBUG -d SEC_DEBUG_SCHED_LOG -d SEC_DEBUG_SUMMARY \
  -d SEC_DEBUG_DUMP_TASK_STACK -d SEC_DEBUG_MDM_FILE_INFO \
  -d SEC_DEBUG_MODULE_INFO -d SEC_DEBUG_APPS_CLK_LOGGING \
  -d SEC_DEBUG_TSP_LOG

# This vendor tree's techpack/Kbuild enumerates every first-level directory,
# independently of the normal SOUND/MEDIA/DRM Kconfig gates. Exclude the late
# hardware stacks P0 intentionally omits. Keep all other Qualcomm techpacks
# available for platform and early-boot dependencies.
TECHPACK_KBUILD="$KERNEL_DIR/techpack/Kbuild"
[[ -s "$TECHPACK_KBUILD" ]] || fail "techpack/Kbuild is missing"
cp "$TECHPACK_KBUILD" "$OUT/techpack.Kbuild.before"
python3 - "$TECHPACK_KBUILD" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
needle = '-type d -not -name ".*")'
replacement = '-type d -not -name ".*" -not -name audio -not -name camera -not -name display -not -name video)'
count = text.count(needle)
if count != 2:
    raise SystemExit(f"expected two techpack directory enumerations, found {count}")
path.write_text(text.replace(needle, replacement))
PY
cp "$TECHPACK_KBUILD" "$OUT/techpack.Kbuild.p0"
grep -Fq -- '-not -name audio -not -name camera -not -name display -not -name video' "$TECHPACK_KBUILD" \
  || fail "late techpack exclusions were not applied"

# Several Samsung input symbols can build touchscreen drivers independently of
# the parent INPUT_TOUCHSCREEN menu. Keep a directory-level guard as a second
# line of defense after disabling the concrete vendor symbols above.
INPUT_MAKEFILE="$KERNEL_DIR/drivers/input/Makefile"
[[ -s "$INPUT_MAKEFILE" ]] || fail "drivers/input/Makefile is missing"
cp "$INPUT_MAKEFILE" "$OUT/drivers-input.Makefile.before"
python3 - "$INPUT_MAKEFILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
needle = 'obj-$(CONFIG_INPUT_TOUCHSCREEN)\t+= touchscreen/'
replacement = '# P0 excludes late touchscreen drivers\nobj-n\t\t\t\t+= touchscreen/'
count = text.count(needle)
if count != 1:
    raise SystemExit(f"expected one touchscreen directory gate, found {count}")
path.write_text(text.replace(needle, replacement))
PY
cp "$INPUT_MAKEFILE" "$OUT/drivers-input.Makefile.p0"
grep -Fq 'obj-n' "$INPUT_MAKEFILE" || fail "touchscreen directory exclusion was not applied"

# When QPNP PON is disabled, this vendor header defines qpnp_pon_wd_config()
# directly in the header without static linkage. Every user then emits a global
# copy and the final vmlinux link fails with multiple definitions. Match the
# surrounding fallback helpers and make it static inline. This remains a safe
# fallback repair even though P0 now requires the real QPNP PON provider.
QPNP_PON_HEADER="$KERNEL_DIR/include/linux/input/qpnp-power-on.h"
[[ -s "$QPNP_PON_HEADER" ]] || fail "qpnp-power-on.h is missing"
cp "$QPNP_PON_HEADER" "$OUT/qpnp-power-on.h.before"
python3 - "$QPNP_PON_HEADER" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
needle = 'int qpnp_pon_wd_config(bool enable)\n{\n\treturn -ENODEV;\n}'
replacement = 'static inline int qpnp_pon_wd_config(bool enable)\n{\n\treturn -ENODEV;\n}'
count = text.count(needle)
if count != 1:
    raise SystemExit(f"expected one non-static qpnp_pon_wd_config fallback, found {count}")
path.write_text(text.replace(needle, replacement))
PY
cp "$QPNP_PON_HEADER" "$OUT/qpnp-power-on.h.p0"
grep -Fq 'static inline int qpnp_pon_wd_config(bool enable)' "$QPNP_PON_HEADER" \
  || fail "QPNP PON fallback linkage repair was not applied"

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
  INPUT INPUT_MISC INPUT_QPNP_POWER_ON
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

optional_off=(
  KSU KSU_SUSFS MEDIA_SUPPORT DRM SOUND SND WLAN CFG80211 MAC80211 BT NFC
  INPUT_TOUCHSCREEN INPUT_TOUCHSCREEN_TCLMV2 TOUCHSCREEN_DUMP_MODE
  TOUCHSCREEN_STM_FTS5CU56A TOUCHSCREEN_ZINITIX_ZT7650 INPUT_SEC_SECURE_TOUCH
  ICNSS ICNSS_QMI CNSS_UTILS QCOM_KGSL QCOM_KGSL_IOMMU
  LEDS_SM5714 SEC_PERF_MANAGER SEC_PERF_MANAGER_QC SEC_DEBUG
)
: > "$OUT/late-stack-audit.txt"
for symbol in "${optional_off[@]}"; do
  if grep -Fxq "CONFIG_${symbol}=y" "$FINAL_CONFIG" || grep -Fxq "CONFIG_${symbol}=m" "$FINAL_CONFIG"; then
    printf 'UNEXPECTED CONFIG_%s enabled\n' "$symbol" | tee -a "$OUT/late-stack-audit.txt" >&2
    fail "Late-stack symbol CONFIG_${symbol} survived the P0 reduction"
  fi
  grep -E "^(CONFIG_${symbol}=|# CONFIG_${symbol} is not set)" "$FINAL_CONFIG" \
    >> "$OUT/late-stack-audit.txt" || printf 'CONFIG_%s absent\n' "$symbol" >> "$OUT/late-stack-audit.txt"
done

# Prove that the build-system exclusions worked, not merely the Kconfig request.
for stack in audio camera display video; do
  if find "$KERNEL_DIR/out/techpack/$stack" -type f \( -name '*.o' -o -name '*.a' \) -print -quit 2>/dev/null | grep -q .; then
    fail "techpack/$stack objects were built despite the P0 exclusion"
  fi
done
if find "$KERNEL_DIR/out/drivers/input/touchscreen" -type f \( -name '*.o' -o -name '*.a' \) -print -quit 2>/dev/null | grep -q .; then
  fail "touchscreen objects were built despite the P0 exclusion"
fi
printf 'excluded_techpacks=audio,camera,display,video\n' >> "$OUT/late-stack-audit.txt"
printf 'excluded_driver_dirs=drivers/input/touchscreen\n' >> "$OUT/late-stack-audit.txt"

raw_bytes=$(stat -c %s "$FINAL_IMAGE")
gz_bytes=$(stat -c %s "$IMAGE_GZ")
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
