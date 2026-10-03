#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
ADB="$INSTANCE/bin/adb"
OUT_ROOT="$LAB_ROOT/captures"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$OUT_ROOT/sfq1b_no_bootanim_$STAMP"
TRACE_REMOTE="/data/misc/perfetto-traces/sfq1b_$STAMP.perfetto-trace"

test -x "$ADB"
mkdir -p "$OUT"

echo "Waiting for Cuttlefish ADB..."
if ! timeout 45s "$ADB" wait-for-device; then
  echo "FAIL: no Cuttlefish ADB device appeared within 45 seconds."
  echo
  echo "ADB devices:"
  "$ADB" devices -l || true
  echo
  echo "QEMU/Cuttlefish processes:"
  ps -ef | grep -E 'run_cvd|qemu-system-x86_64' | grep -v grep || true
  echo
  echo "Start Cuttlefish with ./launch.sh, then rerun this control."
  exit 1
fi

"$ADB" root >/dev/null 2>&1 || true
if ! timeout 45s "$ADB" wait-for-device; then
  echo "FAIL: ADB disappeared after adb root."
  "$ADB" devices -l || true
  exit 1
fi

orig_noboot="$("$ADB" shell getprop debug.sf.nobootanimation 2>/dev/null | tr -d '\r')"
printf '%s\n' "$orig_noboot" > "$OUT/original-debug.sf.nobootanimation.txt"

restore() {
  set +e
  "$ADB" shell start zygote >/dev/null 2>&1 || true
  if [[ -n "$orig_noboot" ]]; then
    "$ADB" shell setprop debug.sf.nobootanimation "$orig_noboot" >/dev/null 2>&1 || true
  else
    "$ADB" shell setprop debug.sf.nobootanimation 0 >/dev/null 2>&1 || true
  fi
}
trap restore EXIT

cat > "$OUT/perfetto.pbtxt" <<'EOF'
duration_ms: 45000
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

mark() {
  local name="$1"
  local ms
  ms="$(date +%s%3N)"
  printf '%s\t%s\n' "$ms" "$name" | tee -a "$OUT/milestones.tsv"
  "$ADB" shell log -t SFQ1B "MARK $name host_epoch_ms=$ms" >/dev/null 2>&1 || true
}

printf 'host_epoch_ms\tmilestone\n' > "$OUT/milestones.tsv"

"$ADB" shell getprop > "$OUT/getprop-before.txt"
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-before.txt"
"$ADB" shell ps -A -T > "$OUT/ps-before.txt"

"$ADB" shell setprop debug.sf.nobootanimation 1
"$ADB" shell stop bootanim >/dev/null 2>&1 || true
"$ADB" logcat -b all -c

cat "$OUT/perfetto.pbtxt" |   "$ADB" shell perfetto -c - --txt -o "$TRACE_REMOTE"   > "$OUT/perfetto-stdout.txt" 2> "$OUT/perfetto-stderr.txt" &
PERFETTO_PID=$!

sleep 2
mark "STOP_ZYGOTE"
"$ADB" shell stop zygote
sleep 2

old_sf="$("$ADB" shell pidof surfaceflinger 2>/dev/null | tr -d '\r' || true)"
printf '%s\n' "$old_sf" > "$OUT/surfaceflinger-pid-before.txt"

mark "RESTART_SURFACEFLINGER_NO_CLIENTS"
"$ADB" shell stop surfaceflinger
sleep 1
"$ADB" shell start surfaceflinger

new_sf=""
for _ in $(seq 1 100); do
  new_sf="$("$ADB" shell pidof surfaceflinger 2>/dev/null | tr -d '\r' || true)"
  if [[ -n "$new_sf" && "$new_sf" != "$old_sf" ]]; then
    break
  fi
  sleep 0.1
done
printf '%s\n' "$new_sf" > "$OUT/surfaceflinger-pid-after.txt"
mark "NEW_SURFACEFLINGER_PID_$new_sf"

# Deliberately leave SF with no normal framework/app clients.
sleep 6
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-no-clients.txt" 2>/dev/null || true
mark "START_ZYGOTE"
"$ADB" shell start zygote

# Give system_server/SystemUI/Launcher enough time to create real drawable layers.
# The first SF-Q1B capture showed SystemUI starting just after the old 26 s trace ended.
sleep 25
mark "END_OBSERVATION"

wait "$PERFETTO_PID" || true
"$ADB" pull "$TRACE_REMOTE" "$OUT/sfq1b.perfetto-trace" >/dev/null
"$ADB" shell rm -f "$TRACE_REMOTE" >/dev/null 2>&1 || true

"$ADB" logcat -b all -d -v epoch > "$OUT/logcat.txt"
"$ADB" shell dumpsys SurfaceFlinger > "$OUT/surfaceflinger-after.txt" 2>/dev/null || true
"$ADB" shell ps -A -T > "$OUT/ps-after.txt" 2>/dev/null || true
"$ADB" shell getprop > "$OUT/getprop-after.txt" 2>/dev/null || true

{
  echo "SF-Q1B no-bootanimation/no-client control"
  echo "timestamp=$STAMP"
  echo "old_surfaceflinger_pid=$old_sf"
  echo "new_surfaceflinger_pid=$new_sf"
  echo "debug.sf.nobootanimation=1"
  echo "trace=$OUT/sfq1b.perfetto-trace"
  echo
  echo "Question:"
  echo "Does SurfaceFlinger perform HWC validate/present while zygote/system_server/app clients are absent,"
  echo "and does composition begin promptly after zygote/UI clients return?"
} > "$OUT/README.txt"

echo
echo "SF-Q1B capture complete: $OUT"
echo "Upload that directory (or zip it) for comparison with SF-Q1 and A52."
