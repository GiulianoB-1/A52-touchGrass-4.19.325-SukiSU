#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

test -d "$KERNEL_DIR/.git" || fail "Run 01_prepare_source.sh first"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Source commit changed"
test "$(kernel_version)" = "$TOUCHGRASS_BASE_VERSION" || fail "Kernel version changed"

build_kernel "baseline-4.19.152"
info "Untouched touchGrass baseline compiled successfully"
