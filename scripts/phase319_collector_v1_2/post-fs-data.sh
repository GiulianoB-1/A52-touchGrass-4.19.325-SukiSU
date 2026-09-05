#!/system/bin/sh

MODDIR=${0%/*}
BASE=/data/adb/phase319_capture
EARLY="$BASE/early-kmsg.log"
PIDFILE="$BASE/kmsg.pid"

mkdir -p "$BASE"
chmod 700 "$BASE" 2>/dev/null

# Preserve the previous boot's capture before truncating the live file.
if [ -s "$EARLY" ]; then
    cp -f "$EARLY" "$BASE/early-kmsg.previous.log" 2>/dev/null
fi

: > "$EARLY"
chmod 600 "$EARLY" 2>/dev/null

# Start as early as KernelSU's post-fs-data stage permits. Reading /dev/kmsg
# continuously prevents early TG319F records from being lost when printk wraps.
(
    echo "PHASE319_CAPTURE_START $(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null)" >> "$EARLY"
    cat /dev/kmsg >> "$EARLY" 2>&1
) &

echo $! > "$PIDFILE"
chmod 600 "$PIDFILE" 2>/dev/null
