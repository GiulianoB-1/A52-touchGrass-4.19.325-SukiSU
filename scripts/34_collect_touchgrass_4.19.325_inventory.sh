#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
DEST="$PROJECT_DIR/artifacts/compatibility/touchgrass-4.19.325"
OUT_DIR="$KERNEL_DIR/out"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION source"
[[ -s "$OUT_DIR/arch/arm64/boot/Image" ]] || fail "touchGrass Image is missing"
[[ -s "$OUT_DIR/.config" ]] || fail "touchGrass final config is missing"
[[ -s "$OUT_DIR/Module.symvers" ]] || fail "touchGrass Module.symvers is missing"

rm -rf "$DEST"
mkdir -p "$DEST"

cp "$OUT_DIR/arch/arm64/boot/Image" "$DEST/Image"
cp "$OUT_DIR/.config" "$DEST/config"
cp "$OUT_DIR/Module.symvers" "$DEST/Module.symvers"
[[ -s "$OUT_DIR/System.map" ]] && cp "$OUT_DIR/System.map" "$DEST/System.map"

find "$OUT_DIR" -type f -name '*.ko' -printf '%P\n' | sort > "$DEST/modules.list"
find "$OUT_DIR/arch/arm64/boot" -type f \( -name '*.dtb' -o -name '*.dtbo' \) \
  -printf '%P\n' | sort > "$DEST/device-trees.list"

strings "$DEST/Image" | grep -E 'Linux version|touchGrassKernel|qcom,lagoon|a52xq|sm7125|sm7225' \
  | sort -u > "$DEST/image-platform-strings.txt" || true

{
  echo "source_repository=$TOUCHGRASS_REPO"
  echo "source_base_commit=$TOUCHGRASS_COMMIT"
  echo "kernel_version=$(kernel_version)"
  echo "kernel_release=$(make -s -C "$KERNEL_DIR" O="$OUT_DIR" ARCH=arm64 kernelrelease)"
  echo "image_bytes=$(stat -c %s "$DEST/Image")"
  echo "module_count=$(wc -l < "$DEST/modules.list")"
  echo "device_tree_count=$(wc -l < "$DEST/device-trees.list")"
  echo "exported_symbol_count=$(wc -l < "$DEST/Module.symvers")"
  echo "flashable=no"
} > "$DEST/metadata.txt"

(
  cd "$DEST"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
