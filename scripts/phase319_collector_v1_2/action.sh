#!/system/bin/sh

BASE=/data/adb/phase319_capture
OUTDIR=/sdcard
STAMP=$(date '+%Y%m%d_%H%M%S' 2>/dev/null)
[ -n "$STAMP" ] || STAMP=unknown
WORK="$BASE/export_$STAMP"
ZIP="$OUTDIR/A52_PHASE319_GOLDEN_CAPTURE_$STAMP.zip"

mkdir -p "$WORK"

# Preserve the continuously captured early stream first.
cp -f "$BASE/early-kmsg.log" "$WORK/early-kmsg.log" 2>/dev/null
cp -f "$BASE/early-kmsg.previous.log" "$WORK/early-kmsg.previous.log" 2>/dev/null

# Current snapshots for context.
dmesg > "$WORK/dmesg.txt" 2>&1
cat /proc/version > "$WORK/proc-version.txt" 2>&1
cat /proc/cmdline > "$WORK/proc-cmdline.txt" 2>&1
uname -a > "$WORK/uname.txt" 2>&1
getprop > "$WORK/getprop.txt" 2>&1

# Exact Phase319 evidence only. Do not pollute this file with generic DSI logs.
{
    grep -a 'TG319F' "$WORK/early-kmsg.log" 2>/dev/null
    grep -a 'TG319F' "$WORK/dmesg.txt" 2>/dev/null
} | awk '!seen[$0]++' > "$WORK/phase319-markers.txt"

# Keep broad display context separately.
{
    grep -aiE 'dsi|drm|mdss|sde|SW_TRIGGER|DMA_DONE|TG315|TG319F|P276|GDM' "$WORK/early-kmsg.log" 2>/dev/null
    grep -aiE 'dsi|drm|mdss|sde|SW_TRIGGER|DMA_DONE|TG315|TG319F|P276|GDM' "$WORK/dmesg.txt" 2>/dev/null
} | awk '!seen[$0]++' > "$WORK/display-context.txt"

# Pstore/ramoops, if available.
mkdir -p "$WORK/pstore"
for f in /sys/fs/pstore/*; do
    [ -f "$f" ] && cp -f "$f" "$WORK/pstore/" 2>/dev/null
done

TG319_COUNT=$(grep -ac 'TG319F' "$WORK/phase319-markers.txt" 2>/dev/null)
[ -n "$TG319_COUNT" ] || TG319_COUNT=0
EARLY_FIRST=$(head -n 1 "$WORK/early-kmsg.log" 2>/dev/null)
EARLY_LAST=$(tail -n 1 "$WORK/early-kmsg.log" 2>/dev/null)

cat > "$WORK/manifest.txt" <<EOF
collector=PHASE319-GOLDEN-COLLECTOR-V1.2
capture_base=$BASE
exact_marker=TG319F
TG319F_count=$TG319_COUNT
early_first=$EARLY_FIRST
early_last=$EARLY_LAST
EOF

# Try common zip implementations available on Android/KernelSU environments.
rm -f "$ZIP" 2>/dev/null
if command -v zip >/dev/null 2>&1; then
    (cd "$WORK" && zip -qr "$ZIP" .)
elif command -v toybox >/dev/null 2>&1 && toybox zip --help >/dev/null 2>&1; then
    (cd "$WORK" && toybox zip -r "$ZIP" . >/dev/null 2>&1)
else
    # Fallback: leave an uncompressed export directory if zip is unavailable.
    FALLBACK="$OUTDIR/A52_PHASE319_GOLDEN_CAPTURE_$STAMP"
    rm -rf "$FALLBACK" 2>/dev/null
    cp -R "$WORK" "$FALLBACK"
    echo "ZIP unavailable. Exported directory: $FALLBACK"
    exit 0
fi

if [ -f "$ZIP" ]; then
    echo "Phase319 capture exported:"
    echo "$ZIP"
    echo "TG319F markers: $TG319_COUNT"
else
    echo "Phase319 export failed"
    exit 1
fi
