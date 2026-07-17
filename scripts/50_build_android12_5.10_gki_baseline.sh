#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
GKI_ROOT="${GKI_ROOT:-$ROOT/gki}"
OUT_DIR="${OUT_DIR:-$ROOT/gki-out}"
DIST_DIR="${DIST_DIR:-$OUT_DIR/dist}"
ARTIFACT_DIR="${ARTIFACT_DIR:-$ROOT/artifacts/android12-5.10-gki-baseline}"
JOBS="${JOBS:-4}"
EXPECTED_TAG="android12-5.10-2026-04_r1"
EXPECTED_SHA="f960ed27302b1ff8e61e152fc202554d778deccd"
EXPECTED_VERSION_PREFIX="5.10.252"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -d "$GKI_ROOT/common/.git" ]] || fail "GKI common source is missing: $GKI_ROOT/common"
[[ -x "$GKI_ROOT/build/build.sh" ]] || fail "Android kernel build.sh is missing"

actual_sha="$(git -C "$GKI_ROOT/common" rev-parse HEAD)"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || fail "Expected common $EXPECTED_SHA, found $actual_sha"

git -C "$GKI_ROOT/common" diff --check

rm -rf "$OUT_DIR" "$ARTIFACT_DIR"
mkdir -p "$DIST_DIR" "$ARTIFACT_DIR"

(
  cd "$GKI_ROOT"
  BUILD_CONFIG=common/build.config.gki.aarch64 \
  OUT_DIR="$OUT_DIR" \
  DIST_DIR="$DIST_DIR" \
  LTO=thin \
  build/build.sh -j"$JOBS"
)

[[ -s "$DIST_DIR/Image" ]] || fail "GKI Image was not produced"
[[ -s "$DIST_DIR/Image.lz4" ]] || fail "GKI Image.lz4 was not produced"

config_path="$(find "$OUT_DIR" -type f -path '*/common/.config' -print -quit)"
[[ -n "$config_path" && -s "$config_path" ]] || fail "Built GKI .config was not found"

for required in \
  'CONFIG_ARM64=y' \
  'CONFIG_MODULES=y' \
  'CONFIG_ANDROID_BINDER_IPC=y'; do
  grep -Fxq "$required" "$config_path" || fail "Missing required GKI config: $required"
done

copy_if_present() {
  local source="$1"
  local name="${2:-$(basename "$source")}" 
  if [[ -s "$source" ]]; then
    cp -a "$source" "$ARTIFACT_DIR/$name"
  fi
}

copy_if_present "$DIST_DIR/Image"
copy_if_present "$DIST_DIR/Image.lz4"
copy_if_present "$DIST_DIR/vmlinux"
copy_if_present "$DIST_DIR/System.map"
copy_if_present "$DIST_DIR/vmlinux.symvers"
copy_if_present "$DIST_DIR/modules.builtin"
copy_if_present "$DIST_DIR/modules.builtin.modinfo"
copy_if_present "$DIST_DIR/abi.xml"
copy_if_present "$config_path" "gki-5.10.config"

kernel_release="$(strings "$DIST_DIR/Image" | grep -m1 '^Linux version 5\.10\.' || true)"
[[ "$kernel_release" == *"$EXPECTED_VERSION_PREFIX"* ]] || \
  fail "Built image does not identify as expected $EXPECTED_VERSION_PREFIX: $kernel_release"

cat > "$ARTIFACT_DIR/build-metadata.txt" <<EOF
artifact_type=official-gki-reference-not-flashable
manifest_branch=common-android12-5.10-2026-04
common_tag=$EXPECTED_TAG
common_commit=$actual_sha
expected_version_prefix=$EXPECTED_VERSION_PREFIX
build_config=common/build.config.gki.aarch64
architecture=arm64
lto=thin
kernel_version_string=$kernel_release
built_at_utc=$(date -u +%FT%TZ)
EOF

cat > "$ARTIFACT_DIR/NOTICE.txt" <<'EOF'
ANDROID 12 GKI 5.10 REFERENCE BUILD

This artifact is the unmodified official arm64 GKI baseline. It contains no
A52-specific Samsung/Qualcomm hardware layer and is NOT SAFE TO FLASH on a52xq.

The purpose of this build is to establish a reproducible 5.10 GKI source,
configuration, binary and KMI baseline before device-driver porting begins.
EOF

(
  cd "$ARTIFACT_DIR"
  sha256sum ./* > SHA256SUMS
)

printf 'Built official GKI baseline:\n'
cat "$ARTIFACT_DIR/build-metadata.txt"
cat "$ARTIFACT_DIR/SHA256SUMS"
