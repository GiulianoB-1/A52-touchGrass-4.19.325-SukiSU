#!/system/bin/sh
MODDIR=${0%/*}
RUN=/data/local/tmp/tg_ksu_composer_reference/current
mkdir -p "$RUN/manual"
TS=$(date '+%Y%m%d_%H%M%S' 2>/dev/null)
[ -n "$TS" ] || TS=$(awk '{printf "%d", $1}' /proc/uptime 2>/dev/null)
OUT="$RUN/manual/$TS"
mkdir -p "$OUT"
printf '%s|MANUAL_SNAPSHOT label=%s\n' "$(awk '{print $1}' /proc/uptime 2>/dev/null)" "$TS" >> "$RUN/events.log"
getprop > "$OUT/getprop.txt" 2>&1 || true
ps -A -T -o PID,TID,PPID,USER,STAT,NAME,ARGS > "$OUT/ps.txt" 2>&1 || ps -A > "$OUT/ps.txt" 2>&1 || true
dmesg > "$OUT/dmesg.txt" 2>&1 || true
logcat -b all -d -v threadtime > "$OUT/logcat-all.txt" 2>&1 || true
for p in tg_display_reference tg_final_boot_reference tg_gpu_reference; do
    [ -r "/proc/$p" ] && cat "/proc/$p" > "$OUT/proc-$p.txt" 2>&1 || true
done
chmod -R a+rX "$RUN" 2>/dev/null || true
