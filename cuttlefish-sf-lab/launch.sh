#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
GPU_MODE="${CF_GPU_MODE:-gfxstream}"

test -x "$INSTANCE/bin/launch_cvd"
test -e /dev/kvm

if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
  echo "FAIL: current user cannot access /dev/kvm."
  echo "Reboot/log in again after install_host.sh, then rerun check_host.sh."
  exit 1
fi

cd "$INSTANCE"
export HOME="$INSTANCE"

echo "Launching Android 16 Cuttlefish with gpu_mode=$GPU_MODE"
./bin/launch_cvd   --daemon   --start_webrtc=true   --gpu_mode="$GPU_MODE"

./bin/adb wait-for-device

echo "Waiting for Android boot_completed..."
for _ in $(seq 1 180); do
  if [[ "$(./bin/adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
    break
  fi
  sleep 1
done

if [[ "$(./bin/adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]]; then
  echo "FAIL: Android did not report sys.boot_completed=1."
  exit 1
fi

./bin/adb root >/dev/null 2>&1 || true
./bin/adb wait-for-device

echo
echo "Cuttlefish booted."
echo "Android: $(./bin/adb shell getprop ro.build.version.release | tr -d '\r')"
echo "SDK:     $(./bin/adb shell getprop ro.build.version.sdk | tr -d '\r')"
echo "Build:   $(./bin/adb shell getprop ro.build.fingerprint | tr -d '\r')"
echo "SF PID:  $(./bin/adb shell pidof surfaceflinger | tr -d '\r')"
echo "Web UI:  https://localhost:8443"
echo
echo "Next: $(cd "$(dirname "$0")" && pwd)/collect_sf_baseline.sh"
