#!/system/bin/sh
# TG Composer Reference Recorder v1
# Diagnostic-only KernelSU companion. No ptrace, no service/property mutation.

MODDIR=${0%/*}
ROOT=/data/local/tmp/tg_ksu_composer_reference
RUN=$ROOT/current
EVENTS=$RUN/events.log
TRACE_ROOT=""
TRACE_DIR=""
TRACE_ACTIVE=0
COMPOSER_PID=""
KPROBE_NAMES=""

umask 022
rm -rf "$RUN"
mkdir -p "$RUN" "$RUN/snapshots" "$RUN/runtime" "$RUN/trace"
chmod 0755 "$ROOT" "$RUN" "$RUN/snapshots" "$RUN/runtime" "$RUN/trace" 2>/dev/null || true

uptime_now() {
    awk '{print $1}' /proc/uptime 2>/dev/null
}

uptime_int() {
    awk '{printf "%d", $1}' /proc/uptime 2>/dev/null
}

log_event() {
    printf '%s|%s\n' "$(uptime_now)" "$*" >> "$EVENTS"
}

safe_cmd() {
    out=$1
    shift
    "$@" > "$out" 2>&1 || true
}

limited_cmd() {
    out=$1
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout 5 "$@" > "$out" 2>&1 || true
    else
        printf 'SKIPPED: timeout utility unavailable; refusing potentially blocking command\n' > "$out"
    fi
}

capture_proc_recorders() {
    d=$1
    for p in tg_display_reference tg_final_boot_reference tg_gpu_reference; do
        if [ -r "/proc/$p" ]; then
            cat "/proc/$p" > "$d/proc-$p.txt" 2>&1 || true
        fi
    done
}

capture_drm_sysfs() {
    d=$1
    {
        echo '=== /dev display/allocator nodes ==='
        ls -laZ /dev/dri /dev/kgsl-3d0 /dev/ion /dev/dma_heap /dev/binder /dev/hwbinder /dev/vndbinder 2>&1 || true
        echo
        echo '=== /sys/class/drm ==='
        ls -la /sys/class/drm 2>&1 || true
        for n in /sys/class/drm/*; do
            [ -e "$n" ] || continue
            echo "--- $n ---"
            for f in status enabled dpms modes uevent connector_id; do
                [ -r "$n/$f" ] && { echo "[$f]"; cat "$n/$f" 2>&1; }
            done
        done
        echo
        echo '=== KGSL ==='
        for f in /sys/class/kgsl/kgsl-3d0/state /sys/class/kgsl/kgsl-3d0/gpuclk /sys/class/kgsl/kgsl-3d0/max_gpuclk /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq; do
            [ -r "$f" ] && { echo "[$f]"; cat "$f" 2>&1; }
        done
        echo
        echo '=== DRM debug state ==='
        for f in /sys/kernel/debug/dri/0/state /sys/kernel/debug/dri/0/name /sys/kernel/debug/dri/0/clients; do
            [ -r "$f" ] && { echo "[$f]"; cat "$f" 2>&1; }
        done
    } > "$d/display-topology.txt" 2>&1
}

capture_binder_state() {
    d=$1
    if [ -n "$COMPOSER_PID" ] && [ -r "/sys/kernel/debug/binder/proc/$COMPOSER_PID" ]; then
        cat "/sys/kernel/debug/binder/proc/$COMPOSER_PID" > "$d/binder-composer.txt" 2>&1 || true
    elif [ -n "$COMPOSER_PID" ] && [ -r "/sys/kernel/debug/binder/state" ]; then
        awk -v p="$COMPOSER_PID" '
            $1 == "proc" { keep = ($2 == p) }
            keep { print }
        ' /sys/kernel/debug/binder/state > "$d/binder-composer.txt" 2>&1 || true
    fi
}

capture_composer_proc() {
    d=$1
    [ -n "$COMPOSER_PID" ] || return 0
    [ -d "/proc/$COMPOSER_PID" ] || return 0

    tr '\000' ' ' < "/proc/$COMPOSER_PID/cmdline" > "$d/composer-cmdline.txt" 2>/dev/null || true
    readlink "/proc/$COMPOSER_PID/exe" > "$d/composer-exe.txt" 2>&1 || true
    for f in status maps smaps_rollup mountinfo cgroup sched limits oom_score oom_score_adj; do
        [ -r "/proc/$COMPOSER_PID/$f" ] && cat "/proc/$COMPOSER_PID/$f" > "$d/composer-$f.txt" 2>&1 || true
    done
    ls -la "/proc/$COMPOSER_PID/task" > "$d/composer-tasks.txt" 2>&1 || true

    {
        for fd in /proc/$COMPOSER_PID/fd/[0-9]*; do
            [ -e "$fd" ] || continue
            n=${fd##*/}
            target=$(readlink "$fd" 2>/dev/null)
            printf '%s|%s\n' "$n" "$target"
            if [ -r "/proc/$COMPOSER_PID/fdinfo/$n" ]; then
                sed 's/^/  /' "/proc/$COMPOSER_PID/fdinfo/$n" 2>/dev/null
            fi
        done
    } > "$d/composer-fds.txt" 2>&1

    {
        for td in /proc/$COMPOSER_PID/task/[0-9]*; do
            [ -d "$td" ] || continue
            tid=${td##*/}
            comm=$(cat "$td/comm" 2>/dev/null)
            wchan=$(cat "$td/wchan" 2>/dev/null)
            syscall=$(cat "$td/syscall" 2>/dev/null)
            printf '%s|%s|wchan=%s|syscall=%s\n' "$tid" "$comm" "$wchan" "$syscall"
            if [ -r "$td/stack" ]; then
                echo "--- stack $tid ---"
                cat "$td/stack" 2>/dev/null || true
            fi
        done
    } > "$d/composer-threads.txt" 2>&1
}

capture_snapshot() {
    label=$1
    d="$RUN/snapshots/$label"
    mkdir -p "$d"
    printf 'label=%s\nuptime=%s\nwall=%s\ncomposer_pid=%s\n' \
        "$label" "$(uptime_now)" "$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null)" "$COMPOSER_PID" > "$d/identity.txt"

    ps -A -T -o PID,TID,PPID,USER,STAT,NAME,ARGS > "$d/ps-threads.txt" 2>&1 || ps -A > "$d/ps-threads.txt" 2>&1 || true
    getprop > "$d/getprop.txt" 2>&1 || true
    capture_composer_proc "$d"
    capture_binder_state "$d"
    capture_drm_sysfs "$d"
    capture_proc_recorders "$d"

    limited_cmd "$d/lshal.txt" lshal
    limited_cmd "$d/service-list.txt" service list
    limited_cmd "$d/dumpsys-SurfaceFlinger.txt" dumpsys SurfaceFlinger

    dmesg 2>/dev/null | tail -n 5000 > "$d/dmesg-tail.txt" || true
    logcat -b all -d -t 5000 -v threadtime > "$d/logcat-tail.txt" 2>&1 || true
    log_event "SNAPSHOT label=$label pid=$COMPOSER_PID"
}

find_composer_pid() {
    for p in /proc/[0-9]*; do
        [ -r "$p/cmdline" ] || continue
        cmd=$(tr '\000' ' ' < "$p/cmdline" 2>/dev/null)
        case "$cmd" in
            *vendor.qti.hardware.display.composer*|*/vendor/bin/hw/vendor.qti.hardware.displ*|*android.hardware.graphics.composer*)
                printf '%s\n' "${p##*/}"
                return 0
                ;;
        esac
    done
    return 1
}

write_fd_state() {
    out=$1
    [ -n "$COMPOSER_PID" ] || return 0
    {
        for fd in /proc/$COMPOSER_PID/fd/[0-9]*; do
            [ -e "$fd" ] || continue
            n=${fd##*/}
            target=$(readlink "$fd" 2>/dev/null)
            flags=$(awk '$1 == "flags:" {print $2}' "/proc/$COMPOSER_PID/fdinfo/$n" 2>/dev/null)
            printf '%s|flags=%s|%s\n' "$n" "$flags" "$target"
        done
    } | sort -n > "$out" 2>/dev/null
}

write_thread_state() {
    out=$1
    [ -n "$COMPOSER_PID" ] || return 0
    {
        for td in /proc/$COMPOSER_PID/task/[0-9]*; do
            [ -d "$td" ] || continue
            tid=${td##*/}
            comm=$(cat "$td/comm" 2>/dev/null)
            wchan=$(cat "$td/wchan" 2>/dev/null)
            syscall=$(cat "$td/syscall" 2>/dev/null)
            printf '%s|%s|wchan=%s|syscall=%s\n' "$tid" "$comm" "$wchan" "$syscall"
        done
    } | sort -n > "$out" 2>/dev/null
}

current_tids() {
    [ -n "$COMPOSER_PID" ] || return 0
    for td in /proc/$COMPOSER_PID/task/[0-9]*; do
        [ -d "$td" ] || continue
        printf '%s ' "${td##*/}"
    done
}

set_trace_pid_filter() {
    [ "$TRACE_ACTIVE" -eq 1 ] || return 0
    tids=$(current_tids)
    [ -n "$tids" ] || return 0
    if [ -w "$TRACE_DIR/set_event_pid" ]; then
        printf '%s\n' "$tids" > "$TRACE_DIR/set_event_pid" 2>/dev/null || true
    else
        expr=""
        for t in $tids; do
            if [ -z "$expr" ]; then expr="common_pid == $t"; else expr="$expr || common_pid == $t"; fi
        done
        for g in syscalls binder drm kgsl tgcr; do
            [ -w "$TRACE_DIR/events/$g/filter" ] && printf '%s\n' "$expr" > "$TRACE_DIR/events/$g/filter" 2>/dev/null || true
        done
    fi
}

enable_event() {
    g=$1
    e=$2
    if [ -w "$TRACE_DIR/events/$g/$e/enable" ]; then
        echo 1 > "$TRACE_DIR/events/$g/$e/enable" 2>/dev/null || true
        log_event "TRACE_EVENT enabled=$g/$e"
    fi
}

add_kprobe() {
    name=$1
    symbol=$2
    [ -n "$TRACE_ROOT" ] || return 0
    [ -w "$TRACE_ROOT/kprobe_events" ] || return 0
    grep -Eq "[[:space:]]$symbol$" /proc/kallsyms 2>/dev/null || return 0
    echo "p:tgcr/$name $symbol" >> "$TRACE_ROOT/kprobe_events" 2>/dev/null || return 0
    KPROBE_NAMES="$KPROBE_NAMES $name"
    [ -w "$TRACE_DIR/events/tgcr/$name/enable" ] && echo 1 > "$TRACE_DIR/events/tgcr/$name/enable" 2>/dev/null || true
    log_event "KPROBE enabled=$name symbol=$symbol"
}

setup_trace() {
    for r in /sys/kernel/tracing /sys/kernel/debug/tracing; do
        if [ -d "$r/events" ] && [ -w "$r/tracing_on" ] && [ -d "$r/instances" ]; then
            TRACE_ROOT=$r
            break
        fi
    done

    if [ -z "$TRACE_ROOT" ]; then
        log_event "TRACE unavailable reason=no_writable_isolated_tracefs"
        return 0
    fi

    TRACE_DIR="$TRACE_ROOT/instances/tgcomposer_ref"
    rmdir "$TRACE_DIR" 2>/dev/null || true
    mkdir "$TRACE_DIR" 2>/dev/null || { log_event "TRACE unavailable reason=instance_create_failed root=$TRACE_ROOT"; TRACE_DIR=""; return 0; }

    echo 0 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
    echo nop > "$TRACE_DIR/current_tracer" 2>/dev/null || true
    echo 2048 > "$TRACE_DIR/buffer_size_kb" 2>/dev/null || true
    [ -w "$TRACE_DIR/options/overwrite" ] && echo 0 > "$TRACE_DIR/options/overwrite" 2>/dev/null || true
    : > "$TRACE_DIR/trace" 2>/dev/null || true

    TRACE_ACTIVE=1
    log_event "TRACE instance=$TRACE_DIR overwrite=0"

    for e in \
        sys_enter_openat sys_exit_openat \
        sys_enter_ioctl sys_exit_ioctl \
        sys_enter_mmap sys_exit_mmap \
        sys_enter_poll sys_exit_poll \
        sys_enter_ppoll sys_exit_ppoll \
        sys_enter_epoll_wait sys_exit_epoll_wait \
        sys_enter_epoll_pwait sys_exit_epoll_pwait \
        sys_enter_futex sys_exit_futex; do
        enable_event syscalls "$e"
    done

    if [ -d "$TRACE_DIR/events/binder" ]; then
        echo 1 > "$TRACE_DIR/events/binder/enable" 2>/dev/null || true
        log_event "TRACE_GROUP enabled=binder"
    fi
    if [ -d "$TRACE_DIR/events/drm" ]; then
        echo 1 > "$TRACE_DIR/events/drm/enable" 2>/dev/null || true
        log_event "TRACE_GROUP enabled=drm"
    fi
    if [ -d "$TRACE_DIR/events/kgsl" ]; then
        echo 1 > "$TRACE_DIR/events/kgsl/enable" 2>/dev/null || true
        log_event "TRACE_GROUP enabled=kgsl"
    fi

    add_kprobe tgcr_drm_ioctl drm_ioctl
    add_kprobe tgcr_drm_getcap drm_getcap
    add_kprobe tgcr_drm_setclientcap drm_setclientcap
    add_kprobe tgcr_getresources drm_mode_getresources
    add_kprobe tgcr_getconnector drm_mode_getconnector
    add_kprobe tgcr_getproperty drm_mode_getproperty_ioctl
    add_kprobe tgcr_getblob drm_mode_getblob_ioctl
    add_kprobe tgcr_obj_getprops drm_mode_obj_get_properties_ioctl
    add_kprobe tgcr_getplaneres drm_mode_getplane_res
    add_kprobe tgcr_getplane drm_mode_getplane
    add_kprobe tgcr_atomic drm_atomic_ioctl
    add_kprobe tgcr_msm_open msm_open
    add_kprobe tgcr_msm_ioctl msm_ioctl
    add_kprobe tgcr_msm_atomic_check msm_atomic_check
    add_kprobe tgcr_sde_atomic_check sde_crtc_atomic_check

    set_trace_pid_filter
    cat "$TRACE_ROOT/available_events" > "$RUN/trace/available-events.txt" 2>/dev/null || true
    cat /proc/kallsyms 2>/dev/null | grep -E ' (drm_ioctl|drm_getcap|drm_setclientcap|drm_mode_getresources|drm_mode_getconnector|drm_mode_getproperty_ioctl|drm_mode_getblob_ioctl|drm_mode_obj_get_properties_ioctl|drm_mode_getplane_res|drm_mode_getplane|drm_atomic_ioctl|msm_open|msm_ioctl|msm_atomic_check|sde_crtc_atomic_check)$' > "$RUN/trace/target-symbols.txt" || true
    echo 1 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
}

stop_trace() {
    [ "$TRACE_ACTIVE" -eq 1 ] || return 0
    echo 0 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
    cat "$TRACE_DIR/trace" > "$RUN/trace/trace.txt" 2>&1 || true
    cat "$TRACE_DIR/trace_stat" > "$RUN/trace/trace-stat.txt" 2>&1 || true
    cat "$TRACE_DIR/set_event_pid" > "$RUN/trace/set-event-pid.txt" 2>&1 || true
    log_event "TRACE stopped"
    TRACE_ACTIVE=0
}

cleanup_kprobes() {
    [ -n "$TRACE_ROOT" ] || return 0
    [ -w "$TRACE_ROOT/kprobe_events" ] || return 0
    for n in $KPROBE_NAMES; do
        echo "-:tgcr/$n" >> "$TRACE_ROOT/kprobe_events" 2>/dev/null || true
    done
}

finish() {
    stop_trace
    cleanup_kprobes
    if [ -n "$TRACE_DIR" ]; then
        rmdir "$TRACE_DIR" 2>/dev/null || true
    fi
    chmod -R a+rX "$RUN" 2>/dev/null || true
}
trap finish EXIT INT TERM

# Boot/module identity and feature audit.
{
    echo 'TG_COMPOSER_KSU_REFERENCE_V1'
    echo "module_dir=$MODDIR"
    echo "ksu=$KSU"
    echo "kernel=$(uname -a 2>/dev/null)"
    echo "fingerprint=$(getprop ro.build.fingerprint 2>/dev/null)"
    echo "vendor_fingerprint=$(getprop ro.vendor.build.fingerprint 2>/dev/null)"
    echo "hardware=$(getprop ro.hardware 2>/dev/null)"
    echo "first_api=$(getprop ro.product.first_api_level 2>/dev/null)"
    echo "boot_uptime=$(uptime_now)"
    echo "wall=$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null)"
} > "$RUN/IDENTITY.txt"

{
    for p in /sys/kernel/tracing /sys/kernel/debug/tracing; do
        printf '%s exists=%s events=%s instances=%s writable=%s\n' "$p" \
            "$([ -d "$p" ] && echo 1 || echo 0)" \
            "$([ -d "$p/events" ] && echo 1 || echo 0)" \
            "$([ -d "$p/instances" ] && echo 1 || echo 0)" \
            "$([ -w "$p/tracing_on" ] && echo 1 || echo 0)"
    done
    printf 'kallsyms_readable=%s\n' "$([ -r /proc/kallsyms ] && echo 1 || echo 0)"
    printf 'binder_debugfs=%s\n' "$([ -d /sys/kernel/debug/binder ] && echo 1 || echo 0)"
    printf 'proc_tg_display_reference=%s\n' "$([ -r /proc/tg_display_reference ] && echo 1 || echo 0)"
    printf 'proc_tg_final_boot_reference=%s\n' "$([ -r /proc/tg_final_boot_reference ] && echo 1 || echo 0)"
    printf 'proc_tg_gpu_reference=%s\n' "$([ -r /proc/tg_gpu_reference ] && echo 1 || echo 0)"
    printf 'selinux=%s\n' "$(getenforce 2>/dev/null)"
} > "$RUN/FEATURES.txt"

log_event "MODULE_START"
capture_snapshot precomposer

wait_start=$(uptime_int)
while :; do
    COMPOSER_PID=$(find_composer_pid 2>/dev/null)
    if [ -n "$COMPOSER_PID" ]; then
        break
    fi
    now=$(uptime_int)
    [ $((now - wait_start)) -ge 240 ] && break
    sleep 0.25
 done

if [ -z "$COMPOSER_PID" ]; then
    log_event "COMPOSER_NOT_FOUND timeout=240"
    capture_snapshot composer-not-found
    exit 0
fi

cmd=$(tr '\000' ' ' < "/proc/$COMPOSER_PID/cmdline" 2>/dev/null)
log_event "COMPOSER_FOUND pid=$COMPOSER_PID cmd=$cmd"
printf '%s\n' "$COMPOSER_PID" > "$RUN/composer.pid"
printf '%s\n' "$cmd" > "$RUN/composer.cmdline"

capture_snapshot composer-found
setup_trace

: > "$RUN/runtime/fd-changes.log"
: > "$RUN/runtime/thread-changes.log"
LAST_FD="$RUN/runtime/.fd-last"
CUR_FD="$RUN/runtime/.fd-cur"
LAST_THR="$RUN/runtime/.thread-last"
CUR_THR="$RUN/runtime/.thread-cur"
LAST_TIDS=""

start=$(uptime_int)
next_snapshot_1=1
next_snapshot_3=1
next_snapshot_5=1
next_snapshot_10=1
next_snapshot_20=1
next_snapshot_40=1
next_snapshot_80=1
next_snapshot_120=1

while [ -d "/proc/$COMPOSER_PID" ]; do
    now=$(uptime_int)
    elapsed=$((now - start))

    write_fd_state "$CUR_FD"
    if [ ! -f "$LAST_FD" ] || ! cmp -s "$CUR_FD" "$LAST_FD"; then
        {
            echo "=== uptime=$(uptime_now) elapsed=$elapsed ==="
            cat "$CUR_FD"
        } >> "$RUN/runtime/fd-changes.log"
        cp "$CUR_FD" "$LAST_FD" 2>/dev/null || true
    fi

    write_thread_state "$CUR_THR"
    if [ ! -f "$LAST_THR" ] || ! cmp -s "$CUR_THR" "$LAST_THR"; then
        {
            echo "=== uptime=$(uptime_now) elapsed=$elapsed ==="
            cat "$CUR_THR"
        } >> "$RUN/runtime/thread-changes.log"
        cp "$CUR_THR" "$LAST_THR" 2>/dev/null || true
    fi

    tids=$(current_tids)
    if [ "$tids" != "$LAST_TIDS" ]; then
        log_event "THREAD_SET tids=$tids"
        LAST_TIDS="$tids"
        set_trace_pid_filter
    fi

    if [ "$next_snapshot_1" -eq 1 ] && [ "$elapsed" -ge 1 ]; then capture_snapshot plus-001s; next_snapshot_1=0; fi
    if [ "$next_snapshot_3" -eq 1 ] && [ "$elapsed" -ge 3 ]; then capture_snapshot plus-003s; next_snapshot_3=0; fi
    if [ "$next_snapshot_5" -eq 1 ] && [ "$elapsed" -ge 5 ]; then capture_snapshot plus-005s; next_snapshot_5=0; fi
    if [ "$next_snapshot_10" -eq 1 ] && [ "$elapsed" -ge 10 ]; then capture_snapshot plus-010s; next_snapshot_10=0; fi
    if [ "$next_snapshot_20" -eq 1 ] && [ "$elapsed" -ge 20 ]; then capture_snapshot plus-020s; next_snapshot_20=0; fi
    if [ "$next_snapshot_40" -eq 1 ] && [ "$elapsed" -ge 40 ]; then capture_snapshot plus-040s; next_snapshot_40=0; fi
    if [ "$next_snapshot_80" -eq 1 ] && [ "$elapsed" -ge 80 ]; then capture_snapshot plus-080s; next_snapshot_80=0; fi
    if [ "$next_snapshot_120" -eq 1 ] && [ "$elapsed" -ge 120 ]; then capture_snapshot plus-120s; next_snapshot_120=0; fi

    [ "$elapsed" -ge 125 ] && break
    if [ "$elapsed" -lt 30 ]; then sleep 0.20; else sleep 0.50; fi
 done

if [ -d "/proc/$COMPOSER_PID" ]; then
    log_event "MONITOR_COMPLETE pid=$COMPOSER_PID"
else
    log_event "COMPOSER_EXITED pid=$COMPOSER_PID"
fi
capture_snapshot final

# High-value terminal evidence.
dmesg 2>/dev/null | grep -iE 'avc:.*denied|drm|sde|msm|composer|binder|kgsl|iommu|ion|dma.heap' > "$RUN/dmesg-display-filtered.txt" || true
logcat -b all -d -v threadtime 2>/dev/null | grep -iE 'composer|SurfaceFlinger|hwservicemanager|drm|sde|kgsl|gralloc|allocator|mapper|avc:.*denied' > "$RUN/logcat-display-filtered.txt" || true

log_event "MODULE_COMPLETE"
exit 0
