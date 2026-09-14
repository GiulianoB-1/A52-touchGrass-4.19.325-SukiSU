#!/system/bin/sh

DRIVER="${1:-/data/local/tmp/vulkan.adreno.so}"
PROBE="${2:-/data/local/tmp/turnip-vk-probe}"
OUT="${3:-/data/local/tmp/turnip-live-test.txt}"

TARGET=/vendor/lib64/hw/vulkan.adreno.so
STAGE_DIR=/dev/touchgrass-turnip-live
STAGE="$STAGE_DIR/vulkan.adreno.so"

cleanup() {
    umount "$TARGET" 2>/dev/null || true
    rm -rf "$STAGE_DIR"
}

trap cleanup EXIT HUP INT TERM

exec >"$OUT" 2>&1

echo "========== TURNIP A619 LIVE TEST =========="
date
echo

echo "=== PRECHECK ==="
getprop ro.product.device
getprop ro.product.model
getprop ro.board.platform
getprop ro.build.version.sdk
echo "stock_hal=$TARGET"
ls -lZ "$TARGET" 2>/dev/null || ls -l "$TARGET"
sha256sum "$TARGET" 2>/dev/null || true
echo "driver=$DRIVER"
ls -lZ "$DRIVER" 2>/dev/null || ls -l "$DRIVER"
sha256sum "$DRIVER" 2>/dev/null || true
echo

if [ ! -f "$TARGET" ]; then
    echo "FAIL: stock Vulkan HAL missing"
    exit 10
fi
if [ ! -f "$DRIVER" ]; then
    echo "FAIL: Turnip driver missing"
    exit 11
fi
if [ ! -x "$PROBE" ]; then
    echo "FAIL: Vulkan probe missing/not executable"
    exit 12
fi

mkdir -p "$STAGE_DIR" || exit 13
cp -f "$DRIVER" "$STAGE" || exit 14
chown 0:0 "$STAGE" 2>/dev/null || true
chmod 0644 "$STAGE" || exit 15
chcon u:object_r:vendor_file:s0 "$STAGE" 2>/dev/null || true

echo "=== BIND MOUNT ==="
mount -o bind "$STAGE" "$TARGET" || {
    echo "FAIL: bind mount"
    exit 16
}
cat /proc/mounts | grep -F 'vulkan.adreno.so' || true
sha256sum "$TARGET" 2>/dev/null || true
echo

echo "=== DIRECT VULKAN PROBE ==="
"$PROBE"
PROBE_RC=$?
echo "probe_exit=$PROBE_RC"
echo

echo "=== CMD GPU VKJSON ==="
cmd gpu vkjson 2>&1 || true
echo

echo "=== RECENT VULKAN LOGCAT ==="
logcat -d -b all | grep -i -E 'turnip|mesa|vulkan|freedreno|kgsl|adreno' | tail -250
echo

echo "=== GPU/KGSL FAULTS ==="
dmesg | grep -Ei 'kgsl|adreno|gmu|gpu|iommu|smmu' | grep -Ei 'fault|error|timeout|hang|panic|oops|BUG|recover|reset' | tail -200
echo

echo "=== RESULT ==="
if [ "$PROBE_RC" -eq 0 ]; then
    echo "TURNIP_LIVE_TEST=PASS"
else
    echo "TURNIP_LIVE_TEST=FAIL"
fi
echo "========== END =========="

exit "$PROBE_RC"
