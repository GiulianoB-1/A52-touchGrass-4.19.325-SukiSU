#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

PROFILE="${1:-lockdep}"
STRESS_SECONDS="${2:-600}"
TARGET_VERSION=4.19.153
A52_AUDIT_OUT="$KERNEL_DIR/out-a52-full-stack-audit-$PROFILE"
QEMU_OUT="$KERNEL_DIR/out-qemu-full-stack-$PROFILE"
QEMU_ARTIFACT_DIR="$ARTIFACTS_DIR/qemu-$PROFILE"
QEMU_CONFIG="$QEMU_OUT/.config"
QEMU_IMAGE="$QEMU_OUT/arch/arm64/boot/Image"
GUEST_SOURCE="$PROJECT_DIR/tests/qemu/full_stack_stress.c"
ROOTFS="$WORKSPACE/qemu-rootfs-$PROFILE"
INITRAMFS="$QEMU_ARTIFACT_DIR/initramfs-$PROFILE.cpio.gz"
SERIAL_LOG="$QEMU_ARTIFACT_DIR/serial-$PROFILE.log"
A52_AUDIT_LOG="$LOG_DIR/a52-full-stack-object-audit-$PROFILE.log"
QEMU_BUILD_LOG="$LOG_DIR/qemu-full-stack-build-$PROFILE.log"
REPORT="$QEMU_ARTIFACT_DIR/report-$PROFILE.txt"
AUDIT_FLAGS="-Werror=incompatible-pointer-types -Werror=implicit-function-declaration -Werror=return-type -Werror=uninitialized"

case "$PROFILE" in
  lockdep|kasan) ;;
  *) fail "Unknown QEMU stress profile: $PROFILE" ;;
esac
case "$STRESS_SECONDS" in
  ''|*[!0-9]*) fail "Stress duration must be an integer" ;;
esac
test "$STRESS_SECONDS" -ge 30 || fail "Stress duration must be at least 30 seconds"
test "$STRESS_SECONDS" -le 7200 || fail "Stress duration must not exceed 7200 seconds"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before full-stack QEMU stress"
test -f "$GUEST_SOURCE" || fail "QEMU guest stress source is missing"
test -d "$KERNEL_DIR/KernelSU/.git" || fail "Pinned SukiSU source is missing"
test "$(git -C "$KERNEL_DIR/KernelSU" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"
test -f "$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt" || fail "SUSFS integration report is missing"
grep -Fq 'susfs_version=v1.5.5' "$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt" || fail "Unexpected SUSFS version"
grep -Fq 'manager_abi=reboot-supercall-compat' "$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt" || fail "SUSFS reboot ABI bridge is missing"
grep -Fq 'legacy_abi=prctl-compat' "$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt" || fail "SUSFS prctl ABI bridge is missing"
grep -Fq 'default_non_root_profile.umount_modules = false;' "$KERNEL_DIR/KernelSU/kernel/policy/allowlist.c" || fail "Default module unmount is not disabled"
grep -Fq 'static bool ksu_kernel_umount_enabled = false;' "$KERNEL_DIR/KernelSU/kernel/feature/kernel_umount.c" || fail "Kernel unmount does not start disabled"
grep -Fq 'enable request ignored by project safety policy' "$KERNEL_DIR/KernelSU/kernel/feature/kernel_umount.c" || fail "Kernel unmount enable path is not blocked"
! grep -Fq 'ksu_kernel_umount_enabled = enable;' "$KERNEL_DIR/KernelSU/kernel/feature/kernel_umount.c" || fail "Kernel unmount can still be enabled"

mkdir -p "$QEMU_ARTIFACT_DIR" "$LOG_DIR"
cat > "$QEMU_ARTIFACT_DIR/DO-NOT-FLASH.txt" <<'EOF'
This artifact is a generic QEMU ARM64 virt-machine diagnostic build.
It is NOT an A52XQ kernel, boot image, AnyKernel package, or phone firmware.
Never flash the QEMU Image or initramfs to a physical device.
EOF

configure_toolchain

config_enable() {
  "$KERNEL_DIR/scripts/config" --file "$QEMU_CONFIG" -e "$1"
}

config_disable() {
  "$KERNEL_DIR/scripts/config" --file "$QEMU_CONFIG" -d "$1"
}

config_value() {
  "$KERNEL_DIR/scripts/config" --file "$QEMU_CONFIG" --set-val "$1" "$2"
}

require_config_y() {
  grep -Fq "CONFIG_$1=y" "$QEMU_CONFIG" || fail "QEMU config did not enable CONFIG_$1"
}

info "Compiling exact A52 full-stack objects without producing a flashable device Image"
rm -rf "$A52_AUDIT_OUT"
mkdir -p "$A52_AUDIT_OUT"
set +e
{
  make -C "$KERNEL_DIR" O="$A52_AUDIT_OUT" \
    DTC_EXT="$KERNEL_DIR/tools/dtc" \
    CONFIG_BUILD_ARM64_DT_OVERLAY=y \
    KCFLAGS="$AUDIT_FLAGS" \
    CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
    a52xq_defconfig

  make -C "$KERNEL_DIR" O="$A52_AUDIT_OUT" \
    DTC_EXT="$KERNEL_DIR/tools/dtc" \
    CONFIG_BUILD_ARM64_DT_OVERLAY=y \
    KCFLAGS="$AUDIT_FLAGS" \
    CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
    scripts/selinux/genheaders/

  genheaders="$A52_AUDIT_OUT/scripts/selinux/genheaders/genheaders"
  test -x "$genheaders"
  mkdir -p "$A52_AUDIT_OUT/security/selinux"
  "$genheaders" \
    "$A52_AUDIT_OUT/security/selinux/flask.h" \
    "$A52_AUDIT_OUT/security/selinux/av_permissions.h"

  make -C "$KERNEL_DIR" O="$A52_AUDIT_OUT" \
    DTC_EXT="$KERNEL_DIR/tools/dtc" \
    CONFIG_BUILD_ARM64_DT_OVERLAY=y \
    KCFLAGS="$AUDIT_FLAGS" \
    CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
    W=1 V=1 -j"${JOBS:-4}" \
    kernel/bpf/verifier.o \
    drivers/kernelsu/ \
    fs/susfs.o \
    fs/susfs_sukisu_compat.o \
    fs/namespace.o \
    fs/proc_namespace.o \
    kernel/sys.o
} 2>&1 | tee "$A52_AUDIT_LOG"
a52_audit_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$a52_audit_rc" > "$QEMU_ARTIFACT_DIR/a52-object-audit.status"
test "$a52_audit_rc" -eq 0 || fail "A52 full-stack object audit failed"

for object in \
  kernel/bpf/verifier.o \
  drivers/kernelsu/built-in.a \
  fs/susfs.o \
  fs/susfs_sukisu_compat.o \
  fs/namespace.o \
  fs/proc_namespace.o \
  kernel/sys.o; do
  test -s "$A52_AUDIT_OUT/$object" || fail "A52 audit object is missing: $object"
done
cp "$A52_AUDIT_OUT/.config" "$QEMU_ARTIFACT_DIR/a52-audit-config"
sha256sum \
  "$A52_AUDIT_OUT/kernel/bpf/verifier.o" \
  "$A52_AUDIT_OUT/drivers/kernelsu/built-in.a" \
  "$A52_AUDIT_OUT/fs/susfs.o" \
  "$A52_AUDIT_OUT/fs/susfs_sukisu_compat.o" \
  "$A52_AUDIT_OUT/fs/namespace.o" \
  "$A52_AUDIT_OUT/fs/proc_namespace.o" \
  "$A52_AUDIT_OUT/kernel/sys.o" \
  > "$QEMU_ARTIFACT_DIR/a52-audit-objects.sha256"

info "Generating generic ARM64 virt config from the exact patched source tree"
rm -rf "$QEMU_OUT"
mkdir -p "$QEMU_OUT"
make -C "$KERNEL_DIR" O="$QEMU_OUT" \
  DTC_EXT="$KERNEL_DIR/tools/dtc" \
  defconfig

# Generic QEMU virt platform and initramfs support.
for symbol in \
  ARCH_VEXPRESS \
  PCI \
  PCI_HOST_GENERIC \
  SERIAL_AMBA_PL011 \
  SERIAL_AMBA_PL011_CONSOLE \
  VIRTIO \
  VIRTIO_MMIO \
  VIRTIO_BLK \
  VIRTIO_NET \
  DEVTMPFS \
  DEVTMPFS_MOUNT \
  BLK_DEV_INITRD \
  RD_GZIP \
  TMPFS \
  PROC_FS \
  SYSFS \
  DEBUG_FS \
  NAMESPACES \
  UTS_NS \
  IPC_NS \
  PID_NS \
  NET_NS \
  CGROUPS \
  BPF \
  BPF_SYSCALL \
  BPF_EVENTS \
  KPROBES \
  KPROBE_EVENTS \
  SECURITY \
  SECURITYFS \
  SECURITY_SELINUX \
  SECURITY_SELINUX_BOOTPARAM \
  SECCOMP \
  SECCOMP_FILTER \
  DEBUG_KERNEL \
  DETECT_HUNG_TASK \
  WQ_WATCHDOG \
  SOFTLOCKUP_DETECTOR \
  DEBUG_ATOMIC_SLEEP \
  DEBUG_LIST \
  DEBUG_OBJECTS \
  SLUB_DEBUG \
  KSU \
  KSU_MANUAL_SU \
  KSU_SUSFS \
  KSU_SUSFS_HAS_MAGIC_MOUNT \
  KSU_SUSFS_SUS_MOUNT \
  KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT \
  KSU_SUSFS_SPOOF_UNAME \
  KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \
  KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG; do
  config_enable "$symbol"
done

for symbol in \
  MODULES \
  KPM \
  KSU_DEBUG \
  KSU_SUSFS_SUS_PATH \
  KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT \
  KSU_SUSFS_SUS_KSTAT \
  KSU_SUSFS_SUS_OVERLAYFS \
  KSU_SUSFS_TRY_UMOUNT \
  KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT \
  KSU_SUSFS_ENABLE_LOG \
  KSU_SUSFS_OPEN_REDIRECT \
  KSU_SUSFS_SUS_SU; do
  config_disable "$symbol"
done

config_value HZ 250
config_value DEFAULT_HUNG_TASK_TIMEOUT 60

if test "$PROFILE" = lockdep; then
  for symbol in \
    LOCKDEP \
    DEBUG_LOCK_ALLOC \
    PROVE_LOCKING \
    PROVE_RCU \
    DEBUG_SPINLOCK \
    DEBUG_MUTEXES \
    DEBUG_RWSEMS; do
    config_enable "$symbol"
  done
  config_disable KASAN
else
  config_enable KASAN
  config_enable KASAN_INLINE
  config_disable KASAN_OUTLINE
  config_disable PROVE_LOCKING
  config_disable LOCK_STAT
fi

make -C "$KERNEL_DIR" O="$QEMU_OUT" \
  DTC_EXT="$KERNEL_DIR/tools/dtc" \
  olddefconfig </dev/null

for required in \
  ARCH_VEXPRESS \
  SERIAL_AMBA_PL011_CONSOLE \
  VIRTIO_MMIO \
  BLK_DEV_INITRD \
  BPF_SYSCALL \
  KPROBES \
  KSU \
  KSU_MANUAL_SU \
  KSU_SUSFS \
  KSU_SUSFS_SUS_MOUNT \
  KSU_SUSFS_HAS_MAGIC_MOUNT \
  DEBUG_ATOMIC_SLEEP \
  DETECT_HUNG_TASK; do
  require_config_y "$required"
done
if test "$PROFILE" = lockdep; then
  require_config_y PROVE_LOCKING
else
  require_config_y KASAN
fi
! grep -Eq '^CONFIG_KSU_SUSFS_(SUS_PATH|SUS_KSTAT|TRY_UMOUNT|OPEN_REDIRECT|SUS_SU)=y$' "$QEMU_CONFIG" \
  || fail "Risky SUSFS features are enabled in QEMU config"
cp "$QEMU_CONFIG" "$QEMU_ARTIFACT_DIR/qemu-config-$PROFILE"

info "Building generic ARM64 QEMU kernel with $PROFILE diagnostics"
set +e
make -C "$KERNEL_DIR" O="$QEMU_OUT" \
  DTC_EXT="$KERNEL_DIR/tools/dtc" \
  KCFLAGS="$AUDIT_FLAGS" \
  CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
  -j"${JOBS:-4}" Image 2>&1 | tee "$QEMU_BUILD_LOG"
qemu_build_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$qemu_build_rc" > "$QEMU_ARTIFACT_DIR/qemu-build.status"
test "$qemu_build_rc" -eq 0 || fail "QEMU $PROFILE kernel build failed"
test -s "$QEMU_IMAGE" || fail "QEMU build completed without an Image"
cp "$QEMU_IMAGE" "$QEMU_ARTIFACT_DIR/Image-qemu-virt-$PROFILE-NOT-FOR-A52"
sha256sum "$QEMU_ARTIFACT_DIR/Image-qemu-virt-$PROFILE-NOT-FOR-A52" \
  > "$QEMU_ARTIFACT_DIR/Image-qemu-virt-$PROFILE-NOT-FOR-A52.sha256"

info "Building static ARM64 stress initramfs"
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"/{dev,proc,sys,tmp,mnt}
aarch64-linux-gnu-gcc \
  -O2 -static -pthread -Wall -Wextra \
  -o "$ROOTFS/init" "$GUEST_SOURCE"
file "$ROOTFS/init" | tee "$QEMU_ARTIFACT_DIR/init-file.txt"
grep -Fq 'ARM aarch64' "$QEMU_ARTIFACT_DIR/init-file.txt" || fail "Stress init is not ARM64"
sudo mknod -m 600 "$ROOTFS/dev/console" c 5 1
sudo mknod -m 666 "$ROOTFS/dev/null" c 1 3
sudo chown -R root:root "$ROOTFS"
(
  cd "$ROOTFS"
  find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0 2>/dev/null | gzip -9
) > "$INITRAMFS"
test -s "$INITRAMFS" || fail "QEMU initramfs was not produced"
sha256sum "$INITRAMFS" > "$INITRAMFS.sha256"

info "Running $PROFILE full-stack stress for $STRESS_SECONDS seconds"
qemu_timeout=$((STRESS_SECONDS + 300))
set +e
timeout --foreground --signal=TERM "$qemu_timeout" \
  qemu-system-aarch64 \
    -machine virt,gic-version=3 \
    -cpu cortex-a72 \
    -smp 4 \
    -m 3072 \
    -nographic \
    -no-reboot \
    -kernel "$QEMU_IMAGE" \
    -initrd "$INITRAMFS" \
    -append "console=ttyAMA0 earlycon=pl011,0x9000000 rdinit=/init nokaslr panic=1 oops=panic hung_task_panic=1 softlockup_panic=1 rcupdate.rcu_cpu_stall_timeout=21 slub_debug=FZPU page_poison=1 selinux=1 enforcing=0 stress_seconds=$STRESS_SECONDS" \
    2>&1 | tee "$SERIAL_LOG"
qemu_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$qemu_rc" > "$QEMU_ARTIFACT_DIR/qemu-run.status"
test "$qemu_rc" -ne 124 || fail "QEMU stress timed out; probable guest hang"
test "$qemu_rc" -eq 0 || fail "QEMU exited with status $qemu_rc"
grep -Fq 'A52_QEMU_FULL_STACK_STRESS_PASS' "$SERIAL_LOG" || fail "Guest did not emit the full-stack PASS marker"

failure_pattern='Kernel panic|BUG: KASAN|BUG: sleeping function called from invalid context|possible circular locking dependency|rcu[^[:space:]]* detected stalls|INFO: task .* blocked for more than|watchdog: BUG: soft lockup|hard LOCKUP|Unable to handle kernel|Internal error: Oops|A52_QEMU_FULL_STACK_STRESS_FAIL|A52_QEMU_STRESS_WORKER_FAIL'
if grep -Eiq "$failure_pattern" "$SERIAL_LOG"; then
  grep -Ein "$failure_pattern" "$SERIAL_LOG" > "$QEMU_ARTIFACT_DIR/failure-signatures.txt" || true
  fail "Kernel diagnostic or guest failure signature detected"
fi

warning_count=$(grep -Eic 'WARNING:|BUG:|lockdep|stall|blocked for more than' "$SERIAL_LOG" || true)
{
  printf 'profile=%s\n' "$PROFILE"
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'touchgrass_commit=%s\n' "$TOUCHGRASS_COMMIT"
  printf 'sukisu_commit=%s\n' "$SUKISU_COMMIT"
  printf 'susfs_version=v1.5.5\n'
  printf 'bpf_repair=integrated\n'
  printf 'linux_update=4.19.152-to-4.19.153\n'
  printf 'a52_object_audit=passed\n'
  printf 'qemu_machine=virt\n'
  printf 'qemu_cpus=4\n'
  printf 'stress_seconds=%s\n' "$STRESS_SECONDS"
  printf 'guest_pass_marker=yes\n'
  printf 'kernel_diagnostic_matches=%s\n' "$warning_count"
  printf 'kernel_unmount=permanently-disabled\n'
  printf 'susfs_risky_features=disabled\n'
  printf 'flashable_output=no\n'
  printf 'hardware_equivalence=no\n'
  printf 'result=pass\n'
} | tee "$REPORT"
sha256sum "$REPORT" > "$REPORT.sha256"

info "QEMU $PROFILE full-stack stress completed successfully"
