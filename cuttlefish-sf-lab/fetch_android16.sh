#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
BUILD="${CF_BUILD:-android16-qpr2-release/aosp_cf_x86_64_only_phone-userdebug}"

mkdir -p "$INSTANCE"

if ! command -v cvd >/dev/null 2>&1; then
  echo "FAIL: 'cvd' is not installed."
  echo "Run install_host.sh, reboot, then retry."
  exit 1
fi

echo "Fetching Cuttlefish artifacts:"
echo "  build:     $BUILD"
echo "  directory: $INSTANCE"
echo

set +e
cvd fetch \
  --target_directory="$INSTANCE" \
  --default_build="$BUILD"
rc=$?
set -e

if [[ $rc -ne 0 && "$BUILD" == "android16-qpr2-release/aosp_cf_x86_64_only_phone-userdebug" ]]; then
  echo "QPR2 current target fetch failed; retrying legacy target name..."
  BUILD="android16-qpr2-release/aosp_cf_x86_64_phone-userdebug"
  set +e
  cvd fetch \
    --target_directory="$INSTANCE" \
    --default_build="$BUILD"
  rc=$?
  set -e
fi

if [[ $rc -ne 0 ]]; then
  echo
  echo "Automatic public Build API fetch failed (rc=$rc)."
  echo
  latest_log="$(ls -1t /var/tmp/cvd/"$(id -u)"/logs/cvd_*.log 2>/dev/null | head -1 || true)"
  if [[ -n "$latest_log" && -s "$latest_log" ]]; then
    echo "===== latest cvd fetch failure ====="
    tail -n 120 "$latest_log" || true
    echo "===== end cvd failure ====="
    echo
  fi

  cat <<EOF

Use the official Android CI fallback:
  1. Open https://ci.android.com/
  2. Branch: android16-qpr2-release
  3. Target: aosp_cf_x86_64_only_phone
  4. Variant: userdebug
  5. Download BOTH artifacts from the SAME build:
       - aosp_cf_x86_64_phone-img-<BUILD>.zip (artifact name may omit "_only")
       - cvd-host_package.tar.gz
  6. Run:
       ./prepare_instance.sh /path/to/cvd-host_package.tar.gz \
         /path/to/aosp_cf_x86_64_phone-img-<BUILD>.zip
EOF
  exit "$rc"
fi

test -x "$INSTANCE/bin/launch_cvd" || {
  echo "FAIL: fetch completed but bin/launch_cvd is missing."
  exit 1
}

echo
echo "Android 16 Cuttlefish artifacts prepared at $INSTANCE"
echo "Next: ./launch.sh"
