#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/rom-sources.lock"

WORK_DIR="${WORK_DIR:-$ROOT_DIR/work-un1ca-sm7225}"
SRC_DIR="$WORK_DIR/source"
DEST="$ROOT_DIR/artifacts/compatibility/un1ca-sm7225"

rm -rf "$WORK_DIR" "$DEST"
mkdir -p "$WORK_DIR" "$DEST/source-files"

git init -q "$SRC_DIR"
git -C "$SRC_DIR" remote add origin "$UN1CA_REPO"
git -C "$SRC_DIR" config core.sparseCheckout true
cat > "$SRC_DIR/.git/info/sparse-checkout" <<'EOF'
/README.md
/.github/workflows/build.yml
/platform/sm7225/config.sh
/platform/sm7225/installer/recovery.fstab
/target/a52xq/config.sh
/target/a52xq/patches/kernel/customize.sh
/target/a52xq/patches/kernel/module.prop
/target/a52xq/patches/wpss/vendor/etc/init/wifi_firmware.rc
/target/a52xq/vintf/compatibility_matrix.device.xml
EOF

git -C "$SRC_DIR" fetch --no-tags --depth=1 origin "$UN1CA_COMMIT"
git -C "$SRC_DIR" checkout --detach FETCH_HEAD
actual_commit="$(git -C "$SRC_DIR" rev-parse HEAD)"
[[ "$actual_commit" == "$UN1CA_COMMIT" ]] || {
  echo "UN1CA commit mismatch: expected $UN1CA_COMMIT, got $actual_commit" >&2
  exit 1
}

while IFS= read -r relative_path; do
  [[ -f "$SRC_DIR/$relative_path" ]] || {
    echo "Missing pinned UN1CA file: $relative_path" >&2
    exit 1
  }
  mkdir -p "$DEST/source-files/$(dirname "$relative_path")"
  cp "$SRC_DIR/$relative_path" "$DEST/source-files/$relative_path"
done <<'EOF'
README.md
.github/workflows/build.yml
platform/sm7225/config.sh
platform/sm7225/installer/recovery.fstab
target/a52xq/config.sh
target/a52xq/patches/kernel/customize.sh
target/a52xq/patches/kernel/module.prop
target/a52xq/patches/wpss/vendor/etc/init/wifi_firmware.rc
target/a52xq/vintf/compatibility_matrix.device.xml
EOF

platform_config="$SRC_DIR/platform/sm7225/config.sh"
target_config="$SRC_DIR/target/a52xq/config.sh"
kernel_patch="$SRC_DIR/target/a52xq/patches/kernel/customize.sh"
fstab="$SRC_DIR/platform/sm7225/installer/recovery.fstab"
vintf="$SRC_DIR/target/a52xq/vintf/compatibility_matrix.device.xml"

read_assignment() {
  local file="$1"
  local key="$2"
  sed -n "s/^${key}=//p" "$file" | head -n 1 | tr -d '"'
}

boot_size="$(read_assignment "$platform_config" TARGET_BOOT_PARTITION_SIZE)"
dtbo_size="$(read_assignment "$platform_config" TARGET_DTBO_PARTITION_SIZE)"
vendor_boot_size="$(read_assignment "$platform_config" TARGET_VENDOR_BOOT_PARTITION_SIZE)"
board_api="$(read_assignment "$platform_config" TARGET_BOARD_API_LEVEL)"
shipping_api="$(read_assignment "$target_config" TARGET_PRODUCT_SHIPPING_API_LEVEL)"
platform_sdk="$(read_assignment "$target_config" TARGET_PLATFORM_SDK_VERSION)"
firmware="$(read_assignment "$target_config" TARGET_FIRMWARE)"
super_size="$(read_assignment "$target_config" TARGET_SUPER_PARTITION_SIZE)"

kernel_url="$(sed -n 's/^KERNEL_ZIP="\(.*\)"/\1/p' "$kernel_patch" | head -n 1)"
kernel_member="$(sed -n 's/.*unzip .* "\([^"]*Image[^" ]*\)".*/\1/p' "$kernel_patch" | head -n 1)"

[[ "$kernel_url" == "$UN1CA_KERNEL_ZIP" ]] || {
  echo "Pinned kernel URL no longer matches UN1CA source" >&2
  exit 1
}
[[ "$kernel_member" == "$UN1CA_KERNEL_MEMBER" ]] || {
  echo "Pinned kernel member no longer matches UN1CA source" >&2
  exit 1
}

grep -Fq 'unpack_bootimg --boot_img' "$kernel_patch"
grep -Fq 'mv "$TMP_DIR/out/Image.gz" "$TMP_DIR/out/kernel"' "$kernel_patch"
grep -Fq 'mkbootimg $MKBOOTIMG_ARGS' "$kernel_patch"
grep -Fq 'SEANDROIDENFORCE' "$kernel_patch"

{
  echo "repository=$UN1CA_REPO"
  echo "branch=$UN1CA_BRANCH"
  echo "commit=$actual_commit"
  echo "platform=$UN1CA_PLATFORM"
  echo "target=$UN1CA_TARGET"
  echo "firmware=$firmware"
  echo "board_api_level=$board_api"
  echo "shipping_api_level=$shipping_api"
  echo "platform_sdk_version=$platform_sdk"
  echo "boot_partition_bytes=$boot_size"
  echo "dtbo_partition_bytes=$dtbo_size"
  echo "vendor_boot_partition_bytes=$vendor_boot_size"
  echo "super_partition_bytes=$super_size"
  echo "kernel_zip=$kernel_url"
  echo "kernel_member=$kernel_member"
} > "$DEST/metadata.txt"

awk 'NF && $1 !~ /^#/' "$fstab" > "$DEST/recovery-fstab.entries.txt"
grep -E '<kernel-sepolicy-version>|<sepolicy-version>|<vbmeta-version>' "$vintf" \
  > "$DEST/security-compatibility.txt"

grep -E '^on |property:ro\.boot\.(rp|em\.model)' \
  "$SRC_DIR/target/a52xq/patches/wpss/vendor/etc/init/wifi_firmware.rc" \
  > "$DEST/wifi-firmware-selection.txt"

cat > "$DEST/UN1CA-ROM-REQUIREMENTS.md" <<EOF
# UN1CA SM7225 requirements for the A52 GKI project

## Pinned ROM source

- Repository: \`$UN1CA_REPO\`
- Branch: \`$UN1CA_BRANCH\`
- Commit: \`$actual_commit\`
- Target: \`$UN1CA_TARGET\`
- Platform: \`$UN1CA_PLATFORM\`
- Stock firmware identity: \`$firmware\`

## Kernel integration contract

UN1CA does not rebuild the A52 boot environment around a new kernel format. Its device patch:

1. unpacks the existing \`boot.img\`,
2. downloads the TouchGrass kernel ZIP,
3. extracts \`$kernel_member\`,
4. replaces only the unpacked kernel payload,
5. repacks with the original \`mkbootimg\` arguments,
6. appends the Samsung \`SEANDROIDENFORCE\` trailer.

Therefore the first compatible GKI-derived candidate must be delivered as a gzip-compressed arm64 kernel payload that can replace the existing payload without changing the ramdisk, header arguments, DTB placement, page size, or offsets.

## Partition and Android constraints

| Requirement | Value |
|---|---:|
| Boot partition | $boot_size bytes |
| DTBO partition | $dtbo_size bytes |
| Vendor boot partition | $vendor_boot_size bytes |
| Super partition | $super_size bytes |
| Board API level | $board_api |
| Product shipping API level | $shipping_api |
| Target platform SDK | $platform_sdk |
| Dynamic partitions | enabled |
| ROM filesystem | EROFS |

The recovery fstab names \`boot\`, \`dtbo\`, \`vendor_boot\`, \`vbmeta_system\`, \`vbmeta_samsung\`, and logical first-stage \`system\`, \`vendor\`, \`product\`, and \`odm\` partitions. Presence in the installer configuration does not prove that UN1CA uses a GKI header-v3 vendor ramdisk arrangement. The generated binary \`boot.img\` and \`vendor_boot.img\` still need to be unpacked and measured.

## Implications for the hybrid kernel

- Preserve Samsung's existing boot image structure during the first boot probe.
- Produce and validate \`Image.gz\`, not only raw \`Image\`.
- Keep UFS, IOMMU, clocks, RPMh, regulators, binder, SELinux, dm-verity, EROFS and the first-stage filesystem chain available early enough for the ROM.
- Treat Wi-Fi firmware selection as userspace/firmware work keyed by \`ro.boot.rp\` and \`ro.boot.em.model\`; it is not evidence that a generic GKI WLAN driver is sufficient.
- Keep the existing A52 DTB/DTBO selection until a controlled device-tree migration is proven.

## Remaining binary inputs

The source repository is enough to define the packaging contract, but not enough to verify the exact boot binary layout. The most useful remaining files are the generated UN1CA \`boot.img\`, \`vendor_boot.img\`, \`dtbo.img\`, and module directories from \`vendor\`, \`vendor_dlkm\`, or \`system_dlkm\` if present.
EOF

(
  cd "$DEST"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
