#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
BUILD_DIR="${2:-$ROOT/artifacts/turnip-mesa-26.2.2}"
OUT_DIR="${3:-$ROOT/release-turnip}"

DRIVER="$BUILD_DIR/dist/vulkan.adreno.so"
PROBE="$BUILD_DIR/dist/turnip-vk-probe"
LIVE_TEST="$ROOT/scripts/92_turnip_live_test.sh"
INFO="$BUILD_DIR/BUILD-INFO.txt"

test -s "$DRIVER"
test -s "$PROBE"
test -s "$LIVE_TEST"
test -s "$INFO"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/module/payload" "$OUT_DIR/module/tools"

cp "$DRIVER" "$OUT_DIR/module/payload/vulkan.adreno.so"
cp "$PROBE" "$OUT_DIR/module/tools/turnip-vk-probe"
cp "$LIVE_TEST" "$OUT_DIR/module/tools/turnip-live-test.sh"
cp "$INFO" "$OUT_DIR/module/BUILD-INFO.txt"
sha256sum "$OUT_DIR/module/payload/vulkan.adreno.so" > "$OUT_DIR/module/driver.sha256"
sha256sum "$OUT_DIR/module/tools/turnip-vk-probe" > "$OUT_DIR/module/probe.sha256"

cat > "$OUT_DIR/module/module.prop" <<'EOF'
id=touchgrass_turnip_a619
name=touchGrass Turnip A619 Mesa 26.2.2
version=0.4-vk1.3-submit-diagnostic
versionCode=4
author=touchGrass project
description=Mesa 26.2.2 Turnip Vulkan 1.3 diagnostic for Adreno 619/KGSL. Compares stock Qualcomm vs Turnip no-op and buffer-fill submissions without overriding Vulkan during boot.
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
ui_print "- SAFE MODE: no Vulkan HAL replacement during boot"
ui_print "- Use Action for stock-vs-Turnip submission diagnostics"
ui_print "- Original vendor partition will not be modified"

set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
set_perm "$MODPATH/payload/vulkan.adreno.so" 0 0 0644
set_perm "$MODPATH/tools/turnip-vk-probe" 0 0 0755
set_perm "$MODPATH/tools/turnip-live-test.sh" 0 0 0755
EOF

cat > "$OUT_DIR/module/post-fs-data.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
LOG="$MODDIR/turnip-mount.log"

{
  echo "=== touchGrass Turnip safe bring-up $(date) ==="
  echo "boot_override=disabled"
  echo "stock_hal=/vendor/lib64/hw/vulkan.adreno.so"
  echo "Turnip is NOT bind-mounted during boot."
  echo "Use the module Action button for a temporary live probe."
} >> "$LOG" 2>&1
EOF

cat > "$OUT_DIR/module/action.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
OUT="$MODDIR/turnip-live-test.txt"
DRIVER="$MODDIR/payload/vulkan.adreno.so"
PROBE="$MODDIR/tools/turnip-vk-probe"
LIVE="$MODDIR/tools/turnip-live-test.sh"

echo "touchGrass Turnip A619 stock-vs-Turnip submission diagnostic"
echo "No persistent Vulkan override is active."
echo "A temporary bind mount will be created only for this test and removed on exit."
echo

if [ ! -f "$DRIVER" ]; then
  echo "ERROR: Turnip payload missing"
  exit 10
fi

if [ ! -x "$PROBE" ]; then
  chmod 0755 "$PROBE" 2>/dev/null || true
fi

if [ ! -x "$LIVE" ]; then
  chmod 0755 "$LIVE" 2>/dev/null || true
fi

"$LIVE" "$DRIVER" "$PROBE" "$OUT"
RC=$?

echo
echo "=== LIVE TEST RESULT ==="
cat "$OUT" 2>/dev/null || true
echo
echo "exit_code=$RC"
echo "Turnip temporary mount has been removed."

exit "$RC"
EOF

cat > "$OUT_DIR/module/uninstall.sh" <<'EOF'
#!/system/bin/sh
umount /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
rm -rf /dev/touchgrass-turnip-a619
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

This revision deliberately DOES NOT replace Vulkan during boot.
post-fs-data.sh is a no-op logger.

Use the KernelSU/SukiSU module Action button to run a temporary live test.
The test stages Turnip under /dev, bind-mounts it over the 64-bit stock HAL,
runs Vulkan device creation plus a real GPU command submission and memory-verification probe, records diagnostics, then unmounts it automatically.

Rollback from the old boot-override revision:
  adb shell su -c "touch /data/adb/modules/touchgrass_turnip_a619/disable; reboot"

32-bit Vulkan applications continue using the stock Qualcomm 32-bit driver in
this first bring-up.

Safer first test before installing the module:
  1. Extract this ZIP on the PC.
  2. Push payload/vulkan.adreno.so, tools/turnip-vk-probe and
     tools/turnip-live-test.sh to /data/local/tmp.
  3. Run turnip-live-test.sh through su. It bind-mounts Turnip only inside
     that root shell namespace, runs the direct Vulkan probe, records logs,
     and unmounts automatically on exit.
EOF

chmod +x "$OUT_DIR/module/post-fs-data.sh" "$OUT_DIR/module/action.sh" "$OUT_DIR/module/uninstall.sh"

(
  cd "$OUT_DIR/module"
  zip -9 -r "../touchGrass-Turnip-A619-Mesa-26.2.2-KGSL-Vulkan-1.3-KSU.zip" .
)

ZIP="$OUT_DIR/touchGrass-Turnip-A619-Mesa-26.2.2-KGSL-Vulkan-1.3-KSU.zip"
test -s "$ZIP"
unzip -tq "$ZIP"
unzip -p "$ZIP" module.prop | grep -Fxq 'id=touchgrass_turnip_a619'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'boot_override=disabled'
! unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'mount -o bind'
unzip -p "$ZIP" action.sh | grep -Fq 'turnip-live-test.sh'
unzip -l "$ZIP" | grep -Fq 'payload/vulkan.adreno.so'
unzip -l "$ZIP" | grep -Fq 'tools/turnip-vk-probe'
unzip -l "$ZIP" | grep -Fq 'tools/turnip-live-test.sh'
sha256sum "$ZIP" > "$ZIP.sha256"

echo "packaged=$ZIP"
cat "$ZIP.sha256"
