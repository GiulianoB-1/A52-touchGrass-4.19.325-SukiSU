#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
ADB="$INSTANCE/bin/adb"
OUT_ROOT="$LAB_ROOT/captures"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$OUT_ROOT/sfq1_nobootanim_$STAMP"
TRACE_REMOTE="/data/misc/perfetto-traces/sfq1_nobootanim_$STAMP.perfetto-trace"
EMPTY_HOLD_SECS="${SFQ_EMPTY_HOLD_SECS:-8}"

test -x "$ADB"
mkdir -p "$OUT"

echo "Waiting for Cuttlefish ADB..."
timeout 60s "$ADB" wait-for-device
"$ADB" root >/dev/null 2>&1 || true
timeout 60s "$ADB" wait-for-device

if [[ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]]; then
  echo "FAIL: Android is not fully booted."
  exit 1
fi

echo "Preparing no-bootanimation / no-UI control..."
"$ADB" shell setprop debug.sf.nobootanimation 1
"$ADB" shell getprop debug.sf.nobootanimation > "$OUT/debug.sf.nobootanimation.txt"

# Remove Java UI producers so an SF restart does not immediately inherit
# SystemUI/Launcher clients. This is intentionally a control experiment.
"$ADB" shell stop zygote || true
"$ADB" shell stop zygote_secondary || true

for _ in $(seq 1 100); do
  if ! "$ADB" shell pidof system_server >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

"$ADB" shell ps -A > "$OUT/ps-before-sf-restart.txt"
"$ADB" logcat -b all -c

cat > "$OUT/perfetto.pbtxt" <<'EOF'
duration_ms: 30000
buffers: {
  size_kb: 65536
  fill_policy: RING_BUFFER
}
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_wakeup"
      ftrace_events: "sched/sched_waking"
      ftrace_events: "binder/binder_transaction"
      ftrace_events: "binder/binder_transaction_received"
      atrace_categories: "gfx"
      atrace_categories: "view"
      atrace_categories: "wm"
      atrace_categories: "am"
      atrace_categories: "binder_driver"
      atrace_categories: "hal"
      atrace_categories: "input"
    }
  }
}
data_sources: {
  config {
    name: "android.surfaceflinger.layers"
    surfaceflinger_layers_config {
      mode: MODE_ACTIVE
      trace_flags: TRACE_FLAG_COMPOSITION
      trace_flags: TRACE_FLAG_HWC
      trace_flags: TRACE_FLAG_BUFFERS
      trace_flags: TRACE_FLAG_VIRTUAL_DISPLAYS
    }
  }
}
data_sources: {
  config {
    name: "android.surfaceflinger.transactions"
    surfaceflinger_transactions_config {
      mode: MODE_ACTIVE
    }
  }
}
EOF

cat "$OUT/perfetto.pbtxt" |   "$ADB" shell perfetto -c - --txt -o "$TRACE_REMOTE"   > "$OUT/perfetto-stdout.txt" 2> "$OUT/perfetto-stderr.txt" &
PERFETTO_HOST_PID=$!

(
  printf 'host_epoch_ms\tsf_state\tsf_pid\tbootanim_state\tbootanim_pid\tzygote_state\tzygote_pid\tsystem_server_pid\n'
  for _ in $(seq 1 300); do
    host_ms="$(date +%s%3N)"
    sample="$("$ADB" shell 'printf "%s\t%s\t%s\t%s\t%s\t%s\t%s" \
      "$(getprop init.svc.surfaceflinger)" \
      "$(pidof surfaceflinger)" \
      "$(getprop init.svc.bootanim)" \
      "$(pidof bootanimation)" \
      "$(getprop init.svc.zygote)" \
      "$(pidof zygote64 zygote 2>/dev/null)" \
      "$(pidof system_server)"' 2>/dev/null | tr -d '\r' || true)"
    printf '%s\t%s\n' "$host_ms" "$sample"
    sleep 0.1
  done
) > "$OUT/timeline.tsv" &
TIMELINE_PID=$!

sleep 2

old_sf="$("$ADB" shell pidof surfaceflinger | tr -d '\r' || true)"
echo "$old_sf" > "$OUT/sf-pid-before.txt"

restart_epoch_ms="$(date +%s%3N)"
echo "$restart_epoch_ms" > "$OUT/sf-restart-epoch-ms.txt"

echo "Restarting SurfaceFlinger with bootanimation disabled and zygote stopped..."
"$ADB" shell stop surfaceflinger
sleep 1
"$ADB" shell start surfaceflinger

new_sf=""
for _ in $(seq 1 120); do
  new_sf="$("$ADB" shell pidof surfaceflinger | tr -d '\r' || true)"
  if [[ -n "$new_sf" && "$new_sf" != "$old_sf" ]]; then
    break
  fi
  sleep 0.1
done
echo "$new_sf" > "$OUT/sf-pid-after.txt"

echo "Holding SF without Java UI producers for ${EMPTY_HOLD_SECS}s..."
sleep "$EMPTY_HOLD_SECS"

empty_epoch_ms="$(date +%s%3N)"
echo "$empty_epoch_ms" > "$OUT/empty-window-end-epoch-ms.txt"
"$ADB" shell dumpsys SurfaceFlinger --list > "$OUT/sf-layers-empty.txt" 2>/dev/null || true
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-empty.txt" 2>/dev/null || true

zygote_start_epoch_ms="$(date +%s%3N)"
echo "$zygote_start_epoch_ms" > "$OUT/zygote-start-epoch-ms.txt"
echo "Starting zygote / UI producers..."
"$ADB" shell start zygote
"$ADB" shell start zygote_secondary 2>/dev/null || true

for _ in $(seq 1 180); do
  if "$ADB" shell pidof system_server >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

sleep 8

wait "$TIMELINE_PID" || true
wait "$PERFETTO_HOST_PID" || true

"$ADB" pull "$TRACE_REMOTE" "$OUT/sfq1-nobootanim.perfetto-trace" >/dev/null || true
"$ADB" shell rm -f "$TRACE_REMOTE" || true

"$ADB" shell dumpsys SurfaceFlinger --list > "$OUT/sf-layers-after-ui.txt" 2>/dev/null || true
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-after-ui.txt" 2>/dev/null || true
"$ADB" logcat -b all -d -v epoch > "$OUT/logcat.txt"

grep -Ei 'SurfaceFlinger|BootAnimation|bootanim|composer|hwc|validate|present|display|EGL|kgsl|gralloc'   "$OUT/logcat.txt" > "$OUT/relevant-logcat.txt" || true

{
  echo "SF-Q1 no-bootanimation / no-UI control"
  echo "timestamp=$STAMP"
  echo "debug.sf.nobootanimation=$("$ADB" shell getprop debug.sf.nobootanimation | tr -d '\r')"
  echo "old_surfaceflinger_pid=$old_sf"
  echo "new_surfaceflinger_pid=$new_sf"
  echo "sf_restart_epoch_ms=$restart_epoch_ms"
  echo "empty_window_end_epoch_ms=$empty_epoch_ms"
  echo "zygote_start_epoch_ms=$zygote_start_epoch_ms"
  echo "empty_hold_seconds=$EMPTY_HOLD_SECS"
  echo "trace=$OUT/sfq1-nobootanim.perfetto-trace"
} > "$OUT/README.txt"

echo
echo "Control capture complete: $OUT"
echo "Key question: are validate/present/composition slices absent during the empty window, then appear after zygote/UI returns?"
echo
echo "To package:"
echo "  cd $OUT_ROOT && zip -r $(basename "$OUT").zip $(basename "$OUT")"
