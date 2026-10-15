#!/system/bin/sh
MODDIR=${MODDIR:-${0%/*}/..}
export MODDIR
. "$MODDIR/bin/common.sh"

session=$1
duration=${2:-90}
auto_cycle=${3:-0}
traceout="$session/trace"
state="$session/state"
mkdir -p "$traceout" "$state"

tracefs=$(find_tracefs 2>/dev/null || true)
if [ -z "$tracefs" ]; then
  echo "tracefs unavailable; snapshot-only capture" >"$traceout/TRACE-STATUS.txt"
  sleep "$duration"
  exit 0
fi

current=$(cat "$tracefs/current_tracer" 2>/dev/null)
if [ -n "$current" ] && [ "$current" != "nop" ]; then
  {
    echo "active tracer detected: $current"
    echo "The module refused to replace another tracer."
    echo "Snapshot-only capture was used."
  } >"$traceout/TRACE-STATUS.txt"
  sleep "$duration"
  exit 0
fi

for f in current_tracer tracing_on buffer_size_kb trace_clock set_ftrace_filter; do
  [ -r "$tracefs/$f" ] && cat "$tracefs/$f" >"$state/$f" 2>/dev/null || true
done
cat "$tracefs/available_tracers" >"$traceout/available-tracers.txt" 2>/dev/null || true
cat "$tracefs/available_events" >"$traceout/available-events.txt" 2>/dev/null || true
cat "$tracefs/available_filter_functions" >"$traceout/available-filter-functions.txt" 2>/dev/null || true

enabled_events="$state/enabled-events.txt"
created_kprobes="$state/created-kprobes.txt"
changed_options="$state/changed-options.txt"
: >"$enabled_events"
: >"$created_kprobes"
: >"$changed_options"

restore_trace() {
  echo 0 >"$tracefs/tracing_on" 2>/dev/null || true

  if [ -d "$tracefs/events/tgprobe" ]; then
    echo 0 >"$tracefs/events/tgprobe/enable" 2>/dev/null || true
  fi
  if [ -w "$tracefs/kprobe_events" ]; then
    sed '1!G;h;$!d' "$created_kprobes" 2>/dev/null | while IFS= read -r e; do
      [ -n "$e" ] && echo "-:$e" >>"$tracefs/kprobe_events" 2>/dev/null || true
    done
  fi

  while IFS='|' read -r path old; do
    [ -n "$path" ] && [ -w "$path" ] && echo "$old" >"$path" 2>/dev/null || true
  done <"$enabled_events"

  while IFS='|' read -r path old; do
    [ -n "$path" ] && [ -w "$path" ] && echo "$old" >"$path" 2>/dev/null || true
  done <"$changed_options"

  echo nop >"$tracefs/current_tracer" 2>/dev/null || true
  : >"$tracefs/set_ftrace_filter" 2>/dev/null || true
  [ -s "$state/set_ftrace_filter" ] && cat "$state/set_ftrace_filter" >"$tracefs/set_ftrace_filter" 2>/dev/null || true
  [ -s "$state/buffer_size_kb" ] && cat "$state/buffer_size_kb" >"$tracefs/buffer_size_kb" 2>/dev/null || true
  [ -s "$state/trace_clock" ] && cat "$state/trace_clock" >"$tracefs/trace_clock" 2>/dev/null || true
  [ -s "$state/current_tracer" ] && cat "$state/current_tracer" >"$tracefs/current_tracer" 2>/dev/null || true
  [ -s "$state/tracing_on" ] && cat "$state/tracing_on" >"$tracefs/tracing_on" 2>/dev/null || true
}
trap 'restore_trace' EXIT HUP INT TERM

echo 0 >"$tracefs/tracing_on" 2>/dev/null || true
echo nop >"$tracefs/current_tracer" 2>/dev/null || true
: >"$tracefs/trace" 2>/dev/null || true
: >"$tracefs/set_ftrace_filter" 2>/dev/null || true
echo 8192 >"$tracefs/buffer_size_kb" 2>/dev/null || true
[ -w "$tracefs/options/overwrite" ] && {
  old=$(cat "$tracefs/options/overwrite" 2>/dev/null)
  echo "$tracefs/options/overwrite|${old:-0}" >>"$changed_options"
  echo 1 >"$tracefs/options/overwrite" 2>/dev/null || true
}

symbols="$MODDIR/config/symbols.txt"
found="$traceout/found-functions.txt"
missing="$traceout/missing-functions.txt"
: >"$found"
: >"$missing"

if [ -r "$tracefs/available_filter_functions" ]; then
  awk '{print $1}' "$tracefs/available_filter_functions" >"$state/available-function-names.txt"
  while IFS= read -r fn; do
    case "$fn" in ''|\#*) continue ;; esac
    if grep -Fxq "$fn" "$state/available-function-names.txt"; then
      echo "$fn" >>"$found"
    else
      echo "$fn" >>"$missing"
    fi
  done <"$symbols"
fi

tracer=none
if grep -qw function_graph "$tracefs/available_tracers" 2>/dev/null && [ -s "$found" ]; then
  while IFS= read -r fn; do
    echo "$fn" >>"$tracefs/set_ftrace_filter" 2>/dev/null || true
  done <"$found"
  if echo function_graph >"$tracefs/current_tracer" 2>/dev/null; then
    tracer=function_graph
    for opt in funcgraph-abstime funcgraph-duration funcgraph-proc funcgraph-tail; do
      if [ -w "$tracefs/options/$opt" ]; then
        old=$(cat "$tracefs/options/$opt" 2>/dev/null)
        echo "$tracefs/options/$opt|${old:-0}" >>"$changed_options"
        echo 1 >"$tracefs/options/$opt" 2>/dev/null || true
      fi
    done
  fi
elif grep -qw function "$tracefs/available_tracers" 2>/dev/null && [ -s "$found" ]; then
  while IFS= read -r fn; do
    echo "$fn" >>"$tracefs/set_ftrace_filter" 2>/dev/null || true
  done <"$found"
  echo function >"$tracefs/current_tracer" 2>/dev/null && tracer=function
fi

add_event() {
  rel=$1
  path="$tracefs/events/$rel/enable"
  if [ -w "$path" ]; then
    old=$(cat "$path" 2>/dev/null)
    echo "$path|${old:-0}" >>"$enabled_events"
    echo 1 >"$path" 2>/dev/null || true
  fi
}

# Enable broad but bounded subsystem tracepoints selected from actual availability.
if [ -r "$tracefs/available_events" ]; then
  grep -E -f "$MODDIR/config/event-patterns.txt" "$tracefs/available_events" \
    | sort -u >"$traceout/enabled-event-names.txt" 2>/dev/null || true
  while IFS= read -r ev; do
    [ -n "$ev" ] && add_event "$ev"
  done <"$traceout/enabled-event-names.txt"
fi

# Dynamic probes provide key arguments and return values when supported.
if [ -w "$tracefs/kprobe_events" ] && [ -s "$found" ]; then
  add_probe() {
    event=$1
    spec=$2
    if printf '%s\n' "$spec" >>"$tracefs/kprobe_events" 2>"$traceout/kprobe-errors.tmp"; then
      echo "tgprobe/$event" >>"$created_kprobes"
    else
      {
        echo "event=$event"
        echo "spec=$spec"
        cat "$traceout/kprobe-errors.tmp" 2>/dev/null
      } >>"$traceout/kprobe-failures.txt"
    fi
    rm -f "$traceout/kprobe-errors.tmp"
  }

  if grep -Fxq ion_alloc "$found"; then
    add_probe ion_alloc 'p:tgprobe/ion_alloc ion_alloc len=%x0:u64 mask=%x1:u32 flags=%x2:u32'
    add_probe ion_alloc_ret 'r:tgprobe/ion_alloc_ret ion_alloc ret=$retval:s64'
  fi
  if grep -Fxq ion_cma_allocate "$found"; then
    add_probe ion_cma_allocate 'p:tgprobe/ion_cma_allocate ion_cma_allocate len=%x2:u64 flags=%x3:u64'
    add_probe ion_cma_allocate_ret 'r:tgprobe/ion_cma_allocate_ret ion_cma_allocate ret=$retval:s64'
  fi

  while IFS= read -r fn; do
    case "$fn" in ''|\#*) continue ;; esac
    grep -Fxq "$fn" "$found" || continue
    ev=$(echo "$fn" | tr -cd 'A-Za-z0-9_')
    add_probe "$ev" "p:tgprobe/$ev $fn"
    add_probe "${ev}_ret" "r:tgprobe/${ev}_ret $fn ret=\$retval:s64"
  done <"$MODDIR/config/return-probes.txt"

  [ -w "$tracefs/events/tgprobe/enable" ] && echo 1 >"$tracefs/events/tgprobe/enable" 2>/dev/null || true
fi

{
  echo "tracefs=$tracefs"
  echo "selected_tracer=$tracer"
  echo "function_count=$(wc -l <"$found" 2>/dev/null)"
  echo "missing_function_count=$(wc -l <"$missing" 2>/dev/null)"
  echo "event_count=$(wc -l <"$traceout/enabled-event-names.txt" 2>/dev/null)"
  echo "kprobe_count=$(wc -l <"$created_kprobes" 2>/dev/null)"
  echo "duration_seconds=$duration"
  echo "automatic_screen_cycle=$auto_cycle"
  echo "buffer_size_kb=$(cat "$tracefs/buffer_size_kb" 2>/dev/null)"
} >"$traceout/TRACE-STATUS.txt"

echo 1 >"$tracefs/tracing_on" 2>/dev/null || true

if [ "$auto_cycle" = "1" ]; then
  pre=10
  post=$((duration - 18))
  [ "$post" -lt 5 ] && post=5
  sleep "$pre"
  {
    echo "screen_off_at=$(date +%s.%N 2>/dev/null)"
    input keyevent 26
    sleep 3
    echo "screen_on_at=$(date +%s.%N 2>/dev/null)"
    input keyevent 26
    sleep 5
    echo "wake_at=$(date +%s.%N 2>/dev/null)"
    input keyevent 224
  } >"$traceout/screen-cycle.txt" 2>&1
  sleep "$post"
else
  sleep "$duration"
fi

echo 0 >"$tracefs/tracing_on" 2>/dev/null || true
copy_capped "$tracefs/trace" "$traceout/trace.txt" 67108864
cat "$tracefs/trace_stat" >"$traceout/trace-stat.txt" 2>/dev/null || true
cat "$tracefs/per_cpu/cpu0/stats" >"$traceout/cpu0-stats.txt" 2>/dev/null || true
for d in "$tracefs"/per_cpu/cpu*/stats; do
  [ -r "$d" ] || continue
  n=$(sanitize_name "$d")
  cat "$d" >"$traceout/$n.txt" 2>/dev/null || true
done

exit 0
