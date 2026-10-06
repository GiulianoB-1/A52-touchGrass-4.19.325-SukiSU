#!/sbin/sh
# Phase444 recovery collector.
# Run from OrangeFox/recovery immediately after the tested Phase444 boot.
# Read-only with respect to Samsung debug storage and reserved RAM.

set -u

STAMP="$(date +%Y%m%d_%H%M%S 2>/dev/null || echo unknown)"
OUT="/sdcard/A52_PHASE444_RECOVERY_${STAMP}"
mkdir -p "$OUT"

echo "Phase444 recovery collection -> $OUT"

cat /proc/cmdline > "$OUT/recovery_cmdline.txt" 2>/dev/null || true
cat /proc/iomem > "$OUT/recovery_iomem.txt" 2>/dev/null || true
cat /proc/version > "$OUT/recovery_version.txt" 2>/dev/null || true
id > "$OUT/recovery_id.txt" 2>&1 || true
mount > "$OUT/mounts.txt" 2>&1 || true
ls -l /dev/block/by-name > "$OUT/by-name.txt" 2>&1 || true

# Preserve ramoops/pstore context too.
if [ -r /dev/a52_ramoops_raw ]; then
    dd if=/dev/a52_ramoops_raw of="$OUT/ramoops_raw_1m.bin" bs=1M count=1 2>"$OUT/ramoops_dd.txt" || true
else
    echo "/dev/a52_ramoops_raw unavailable" > "$OUT/ramoops_dd.txt"
fi

if [ -d /sys/fs/pstore ]; then
    mkdir -p "$OUT/pstore"
    cp -a /sys/fs/pstore/* "$OUT/pstore/" 2>/dev/null || true
fi

# P414 and older witnesses still live in the Samsung debug partition.
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
    dd if="$DEBUG" of="$OUT/debug.bin" bs=1M 2>"$OUT/debug_dd.txt" || true
else
    echo "debug partition not found" > "$OUT/debug_dd.txt"
fi

# Phase444 storage v2:
#   physical base 0xB1A62000
#   length        0x00098000 (608 KiB)
# It lies inside the proven Phase389 7 MiB recovery window
# 0xB1400000..0xB1AFFFFF at file offset 0x662000.
if [ -r /dev/mem ]; then
    SKIP444=$((0xB1A62000 / 4096))
    CNT444=$((0x00098000 / 4096))
    dd if=/dev/mem of="$OUT/phase444_reserved_b1a62000_608k.bin" \
       bs=4096 skip="$SKIP444" count="$CNT444" 2>"$OUT/phase444_reserved_dd.txt" || true

    SKIP7=$((0xB1400000 / 4096))
    CNT7=$((7 * 1024 * 1024 / 4096))
    dd if=/dev/mem of="$OUT/phase389_reserved_b140_7m.bin" \
       bs=4096 skip="$SKIP7" count="$CNT7" 2>"$OUT/reserved7_dd.txt" || true
else
    echo "/dev/mem unavailable in this recovery" > "$OUT/phase444_reserved_dd.txt"
    echo "/dev/mem unavailable in this recovery" > "$OUT/reserved7_dd.txt"
fi

cat > "$OUT/PHASE444_LAYOUT.txt" <<'EOF'
Phase444 storage v2
physical_base=0xB1A62000
bytes=0x00098000
phase389_7m_base=0xB1400000
offset_inside_phase389_7m=0x00662000
end_offset_inside_phase389_7m=0x006FA000
disk_tier=disabled
EOF

(
    cd "$OUT" || exit 0
    if command -v sha256sum >/dev/null 2>&1; then
        find . -type f ! -name SHA256SUMS -exec sha256sum {} \; > SHA256SUMS 2>/dev/null || true
    fi
)

sync
echo "DONE: $OUT"
echo "Pull with: adb pull $OUT"
