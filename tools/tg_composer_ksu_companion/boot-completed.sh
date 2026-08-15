#!/system/bin/sh
MODDIR=${0%/*}
RUN=/data/local/tmp/tg_ksu_composer_reference/current
[ -d "$RUN" ] || exit 0
printf '%s|BOOT_COMPLETED property=%s\n' "$(awk '{print $1}' /proc/uptime 2>/dev/null)" "$(getprop sys.boot_completed 2>/dev/null)" >> "$RUN/events.log"
getprop > "$RUN/boot-completed-getprop.txt" 2>&1 || true
ps -A -T -o PID,TID,PPID,USER,STAT,NAME,ARGS > "$RUN/boot-completed-ps.txt" 2>&1 || ps -A > "$RUN/boot-completed-ps.txt" 2>&1 || true
chmod -R a+rX "$RUN" 2>/dev/null || true
