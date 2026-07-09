#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
AUDIT_OUT="$KERNEL_DIR/out-sukisu-audit"
CONFIG_LOG="$LOG_DIR/configure-sukisu-audit.log"
CONFIG_STATUS="$ARTIFACTS_DIR/configure-sukisu-audit.status"
CONFIG_SUMMARY="$ARTIFACTS_DIR/config-sukisu-audit-summary.txt"
AUDIT_LOG="$LOG_DIR/build-sukisu-audit.log"
AUDIT_STATUS="$ARTIFACTS_DIR/build-sukisu-audit.status"
AUDIT_FLAGS="-Werror=incompatible-pointer-types -Werror=implicit-function-declaration -Werror=return-type -Werror=uninitialized"
BUILD_LABEL="linux-4.19.153-sukisu-unmount-off"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SukiSU audit"
test -d "$SUKISU_DIR/.git" || fail "Pinned SukiSU source is missing"
test -f "$ARTIFACTS_DIR/sukisu-integration.txt" || fail "SukiSU integration report is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"

# Repeat the most important source checks at the build boundary.
grep -Fq 'default_non_root_profile.umount_modules = false;' "$SUKISU_DIR/kernel/policy/allowlist.c" || fail "Default module unmount is not disabled"
grep -Fq 'static bool ksu_kernel_umount_enabled = false;' "$SUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Kernel unmount does not start disabled"
grep -Fq 'enable request ignored by project safety policy' "$SUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Kernel unmount enable path is not blocked"
! grep -Fq 'ksu_kernel_umount_enabled = enable;' "$SUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Kernel unmount can still be enabled"
grep -Fq '#define FILE_FORMAT_VERSION 4' "$SUKISU_DIR/kernel/policy/allowlist.c" || fail "Unexpected allowlist format"
grep -Fq '#define KSU_APP_PROFILE_VER 4' "$SUKISU_DIR/uapi/app_profile.h" || fail "Unexpected app-profile ABI"

configure_toolchain
rm -rf "$AUDIT_OUT"
mkdir -p "$AUDIT_OUT"

info "Configuring warning-enabled SukiSU audit build"
set +e
{
  make -C "$KERNEL_DIR" O="$AUDIT_OUT" \
    DTC_EXT="$KERNEL_DIR/tools/dtc" \
    CONFIG_BUILD_ARM64_DT_OVERLAY=y \
    KCFLAGS="$AUDIT_FLAGS" \
    CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
    a52xq_defconfig
} 2>&1 | tee "$CONFIG_LOG"
config_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$config_rc" > "$CONFIG_STATUS"

AUDIT_CONFIG="$AUDIT_OUT/.config"
if test -s "$AUDIT_CONFIG"; then
  cp "$AUDIT_CONFIG" "$ARTIFACTS_DIR/config-sukisu-audit"
  {
    printf 'kernel_version=%s\n' "$(kernel_version)"
    printf 'configure_exit=%s\n' "$config_rc"
    grep -E '^(CONFIG_(KPROBES|HAVE_KPROBES|MODULES|EXT4_FS|KSU|KSU_MANUAL_SU|KSU_DEBUG|KPM|KSU_DISABLE_MANAGER|KSU_DISABLE_POLICY|KSU_SUSFS)=|# CONFIG_(KPROBES|KSU|KSU_MANUAL_SU|KSU_DEBUG|KPM|KSU_DISABLE_MANAGER|KSU_DISABLE_POLICY|KSU_SUSFS) is not set)' "$AUDIT_CONFIG" || true
  } | tee "$CONFIG_SUMMARY"
else
  {
    printf 'kernel_version=%s\n' "$(kernel_version)"
    printf 'configure_exit=%s\n' "$config_rc"
    printf 'generated_config=missing\n'
  } | tee "$CONFIG_SUMMARY"
fi

test "$config_rc" -eq 0 || fail "SukiSU audit configuration failed. See $CONFIG_LOG"
test -s "$AUDIT_CONFIG" || fail "Audit configuration was not generated"
grep -Fq 'CONFIG_KPROBES=y' "$AUDIT_CONFIG" || fail "Final config does not enable KPROBES"
grep -Fq 'CONFIG_KSU=y' "$AUDIT_CONFIG" || fail "Final config does not enable SukiSU"
grep -Fq 'CONFIG_KSU_MANUAL_SU=y' "$AUDIT_CONFIG" || fail "Final config does not enable manual su"
grep -Fq '# CONFIG_KSU_DEBUG is not set' "$AUDIT_CONFIG" || fail "SukiSU debug mode is enabled"
grep -Fq '# CONFIG_KPM is not set' "$AUDIT_CONFIG" || fail "KPM is enabled"
grep -Fq '# CONFIG_KSU_DISABLE_MANAGER is not set' "$AUDIT_CONFIG" || fail "Manager integration is disabled"
grep -Fq '# CONFIG_KSU_DISABLE_POLICY is not set' "$AUDIT_CONFIG" || fail "Allowlist policy is disabled"
! grep -Eq '^CONFIG_.*SUSFS.*=y$' "$AUDIT_CONFIG" || fail "SUSFS is unexpectedly enabled"

info "Compiling repaired BPF verifier and SukiSU directory with selected warnings as errors"
set +e
{
  make -C "$KERNEL_DIR" O="$AUDIT_OUT" \
    DTC_EXT="$KERNEL_DIR/tools/dtc" \
    CONFIG_BUILD_ARM64_DT_OVERLAY=y \
    KCFLAGS="$AUDIT_FLAGS" \
    CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
    W=1 V=1 -j"${JOBS:-4}" \
    kernel/bpf/verifier.o drivers/kernelsu/
} 2>&1 | tee "$AUDIT_LOG"
audit_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$audit_rc" > "$AUDIT_STATUS"
test "$audit_rc" -eq 0 || fail "Warning-enabled SukiSU audit build failed. See $AUDIT_LOG"

BPF_OBJECT="$AUDIT_OUT/kernel/bpf/verifier.o"
KSU_OBJECT="$AUDIT_OUT/drivers/kernelsu/kernelsu.o"
test -s "$BPF_OBJECT" || fail "BPF verifier object was not produced"
test -s "$KSU_OBJECT" || fail "SukiSU composite object was not produced"
cp "$BPF_OBJECT" "$ARTIFACTS_DIR/verifier-4.19.153-sukisu.o"
cp "$KSU_OBJECT" "$ARTIFACTS_DIR/sukisu-4.19.153-kernelsu.o"
sha256sum "$ARTIFACTS_DIR/verifier-4.19.153-sukisu.o" > "$ARTIFACTS_DIR/verifier-4.19.153-sukisu.o.sha256"
sha256sum "$ARTIFACTS_DIR/sukisu-4.19.153-kernelsu.o" > "$ARTIFACTS_DIR/sukisu-4.19.153-kernelsu.o.sha256"

warning_count=$(grep -Ec '(^|[[:space:]])[^[:space:]]+:[0-9]+:[0-9]+: warning:' "$AUDIT_LOG" || true)
error_count=$(grep -Ec '(^|[[:space:]])[^[:space:]]+:[0-9]+:[0-9]+: error:' "$AUDIT_LOG" || true)
incompatible_count=$(grep -Eci ':[0-9]+:[0-9]+: (warning|error): .*incompatible.*pointer' "$AUDIT_LOG" || true)
implicit_count=$(grep -Eci ':[0-9]+:[0-9]+: (warning|error): .*implicit.*function' "$AUDIT_LOG" || true)
uninitialized_count=$(grep -Eci ':[0-9]+:[0-9]+: (warning|error): .*uninitialized' "$AUDIT_LOG" || true)

test "$error_count" -eq 0 || fail "SukiSU audit log contains compiler errors"
test "$incompatible_count" -eq 0 || fail "SukiSU audit contains incompatible-pointer diagnostics"
test "$implicit_count" -eq 0 || fail "SukiSU audit contains implicit-function diagnostics"
test "$uninitialized_count" -eq 0 || fail "SukiSU audit contains uninitialized-variable diagnostics"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'compiler=%s\n' "$($CC --version | head -n 1)"
  printf 'audit_flags=%s\n' "$AUDIT_FLAGS"
  printf 'source_warning_diagnostics=%s\n' "$warning_count"
  printf 'source_error_diagnostics=%s\n' "$error_count"
  printf 'incompatible_pointer_diagnostics=%s\n' "$incompatible_count"
  printf 'implicit_function_diagnostics=%s\n' "$implicit_count"
  printf 'uninitialized_diagnostics=%s\n' "$uninitialized_count"
  printf 'bpf_object_sha256=%s\n' "$(cut -d' ' -f1 "$ARTIFACTS_DIR/verifier-4.19.153-sukisu.o.sha256")"
  printf 'sukisu_object_sha256=%s\n' "$(cut -d' ' -f1 "$ARTIFACTS_DIR/sukisu-4.19.153-kernelsu.o.sha256")"
  printf 'allowlist_file_format=4\n'
  printf 'app_profile_abi=4\n'
  printf 'kernel_unmount=permanently-disabled\n'
  printf 'kpm=disabled\n'
  printf 'susfs=not-integrated\n'
  printf 'runtime_tests=deferred-to-device\n'
} | tee "$ARTIFACTS_DIR/sukisu-audit.txt"

info "Targeted audits passed; building complete SukiSU kernel"
build_kernel "$BUILD_LABEL"

FINAL_CONFIG="$ARTIFACTS_DIR/config-$BUILD_LABEL"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-$BUILD_LABEL"
test -s "$FINAL_CONFIG" || fail "Full build configuration is missing"
test -s "$FINAL_IMAGE" || fail "Full SukiSU kernel Image is missing"
grep -Fq 'CONFIG_KPROBES=y' "$FINAL_CONFIG" || fail "Full build lost KPROBES"
grep -Fq 'CONFIG_KSU=y' "$FINAL_CONFIG" || fail "Full build lost SukiSU"
grep -Fq 'CONFIG_KSU_MANUAL_SU=y' "$FINAL_CONFIG" || fail "Full build lost manual su"
grep -Fq '# CONFIG_KPM is not set' "$FINAL_CONFIG" || fail "Full build enabled KPM"
! grep -Eq '^CONFIG_.*SUSFS.*=y$' "$FINAL_CONFIG" || fail "Full build enabled SUSFS"

strings "$FINAL_IMAGE" | grep -E 'SukiSU|KernelSU' | sort -u > "$ARTIFACTS_DIR/sukisu-image-strings.txt" || true

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'image_bytes=%s\n' "$(wc -c < "$FINAL_IMAGE")"
  printf 'image_sha256=%s\n' "$(cut -d' ' -f1 "$FINAL_IMAGE.sha256")"
  printf 'config_ksu=y\n'
  printf 'config_kprobes=y\n'
  printf 'config_manual_su=y\n'
  printf 'config_kpm=n\n'
  printf 'config_susfs=n\n'
  printf 'kernel_unmount=permanently-disabled-in-source\n'
  printf 'flashable_zip=not-created\n'
} | tee "$ARTIFACTS_DIR/sukisu-build-result.txt"

info "Linux $TARGET_VERSION with pinned SukiSU Ultra compiled successfully"
