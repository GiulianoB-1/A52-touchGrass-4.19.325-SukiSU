#!/sbin/sh
# Phase389 recovery collector: read-only for Samsung debug storage.
# Run as root from OrangeFox/recovery after testing the GKI boot.

set -u

STAMP="$(date +%Y%m%d_%H%M%S 2>/dev/null || echo unknown)"
OUT="/sdcard/A52_PHASE389_${STAMP}"
mkdir -p "$OUT"

echo "Phase389 collection -> $OUT"

cat /proc/cmdline > "$OUT/recovery_cmdline.txt" 2>/dev/null || true
cat /proc/iomem > "$OUT/recovery_iomem.txt" 2>/dev/null || true
ls -l /dev/block/by-name > "$OUT/by-name.txt" 2>&1 || true
mount > "$OUT/mounts.txt" 2>&1 || true

if [ -r /dev/a52_ramoops_raw ]; then
    dd if=/dev/a52_ramoops_raw of="$OUT/ramoops_raw_1m.bin" bs=1M count=1 2>"$OUT/ramoops_dd.txt"
else
    echo "/dev/a52_ramoops_raw unavailable" > "$OUT/ramoops_dd.txt"
    if [ -d /sys/fs/pstore ]; then
        mkdir -p "$OUT/pstore"
        cp -a /sys/fs/pstore/* "$OUT/pstore/" 2>/dev/null || true
    fi
fi

DEBUG=""
for CAND in \
    /dev/block/by-name/debug \
    /dev/block/bootdevice/by-name/debug \
    /dev/block/platform/*/by-name/debug; do
    if [ -e "$CAND" ]; then
        DEBUG="$CAND"
        break
    fi
done

if [ -n "$DEBUG" ]; then
    echo "$DEBUG" > "$OUT/debug_device.txt"
    dd if="$DEBUG" of="$OUT/debug.bin" bs=1M 2>"$OUT/debug_dd.txt"
else
    echo "debug partition not found" > "$OUT/debug_dd.txt"
fi

if [ -r /dev/mem ]; then
    SKIP11=$((0xB1000000 / 4096))
    CNT11=$((11 * 1024 * 1024 / 4096))
    dd if=/dev/mem of="$OUT/samsung_reserved_b100_11m.bin" \
       bs=4096 skip="$SKIP11" count="$CNT11" 2>"$OUT/reserved11_dd.txt" || true

    SKIP7=$((0xB1400000 / 4096))
    CNT7=$((7 * 1024 * 1024 / 4096))
    dd if=/dev/mem of="$OUT/phase389_reserved_b140_7m.bin" \
       bs=4096 skip="$SKIP7" count="$CNT7" 2>"$OUT/reserved7_dd.txt" || true
else
    echo "/dev/mem unavailable in this recovery" > "$OUT/reserved11_dd.txt"
    echo "/dev/mem unavailable in this recovery" > "$OUT/reserved7_dd.txt"
fi

sync
echo "DONE: $OUT"
echo "Pull with: adb pull $OUT"
