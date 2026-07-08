#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
AUDIT_OUT="$KERNEL_DIR/out-bpf-audit"
AUDIT_LOG="$LOG_DIR/build-bpf-verifier-audit.log"
AUDIT_STATUS="$ARTIFACTS_DIR/build-bpf-verifier-audit.status"
AUDIT_FLAGS="-Werror=incompatible-pointer-types -Werror=implicit-function-declaration -Werror=return-type -Werror=uninitialized"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before BPF audit"
test -f "$ARTIFACTS_DIR/bpf-verifier-repair.txt" || fail "BPF repair report is missing"
test -s "$ARTIFACTS_DIR/bpf-verifier-repair.patch" || fail "BPF repair patch is missing"

HEADER="$KERNEL_DIR/include/linux/bpf_verifier.h"
VERIFIER="$KERNEL_DIR/kernel/bpf/verifier.c"

! grep -RFn 'REG_LIVE_DONE' "$HEADER" "$VERIFIER" || fail "Dead REG_LIVE_DONE code remains"
! grep -Fq 'struct idpair' "$VERIFIER" || fail "Duplicate id-map type remains"
! grep -Eq '(^|[^A-Z_])ID_MAP_SIZE([^A-Z_]|$)' "$VERIFIER" || fail "Legacy ID_MAP_SIZE remains"
grep -Fq 'static bool check_ids(u32 old_id, u32 cur_id, struct bpf_id_pair *idmap)' "$VERIFIER" || fail "check_ids has the wrong type"
grep -Fq 'if (env->explore_alu_limits)' "$VERIFIER" || fail "explore_alu_limits guard is missing"
grep -Fq 'ctx_access = true;' "$VERIFIER" || fail "ctx_access read assignment is missing"
grep -Fq 'ctx_access = BPF_CLASS(insn->code) == BPF_STX;' "$VERIFIER" || fail "ctx_access write assignment is missing"

configure_toolchain
rm -rf "$AUDIT_OUT"
mkdir -p "$AUDIT_OUT"

info "Configuring warning-enabled BPF verifier audit"
make -C "$KERNEL_DIR" O="$AUDIT_OUT" \
  DTC_EXT="$KERNEL_DIR/tools/dtc" \
  CONFIG_BUILD_ARM64_DT_OVERLAY=y \
  KCFLAGS="$AUDIT_FLAGS" \
  CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
  a52xq_defconfig

info "Compiling kernel/bpf/verifier.o with selected warnings treated as errors"
set +e
{
  make -C "$KERNEL_DIR" O="$AUDIT_OUT" \
    DTC_EXT="$KERNEL_DIR/tools/dtc" \
    CONFIG_BUILD_ARM64_DT_OVERLAY=y \
    KCFLAGS="$AUDIT_FLAGS" \
    CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
    W=1 V=1 -j"${JOBS:-4}" kernel/bpf/verifier.o
} 2>&1 | tee "$AUDIT_LOG"
audit_rc=${PIPESTATUS[0]}
set -e

printf '%s\n' "$audit_rc" > "$AUDIT_STATUS"
test "$audit_rc" -eq 0 || fail "Warning-enabled BPF verifier build failed. See $AUDIT_LOG"

BPF_OBJECT="$AUDIT_OUT/kernel/bpf/verifier.o"
test -s "$BPF_OBJECT" || fail "BPF verifier object was not produced"
cp "$BPF_OBJECT" "$ARTIFACTS_DIR/verifier-4.19.153-repaired.o"
sha256sum "$ARTIFACTS_DIR/verifier-4.19.153-repaired.o" > "$ARTIFACTS_DIR/verifier-4.19.153-repaired.o.sha256"

# Count only real compiler diagnostics associated with source locations.
# Do not count the warning names appearing in compiler command-line flags.
warning_count=$(grep -Ec '(^|[[:space:]])[^[:space:]]+:[0-9]+:[0-9]+: warning:' "$AUDIT_LOG" || true)
error_count=$(grep -Ec '(^|[[:space:]])[^[:space:]]+:[0-9]+:[0-9]+: error:' "$AUDIT_LOG" || true)
incompatible_count=$(grep -Eci ':[0-9]+:[0-9]+: (warning|error): .*incompatible.*pointer' "$AUDIT_LOG" || true)
implicit_count=$(grep -Eci ':[0-9]+:[0-9]+: (warning|error): .*implicit.*function' "$AUDIT_LOG" || true)
uninitialized_count=$(grep -Eci ':[0-9]+:[0-9]+: (warning|error): .*uninitialized' "$AUDIT_LOG" || true)

test "$error_count" -eq 0 || fail "BPF audit log contains compiler errors"
test "$incompatible_count" -eq 0 || fail "BPF audit log still contains incompatible-pointer diagnostics"
test "$implicit_count" -eq 0 || fail "BPF audit log still contains implicit-function diagnostics"
test "$uninitialized_count" -eq 0 || fail "BPF audit log still contains uninitialized-variable diagnostics"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'compiler=%s\n' "$($CC --version | head -n 1)"
  printf 'audit_flags=%s\n' "$AUDIT_FLAGS"
  printf 'verifier_object_bytes=%s\n' "$(wc -c < "$BPF_OBJECT")"
  printf 'verifier_object_sha256=%s\n' "$(cut -d' ' -f1 "$ARTIFACTS_DIR/verifier-4.19.153-repaired.o.sha256")"
  printf 'source_warning_diagnostics=%s\n' "$warning_count"
  printf 'source_error_diagnostics=%s\n' "$error_count"
  printf 'incompatible_pointer_diagnostics=%s\n' "$incompatible_count"
  printf 'implicit_function_diagnostics=%s\n' "$implicit_count"
  printf 'uninitialized_diagnostics=%s\n' "$uninitialized_count"
  printf 'runtime_selftests=deferred-to-device\n'
} | tee "$ARTIFACTS_DIR/bpf-verifier-audit.txt"

info "Warning-enabled BPF verifier object passed; building full repaired kernel"
build_kernel "linux-4.19.153-bpf-repaired"

info "Linux $TARGET_VERSION with repaired BPF verifier compiled successfully"
