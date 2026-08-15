#!/system/bin/sh
# TG Composer Reference Recorder v1.1 core
# Read-only diagnostics. No ptrace, no service/property mutation.

MODDIR=${0%/*}
MODE=${1:-late}
ROOT=/data/local/tmp/tg_ksu_composer_reference
RUN=$ROOT/current
EVENTS=$RUN/events.log
LOCK=$ROOT/recorder.pid
TRACE_ROOT=
TRACE_DIR=
TRACE_ACTIVE=0
KPROBES=
COMPOSER_PID=

umask 022
mkdir -p "$ROOT"
if [ -r "$LOCK" ]; then
    old=$(cat "$LOCK" 2>/dev/null)
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCK"
rm -rf "$RUN"
mkdir -p "$RUN/snapshots" "$RUN/runtime" "$RUN/trace"

up() { awk '{print $1}' /proc/uptime 2>/dev/null; }
upi() { awk '{printf "%d",$1}' /proc/uptime 2>/dev/null; }
log() { printf '%s|%s\n' "$(up)" "$*" >> "$EVENTS"; }

capture_recorders() {
    d=$1
    mkdir -p "$d"
    for p in tg_display_reference tg_boot_reference tg_final_boot_reference tg_gpu_reference; do
        [ -r "/proc/$p" ] && cat "/proc/$p" > "$d/proc-$p.txt" 2>&1 || true
    done
}

find_composer() {
    for p in /proc/[0-9]*; do
        [ -r "$p/cmdline" ] || continue
        c=$(tr '\000' ' ' < "$p/cmdline" 2>/dev/null)
        case "$c" in
            /vendor/bin/hw/vendor.qti.hardware.display.composer-service*|*vendor.qti.hardware.display.composer-service*|*android.hardware.graphics.composer-service*)
                printf '%s\n' "${p##*/}"; return 0 ;;
        esac
    done
    return 1
}

fd_state() {
    [ -d "/proc/$COMPOSER_PID/fd" ] || return
    for f in /proc/$COMPOSER_PID/fd/[0-9]*; do
        [ -e "$f" ] || continue
        n=${f##*/}; target=$(readlink "$f" 2>/dev/null)
        flags=$(awk '$1=="flags:"{print $2}' "/proc/$COMPOSER_PID/fdinfo/$n" 2>/dev/null)
        printf '%s|flags=%s|%s\n' "$n" "$flags" "$target"
    done | sort -n
}

thread_state() {
    [ -d "/proc/$COMPOSER_PID/task" ] || return
    for td in /proc/$COMPOSER_PID/task/[0-9]*; do
        [ -d "$td" ] || continue
        tid=${td##*/}; comm=$(cat "$td/comm" 2>/dev/null)
        wchan=$(cat "$td/wchan" 2>/dev/null); syscall=$(cat "$td/syscall" 2>/dev/null)
        printf '%s|%s|wchan=%s|syscall=%s\n' "$tid" "$comm" "$wchan" "$syscall"
    done | sort -n
}

tids() {
    for td in /proc/$COMPOSER_PID/task/[0-9]*; do [ -d "$td" ] && printf '%s ' "${td##*/}"; done
}

snapshot() {
    label=$1; heavy=${2:-0}; d="$RUN/snapshots/$label"; mkdir -p "$d"
    printf 'label=%s\nuptime=%s\ncomposer_pid=%s\nmode=%s\n' "$label" "$(up)" "$COMPOSER_PID" "$MODE" > "$d/identity.txt"
    if [ -d "/proc/$COMPOSER_PID" ]; then
        tr '\000' ' ' < "/proc/$COMPOSER_PID/cmdline" > "$d/cmdline.txt" 2>/dev/null || true
        readlink "/proc/$COMPOSER_PID/exe" > "$d/exe.txt" 2>&1 || true
        for f in status maps smaps_rollup cgroup sched limits mountinfo; do
            [ -r "/proc/$COMPOSER_PID/$f" ] && cat "/proc/$COMPOSER_PID/$f" > "$d/$f.txt" 2>&1 || true
        done
        fd_state > "$d/fds.txt" 2>&1
        thread_state > "$d/threads.txt" 2>&1
        for td in /proc/$COMPOSER_PID/task/[0-9]*; do
            [ -r "$td/stack" ] || continue
            tid=${td##*/}; cat "$td/stack" > "$d/stack-$tid.txt" 2>&1 || true
        done
    fi
    capture_recorders "$d"
    ls -laZ /dev/dri /dev/kgsl-3d0 /dev/ion /dev/dma_heap /dev/binder /dev/hwbinder /dev/vndbinder > "$d/device-nodes.txt" 2>&1 || true
    ls -la /sys/class/drm > "$d/sys-class-drm.txt" 2>&1 || true
    [ -r /sys/kernel/debug/binder/state ] && cat /sys/kernel/debug/binder/state > "$d/binder-state.txt" 2>&1 || true
    dmesg 2>/dev/null | tail -n 3500 > "$d/dmesg-tail.txt" || true
    logcat -b all -d -t 3500 -v threadtime > "$d/logcat-tail.txt" 2>&1 || true
    if [ "$heavy" = 1 ] && command -v timeout >/dev/null 2>&1; then
        timeout 5 lshal > "$d/lshal.txt" 2>&1 || true
        timeout 5 service list > "$d/service-list.txt" 2>&1 || true
        timeout 5 dumpsys SurfaceFlinger > "$d/dumpsys-SurfaceFlinger.txt" 2>&1 || true
    fi
    log "SNAPSHOT label=$label heavy=$heavy pid=$COMPOSER_PID"
}

enable_event() {
    [ -w "$TRACE_DIR/events/$1/$2/enable" ] || return
    echo 1 > "$TRACE_DIR/events/$1/$2/enable" 2>/dev/null || true
}

add_probe() {
    n=$1; sym=$2
    [ -w "$TRACE_ROOT/kprobe_events" ] || return
    grep -Eq "[[:space:]]$sym$" /proc/kallsyms 2>/dev/null || return
    echo "p:tgcr2/$n $sym" >> "$TRACE_ROOT/kprobe_events" 2>/dev/null || return
    KPROBES="$KPROBES $n"
    [ -w "$TRACE_DIR/events/tgcr2/$n/enable" ] && echo 1 > "$TRACE_DIR/events/tgcr2/$n/enable" 2>/dev/null || true
    log "KPROBE name=$n symbol=$sym"
}

filter_trace() {
    [ "$TRACE_ACTIVE" = 1 ] || return
    ts=$(tids); [ -n "$ts" ] || return
    [ -w "$TRACE_DIR/set_event_pid" ] && printf '%s\n' "$ts" > "$TRACE_DIR/set_event_pid" 2>/dev/null || true
}

setup_trace() {
    for r in /sys/kernel/tracing /sys/kernel/debug/tracing; do
        [ -d "$r/events" ] && [ -d "$r/instances" ] && [ -w "$r/tracing_on" ] && TRACE_ROOT=$r && break
    done
    [ -n "$TRACE_ROOT" ] || { log 'TRACE unavailable'; return; }
    TRACE_DIR="$TRACE_ROOT/instances/tgcomposer_ref2"
    rmdir "$TRACE_DIR" 2>/dev/null || true
    mkdir "$TRACE_DIR" 2>/dev/null || { log 'TRACE instance_failed'; TRACE_DIR=; return; }
    echo 0 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
    echo 4096 > "$TRACE_DIR/buffer_size_kb" 2>/dev/null || true
    [ -w "$TRACE_DIR/options/overwrite" ] && echo 0 > "$TRACE_DIR/options/overwrite" 2>/dev/null || true
    : > "$TRACE_DIR/trace" 2>/dev/null || true
    TRACE_ACTIVE=1
    for e in sys_enter_openat sys_exit_openat sys_enter_ioctl sys_exit_ioctl sys_enter_mmap sys_exit_mmap sys_enter_futex sys_exit_futex sys_enter_poll sys_exit_poll sys_enter_ppoll sys_exit_ppoll sys_enter_epoll_wait sys_exit_epoll_wait sys_enter_epoll_pwait sys_exit_epoll_pwait; do enable_event syscalls "$e"; done
    [ -d "$TRACE_DIR/events/binder" ] && echo 1 > "$TRACE_DIR/events/binder/enable" 2>/dev/null || true
    [ -d "$TRACE_DIR/events/drm" ] && echo 1 > "$TRACE_DIR/events/drm/enable" 2>/dev/null || true
    [ -d "$TRACE_DIR/events/kgsl" ] && echo 1 > "$TRACE_DIR/events/kgsl/enable" 2>/dev/null || true
    add_probe drm_ioctl drm_ioctl
    add_probe drm_getcap drm_getcap
    add_probe drm_setclientcap drm_setclientcap
    add_probe getresources drm_mode_getresources
    add_probe getconnector drm_mode_getconnector
    add_probe getproperty drm_mode_getproperty_ioctl
    add_probe getblob drm_mode_getblob_ioctl
    add_probe obj_getprops drm_mode_obj_get_properties_ioctl
    add_probe getplaneres drm_mode_getplane_res
    add_probe getplane drm_mode_getplane
    add_probe atomic drm_atomic_ioctl
    add_probe msm_open msm_open
    add_probe msm_ioctl msm_ioctl
    add_probe msm_atomic_check msm_atomic_check
    filter_trace
    cat "$TRACE_ROOT/available_events" > "$RUN/trace/available-events.txt" 2>/dev/null || true
    echo 1 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
    log "TRACE armed dir=$TRACE_DIR"
}

cleanup() {
    if [ "$TRACE_ACTIVE" = 1 ]; then
        echo 0 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
        cat "$TRACE_DIR/trace" > "$RUN/trace/trace.txt" 2>&1 || true
        cat "$TRACE_DIR/trace_stat" > "$RUN/trace/trace-stat.txt" 2>&1 || true
    fi
    if [ -w "$TRACE_ROOT/kprobe_events" ]; then for n in $KPROBES; do echo "-:tgcr2/$n" >> "$TRACE_ROOT/kprobe_events" 2>/dev/null || true; done; fi
    [ -n "$TRACE_DIR" ] && rmdir "$TRACE_DIR" 2>/dev/null || true
    chmod -R a+rX "$RUN" 2>/dev/null || true
    rm -f "$LOCK" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

{
    echo TG_COMPOSER_KSU_REFERENCE_V1_1
    echo "start_mode=$MODE"
    echo "kernel=$(uname -a 2>/dev/null)"
    echo "fingerprint=$(getprop ro.build.fingerprint 2>/dev/null)"
    echo "vendor_fingerprint=$(getprop ro.vendor.build.fingerprint 2>/dev/null)"
    echo "boot_uptime=$(up)"
} > "$RUN/IDENTITY.txt"
{
    echo "tracefs=$([ -d /sys/kernel/tracing/events ] && echo 1 || echo 0)"
    echo "kallsyms=$([ -r /proc/kallsyms ] && echo 1 || echo 0)"
    echo "tg_display=$([ -r /proc/tg_display_reference ] && echo 1 || echo 0)"
    echo "tg_boot=$([ -r /proc/tg_boot_reference ] && echo 1 || echo 0)"
    echo "tg_final=$([ -r /proc/tg_final_boot_reference ] && echo 1 || echo 0)"
    echo "tg_gpu=$([ -r /proc/tg_gpu_reference ] && echo 1 || echo 0)"
    echo "selinux=$(getenforce 2>/dev/null)"
} > "$RUN/FEATURES.txt"

log "MODULE_START mode=$MODE"
capture_recorders "$RUN"
log PRECOMPOSER_ARMED
start=$(upi)
while :; do
    COMPOSER_PID=$(find_composer 2>/dev/null)
    [ -n "$COMPOSER_PID" ] && break
    now=$(upi); [ $((now-start)) -ge 240 ] && break
    sleep 0.05
done

if [ -z "$COMPOSER_PID" ]; then log 'COMPOSER_NOT_FOUND timeout=240'; exit 0; fi
cmd=$(tr '\000' ' ' < "/proc/$COMPOSER_PID/cmdline" 2>/dev/null)
echo "$COMPOSER_PID" > "$RUN/composer.pid"; echo "$cmd" > "$RUN/composer.cmdline"
log "COMPOSER_FOUND pid=$COMPOSER_PID cmd=$cmd"
setup_trace
snapshot composer-found 0

LASTFD=$RUN/runtime/.fd-last; LASTTH=$RUN/runtime/.thread-last; LASTTIDS=
: > "$RUN/runtime/fd-changes.log"; : > "$RUN/runtime/thread-changes.log"
start=$(upi); s1=1; s3=1; s5=1; s20=1; s80=1; s120=1
while [ -d "/proc/$COMPOSER_PID" ]; do
    now=$(upi); elapsed=$((now-start))
    fd_state > "$RUN/runtime/.fd-cur" 2>/dev/null
    if [ ! -f "$LASTFD" ] || ! cmp -s "$RUN/runtime/.fd-cur" "$LASTFD"; then { echo "=== uptime=$(up) elapsed=$elapsed ==="; cat "$RUN/runtime/.fd-cur"; } >> "$RUN/runtime/fd-changes.log"; cp "$RUN/runtime/.fd-cur" "$LASTFD"; fi
    thread_state > "$RUN/runtime/.thread-cur" 2>/dev/null
    if [ ! -f "$LASTTH" ] || ! cmp -s "$RUN/runtime/.thread-cur" "$LASTTH"; then { echo "=== uptime=$(up) elapsed=$elapsed ==="; cat "$RUN/runtime/.thread-cur"; } >> "$RUN/runtime/thread-changes.log"; cp "$RUN/runtime/.thread-cur" "$LASTTH"; fi
    ts=$(tids); if [ "$ts" != "$LASTTIDS" ]; then LASTTIDS=$ts; log "THREAD_SET tids=$ts"; filter_trace; fi
    [ "$s1" = 1 ] && [ "$elapsed" -ge 1 ] && { snapshot plus-001s 0; s1=0; }
    [ "$s3" = 1 ] && [ "$elapsed" -ge 3 ] && { snapshot plus-003s 0; s3=0; }
    [ "$s5" = 1 ] && [ "$elapsed" -ge 5 ] && { snapshot plus-005s 1; s5=0; }
    [ "$s20" = 1 ] && [ "$elapsed" -ge 20 ] && { snapshot plus-020s 1; s20=0; }
    [ "$s80" = 1 ] && [ "$elapsed" -ge 80 ] && { snapshot plus-080s 1; s80=0; }
    [ "$s120" = 1 ] && [ "$elapsed" -ge 120 ] && { snapshot plus-120s 1; s120=0; }
    [ "$elapsed" -ge 125 ] && break
    [ "$elapsed" -lt 10 ] && sleep 0.10 || sleep 0.25
done

[ -d "/proc/$COMPOSER_PID" ] && log "MONITOR_COMPLETE pid=$COMPOSER_PID" || log "COMPOSER_EXITED pid=$COMPOSER_PID"
snapshot final 1
dmesg 2>/dev/null | grep -iE 'avc:.*denied|drm|sde|msm|composer|binder|kgsl|iommu|ion|dma.heap' > "$RUN/dmesg-display-filtered.txt" || true
logcat -b all -d -v threadtime 2>/dev/null | grep -iE 'composer|SurfaceFlinger|hwservicemanager|drm|sde|kgsl|gralloc|allocator|mapper|avc:.*denied' > "$RUN/logcat-display-filtered.txt" || true
log MODULE_COMPLETE
exit 0
