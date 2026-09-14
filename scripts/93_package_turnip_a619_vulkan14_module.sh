#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$1"
BUILD_DIR="$2"
OUT_DIR="$3"

DRIVER64="$BUILD_DIR/arm64/vulkan.adreno.so"
DRIVER32="$BUILD_DIR/arm32/vulkan.adreno.so"
INFO="$BUILD_DIR/BUILD-INFO.txt"

test -s "$DRIVER64"
test -s "$DRIVER32"
test -s "$INFO"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/module/payload/lib64" "$OUT_DIR/module/payload/lib"

cp "$DRIVER64" "$OUT_DIR/module/payload/lib64/vulkan.adreno.so"
cp "$DRIVER32" "$OUT_DIR/module/payload/lib/vulkan.adreno.so"
cp "$INFO" "$OUT_DIR/module/BUILD-INFO.txt"

sha256sum "$OUT_DIR/module/payload/lib64/vulkan.adreno.so" > "$OUT_DIR/module/driver64.sha256"
sha256sum "$OUT_DIR/module/payload/lib/vulkan.adreno.so" > "$OUT_DIR/module/driver32.sha256"

cat > "$OUT_DIR/module/module.prop" <<'EOF'
id=touchgrass_turnip_a619_vk14
name=touchGrass Turnip A619 Mesa 26.2.2 Vulkan 1.4
version=0.2-vk1.4-dualabi
versionCode=2
author=touchGrass project
description=Systemless Mesa 26.2.2 Turnip Vulkan 1.4 replacement for Adreno 619/KGSL. Replaces both 64-bit and 32-bit stock Qualcomm Vulkan HALs.
EOF

cat > "$OUT_DIR/module/customize.sh" <<'EOF'
SKIPUNZIP=0

ui_print "*************************************************"
ui_print " touchGrass Turnip A619 / Mesa 26.2.2"
ui_print " Vulkan 1.4 / KGSL / arm64 + arm32"
ui_print "*************************************************"

DEVICE="$(getprop ro.product.device)"
VENDOR_DEVICE="$(getprop ro.product.vendor.device)"
MODEL="$(getprop ro.product.model)"
DEVICE_API="$(getprop ro.build.version.sdk)"

case "$DEVICE:$VENDOR_DEVICE:$MODEL" in
  *a52xq*|*SM-A526B*) ;;
  *)
    abort "Unsupported device: device=$DEVICE vendor_device=$VENDOR_DEVICE model=$MODEL"
    ;;
esac

[ "$DEVICE_API" = "36" ] || ui_print "! Warning: package was built against Android API 36, device reports API $DEVICE_API"

[ -f /vendor/lib64/hw/vulkan.adreno.so ] || abort "Stock 64-bit Vulkan HAL not found"
[ -f /vendor/lib/hw/vulkan.adreno.so ] || abort "Stock 32-bit Vulkan HAL not found"

ui_print "- Stock Qualcomm Vulkan HALs found"
ui_print "- Installing Turnip Vulkan 1.4 for both ABIs"
ui_print "- /vendor itself will NOT be modified"
ui_print "- Disable/remove this module and reboot to roll back"

set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
set_perm "$MODPATH/payload/lib64/vulkan.adreno.so" 0 0 0644
set_perm "$MODPATH/payload/lib/vulkan.adreno.so" 0 0 0644
EOF

cat > "$OUT_DIR/module/post-fs-data.sh" <<'EOF'
#!/system/bin/sh
MODDIR="$(dirname "$0")"

TARGET64=/vendor/lib64/hw/vulkan.adreno.so
TARGET32=/vendor/lib/hw/vulkan.adreno.so
DRIVER64="$MODDIR/payload/lib64/vulkan.adreno.so"
DRIVER32="$MODDIR/payload/lib/vulkan.adreno.so"

STAGE_DIR=/dev/touchgrass-turnip-a619-vk14
STAGE64="$STAGE_DIR/lib64/vulkan.adreno.so"
STAGE32="$STAGE_DIR/lib/vulkan.adreno.so"
LOG="$MODDIR/turnip-mount.log"

exec >>"$LOG" 2>&1
echo "=== touchGrass Turnip Vulkan 1.4 mount $(date) ==="

fail() {
    echo "ERROR: $1"
    umount "$TARGET32" 2>/dev/null || true
    umount "$TARGET64" 2>/dev/null || true
    rm -rf "$STAGE_DIR"
    exit "$2"
}

[ -f "$TARGET64" ] || fail "stock 64-bit Vulkan HAL missing" 10
[ -f "$TARGET32" ] || fail "stock 32-bit Vulkan HAL missing" 11
[ -f "$DRIVER64" ] || fail "Turnip arm64 payload missing" 12
[ -f "$DRIVER32" ] || fail "Turnip arm32 payload missing" 13

mkdir -p "$STAGE_DIR/lib64" "$STAGE_DIR/lib" || fail "staging mkdir failed" 14
cp -f "$DRIVER64" "$STAGE64" || fail "arm64 staging copy failed" 15
cp -f "$DRIVER32" "$STAGE32" || fail "arm32 staging copy failed" 16

chown 0:0 "$STAGE64" "$STAGE32" 2>/dev/null || true
chmod 0644 "$STAGE64" "$STAGE32" || fail "chmod failed" 17
chcon u:object_r:vendor_file:s0 "$STAGE64" 2>/dev/null || true
chcon u:object_r:vendor_file:s0 "$STAGE32" 2>/dev/null || true

mount -o bind "$STAGE64" "$TARGET64" || fail "64-bit bind mount failed" 18
mount -o bind "$STAGE32" "$TARGET32" || fail "32-bit bind mount failed" 19

echo "Turnip Vulkan 1.4 dual-ABI bind mounts active"
echo "--- 64-bit ---"
ls -lZ "$TARGET64" 2>/dev/null || ls -l "$TARGET64"
sha256sum "$TARGET64" 2>/dev/null || true
echo "--- 32-bit ---"
ls -lZ "$TARGET32" 2>/dev/null || ls -l "$TARGET32"
sha256sum "$TARGET32" 2>/dev/null || true
EOF

cat > "$OUT_DIR/module/action.sh" <<'EOF'
#!/system/bin/sh
MODDIR="$(dirname "$0")"
OUT="$MODDIR/turnip-status.txt"

{
  echo "=== touchGrass Turnip A619 Vulkan 1.4 status ==="
  date
  echo
  echo "=== DEVICE ==="
  getprop ro.product.device
  getprop ro.product.model
  getprop ro.hardware.vulkan
  getprop ro.board.platform
  echo
  echo "=== 64-BIT HAL ==="
  ls -lZ /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || ls -l /vendor/lib64/hw/vulkan.adreno.so
  sha256sum /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
  echo
  echo "=== 32-BIT HAL ==="
  ls -lZ /vendor/lib/hw/vulkan.adreno.so 2>/dev/null || ls -l /vendor/lib/hw/vulkan.adreno.so
  sha256sum /vendor/lib/hw/vulkan.adreno.so 2>/dev/null || true
  echo
  echo "=== MOUNTS ==="
  cat /proc/mounts | grep -F 'vulkan.adreno.so' || true
  echo
  echo "=== VULKAN JSON ==="
  cmd gpu vkjson 2>&1 || true
  echo
  echo "=== SURFACEFLINGER ==="
  dumpsys SurfaceFlinger 2>&1 | grep -i -E 'RE Vulkan|RE GLES|Vulkan device|Ganesh|driver|Adreno|Turnip|Mesa' || true
  echo
  echo "=== GPU/KGSL FAULTS ==="
  dmesg | grep -Ei 'kgsl|adreno|gmu|gpu|iommu|smmu' | grep -Ei 'fault|error|timeout|hang|panic|oops|BUG|recover|reset' | tail -150
} > "$OUT" 2>&1

cat "$OUT"
EOF

cat > "$OUT_DIR/module/uninstall.sh" <<'EOF'
#!/system/bin/sh
umount /vendor/lib/hw/vulkan.adreno.so 2>/dev/null || true
umount /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
rm -rf /dev/touchgrass-turnip-a619-vk14
EOF

cat > "$OUT_DIR/module/README.txt" <<'EOF'
touchGrass Turnip A619 Mesa 26.2.2 Vulkan 1.4

Target:
  Samsung Galaxy A52 5G / a52xq / SM-A526B
  Adreno 619
  Android 16 / API 36
  KGSL kernel backend

Purpose:
  Replace the stock Qualcomm Vulkan 1.1 HAL with Mesa Turnip Vulkan 1.4.

Replacement targets:
  /vendor/lib64/hw/vulkan.adreno.so
  /vendor/lib/hw/vulkan.adreno.so

Both 64-bit and 32-bit Vulkan HALs are replaced systemlessly at post-fs-data
using bind mounts from /dev tmpfs. The vendor partition is not modified.

Install:
  Flash this ZIP from the KernelSU/SukiSU module manager, then reboot.

Rollback:
  Disable or remove the module and reboot.

Emergency rollback with ADB:
  adb shell su -c "touch /data/adb/modules/touchgrass_turnip_a619_vk14/disable; reboot"

KernelSU modules are not custom-recovery flashable. Use the KernelSU/SukiSU
manager for this Vulkan driver package.
EOF

chmod +x "$OUT_DIR/module/post-fs-data.sh" "$OUT_DIR/module/action.sh" "$OUT_DIR/module/uninstall.sh"

ZIP="$OUT_DIR/touchGrass-A52XQ-Turnip-Mesa-26.2.2-Vulkan-1.4-KSU.zip"
(
  cd "$OUT_DIR/module"
  zip -9 -r "../$(basename "$ZIP")" .
)

test -s "$ZIP"
unzip -tq "$ZIP"
unzip -p "$ZIP" module.prop | grep -Fxq 'id=touchgrass_turnip_a619_vk14'
unzip -p "$ZIP" module.prop | grep -Fq 'Vulkan 1.4'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq '/vendor/lib64/hw/vulkan.adreno.so'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq '/vendor/lib/hw/vulkan.adreno.so'
unzip -l "$ZIP" | grep -Fq 'payload/lib64/vulkan.adreno.so'
unzip -l "$ZIP" | grep -Fq 'payload/lib/vulkan.adreno.so'
sha256sum "$ZIP" > "$ZIP.sha256"

echo "packaged=$ZIP"
cat "$ZIP.sha256"
