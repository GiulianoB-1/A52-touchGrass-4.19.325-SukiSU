#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
if compgen -G "/dev/dri/renderD*" >/dev/null; then
  DEFAULT_GPU_MODE="gfxstream"
else
  DEFAULT_GPU_MODE="guest_swiftshader"
fi
GPU_MODE="${CF_GPU_MODE:-$DEFAULT_GPU_MODE}"
WEBRTC="${CF_WEBRTC:-false}"
WIFI="${CF_WIFI:-false}"
BOOT_TIMEOUT="${CF_BOOT_TIMEOUT:-90}"

test -x "$INSTANCE/bin/launch_cvd"
test -e /dev/kvm

if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
  echo "FAIL: current user cannot access /dev/kvm."
  exit 1
fi

cd "$INSTANCE"
export HOME="$INSTANCE"

if [[ -e "$INSTANCE/etc/debian_substitution_marker" ]]; then
  echo "FAIL: host substitution marker is still active."
  echo "Rerun prepare_instance.sh with the matching CI host package and image."
  exit 1
fi

echo "Host package provenance:"
for tool in launch_cvd run_cvd crosvm adb; do
  if [[ -e "$INSTANCE/bin/$tool" ]]; then
    printf '  %-12s -> %s\n' "$tool" "$(readlink -f "$INSTANCE/bin/$tool")"
  fi
done
echo

echo "Launching minimal SF-Q1 Cuttlefish:"
echo "  gpu_mode=$GPU_MODE"
echo "  webrtc=$WEBRTC"
echo "  wifi=$WIFI"
echo "  boot_timeout=${BOOT_TIMEOUT}s"
echo

dump_boot_failure() {
  I="$INSTANCE/cuttlefish/instances/cvd-1"
  echo
  echo "===== SF-Q1 BOOT FAILURE DIAGNOSTICS ====="
  echo "Processes:"
  ps -ef | grep -E 'run_cvd|crosvm|qemu-system' | grep -v grep || true
  echo
  echo "Kernel/logcat sizes:"
  stat -Lc '  %n: %s bytes' "$I/kernel.log" "$I/logs/logcat" 2>/dev/null || true
  echo
  echo "Kernel log tail:"
  tail -n 180 "$I/kernel.log" 2>/dev/null || true
  echo
  echo "Crosvm process state:"
  for pid in $(pgrep -f "$INSTANCE/bin/x86_64-linux-gnu/crosvm" 2>/dev/null || true); do
    ps -o pid,ppid,stat,wchan:32,etime,cmd -p "$pid" || true
    printf '  wchan: '; cat "/proc/$pid/wchan" 2>/dev/null || true; echo
  done
  echo
  echo "Launcher: first relevant failures:"
  grep -nEi 'Subprocess .* exited|crosvm has exited|Detected unexpected exit|boot.*fail|timed out|timeout|failed|fatal|panic|permission denied|No such device' \
    "$I/logs/launcher.log" 2>/dev/null | head -n 160 || true
  echo
  echo "Launcher tail:"
  tail -n 160 "$I/logs/launcher.log" 2>/dev/null || true
  echo "===== END DIAGNOSTICS ====="
}

set +e
timeout --foreground "${BOOT_TIMEOUT}s" ./bin/launch_cvd \
  --report_anonymous_usage_stats=n \
  --daemon \
  --start_webrtc="$WEBRTC" \
  --enable_wifi="$WIFI" \
  --gpu_mode="$GPU_MODE"
launch_rc=$?
set -e

if [[ "$launch_rc" -ne 0 ]]; then
  if [[ "$launch_rc" -eq 124 ]]; then
    echo "FAIL: launch_cvd exceeded ${BOOT_TIMEOUT}s."
  else
    echo "FAIL: launch_cvd exited with rc=$launch_rc."
  fi
  dump_boot_failure
  exit "$launch_rc"
fi

echo
echo "Waiting up to 90s for Android ADB..."
if ! timeout 90s ./bin/adb wait-for-device; then
  echo "FAIL: Android ADB never appeared."
  dump_boot_failure
  exit 1
fi

echo "Waiting for Android boot_completed..."
boot_ok=0
for _ in $(seq 1 180); do
  if [[ "$(./bin/adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
    boot_ok=1
    break
  fi
  sleep 1
done

if [[ "$boot_ok" -ne 1 ]]; then
  echo "FAIL: Android did not report sys.boot_completed=1."
  exit 1
fi

./bin/adb root >/dev/null 2>&1 || true
timeout 45s ./bin/adb wait-for-device || true

echo
echo "Cuttlefish booted."
echo "Android: $(./bin/adb shell getprop ro.build.version.release | tr -d '\r')"
echo "SDK:     $(./bin/adb shell getprop ro.build.version.sdk | tr -d '\r')"
echo "Build:   $(./bin/adb shell getprop ro.build.fingerprint | tr -d '\r')"
echo "SF PID:  $(./bin/adb shell pidof surfaceflinger | tr -d '\r')"
echo
echo "Next: $(cd "$(dirname "$0")" && pwd)/collect_sf_baseline.sh"
