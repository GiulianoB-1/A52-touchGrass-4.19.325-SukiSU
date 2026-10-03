#!/usr/bin/env bash
set -Eeuo pipefail

LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
BUILD="${CF_BUILD:-android16-qpr2-release/aosp_cf_x86_64_only_phone-aosp_current-userdebug}"

# Public catch-all key embedded by upstream Cuttlefish for unauthenticated
# Android Build API v4 requests. Override only if Google changes it.
AB_API_KEY="${CF_ANDROID_BUILD_API_KEY:-AIzaSyBIelMvbjtNkpa5O96eqbm_IuSUA5WsO14}"
AB_API_BASE="https://androidbuild-pa.googleapis.com/v4"

mkdir -p "$INSTANCE"

if ! command -v cvd >/dev/null 2>&1; then
  echo "FAIL: 'cvd' is not installed."
  echo "Run install_host.sh, reboot, then retry."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: curl and python3 are required."
  exit 1
fi

cvd_fetch() {
  local spec="$1"
  cvd fetch \
    --target_directory="$INSTANCE" \
    --default_build="$spec"
}

resolve_build_id_without_safe_level() {
  local branch="$1"
  local target="$2"
  local json

  # Current cvd releases can fall through to SafeLevel::Unspecified and emit
  # safeLevel=, which Android Build API v4 rejects. Querying the same endpoint
  # without safeLevel avoids that client-side bug.
  if ! json="$(curl -fsS \
      -H "X-goog-api-key: $AB_API_KEY" \
      --get "$AB_API_BASE/builds" \
      --data-urlencode "buildAttemptStatus=complete" \
      --data-urlencode "buildType=submitted" \
      --data-urlencode "pageSize=1" \
      --data-urlencode "successful=true" \
      --data-urlencode "branches=$branch" \
      --data-urlencode "targets=$target")"; then
    return 1
  fi

  python3 -c '
import json, sys
d=json.load(sys.stdin)
b=d.get("builds") or []
if not b:
    raise SystemExit(1)
bid=b[0].get("buildId")
if not bid:
    raise SystemExit(1)
print(bid)
' <<<"$json"
}

echo "Fetching Cuttlefish artifacts:"
echo "  build:     $BUILD"
echo "  directory: $INSTANCE"
echo

set +e
cvd_fetch "$BUILD"
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  echo
  echo "Branch-based cvd fetch failed."
  echo "Trying direct Android Build API resolution without the broken empty safeLevel..."
  echo

  candidates=(
    "android16-qpr2-release/aosp_cf_x86_64_only_phone-aosp_current-userdebug"
    "android16-qpr2-release/aosp_cf_x86_64_only_phone-userdebug"
    "android16-qpr2-release/aosp_cf_x86_64_phone-userdebug"
    "android16-release/aosp_cf_x86_64_only_phone-aosp_current-userdebug"
    "android16-release/aosp_cf_x86_64_only_phone-userdebug"
    "android16-release/aosp_cf_x86_64_phone-userdebug"
  )

  resolved_spec=""
  for candidate in "${candidates[@]}"; do
    branch_name="${candidate%%/*}"
    target_name="${candidate#*/}"
    echo "Resolving: $branch_name / $target_name"

    if build_id="$(resolve_build_id_without_safe_level "$branch_name" "$target_name")"; then
      resolved_spec="$build_id/$target_name"
      echo "Resolved concrete build: $resolved_spec"
      break
    else
      echo "  no public completed build returned"
    fi
  done

  if [[ -n "$resolved_spec" ]]; then
    echo
    echo "Retrying cvd fetch with concrete build ID (bypasses branch/safeLevel lookup)..."
    set +e
    cvd_fetch "$resolved_spec"
    rc=$?
    set -e
  fi
fi

if [[ $rc -ne 0 ]]; then
  echo
  echo "Automatic Android 16 fetch failed (rc=$rc)."
  echo
  latest_log="$(ls -1t /var/tmp/cvd/"$(id -u)"/logs/cvd_*.log 2>/dev/null | head -1 || true)"
  if [[ -n "$latest_log" && -s "$latest_log" ]]; then
    echo "===== latest cvd fetch failure ====="
    tail -n 120 "$latest_log" || true
    echo "===== end cvd failure ====="
    echo
  fi

  cat <<'EOF'

Use the official Android CI fallback:
  1. Open https://ci.android.com/
  2. Branch: aosp-android-latest-release
  3. Find a historical Android 16 QPR2 build for aosp_cf_x86_64_only_phone
  4. Variant: userdebug
  5. Download BOTH artifacts from the SAME build:
       - aosp_cf_x86_64_phone-img-<BUILD>.zip
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
