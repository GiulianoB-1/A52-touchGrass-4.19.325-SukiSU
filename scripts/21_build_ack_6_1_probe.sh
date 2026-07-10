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
ACK_COMMIT_PIN="${ACK_COMMIT_PIN:-52939c41021c7c0646679b68df13e82c1a5be699}"
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

info "Fetching pinned Android Common Kernel commit $ACK_COMMIT_PIN from $ACK_BRANCH"
if [ ! -d "$SRC_DIR/.git" ]; then
  rm -rf "$SRC_DIR"
  git init "$SRC_DIR"
  git -C "$SRC_DIR" remote add origin "$ACK_URL"
fi

git -C "$SRC_DIR" fetch --depth=1 origin "$ACK_COMMIT_PIN"
git -C "$SRC_DIR" checkout --detach FETCH_HEAD

# The probe source and Makefile entry are generated below. Always restore a
# pristine ACK checkout so local reruns cannot accumulate duplicate entries.
git -C "$SRC_DIR" reset --hard HEAD
git -C "$SRC_DIR" clean -fdx

ACK_COMMIT="$(git -C "$SRC_DIR" rev-parse HEAD)"
ACK_DESCRIBE="$(git -C "$SRC_DIR" describe --always --dirty 2>/dev/null || printf '%s' "$ACK_COMMIT")"

[ "$ACK_COMMIT" = "$ACK_COMMIT_PIN" ] || \
  fail "ACK checkout mismatch: expected $ACK_COMMIT_PIN, got $ACK_COMMIT"

info "Resolved ACK commit: $ACK_COMMIT"
{
  printf 'source_url=%s\n' "$ACK_URL"
  printf 'source_branch=%s\n' "$ACK_BRANCH"
  printf 'source_commit_pin=%s\n' "$ACK_COMMIT_PIN"
  printf 'source_commit=%s\n' "$ACK_COMMIT"
  printf 'source_describe=%s\n' "$ACK_DESCRIBE"
  printf 'project_commit=%s\n' "${GITHUB_SHA:-local}"
  printf 'probe_revision=v2-non-rebooting-panic\n'
} | tee "$ARTIFACTS_DIR/ack-6.1-source-manifest.txt"

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

info "Installing non-rebooting late-init panic probe v2"
cat > "$SRC_DIR/drivers/misc/a52xq_hybrid_probe.c" <<'PROBE_C'
// SPDX-License-Identifier: GPL-2.0-only
/*
 * Deliberate persistent-log probe for the Samsung A52XQ hybrid GKI experiment.
 *
 * Probe v2 reaches late init, reports whether the live-DT ramoops platform
 * device is bound, emits unique emergency markers, and enters the normal panic
 * path with panic_timeout=0. The generic panic path invokes the registered
 * kmsg dumpers, including pstore/ramoops, and then remains halted instead of
 * intentionally rebooting. This file must be removed after logging is proven.
 */
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/panic.h>
#include <linux/platform_device.h>

#define A52XQ_PROBE_MARKER \
    "A52XQ_HYBRID_GKI_6_1_V2_REACHED_LATE_INIT"
#define A52XQ_PROBE_PANIC \
    "A52XQ_HYBRID_GKI_6_1_V2_INTENTIONAL_PANIC_NO_AUTO_REBOOT"
#define A52XQ_RAMOOPS_DEVICE "b1b00000.ramoops"

static void __init a52xq_report_ramoops_status(void)
{
    struct device *dev;

    dev = bus_find_device_by_name(&platform_bus_type, NULL,
                                  A52XQ_RAMOOPS_DEVICE);
    if (!dev) {
        pr_emerg("A52XQ_6_1_V2_RAMOOPS_DEVICE_NOT_FOUND name=%s\n",
                 A52XQ_RAMOOPS_DEVICE);
        return;
    }

    pr_emerg("A52XQ_6_1_V2_RAMOOPS_DEVICE_FOUND name=%s\n",
             dev_name(dev));

    if (dev->driver)
        pr_emerg("A52XQ_6_1_V2_RAMOOPS_DRIVER_BOUND driver=%s\n",
                 dev->driver->name);
    else
        pr_emerg("A52XQ_6_1_V2_RAMOOPS_DRIVER_UNBOUND\n");

    put_device(dev);
}

static int __init a52xq_hybrid_probe_init(void)
{
    int i;

    /* A zero panic timeout requests no generic automatic reboot. */
    panic_timeout = 0;

    a52xq_report_ramoops_status();

    for (i = 0; i < 16; i++) {
        pr_emerg("%s iteration=%d\n", A52XQ_PROBE_MARKER, i);
        mdelay(25);
    }

    pr_emerg("%s\n", A52XQ_PROBE_PANIC);

    /* Let the pstore console backend commit the marker before panic. */
    mdelay(1000);

    /* panic() invokes kmsg_dump(KMSG_DUMP_PANIC) for registered dumpers. */
    panic("%s", A52XQ_PROBE_PANIC);
    return 0;
}
late_initcall_sync(a52xq_hybrid_probe_init);
PROBE_C

printf '\n# A52XQ hybrid GKI persistent-log probe v2\nobj-y += a52xq_hybrid_probe.o\n' \
  >> "$SRC_DIR/drivers/misc/Makefile"

export ARCH=arm64
export LLVM=1
export LLVM_IAS=1
export KBUILD_BUILD_USER=hybrid-gki
export KBUILD_BUILD_HOST=github-actions
export KBUILD_BUILD_TIMESTAMP="Thu Jan 1 00:00:00 UTC 1970"
export LOCALVERSION="-a52xq-hybrid-probe-v2-${ACK_COMMIT:0:12}"

CONFIG_LOG="$LOG_DIR/configure-ack-6.1-probe.log"
BUILD_LOG="$LOG_DIR/build-ack-6.1-probe.log"
DTB_LOG="$LOG_DIR/build-ack-6.1-dtbs.log"

info "Creating GKI-based hybrid configuration"
make -C "$SRC_DIR" O="$OUT_DIR" LLVM=1 LLVM_IAS=1 ARCH=arm64 gki_defconfig \
  2>&1 | tee "$CONFIG_LOG"

cfg="$SRC_DIR/scripts/config"
config_args=(--file "$OUT_DIR/.config")

# Core execution, persistent logging and Android ABI requirements.
for symbol in \
  ARM64 ARCH_QCOM SMP OF OF_EARLY_FLATTREE ARM_GIC_V3 ARM_ARCH_TIMER \
  PRINTK PRINTK_TIME PANIC_ON_OOPS \
  PSTORE PSTORE_RAM PSTORE_CONSOLE PSTORE_PMSG \
  IKCONFIG IKCONFIG_PROC KALLSYMS KALLSYMS_ALL \
  BLK_DEV_INITRD ANDROID_BINDER_IPC ANDROID_BINDERFS \
  DEVTMPFS DEVTMPFS_MOUNT TMPFS PROC_FS SYSFS; do
  "$cfg" "${config_args[@]}" --enable "$symbol"
done

"$cfg" "${config_args[@]}" --set-str ANDROID_BINDER_DEVICES "binder,hwbinder,vndbinder"
"$cfg" "${config_args[@]}" --set-val PANIC_TIMEOUT 0
"$cfg" "${config_args[@]}" --set-str DEFAULT_HOSTNAME a52xq-hybrid-probe-v2

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
cp "$FINAL_CONFIG" "$ARTIFACTS_DIR/config-android14-6.1-a52xq-hybrid-probe-v2"

grep -E '^(CONFIG_(ARM64|ARCH_QCOM|OF|ARM_GIC_V3|ARM_ARCH_TIMER|PRINTK_TIME|PSTORE|PSTORE_RAM|PSTORE_CONSOLE|PSTORE_PMSG|ANDROID_BINDER_IPC|ANDROID_BINDERFS|QCOM_RPMH|QCOM_COMMAND_DB|SM_GCC_6350|PINCTRL_SM6350|INTERCONNECT_QCOM_SM6350|SERIAL_QCOM_GENI|SCSI_UFS_QCOM|PHY_QCOM_QMP|PHY_QCOM_QMP_UFS|MODULES)=|# CONFIG_MODULES is not set)' \
  "$FINAL_CONFIG" | tee "$ARTIFACTS_DIR/ack-6.1-probe-config-summary.txt" || true
grep -E '^CONFIG_PANIC_TIMEOUT=' "$FINAL_CONFIG" \
  | tee -a "$ARTIFACTS_DIR/ack-6.1-probe-config-summary.txt"

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
  CONFIG_PSTORE_PMSG
  CONFIG_ANDROID_BINDER_IPC
  CONFIG_ANDROID_BINDERFS
  CONFIG_QCOM_RPMH
  CONFIG_QCOM_COMMAND_DB
  CONFIG_SM_GCC_6350
  CONFIG_PINCTRL_SM6350
  CONFIG_INTERCONNECT_QCOM_SM6350
  CONFIG_SERIAL_QCOM_GENI
  CONFIG_SCSI_UFS_QCOM
  CONFIG_PHY_QCOM_QMP
  CONFIG_PHY_QCOM_QMP_UFS
)

for symbol in "${required_y[@]}"; do
  grep -Fqx "$symbol=y" "$FINAL_CONFIG" || fail "Required config was not enabled: $symbol"
done

grep -Fqx 'CONFIG_PANIC_TIMEOUT=0' "$FINAL_CONFIG" || fail "Probe kernel must not auto-reboot after panic"
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

marker_file="$ARTIFACTS_DIR/probe-v2-markers.txt"
: > "$marker_file"
for marker in \
  A52XQ_HYBRID_GKI_6_1_V2_REACHED_LATE_INIT \
  A52XQ_HYBRID_GKI_6_1_V2_INTENTIONAL_PANIC_NO_AUTO_REBOOT \
  A52XQ_6_1_V2_RAMOOPS_DEVICE_FOUND \
  A52XQ_6_1_V2_RAMOOPS_DRIVER_BOUND \
  A52XQ_6_1_V2_RAMOOPS_DEVICE_NOT_FOUND \
  A52XQ_6_1_V2_RAMOOPS_DRIVER_UNBOUND; do
  strings "$IMAGE" | grep -F "$marker" | head -n1 >> "$marker_file" || \
    fail "Probe marker is absent from Image: $marker"
done

info "Building Qualcomm DTBs as a platform-source sanity check"
set +e
make -C "$SRC_DIR" O="$OUT_DIR" LLVM=1 LLVM_IAS=1 ARCH=arm64 \
  -j"$JOBS" dtbs 2>&1 | tee "$DTB_LOG"
dtb_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$dtb_rc" > "$ARTIFACTS_DIR/build-ack-6.1-dtbs.status"
test "$dtb_rc" -eq 0 || fail "ACK 6.1 DTB build failed"

cp "$IMAGE" "$ARTIFACTS_DIR/Image-android14-6.1-a52xq-hybrid-probe-v2"
cp "$IMAGE_GZ" "$ARTIFACTS_DIR/Image.gz-android14-6.1-a52xq-hybrid-probe-v2"
cp "$OUT_DIR/System.map" "$ARTIFACTS_DIR/System.map-android14-6.1-a52xq-hybrid-probe-v2"

find "$OUT_DIR/arch/arm64/boot/dts/qcom" -maxdepth 1 -type f \
  \( -name 'sm6350*.dtb' -o -name 'sm7225*.dtb' \) -print -exec cp {} "$ARTIFACTS_DIR/" \; \
  > "$ARTIFACTS_DIR/sm6350-dtb-files.txt" || true

sha256sum \
  "$ARTIFACTS_DIR/Image-android14-6.1-a52xq-hybrid-probe-v2" \
  "$ARTIFACTS_DIR/Image.gz-android14-6.1-a52xq-hybrid-probe-v2" \
  "$ARTIFACTS_DIR/config-android14-6.1-a52xq-hybrid-probe-v2" \
  > "$ARTIFACTS_DIR/ack-6.1-probe-v2.sha256"

{
  printf 'source_url=%s\n' "$ACK_URL"
  printf 'source_branch=%s\n' "$ACK_BRANCH"
  printf 'source_commit_pin=%s\n' "$ACK_COMMIT_PIN"
  printf 'source_commit=%s\n' "$ACK_COMMIT"
  printf 'source_describe=%s\n' "$ACK_DESCRIBE"
  printf 'compiler=%s\n' "$(clang --version | head -n1)"
  printf 'linker=%s\n' "$(ld.lld --version | head -n1)"
  printf 'image_bytes=%s\n' "$(wc -c < "$ARTIFACTS_DIR/Image-android14-6.1-a52xq-hybrid-probe-v2")"
  printf 'image_gz_bytes=%s\n' "$(wc -c < "$ARTIFACTS_DIR/Image.gz-android14-6.1-a52xq-hybrid-probe-v2")"
  printf 'probe_revision=v2\n'
  printf 'probe_marker=A52XQ_HYBRID_GKI_6_1_V2_REACHED_LATE_INIT\n'
  printf 'probe_panic=A52XQ_HYBRID_GKI_6_1_V2_INTENTIONAL_PANIC_NO_AUTO_REBOOT\n'
  printf 'probe_action=normal-panic-path-with-pstore-kmsg-dump\n'
  printf 'panic_timeout_seconds=0\n'
  printf 'automatic_reboot=no\n'
  printf 'expected_terminal_state=panic-hang-until-manual-recovery-reboot\n'
  printf 'ramoops_device_name=b1b00000.ramoops\n'
  printf 'modules=disabled\n'
  printf 'device_package=not-created\n'
  printf 'device_flash=not-performed\n'
} | tee "$ARTIFACTS_DIR/ack-6.1-probe-v2-build.txt"

info "ACK 6.1 hybrid persistent-log probe v2 build completed"
