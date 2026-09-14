#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
BUILD_DIR="${2:-$ROOT/artifacts/turnip-mesa-26.2.2}"
OUT_DIR="${3:-$ROOT/release-turnip}"

DRIVER="$BUILD_DIR/dist/vulkan.adreno.so"
INFO="$BUILD_DIR/BUILD-INFO.txt"

test -s "$DRIVER"
test -s "$INFO"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/module/payload"

cp "$DRIVER" "$OUT_DIR/module/payload/vulkan.adreno.so"
cp "$INFO" "$OUT_DIR/module/BUILD-INFO.txt"
sha256sum "$OUT_DIR/module/payload/vulkan.adreno.so" > "$OUT_DIR/module/driver.sha256"

cat > "$OUT_DIR/module/module.prop" <<'EOF'
id=touchgrass_turnip_a619
name=touchGrass Turnip A619 Mesa 26.2.2
version=0.1.1-vk1.3-selinuxfix
versionCode=2
author=touchGrass project
description=Mesa 26.2.2 Turnip Vulkan 1.3 boot override for Adreno 619/KGSL. Fixes the HAL SELinux label to same_process_hal_file and records SurfaceFlinger boot diagnostics.
EOF

cat > "$OUT_DIR/module/customize.sh" <<'EOF'
SKIPUNZIP=0

ui_print "***************************************"
ui_print " touchGrass Turnip A619 / Mesa 26.2.2"
ui_print " Vulkan 1.3 / KGSL / arm64 bring-up"
ui_print "***************************************"

DEVICE="$(getprop ro.product.device)"
VENDOR_DEVICE="$(getprop ro.product.vendor.device)"
MODEL="$(getprop ro.product.model)"

case "$DEVICE:$VENDOR_DEVICE:$MODEL" in
  *a52xq*|*SM-A526B*) ;;
  *)
    abort "Unsupported device: device=$DEVICE vendor_device=$VENDOR_DEVICE model=$MODEL"
    ;;
esac

[ -f /vendor/lib64/hw/vulkan.adreno.so ] || abort "Stock /vendor/lib64/hw/vulkan.adreno.so not found"

ui_print "- Stock Vulkan HAL found"
ui_print "- Installing 64-bit Turnip override"
ui_print "- SELinux fix: same_process_hal_file"
ui_print "- Original vendor partition will not be modified"

set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/payload/vulkan.adreno.so" 0 0 0644
EOF

cat > "$OUT_DIR/module/post-fs-data.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
TARGET=/vendor/lib64/hw/vulkan.adreno.so
DRIVER="$MODDIR/payload/vulkan.adreno.so"
STAGE_DIR=/dev/touchgrass-turnip-a619
STAGE="$STAGE_DIR/vulkan.adreno.so"
LOG="$MODDIR/turnip-mount.log"

exec >>"$LOG" 2>&1
echo "=== touchGrass Turnip mount $(date) ==="
echo "target=$TARGET"
echo "driver=$DRIVER"

if [ ! -f "$TARGET" ]; then
    echo "ERROR: stock Vulkan HAL missing"
    exit 10
fi

if [ ! -f "$DRIVER" ]; then
    echo "ERROR: Turnip payload missing"
    exit 11
fi

mkdir -p "$STAGE_DIR" || exit 12
cp -f "$DRIVER" "$STAGE" || exit 13
chown 0:0 "$STAGE" 2>/dev/null || true
chmod 0644 "$STAGE" || exit 14

# Stage on /dev tmpfs so execution does not depend on /data mount policy.
# IMPORTANT: the stock Vulkan same-process HAL is same_process_hal_file.
# SurfaceFlinger is allowed to execute/map that type.  The original v0.1
# module incorrectly used vendor_file, which caused Vulkan RenderEngine
# initialization to fail before the Turnip HAL was mapped.
echo "stock_target_label:"
ls -lZ "$TARGET" 2>/dev/null || true

if ! chcon u:object_r:same_process_hal_file:s0 "$STAGE" 2>/dev/null; then
    echo "ERROR: unable to apply same_process_hal_file label"
    exit 15
fi

echo "staged_driver_label:"
ls -lZ "$STAGE" 2>/dev/null || ls -l "$STAGE"

if ! ls -lZ "$STAGE" 2>/dev/null | grep -Fq 'u:object_r:same_process_hal_file:s0'; then
    echo "ERROR: staged Turnip HAL does not have same_process_hal_file label"
    exit 16
fi

mount -o bind "$STAGE" "$TARGET" || {
    echo "ERROR: bind mount failed"
    exit 17
}

echo "Turnip bind mount active"
echo "mounted_target_label:"
ls -lZ "$TARGET" 2>/dev/null || ls -l "$TARGET"

if ! ls -lZ "$TARGET" 2>/dev/null | grep -Fq 'u:object_r:same_process_hal_file:s0'; then
    echo "ERROR: mounted Vulkan HAL label mismatch"
    umount "$TARGET" 2>/dev/null || true
    exit 18
fi

sha256sum "$TARGET" 2>/dev/null || true
EOF

cat > "$OUT_DIR/module/action.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
OUT="$MODDIR/turnip-status.txt"

{
  echo "=== touchGrass Turnip A619 status ==="
  date
  echo
  echo "=== DEVICE ==="
  getprop ro.product.device
  getprop ro.product.model
  getprop ro.hardware.vulkan
  getprop ro.board.platform
  echo
  echo "=== HAL ==="
  ls -lZ /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || ls -l /vendor/lib64/hw/vulkan.adreno.so
  sha256sum /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
  echo
  echo "=== MOUNT ==="
  cat /proc/mounts | grep -F 'vulkan.adreno.so' || true
  echo
  echo "=== VULKAN JSON ==="
  cmd gpu vkjson 2>&1 || true
  echo
  echo "=== SURFACEFLINGER ==="
  dumpsys SurfaceFlinger 2>&1 | grep -i -E 'RE Vulkan|RE GLES|Vulkan device|Ganesh|driver' || true
} > "$OUT" 2>&1

cat "$OUT"
EOF

cat > "$OUT_DIR/module/uninstall.sh" <<'EOF'
#!/system/bin/sh
umount /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
rm -rf /dev/touchgrass-turnip-a619
EOF

cat > "$OUT_DIR/module/service.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
OUT="$MODDIR/turnip-boot-diagnostic.txt"

# Give SurfaceFlinger enough time to initialize or crash at least once.
sleep 12

{
  echo "========== TURNIP BOOT DIAGNOSTIC =========="
  date
  echo
  echo "=== HAL MOUNT / LABEL ==="
  ls -lZ /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || ls -l /vendor/lib64/hw/vulkan.adreno.so
  sha256sum /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
  cat /proc/mounts | grep -F 'vulkan.adreno.so' || true
  echo
  echo "=== SURFACEFLINGER PROCESS ==="
  pidof surfaceflinger 2>/dev/null || true
  for P in $(pidof surfaceflinger 2>/dev/null); do
    echo "--- /proc/$P/maps Vulkan entries ---"
    grep -Ei 'libvulkan|vulkan\.adreno|turnip|mesa' "/proc/$P/maps" 2>/dev/null || true
  done
  echo
  echo "=== SURFACEFLINGER / VULKAN LOGCAT ==="
  logcat -b all -d 2>/dev/null | grep -Ei 'surfaceflinger|RenderEngine|VulkanInterface|vulkan|turnip|mesa|freedreno|linker|avc:.*denied' | tail -800
  echo
  echo "=== GPU / KGSL KERNEL ==="
  dmesg 2>/dev/null | grep -Ei 'kgsl|adreno|gmu|gpu|iommu|smmu|fault|error|timeout|hang|reset' | tail -500
  echo
  echo "=== RECENT TOMBSTONES ==="
  ls -lt /data/tombstones 2>/dev/null | head -30
  echo "========== END =========="
} > "$OUT" 2>&1
EOF

cat > "$OUT_DIR/module/README.txt" <<'EOF'
touchGrass Turnip A619 Mesa 26.2.2

Target:
  Samsung Galaxy A52 5G / a52xq / SM-A526B
  Adreno 619
  Android 16 / arm64
  KGSL kernel backend

Driver:
  Mesa 26.2.2 Turnip
  Vulkan API deliberately capped to 1.3 for the first bring-up
  64-bit HAL override only

The module does not modify /vendor. At post-fs-data it copies the driver to
/dev tmpfs, applies the stock same_process_hal_file SELinux label, verifies
that label, and bind-mounts it over:
  /vendor/lib64/hw/vulkan.adreno.so

Why this revision exists:
  The original v0.1 module used vendor_file on the staged Vulkan HAL.
  Ramoops/tombstones showed SurfaceFlinger repeatedly aborting with
  "Could not initialize Vulkan RenderEngine!" and never mapping vulkan.adreno.so.
  The stock HAL is same_process_hal_file, so this revision matches that label.

Boot diagnostics:
  /data/adb/modules/touchgrass_turnip_a619/turnip-mount.log
  /data/adb/modules/touchgrass_turnip_a619/turnip-boot-diagnostic.txt

Rollback:
  Disable or remove the KernelSU module and reboot.
  If Android UI fails but ADB works:
    adb shell su -c "touch /data/adb/modules/touchgrass_turnip_a619/disable; reboot"

32-bit Vulkan applications continue using the stock Qualcomm 32-bit driver in
this first bring-up.
EOF

chmod +x "$OUT_DIR/module/post-fs-data.sh" "$OUT_DIR/module/action.sh" "$OUT_DIR/module/uninstall.sh" "$OUT_DIR/module/service.sh"

(
  cd "$OUT_DIR/module"
  zip -9 -r "../touchGrass-Turnip-A619-Mesa-26.2.2-KGSL-Vulkan-1.3-KSU.zip" .
)

ZIP="$OUT_DIR/touchGrass-Turnip-A619-Mesa-26.2.2-KGSL-Vulkan-1.3-KSU.zip"
test -s "$ZIP"
unzip -tq "$ZIP"
unzip -p "$ZIP" module.prop | grep -Fxq 'id=touchgrass_turnip_a619'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'mount -o bind "$STAGE" "$TARGET"'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'chcon u:object_r:same_process_hal_file:s0 "$STAGE"'
! unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'chcon u:object_r:vendor_file:s0'
unzip -l "$ZIP" | grep -Fq 'service.sh'
unzip -l "$ZIP" | grep -Fq 'payload/vulkan.adreno.so'
sha256sum "$ZIP" > "$ZIP.sha256"

echo "packaged=$ZIP"
cat "$ZIP.sha256"
