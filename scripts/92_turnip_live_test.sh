#!/system/bin/sh

DRIVER="${1:-/data/local/tmp/vulkan.adreno.so}"
PROBE="${2:-/data/local/tmp/turnip-vk-probe}"
OUT="${3:-/data/local/tmp/turnip-live-test.txt}"
WATCH="${OUT%.txt}-kernel-watch.txt"
WATCH_PID=""

TARGET=/vendor/lib64/hw/vulkan.adreno.so
STAGE_DIR=/dev/touchgrass-turnip-live
STAGE="$STAGE_DIR/vulkan.adreno.so"

cleanup() {
    if [ -n "$WATCH_PID" ]; then
        kill "$WATCH_PID" 2>/dev/null || true
        wait "$WATCH_PID" 2>/dev/null || true
    fi
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

echo "=== STOCK QUALCOMM SUBMISSION BASELINE ==="
"$PROBE"
STOCK_RC=$?
echo "stock_probe_exit=$STOCK_RC"
echo
sync

if [ "$STOCK_RC" -ne 0 ]; then
    echo "FAIL: stock Qualcomm Vulkan failed the same submission probe"
    exit 17
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

echo "=== TURNIP VULKAN SUBMISSION PROBE ==="
echo "turnip_probe_begin=1"
echo "kernel_watch=$WATCH"
: > "$WATCH"
{
    echo "========== TURNIP KERNEL WATCH =========="
    date
    echo "=== INITIAL GPU/KGSL STATE ==="
    dmesg | grep -Ei 'kgsl|adreno|gmu|gpu|iommu|smmu' | tail -120
    echo "=== LIVE KERNEL STREAM ==="
} >> "$WATCH" 2>&1
dmesg -w >> "$WATCH" 2>&1 &
WATCH_PID=$!
sync

if command -v timeout >/dev/null 2>&1; then
    timeout 20 "$PROBE"
    PROBE_RC=$?
else
    "$PROBE"
    PROBE_RC=$?
fi

if [ -n "$WATCH_PID" ]; then
    kill "$WATCH_PID" 2>/dev/null || true
    wait "$WATCH_PID" 2>/dev/null || true
    WATCH_PID=""
fi
sync
echo "turnip_probe_exit=$PROBE_RC"
echo "=== TURNIP KERNEL WATCH TAIL ==="
tail -250 "$WATCH" 2>/dev/null || true
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
