#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

rm -rf "$KERNEL_DIR"
mkdir -p "$WORKSPACE"

info "Fetching the exact touchGrass baseline commit"
git init "$KERNEL_DIR"
git -C "$KERNEL_DIR" remote add origin "$TOUCHGRASS_REPO"
git -C "$KERNEL_DIR" fetch --depth=1 origin "$TOUCHGRASS_COMMIT"
git -C "$KERNEL_DIR" checkout --detach FETCH_HEAD

actual_commit=$(git -C "$KERNEL_DIR" rev-parse HEAD)
actual_version=$(kernel_version)

test "$actual_commit" = "$TOUCHGRASS_COMMIT" || fail "Unexpected commit: $actual_commit"
test "$actual_version" = "$TOUCHGRASS_BASE_VERSION" || fail "Unexpected kernel version: $actual_version"

{
  printf 'repository=%s\n' "$TOUCHGRASS_REPO"
  printf 'commit=%s\n' "$actual_commit"
  printf 'kernel_version=%s\n' "$actual_version"
  printf 'prepared_at_utc=%s\n' "$(date -u +%FT%TZ)"
} | tee "$ARTIFACTS_DIR/source-baseline.txt"

info "Source verification passed"
