#!/system/bin/sh

BASE=/data/adb/a52_phase319_captures
OUTDIR=/sdcard/Download/A52_Phase319
STAMP=$(date '+%Y%m%d_%H%M%S' 2>/dev/null)
[ -n "$STAMP" ] || STAMP=unknown
WORK=/data/local/tmp/a52_phase319_export_$STAMP
TAR="$OUTDIR/A52_Phase319_ALL_$STAMP.tar"

rm -rf "$WORK" 2>/dev/null
mkdir -p "$WORK/stored-captures" "$WORK/pstore" "$OUTDIR"

# Export every persisted early-boot capture, not only the current boot.
for d in "$BASE"/early_*; do
    [ -d "$d" ] && cp -R "$d" "$WORK/stored-captures/" 2>/dev/null
done
cp -f "$BASE/current_run" "$WORK/current_run.txt" 2>/dev/null

# Current snapshots for context.
dmesg > "$WORK/dmesg.txt" 2>&1
logcat -d -b all > "$WORK/logcat-all.txt" 2>&1
cat /proc/version > "$WORK/proc-version.txt" 2>&1
cat /proc/cmdline > "$WORK/proc-cmdline.txt" 2>&1
uname -a > "$WORK/uname.txt" 2>&1
getprop > "$WORK/getprop.txt" 2>&1

# Exact Phase319 evidence only. Generic DSI/DRM lines go elsewhere.
{
    for f in "$BASE"/early_*/kmsg.log; do
        [ -f "$f" ] && grep -a 'TG319F' "$f" 2>/dev/null
    done
    grep -a 'TG319F' "$WORK/dmesg.txt" 2>/dev/null
} | awk '!seen[$0]++' > "$WORK/phase319-markers.txt"

# Broad display context kept separately for timing/correlation.
{
    for f in "$BASE"/early_*/kmsg.log; do
        [ -f "$f" ] && grep -aiE 'dsi|drm|mdss|sde|SW_TRIGGER|DMA_DONE|TG315|TG319F|P276|GDM' "$f" 2>/dev/null
    done
    grep -aiE 'dsi|drm|mdss|sde|SW_TRIGGER|DMA_DONE|TG315|TG319F|P276|GDM' "$WORK/dmesg.txt" 2>/dev/null
} | awk '!seen[$0]++' > "$WORK/display-context.txt"

# Pstore/ramoops, if available.
for f in /sys/fs/pstore/*; do
    [ -f "$f" ] && cp -f "$f" "$WORK/pstore/" 2>/dev/null
done

TG319_COUNT=$(grep -ac 'TG319F' "$WORK/phase319-markers.txt" 2>/dev/null)
[ -n "$TG319_COUNT" ] || TG319_COUNT=0
EARLY_FILES=0
for f in "$BASE"/early_*/kmsg.log; do
    [ -f "$f" ] && EARLY_FILES=$((EARLY_FILES + 1))
done

cat > "$WORK/manifest.txt" <<EOF
collector=PHASE319-GOLDEN-COLLECTOR-V1.2
capture_base=$BASE
exact_marker=TG319F
TG319F_count=$TG319_COUNT
early_kmsg_files=$EARLY_FILES
archive=$TAR
EOF

rm -f "$TAR" 2>/dev/null
if command -v tar >/dev/null 2>&1; then
    tar -cf "$TAR" -C "$WORK" .
elif command -v toybox >/dev/null 2>&1; then
    toybox tar -cf "$TAR" -C "$WORK" .
else
    echo "No tar implementation found"
    echo "Unpacked export remains at: $WORK"
    exit 1
fi

if [ -f "$TAR" ]; then
    echo "Phase319 capture exported:"
    echo "$TAR"
    echo "TG319F markers: $TG319_COUNT"
    echo "Persisted early-kmsg files: $EARLY_FILES"
    rm -rf "$WORK" 2>/dev/null
else
    echo "Phase319 export failed"
    exit 1
fi
