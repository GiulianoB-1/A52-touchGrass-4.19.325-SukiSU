#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
BUILD_DIR="${2:-$ROOT/artifacts/turnip-mesa-26.2.2}"
OUT_DIR="${3:-$ROOT/release-turnip-persistent}"

DRIVER="$BUILD_DIR/dist/vulkan.adreno.so"
PROBE="$BUILD_DIR/dist/turnip-vk-probe"
AHB_PROBE="$BUILD_DIR/dist/turnip-ahb-probe"
YV12_PROBE="$BUILD_DIR/dist/turnip-yv12-sample-probe"
INFO="$BUILD_DIR/BUILD-INFO.txt"

test -s "$DRIVER"
test -s "$PROBE"
test -s "$AHB_PROBE"
test -s "$YV12_PROBE"
test -s "$INFO"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/module/payload" "$OUT_DIR/module/tools"

cp "$DRIVER" "$OUT_DIR/module/payload/vulkan.adreno.so"
cp "$PROBE" "$OUT_DIR/module/tools/turnip-vk-probe"
cp "$AHB_PROBE" "$OUT_DIR/module/tools/turnip-ahb-probe"
cp "$YV12_PROBE" "$OUT_DIR/module/tools/turnip-yv12-sample-probe"
cp "$INFO" "$OUT_DIR/module/BUILD-INFO.txt"
sha256sum "$OUT_DIR/module/payload/vulkan.adreno.so" > "$OUT_DIR/module/driver.sha256"

cat > "$OUT_DIR/module/module.prop" <<'EOF'
id=touchgrass_turnip_a619
name=touchGrass Turnip A619 Mesa 26.2.2 Persistent
version=0.19.1-vk1.3-persistent-qti-tp10-gpu-sample
versionCode=24
author=touchGrass project
description=Persistent arm64 Mesa 26.2.2 Turnip Vulkan 1.3 test for Adreno 619/KGSL. Driver is unchanged from v0.19 native TP10; this revision adds an on-device GPU TP10 YCbCr sampling smoke test while retaining validated NV12, exact-size AHB/CCU and YV12 fixes.
EOF

cat > "$OUT_DIR/module/customize.sh" <<'EOF'
SKIPUNZIP=0

ui_print "*********************************************"
ui_print " touchGrass Turnip A619 / Mesa 26.2.2"
ui_print " Vulkan 1.3 / PERSISTENT arm64 HAL test"
ui_print "*********************************************"

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

ui_print "- Persistent 64-bit Turnip HAL override will activate next boot"
ui_print "- Stock vendor partition is not modified"
ui_print "- SELinux label: same_process_hal_file"
ui_print "- 32-bit Vulkan remains stock Qualcomm"
ui_print "- Recovery rollback: create /data/adb/modules/touchgrass_turnip_a619/disable"

set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
set_perm "$MODPATH/payload/vulkan.adreno.so" 0 0 0644
set_perm "$MODPATH/tools/turnip-vk-probe" 0 0 0755
set_perm "$MODPATH/tools/turnip-ahb-probe" 0 0 0755
set_perm "$MODPATH/tools/turnip-yv12-sample-probe" 0 0 0755
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
echo "=== touchGrass Turnip persistent mount $(date) ==="
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

echo "stock_target_label:"
ls -lZ "$TARGET" 2>/dev/null || true

if ! chcon u:object_r:same_process_hal_file:s0 "$STAGE" 2>/dev/null; then
    echo "ERROR: unable to apply same_process_hal_file label"
    exit 15
fi
if ! ls -lZ "$STAGE" 2>/dev/null | grep -Fq 'u:object_r:same_process_hal_file:s0'; then
    echo "ERROR: staged Turnip HAL label mismatch"
    exit 16
fi

mount -o bind "$STAGE" "$TARGET" || {
    echo "ERROR: bind mount failed"
    exit 17
}

if ! ls -lZ "$TARGET" 2>/dev/null | grep -Fq 'u:object_r:same_process_hal_file:s0'; then
    echo "ERROR: mounted Vulkan HAL label mismatch"
    umount "$TARGET" 2>/dev/null || true
    exit 18
fi

echo "Turnip bind mount active"
echo "mounted_target_label:"
ls -lZ "$TARGET" 2>/dev/null || true
echo "mounted_target_sha256:"
sha256sum "$TARGET" 2>/dev/null || true
echo "renderengine_backend=$(getprop debug.renderengine.backend)"
EOF

cat > "$OUT_DIR/module/service.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
OUT="$MODDIR/turnip-boot-diagnostic.txt"

# Capture the first stable userspace window while preserving the failure state
# if SurfaceFlinger crashes or the UI becomes unstable.
sleep 18

{
  echo "========== TURNIP PERSISTENT BOOT DIAGNOSTIC =========="
  date
  echo
  echo "=== PROPERTIES ==="
  echo "renderengine_backend=$(getprop debug.renderengine.backend)"
  echo "vulkan_hw=$(getprop ro.hardware.vulkan)"
  echo
  echo "=== HAL MOUNT / LABEL ==="
  ls -lZ /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || ls -l /vendor/lib64/hw/vulkan.adreno.so
  sha256sum /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
  cat /proc/mounts | grep -F 'vulkan.adreno.so' || true
  echo
  echo "=== SURFACEFLINGER PROCESS / MAPS ==="
  pidof surfaceflinger 2>/dev/null || true
  for P in $(pidof surfaceflinger 2>/dev/null); do
    echo "--- /proc/$P/maps Vulkan entries ---"
    grep -Ei 'libvulkan|vulkan\.adreno|turnip|mesa|freedreno' "/proc/$P/maps" 2>/dev/null || true
  done
  echo
  echo "=== SURFACEFLINGER DUMP ==="
  dumpsys SurfaceFlinger 2>&1 | grep -i -E 'Vulkan|RenderEngine|Ganesh|device initialized|driver' | head -300 || true
  echo
  echo "=== VULKAN / SURFACEFLINGER LOGCAT ==="
  logcat -b all -d 2>/dev/null | grep -Ei 'surfaceflinger|RenderEngine|Ganesh|VulkanInterface|vulkan|turnip|mesa|freedreno|AHardwareBuffer|YV12|invalid texture|Could not initialize Vulkan RenderEngine|avc:.*denied' | tail -1200
  echo
  echo "=== GPU / KGSL FAULTS ==="
  dmesg 2>/dev/null | grep -Ei 'kgsl|adreno|gmu|gpu|iommu|smmu' | grep -Ei 'PAGE FAULT|FAULTING BLOCK|translation fault|smmu.*fault|iommu.*fault|hang|timeout|reset|BUG|Oops|panic' | tail -600
  echo
  echo "=== TOMBSTONES ==="
  ls -lt /data/tombstones 2>/dev/null | head -40
  echo "========== END =========="
} > "$OUT" 2>&1
EOF

cat > "$OUT_DIR/module/action.sh" <<'EOF'
#!/system/bin/sh
MODDIR=${0%/*}
OUT="$MODDIR/turnip-persistent-status.txt"
PROBE="$MODDIR/tools/turnip-vk-probe"
AHB_PROBE="$MODDIR/tools/turnip-ahb-probe"

{
  echo "========== TURNIP PERSISTENT STATUS =========="
  date
  echo
  echo "=== HAL ==="
  ls -lZ /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
  sha256sum /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
  cat /proc/mounts | grep -F 'vulkan.adreno.so' || true
  echo
  echo "=== SURFACEFLINGER ==="
  echo "pid=$(pidof surfaceflinger 2>/dev/null)"
  echo "renderengine_backend=$(getprop debug.renderengine.backend)"
  for P in $(pidof surfaceflinger 2>/dev/null); do
    grep -Ei 'vulkan\.adreno|turnip|mesa' "/proc/$P/maps" 2>/dev/null || true
  done
  echo
  echo "=== DIRECT TURNIP PROBE ==="
  if [ -x "$PROBE" ]; then
    timeout 30 "$PROBE" 2>&1 || true
  fi
  echo
  echo "=== DIRECT AHB IMPORT PROBE ==="
  if [ -x "$AHB_PROBE" ]; then
    timeout 60 "$AHB_PROBE" 2>&1 || true
  else
    echo "turnip-ahb-probe missing"
  fi
  echo
  echo "=== GPU/KGSL FAULTS ==="
  dmesg 2>/dev/null | grep -Ei 'kgsl|adreno|gmu|gpu|iommu|smmu' | grep -Ei 'PAGE FAULT|FAULTING BLOCK|translation fault|smmu.*fault|iommu.*fault|hang|timeout|reset|BUG|Oops|panic' | tail -600
  echo
  echo "=== BOOT DIAGNOSTIC ==="
  cat "$MODDIR/turnip-boot-diagnostic.txt" 2>/dev/null || true
  echo "========== END =========="
} > "$OUT" 2>&1

cat "$OUT"
EOF

cat > "$OUT_DIR/module/uninstall.sh" <<'EOF'
#!/system/bin/sh
umount /vendor/lib64/hw/vulkan.adreno.so 2>/dev/null || true
rm -rf /dev/touchgrass-turnip-a619
EOF

cat > "$OUT_DIR/module/README.txt" <<'EOF'
touchGrass Turnip A619 Mesa 26.2.2 persistent SurfaceFlinger test

This persistent arm64 test adds native Adreno TP10 support for the QTI private TP10 UBWC import path (0x7fa30c09) exposed by the v0.17 SurfaceFlinger crash. The validated gralloc import remains DRM NV15 + QCOM_COMPRESSED, while Turnip now uses native FMT6_TP10 sampling with Qualcomm 48x4 Y and 24x4 UV UBWC metadata geometry instead of treating the storage as P010. It retains the validated NV12 Venus UBWC path (0x7fa30c06), bounded GMEM handling for exact-size linear Android AHBs and all YV12 fixes. The synthetic Vulkan/AHB suite
proved all of the following on the A52 / Adreno 619:
  - Vulkan 1.3 device creation and real KGSL command submission
  - GPU memory fill/readback
  - Vulkan 1.3 dynamic rendering/readback
  - exact 940x1670 Android YV12 import with 960/480 byte pitches
  - real YV12 GPU sampling matching stock Qualcomm exactly
  - post-CPU-touch YV12 import after QCOM mapped-pointer normalization
  - exact-size linear Android AHBs use bounded GMEM edge paths to avoid CCU overrun
  - QTI 0x7fa30c06 NV12 Venus UBWC buffers are validated through legacy PlaneLayoutInfo metadata

At post-fs-data the module:
  1. copies Turnip to /dev tmpfs
  2. applies u:object_r:same_process_hal_file:s0
  3. verifies the label
  4. bind-mounts it over /vendor/lib64/hw/vulkan.adreno.so

Only arm64 is replaced. arm32 Vulkan remains stock Qualcomm.

Automatic diagnostics:
  /data/adb/modules/touchgrass_turnip_a619/turnip-mount.log
  /data/adb/modules/touchgrass_turnip_a619/turnip-boot-diagnostic.txt

Action diagnostics:
  /data/adb/modules/touchgrass_turnip_a619/turnip-persistent-status.txt

Recovery rollback:
  adb shell su -c "touch /data/adb/modules/touchgrass_turnip_a619/disable"
  adb reboot

The original vendor partition is never modified.
EOF

chmod +x "$OUT_DIR/module/post-fs-data.sh" "$OUT_DIR/module/service.sh" "$OUT_DIR/module/action.sh" "$OUT_DIR/module/uninstall.sh"

(
  cd "$OUT_DIR/module"
  zip -9 -r "../touchGrass-Turnip-A619-Mesa-26.2.2-KGSL-Vulkan-1.3-PERSISTENT-KSU.zip" .
)

ZIP="$OUT_DIR/touchGrass-Turnip-A619-Mesa-26.2.2-KGSL-Vulkan-1.3-PERSISTENT-KSU.zip"
test -s "$ZIP"
unzip -tq "$ZIP"
unzip -p "$ZIP" module.prop | grep -Fxq 'id=touchgrass_turnip_a619'
unzip -p "$ZIP" module.prop | grep -Fxq 'version=0.19.1-vk1.3-persistent-qti-tp10-gpu-sample'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'mount -o bind "$STAGE" "$TARGET"'
unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'chcon u:object_r:same_process_hal_file:s0 "$STAGE"'
! unzip -p "$ZIP" post-fs-data.sh | grep -Fq 'u:object_r:vendor_file:s0'
unzip -l "$ZIP" | grep -Fq 'service.sh'
unzip -l "$ZIP" | grep -Fq 'tools/turnip-vk-probe'
unzip -l "$ZIP" | grep -Fq 'tools/turnip-ahb-probe'
unzip -l "$ZIP" | grep -Fq 'tools/turnip-yv12-sample-probe'
unzip -p "$ZIP" action.sh | grep -Fq '=== DIRECT AHB IMPORT PROBE ==='
unzip -p "$ZIP" service.sh | grep -Fq 'Could not initialize Vulkan RenderEngine'
unzip -p "$ZIP" service.sh | grep -Fq 'PAGE FAULT'
sha256sum "$ZIP" > "$ZIP.sha256"

echo "packaged=$ZIP"
cat "$ZIP.sha256"
