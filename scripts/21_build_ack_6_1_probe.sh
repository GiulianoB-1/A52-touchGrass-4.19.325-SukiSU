#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORK_DIR="${WORK_DIR:-$ROOT_DIR/work-hybrid-gki-6.1}"
SRC_DIR="$WORK_DIR/kernel-common"
OUT_DIR="$WORK_DIR/out"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$ROOT_DIR/artifacts-hybrid-gki}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs-hybrid-gki}"

ACK_URL="${ACK_URL:-https://android.googlesource.com/kernel/common}"
ACK_BRANCH="${ACK_BRANCH:-android14-6.1}"
JOBS="${JOBS:-4}"

mkdir -p "$WORK_DIR" "$ARTIFACTS_DIR" "$LOG_DIR"
rm -rf "$OUT_DIR"

info() {
  printf '[hybrid-gki] %s\n' "$*"
}

fail() {
  printf '[hybrid-gki] ERROR: %s\n' "$*" >&2
  exit 1
}

command -v git >/dev/null || fail "git is missing"
command -v make >/dev/null || fail "make is missing"
command -v clang >/dev/null || fail "clang is missing"
command -v ld.lld >/dev/null || fail "ld.lld is missing"
command -v python3 >/dev/null || fail "python3 is missing"

info "Fetching Android Common Kernel $ACK_BRANCH"
if [ ! -d "$SRC_DIR/.git" ]; then
  git clone --depth=1 --single-branch --branch "$ACK_BRANCH" "$ACK_URL" "$SRC_DIR"
else
  git -C "$SRC_DIR" fetch --depth=1 origin "$ACK_BRANCH"
  git -C "$SRC_DIR" checkout --detach FETCH_HEAD
fi

ACK_COMMIT="$(git -C "$SRC_DIR" rev-parse HEAD)"
ACK_DESCRIBE="$(git -C "$SRC_DIR" describe --always --dirty 2>/dev/null || printf '%s' "$ACK_COMMIT")"

info "Resolved ACK commit: $ACK_COMMIT"

required_source_files=(
  arch/arm64/boot/dts/qcom/sm6350.dtsi
  drivers/clk/qcom/gcc-sm6350.c
  drivers/pinctrl/qcom/pinctrl-sm6350.c
  drivers/interconnect/qcom/sm6350.c
  drivers/ufs/host/ufs-qcom.c
  drivers/phy/qualcomm/phy-qcom-qmp-ufs.c
  drivers/soc/qcom/rpmh-rsc.c
)

for path in "${required_source_files[@]}"; do
  test -f "$SRC_DIR/$path" || fail "ACK source is missing required SM6350 file: $path"
done

test -f "$SRC_DIR/arch/arm64/configs/gki_defconfig" || fail "arm64 gki_defconfig is missing"
test -x "$SRC_DIR/scripts/config" || fail "kernel scripts/config is missing"

info "Installing deliberate late-init probe marker"
cat > "$SRC_DIR/drivers/misc/a52xq_hybrid_probe.c" <<'PROBE_C'
// SPDX-License-Identifier: GPL-2.0-only
/*
 * Deliberate first-boot probe for the Samsung A52XQ hybrid GKI experiment.
 *
 * This file must be removed after persistent ramoops logging is proven.
 */
#include <linux/delay.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/panic.h>

#define A52XQ_PROBE_MARKER "A52XQ_HYBRID_GKI_6_1_PROBE_REACHED_LATE_INIT"

static int __init a52xq_hybrid_probe_init(void)
{
    int i;

    for (i = 0; i < 8; i++) {
        pr_emerg("%s iteration=%d\n", A52XQ_PROBE_MARKER, i);
        mdelay(25);
    }

    /* Give pstore/ramoops time to record the emergency messages and panic. */
    panic_timeout = 8;
    panic("%s deliberate panic", A52XQ_PROBE_MARKER);
    return 0;
}
late_initcall_sync(a52xq_hybrid_probe_init);
PROBE_C

printf '\n# A52XQ hybrid GKI first-boot probe\nobj-y += a52xq_hybrid_probe.o\n' \
  >> "$SRC_DIR/drivers/misc/Makefile"

export ARCH=arm64
export LLVM=1
export LLVM_IAS=1
export KBUILD_BUILD_USER=hybrid-gki
export KBUILD_BUILD_HOST=github-actions
export KBUILD_BUILD_TIMESTAMP="Thu Jan 1 00:00:00 UTC 1970"
export LOCALVERSION="-a52xq-hybrid-probe-${ACK_COMMIT:0:12}"

CONFIG_LOG="$LOG_DIR/configure-ack-6.1-probe.log"
BUILD_LOG="$LOG_DIR/build-ack-6.1-probe.log"
DTB_LOG="$LOG_DIR/build-ack-6.1-dtbs.log"

info "Creating GKI-based hybrid configuration"
make -C "$SRC_DIR" O="$OUT_DIR" LLVM=1 LLVM_IAS=1 ARCH=arm64 gki_defconfig \
  2>&1 | tee "$CONFIG_LOG"

cfg="$SRC_DIR/scripts/config"
config_args=(--file "$OUT_DIR/.config")

# Core execution, logging and Android ABI requirements.
for symbol in \
  ARM64 ARCH_QCOM SMP OF OF_EARLY_FLATTREE ARM_GIC_V3 ARM_ARCH_TIMER \
  PRINTK PRINTK_TIME PANIC_ON_OOPS \
  PSTORE PSTORE_RAM PSTORE_CONSOLE PSTORE_PMSG \
  IKCONFIG IKCONFIG_PROC KALLSYMS KALLSYMS_ALL \
  BLK_DEV_INITRD BINDER_IPC ANDROID_BINDERFS \
  DEVTMPFS DEVTMPFS_MOUNT TMPFS PROC_FS SYSFS; do
  "$cfg" "${config_args[@]}" --enable "$symbol"
done

"$cfg" "${config_args[@]}" --set-val PANIC_TIMEOUT 8
"$cfg" "${config_args[@]}" --set-str DEFAULT_HOSTNAME a52xq-hybrid-probe

# SM6350/Lagoon boot-critical platform support. Keep these built in so no
# Linux 4.19 vendor module is required during bring-up.
for symbol in \
  QCOM_SCM QCOM_SMEM QCOM_COMMAND_DB QCOM_RPMH QCOM_RPMHPD \
  QCOM_AOSS_QMP QCOM_PDC QCOM_LLCC QCOM_IPCC QCOM_GENI_SE \
  MAILBOX RESET_CONTROLLER REGULATOR REGULATOR_FIXED_VOLTAGE \
  REGULATOR_QCOM_RPMH COMMON_CLK_QCOM SM_GCC_6350 \
  PINCTRL PINCTRL_MSM PINCTRL_SM6350 \
  INTERCONNECT INTERCONNECT_QCOM INTERCONNECT_QCOM_SM6350 \
  SERIAL_QCOM_GENI SERIAL_QCOM_GENI_CONSOLE \
  SCSI SCSI_UFSHCD SCSI_UFS_QCOM \
  GENERIC_PHY PHY_QCOM_QMP PHY_QCOM_QMP_UFS \
  NVMEM NVMEM_QCOM_QFPROM; do
  "$cfg" "${config_args[@]}" --enable "$symbol"
done

# Keep the initial image self-contained.
"$cfg" "${config_args[@]}" --disable MODULES
"$cfg" "${config_args[@]}" --disable DEBUG_INFO
"$cfg" "${config_args[@]}" --enable DEBUG_KERNEL
"$cfg" "${config_args[@]}" --enable MAGIC_SYSRQ

make -C "$SRC_DIR" O="$OUT_DIR" LLVM=1 LLVM_IAS=1 ARCH=arm64 olddefconfig \
  2>&1 | tee -a "$CONFIG_LOG"

FINAL_CONFIG="$OUT_DIR/.config"
test -s "$FINAL_CONFIG" || fail "Final config was not generated"

required_y=(
  CONFIG_ARM64
  CONFIG_ARCH_QCOM
  CONFIG_OF
  CONFIG_ARM_GIC_V3
  CONFIG_ARM_ARCH_TIMER
  CONFIG_PRINTK_TIME
  CONFIG_PSTORE
  CONFIG_PSTORE_RAM
  CONFIG_PSTORE_CONSOLE
  CONFIG_SM_GCC_6350
  CONFIG_PINCTRL_SM6350
  CONFIG_INTERCONNECT_QCOM_SM6350
  CONFIG_SERIAL_QCOM_GENI
  CONFIG_SCSI_UFS_QCOM
)

for symbol in "${required_y[@]}"; do
  grep -Fqx "$symbol=y" "$FINAL_CONFIG" || fail "Required config was not enabled: $symbol"
done

grep -Fqx '# CONFIG_MODULES is not set' "$FINAL_CONFIG" || fail "Probe kernel unexpectedly enables modules"

info "Building ARM64 probe Image and Image.gz"
set +e
make -C "$SRC_DIR" O="$OUT_DIR" LLVM=1 LLVM_IAS=1 ARCH=arm64 \
  -j"$JOBS" Image Image.gz 2>&1 | tee "$BUILD_LOG"
build_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$build_rc" > "$ARTIFACTS_DIR/build-ack-6.1-probe.status"
test "$build_rc" -eq 0 || fail "ACK 6.1 probe kernel build failed"

IMAGE="$OUT_DIR/arch/arm64/boot/Image"
IMAGE_GZ="$OUT_DIR/arch/arm64/boot/Image.gz"
test -s "$IMAGE" || fail "Raw Image was not produced"
test -s "$IMAGE_GZ" || fail "Compressed Image.gz was not produced"

strings "$IMAGE" | grep -F 'A52XQ_HYBRID_GKI_6_1_PROBE_REACHED_LATE_INIT' \
  > "$ARTIFACTS_DIR/probe-marker.txt"
test -s "$ARTIFACTS_DIR/probe-marker.txt" || fail "Probe marker is absent from Image"

info "Building Qualcomm DTBs as a platform-source sanity check"
set +e
make -C "$SRC_DIR" O="$OUT_DIR" LLVM=1 LLVM_IAS=1 ARCH=arm64 \
  -j"$JOBS" dtbs 2>&1 | tee "$DTB_LOG"
dtb_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$dtb_rc" > "$ARTIFACTS_DIR/build-ack-6.1-dtbs.status"
test "$dtb_rc" -eq 0 || fail "ACK 6.1 DTB build failed"

cp "$IMAGE" "$ARTIFACTS_DIR/Image-android14-6.1-a52xq-hybrid-probe"
cp "$IMAGE_GZ" "$ARTIFACTS_DIR/Image.gz-android14-6.1-a52xq-hybrid-probe"
cp "$FINAL_CONFIG" "$ARTIFACTS_DIR/config-android14-6.1-a52xq-hybrid-probe"
cp "$OUT_DIR/System.map" "$ARTIFACTS_DIR/System.map-android14-6.1-a52xq-hybrid-probe"

find "$OUT_DIR/arch/arm64/boot/dts/qcom" -maxdepth 1 -type f \
  \( -name 'sm6350*.dtb' -o -name 'sm7225*.dtb' \) -print -exec cp {} "$ARTIFACTS_DIR/" \; \
  > "$ARTIFACTS_DIR/sm6350-dtb-files.txt" || true

sha256sum \
  "$ARTIFACTS_DIR/Image-android14-6.1-a52xq-hybrid-probe" \
  "$ARTIFACTS_DIR/Image.gz-android14-6.1-a52xq-hybrid-probe" \
  "$ARTIFACTS_DIR/config-android14-6.1-a52xq-hybrid-probe" \
  > "$ARTIFACTS_DIR/ack-6.1-probe.sha256"

{
  printf 'source_url=%s\n' "$ACK_URL"
  printf 'source_branch=%s\n' "$ACK_BRANCH"
  printf 'source_commit=%s\n' "$ACK_COMMIT"
  printf 'source_describe=%s\n' "$ACK_DESCRIBE"
  printf 'compiler=%s\n' "$(clang --version | head -n1)"
  printf 'linker=%s\n' "$(ld.lld --version | head -n1)"
  printf 'image_bytes=%s\n' "$(wc -c < "$ARTIFACTS_DIR/Image-android14-6.1-a52xq-hybrid-probe")"
  printf 'image_gz_bytes=%s\n' "$(wc -c < "$ARTIFACTS_DIR/Image.gz-android14-6.1-a52xq-hybrid-probe")"
  printf 'probe_marker=A52XQ_HYBRID_GKI_6_1_PROBE_REACHED_LATE_INIT\n'
  printf 'probe_action=deliberate-late-init-panic\n'
  printf 'panic_timeout_seconds=8\n'
  printf 'modules=disabled\n'
  printf 'device_package=not-created\n'
  printf 'device_flash=not-performed\n'
} | tee "$ARTIFACTS_DIR/ack-6.1-probe-build.txt"

info "ACK 6.1 hybrid probe build completed"
