#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

RESUKISU_DIR="$WORKSPACE/resukisu-baseline"
REPORT="$ARTIFACTS_DIR/resukisu-baseline.txt"

test -n "${RESUKISU_REPO:-}" || fail "RESUKISU_REPO is not pinned"
test -n "${RESUKISU_COMMIT:-}" || fail "RESUKISU_COMMIT is not pinned"

info "Fetching exact pinned ReSukiSU source"
rm -rf "$RESUKISU_DIR"
git init -q "$RESUKISU_DIR"
git -C "$RESUKISU_DIR" remote add origin "$RESUKISU_REPO"
git -C "$RESUKISU_DIR" fetch --quiet --depth=1 origin "$RESUKISU_COMMIT"
git -C "$RESUKISU_DIR" checkout --quiet --detach FETCH_HEAD

actual_commit=$(git -C "$RESUKISU_DIR" rev-parse HEAD)
test "$actual_commit" = "$RESUKISU_COMMIT" || fail "ReSukiSU commit mismatch: $actual_commit"

for required in \
  kernel/Kconfig \
  kernel/core/init.c \
  kernel/feature/kernel_umount.c \
  kernel/manager/throne_tracker.c \
  kernel/policy/allowlist.c \
  kernel/selinux/selinux.c \
  kernel/supercall/supercall.c \
  kernel/tools/manual_hook_check.mk; do
  test -f "$RESUKISU_DIR/$required" || fail "Pinned ReSukiSU is missing $required"
done

grep -Fq 'config KSU_MANUAL_HOOK' "$RESUKISU_DIR/kernel/Kconfig" \
  || fail "ReSukiSU manual-hook mode is missing"
grep -Fq 'config KSU_SUSFS' "$RESUKISU_DIR/kernel/Kconfig" \
  || fail "ReSukiSU SUSFS hook mode is missing"
grep -Fq 'TRACK_THRONE_FROM_RENAMEAT | TRACK_THRONE_FORCE_SYNCHRONOUS' \
  "$RESUKISU_DIR/kernel/manager/throne_tracker.c" \
  || fail "ReSukiSU package-list rename tracking is not synchronous"
grep -Fq 'static bool ksu_kernel_umount_enabled = true;' \
  "$RESUKISU_DIR/kernel/feature/kernel_umount.c" \
  || fail "ReSukiSU kernel-unmount default changed and requires review"
grep -Fq 'default_non_root_profile.umount_modules = true;' \
  "$RESUKISU_DIR/kernel/policy/allowlist.c" \
  || fail "ReSukiSU default non-root unmount policy changed and requires review"
grep -Fq 'susfs_init();' "$RESUKISU_DIR/kernel/core/init.c" \
  || fail "ReSukiSU native SUSFS initialization is missing"
grep -Fq 'ksu_handle_susfs_cmd' "$RESUKISU_DIR/kernel/supercall/supercall.c" \
  || fail "ReSukiSU native SUSFS supercall handling is missing"

{
  printf 'repository=%s\n' "$RESUKISU_REPO"
  printf 'commit=%s\n' "$actual_commit"
  printf 'manual_hook_mode=present\n'
  printf 'native_susfs_mode=present\n'
  printf 'package_list_rename_tracking=synchronous\n'
  printf 'kernel_unmount_default=enabled-requires-hard-disable\n'
  printf 'non_root_profile_unmount_default=enabled-requires-hard-disable\n'
  printf 'integration_status=source-only-not-integrated\n'
  printf 'flashable_output=none\n'
} | tee "$REPORT"

info "Pinned ReSukiSU baseline verified without integrating it into the phone kernel"
