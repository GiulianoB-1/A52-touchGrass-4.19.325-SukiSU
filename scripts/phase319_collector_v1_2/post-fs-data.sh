#!/system/bin/sh

BASE=/data/adb/a52_phase319_captures
BOOTID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null | tr -d '\r\n')
[ -n "$BOOTID" ] || BOOTID=$(date '+%Y%m%d_%H%M%S' 2>/dev/null)
[ -n "$BOOTID" ] || BOOTID=unknown
RUN="$BASE/early_$BOOTID"
KMSG="$RUN/kmsg.log"
PIDFILE="$RUN/kmsg.pid"

mkdir -p "$RUN"
chmod 700 "$BASE" "$RUN" 2>/dev/null
printf '%s\n' "$RUN" > "$BASE/current_run"

{
    echo "collector=PHASE319-GOLDEN-COLLECTOR-V1.2"
    echo "boot_id=$BOOTID"
    echo "capture_start=$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null)"
    cat /proc/version 2>/dev/null
    cat /proc/cmdline 2>/dev/null
} > "$RUN/identity.txt"
chmod 600 "$RUN/identity.txt" 2>/dev/null

: > "$KMSG"
chmod 600 "$KMSG" 2>/dev/null

# Start at KernelSU post-fs-data, before the Phase319 display transaction.
# Keeping /dev/kmsg open continuously preserves early TG319F records even
# after the normal printk ring buffer wraps.
cat /dev/kmsg >> "$KMSG" 2>&1 &
KPID=$!
printf '%s\n' "$KPID" > "$PIDFILE"
chmod 600 "$PIDFILE" 2>/dev/null

# The target transaction occurs during early display bring-up. Stop after
# two minutes so vendor printk spam cannot grow this file indefinitely.
(
    sleep 120
    if kill -0 "$KPID" 2>/dev/null; then
        kill "$KPID" 2>/dev/null
    fi
    echo "capture_stop=$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null)" >> "$RUN/identity.txt"
) &
