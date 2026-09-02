#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$PROJECT_DIR/sources.lock"

WORKSPACE="${WORKSPACE:-$PROJECT_DIR/workspace}"
KERNEL_DIR="${KERNEL_DIR:-$WORKSPACE/touchgrass-a52xq}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$PROJECT_DIR/artifacts}"
LOG_DIR="$ARTIFACTS_DIR/logs"
mkdir -p "$WORKSPACE" "$ARTIFACTS_DIR" "$LOG_DIR"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

kernel_version() {
  local tree="${1:-$KERNEL_DIR}"
  local makefile="$tree/Makefile"
  local version patchlevel sublevel
  version=$(awk '$1=="VERSION" {print $3; exit}' "$makefile")
  patchlevel=$(awk '$1=="PATCHLEVEL" {print $3; exit}' "$makefile")
  sublevel=$(awk '$1=="SUBLEVEL" {print $3; exit}' "$makefile")
  printf '%s.%s.%s\n' "$version" "$patchlevel" "$sublevel"
}

configure_toolchain() {
  local clang="$KERNEL_DIR/toolchain/clang/host/linux-x86/clang-r383902/bin/clang"

  export ARCH=arm64
  export PROJECT_NAME=a52xq
  export PATH="$KERNEL_DIR/toolchain/toolchains-gcc-10.3.0/bin:$PATH"
  export CROSS_COMPILE="$KERNEL_DIR/toolchain/toolchains-gcc-10.3.0/bin/aarch64-buildroot-linux-gnu-"
  export CLANG_TRIPLE=aarch64-linux-gnu-
  export KCFLAGS=-w
  export CONFIG_SECTION_MISMATCH_WARN_ONLY=y
  export CONFIG_DRV_BUILD_IN=Y

  test -x "$clang" || fail "Bundled clang was not found: $clang"
  test -x "${CROSS_COMPILE}gcc" || fail "Bundled cross compiler was not found: ${CROSS_COMPILE}gcc"

  if command -v ccache >/dev/null 2>&1; then
    export CC="ccache $clang"
    export CCACHE_BASEDIR="$KERNEL_DIR"
    export CCACHE_NOHASHDIR=true
    export CCACHE_COMPILERCHECK=content
    ccache --max-size "${CCACHE_MAXSIZE:-5G}" >/dev/null
  else
    export CC="$clang"
  fi
}

build_kernel() {
  local label="$1"
  local jobs="${JOBS:-$(nproc)}"
  local log="$LOG_DIR/build-$label.log"
  local status="$ARTIFACTS_DIR/build-$label.status"
  local -a keep_going=()

  if [ "${MAKE_KEEP_GOING:-0}" = "1" ]; then
    keep_going=(-k)
  fi

  configure_toolchain
  rm -rf "$KERNEL_DIR/out"
  mkdir -p "$KERNEL_DIR/out"

  info "Building $label with $jobs jobs"
  set +e
  {
    make -C "$KERNEL_DIR" O="$KERNEL_DIR/out" \
      DTC_EXT="$KERNEL_DIR/tools/dtc" \
      CONFIG_BUILD_ARM64_DT_OVERLAY=y \
      KCFLAGS=-w CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
      a52xq_defconfig &&
    make -C "$KERNEL_DIR" O="$KERNEL_DIR/out" \
      DTC_EXT="$KERNEL_DIR/tools/dtc" \
      CONFIG_BUILD_ARM64_DT_OVERLAY=y \
      KCFLAGS=-w CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
      "${keep_going[@]}" -j"$jobs"
  } 2>&1 | tee "$log"
  local rc=${PIPESTATUS[0]}
  set -e

  if command -v ccache >/dev/null 2>&1; then
    ccache --show-stats > "$ARTIFACTS_DIR/ccache-$label.txt" 2>&1 || true
  fi

  printf '%s\n' "$rc" > "$status"
  test "$rc" -eq 0 || fail "Build failed. See $log"

  local image="$KERNEL_DIR/out/arch/arm64/boot/Image"
  test -s "$image" || fail "Build completed without producing Image"
  cp "$image" "$ARTIFACTS_DIR/Image-$label"
  cp "$KERNEL_DIR/out/.config" "$ARTIFACTS_DIR/config-$label"
  sha256sum "$ARTIFACTS_DIR/Image-$label" | tee "$ARTIFACTS_DIR/Image-$label.sha256"
}
