#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION="${1:-}"
IMAGE_PATH="${2:-}"
ANYKERNEL_DIR="${3:-}"
RELEASE_DIR="${4:-$PROJECT_DIR/release}"
ANYKERNEL_COMMIT="${ANYKERNEL_COMMIT:-unknown}"

case "$TARGET_VERSION" in
  4.19.*) ;;
  *) fail "Expected a Linux 4.19.x target version" ;;
esac

test -s "$IMAGE_PATH" || fail "Kernel Image is missing: $IMAGE_PATH"
test -d "$ANYKERNEL_DIR" || fail "AnyKernel3 checkout is missing: $ANYKERNEL_DIR"
test -f "$ANYKERNEL_DIR/tools/ak3-core.sh" || fail "AnyKernel3 core is missing"
test -f "$ANYKERNEL_DIR/META-INF/com/google/android/update-binary" || fail "AnyKernel3 update-binary is missing"

IMAGE_BASENAME="$(basename "$IMAGE_PATH")"
CONFIG_PATH="$(dirname "$IMAGE_PATH")/config-${IMAGE_BASENAME#Image-}"
test -s "$CONFIG_PATH" || fail "Matching kernel config is missing: $CONFIG_PATH"

grep -Fxq 'CONFIG_KSU=y' "$CONFIG_PATH" || fail "Kernel config does not enable ReSukiSU"
grep -Fxq 'CONFIG_KSU_MANUAL_HOOK=y' "$CONFIG_PATH" || fail "Kernel config does not enable manual hooks"
grep -Fxq '# CONFIG_KSU_SUSFS is not set' "$CONFIG_PATH" || fail "SUSFS must remain disabled for this package"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

strings "$IMAGE_PATH" > "$WORK_DIR/Image.strings"
grep -Fq "Linux version $TARGET_VERSION-touchGrassKernel+" "$WORK_DIR/Image.strings" \
  || fail "Kernel Image does not contain the expected Linux release string"
grep -Fxq "$RESUKISU_VERSION_FULL" "$WORK_DIR/Image.strings" \
  || fail "Kernel Image does not contain the pinned ReSukiSU version"

info "Preparing A52xq AnyKernel3 package"
cp -a "$ANYKERNEL_DIR/." "$WORK_DIR/package"
PACKAGE_DIR="$WORK_DIR/package"

rm -rf \
  "$PACKAGE_DIR/.git" \
  "$PACKAGE_DIR/.github" \
  "$PACKAGE_DIR/ramdisk" \
  "$PACKAGE_DIR/patch" \
  "$PACKAGE_DIR/modules" \
  "$PACKAGE_DIR/vendor_ramdisk" \
  "$PACKAGE_DIR/vendor_patch" \
  "$PACKAGE_DIR/vendor_modules"
rm -f \
  "$PACKAGE_DIR"/Image* \
  "$PACKAGE_DIR"/zImage* \
  "$PACKAGE_DIR"/dtb \
  "$PACKAGE_DIR"/dtb.* \
  "$PACKAGE_DIR"/dtbo.img \
  "$PACKAGE_DIR"/README* \
  "$PACKAGE_DIR"/.gitignore \
  "$PACKAGE_DIR"/.gitattributes

cat > "$PACKAGE_DIR/anykernel.sh" <<EOF
### AnyKernel3 Ramdisk Mod Script
## Samsung Galaxy A52 5G (a52xq)

properties() { '
kernel.string=touchGrass A52xq Linux $TARGET_VERSION + ReSukiSU v4.1.0
do.devicecheck=1
do.modules=0
do.systemless=1
do.cleanup=1
do.cleanuponabort=0
device.name1=a52xq
device.name2=a52xqxx
device.name3=SM-A526B
device.name4=
device.name5=
supported.versions=
supported.patchlevels=
supported.vendorpatchlevels=
'; }

boot_attributes() {
set_perm_recursive 0 0 755 644 \$RAMDISK/*;
set_perm_recursive 0 0 750 750 \$RAMDISK/init* \$RAMDISK/sbin;
}

BLOCK=/dev/block/by-name/boot;
IS_SLOT_DEVICE=0;
RAMDISK_COMPRESSION=auto;
PATCH_VBMETA_FLAG=false;

. tools/ak3-core.sh;

dump_boot;
write_boot;
EOF

cp "$IMAGE_PATH" "$PACKAGE_DIR/Image"
IMAGE_SHA256="$(sha256sum "$IMAGE_PATH" | awk '{print $1}')"
IMAGE_BYTES="$(stat -c %s "$IMAGE_PATH")"

cat > "$PACKAGE_DIR/BUILD-INFO.txt" <<EOF
Samsung Galaxy A52 5G kernel package

device=a52xq
model=SM-A526B
kernel_version=$TARGET_VERSION-touchGrassKernel+
resukisu=$RESUKISU_VERSION_FULL
resukisu_commit=$RESUKISU_COMMIT
source_commit=$TOUCHGRASS_COMMIT
anykernel3_commit=$ANYKERNEL_COMMIT
boot_partition=/dev/block/by-name/boot
slot_device=no
susfs=disabled
kernel_unmount_default=off
module_unmount_default=off
image_sha256=$IMAGE_SHA256
image_bytes=$IMAGE_BYTES
physical_boot_test=not-performed
EOF

chmod 755 "$PACKAGE_DIR/anykernel.sh"
chmod 755 "$PACKAGE_DIR/META-INF/com/google/android/update-binary"
find "$PACKAGE_DIR/tools" -type f -exec chmod 755 {} +

mkdir -p "$RELEASE_DIR"
RELEASE_DIR="$(cd "$RELEASE_DIR" && pwd)"
ZIP_BASENAME="touchGrass-A52xq-Linux-$TARGET_VERSION-ReSukiSU-v4.1.0-BUILD-VERIFIED-FLASHABLE.zip"
OUTPUT_ZIP="$RELEASE_DIR/$ZIP_BASENAME"
rm -f "$OUTPUT_ZIP" "$OUTPUT_ZIP.sha256" "${OUTPUT_ZIP%.zip}-VERIFICATION.txt"

(
  cd "$PACKAGE_DIR"
  zip -r9 "$OUTPUT_ZIP" anykernel.sh Image BUILD-INFO.txt META-INF tools
)

unzip -tq "$OUTPUT_ZIP"

python3 - "$OUTPUT_ZIP" "$IMAGE_PATH" <<'PY'
from __future__ import annotations

import hashlib
import stat
import sys
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1])
image_path = Path(sys.argv[2])

with zipfile.ZipFile(zip_path) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SystemExit("duplicate ZIP entries detected")

    required = {
        "anykernel.sh",
        "Image",
        "BUILD-INFO.txt",
        "META-INF/com/google/android/update-binary",
        "tools/ak3-core.sh",
    }
    missing = sorted(required.difference(names))
    if missing:
        raise SystemExit(f"missing required AnyKernel3 entries: {missing}")

    script = archive.read("anykernel.sh").decode("utf-8")
    checks = (
        "do.devicecheck=1",
        "device.name1=a52xq",
        "BLOCK=/dev/block/by-name/boot;",
        "IS_SLOT_DEVICE=0;",
        "dump_boot;",
        "write_boot;",
    )
    for expected in checks:
        if expected not in script:
            raise SystemExit(f"missing installer safety setting: {expected}")

    embedded_hash = hashlib.sha256(archive.read("Image")).hexdigest()
    source_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if embedded_hash != source_hash:
        raise SystemExit("embedded Image differs from the successful build Image")

    for name in ("META-INF/com/google/android/update-binary", "tools/ak3-core.sh"):
        mode = archive.getinfo(name).external_attr >> 16
        if not mode & stat.S_IXUSR:
            raise SystemExit(f"required executable permission is missing: {name}")
PY

ZIP_SHA256="$(sha256sum "$OUTPUT_ZIP" | awk '{print $1}')"
ZIP_BYTES="$(stat -c %s "$OUTPUT_ZIP")"
ENTRY_COUNT="$(zipinfo -1 "$OUTPUT_ZIP" | wc -l | tr -d ' ')"
printf '%s  %s\n' "$ZIP_SHA256" "$ZIP_BASENAME" > "$OUTPUT_ZIP.sha256"

cat > "${OUTPUT_ZIP%.zip}-VERIFICATION.txt" <<EOF
Samsung Galaxy A52 5G (a52xq) flashable package verification

Package: $ZIP_BASENAME
Package SHA-256: $ZIP_SHA256
Package bytes: $ZIP_BYTES
ZIP entries: $ENTRY_COUNT
Kernel: Linux $TARGET_VERSION-touchGrassKernel+
Kernel Image SHA-256: $IMAGE_SHA256
Kernel Image bytes: $IMAGE_BYTES
ReSukiSU: $RESUKISU_VERSION_FULL
ReSukiSU commit: $RESUKISU_COMMIT
Target device: Samsung Galaxy A52 5G (a52xq / SM-A526B)
Installer: pinned AnyKernel3 $ANYKERNEL_COMMIT
Boot partition: /dev/block/by-name/boot
Slot device: no
SUSFS: disabled
Kernel unmount default: off
Module unmount default: off
ZIP integrity test: passed
Embedded Image match: passed
Installer safety checks: passed
Physical boot test: not performed
EOF

info "Created build-verified A52xq flashable ZIP: $OUTPUT_ZIP"
