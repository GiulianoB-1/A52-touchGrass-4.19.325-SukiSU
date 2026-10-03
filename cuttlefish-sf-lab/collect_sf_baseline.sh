#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
ADB="$INSTANCE/bin/adb"
OUT_ROOT="$LAB_ROOT/captures"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$OUT_ROOT/sfq1_$STAMP"
TRACE_REMOTE="/data/misc/perfetto-traces/sfq1_$STAMP.perfetto-trace"

test -x "$ADB"
mkdir -p "$OUT"

"$ADB" wait-for-device
"$ADB" root >/dev/null 2>&1 || true
"$ADB" wait-for-device

echo "Waiting for Android boot completion..."
boot_ok=0
for _ in $(seq 1 180); do
  if [[ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
    boot_ok=1
    break
  fi
  sleep 1
done
if [[ "$boot_ok" -ne 1 ]]; then
  echo "FAIL: Android did not report sys.boot_completed=1 within 180 seconds."
  exit 1
fi
echo "Android boot complete."

echo "Collecting pre-restart state..."
"$ADB" shell getprop > "$OUT/getprop-before.txt"
"$ADB" shell ps -A -T > "$OUT/ps-before.txt"
"$ADB" shell service list > "$OUT/services-before.txt"
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-before.txt"
"$ADB" shell dumpsys SurfaceFlinger --proto > "$OUT/surfaceflinger-before.pb" 2>/dev/null || true
"$ADB" logcat -b all -d -v threadtime > "$OUT/logcat-before.txt"

cat > "$OUT/sfq1-perfetto.pbtxt" <<'EOF'
duration_ms: 20000
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

# Keep the full boot log above, then isolate the restart window.
"$ADB" logcat -b all -c

echo "Starting 20-second Perfetto capture..."
cat "$OUT/sfq1-perfetto.pbtxt" | \
  "$ADB" shell perfetto -c - --txt -o "$TRACE_REMOTE" \
  > "$OUT/perfetto-stdout.txt" 2> "$OUT/perfetto-stderr.txt" &
PERFETTO_HOST_PID=$!

# 100 ms host-side sampling. One adb round trip per sample is intentional:
# this is a userspace baseline, not a low-perturbation kernel measurement.
(
  printf 'host_epoch_ms\tsf_state\tsf_pid\tbootanim_state\tbootanim_pid\tbootanim_exit\n'
  for _ in $(seq 1 200); do
    host_ms="$(date +%s%3N)"
    sample="$("$ADB" shell 'printf "%s\t%s\t%s\t%s\t%s" \
      "$(getprop init.svc.surfaceflinger)" \
      "$(pidof surfaceflinger)" \
      "$(getprop init.svc.bootanim)" \
      "$(pidof bootanimation)" \
      "$(getprop service.bootanim.exit)"' 2>/dev/null | tr -d '\r' || true)"
    printf '%s\t%s\n' "$host_ms" "$sample"
    sleep 0.1
  done
) > "$OUT/sf-restart-timeline.tsv" &
TIMELINE_PID=$!

sleep 3

old_pid="$("$ADB" shell pidof surfaceflinger | tr -d '\r' || true)"
echo "$old_pid" > "$OUT/surfaceflinger-pid-before.txt"

echo "Restarting only SurfaceFlinger inside the trace window..."
restart_epoch_ms="$(date +%s%3N)"
echo "$restart_epoch_ms" > "$OUT/restart-request-epoch-ms.txt"
"$ADB" shell stop surfaceflinger
sleep 1
"$ADB" shell start surfaceflinger

new_pid=""
for _ in $(seq 1 120); do
  new_pid="$("$ADB" shell pidof surfaceflinger | tr -d '\r' || true)"
  if [[ -n "$new_pid" && "$new_pid" != "$old_pid" ]]; then
    break
  fi
  sleep 0.1
done

echo "$new_pid" > "$OUT/surfaceflinger-pid-after.txt"

wait "$TIMELINE_PID" || true
wait "$PERFETTO_HOST_PID"

"$ADB" pull "$TRACE_REMOTE" "$OUT/sfq1.perfetto-trace" >/dev/null
"$ADB" shell rm -f "$TRACE_REMOTE" || true

"$ADB" shell getprop > "$OUT/getprop-after.txt"
"$ADB" shell ps -A -T > "$OUT/ps-after.txt"
"$ADB" shell service list > "$OUT/services-after.txt"
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-after.txt"
"$ADB" shell dumpsys SurfaceFlinger --proto > "$OUT/surfaceflinger-after.pb" 2>/dev/null || true
"$ADB" logcat -b all -d -v epoch > "$OUT/logcat-restart-window.txt"
"$ADB" logcat -b all -d -v threadtime > "$OUT/logcat-after.txt"

{
  echo "SF-Q1 baseline capture"
  echo "timestamp=$STAMP"
  echo "restart_request_epoch_ms=$restart_epoch_ms"
  echo "old_surfaceflinger_pid=$old_pid"
  echo "new_surfaceflinger_pid=$new_pid"
  echo "trace=$OUT/sfq1.perfetto-trace"
  echo "timeline=$OUT/sf-restart-timeline.tsv"
  echo "android=$("$ADB" shell getprop ro.build.version.release | tr -d '\r')"
  echo "sdk=$("$ADB" shell getprop ro.build.version.sdk | tr -d '\r')"
  echo "fingerprint=$("$ADB" shell getprop ro.build.fingerprint | tr -d '\r')"
} > "$OUT/README.txt"

python3 "$(cd "$(dirname "$0")" && pwd)/analyze_sf_baseline.py" "$OUT" || true

echo
echo "Capture complete: $OUT"
echo "Open sfq1.perfetto-trace in https://ui.perfetto.dev"
