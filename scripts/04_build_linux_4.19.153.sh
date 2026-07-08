#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153

test -d "$KERNEL_DIR/.git" || fail "Run 01_prepare_source.sh first"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before building"
test -f "$ARTIFACTS_DIR/apply-result-v4.19.153.txt" || fail "Stable update result file is missing"
grep -q '^apply_exit=0$' "$ARTIFACTS_DIR/apply-result-v4.19.153.txt" || fail "Stable update did not complete successfully"

build_kernel "linux-4.19.153"
info "Linux $TARGET_VERSION compiled successfully"
