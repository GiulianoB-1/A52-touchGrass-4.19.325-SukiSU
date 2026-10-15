#!/system/bin/sh
MODDIR=${MODDIR:-${0%/*}/..}
export MODDIR
OUTROOT=/data/adb/tgprobe
export OUTROOT
. "$MODDIR/bin/common.sh"

mode=${1:-manual}
duration=${2:-90}
auto_cycle=${3:-0}

case "$duration" in
  ''|*[!0-9]*) duration=90 ;;
esac
[ "$duration" -lt 20 ] && duration=20
[ "$duration" -gt 240 ] && duration=240

mkdir -p "$OUTROOT"
chmod 0700 "$OUTROOT" 2>/dev/null

if ! mkdir "$OUTROOT/.capture-lock" 2>/dev/null; then
  echo "Another parity capture is already active."
  exit 2
fi
trap 'rmdir "$OUTROOT/.capture-lock" 2>/dev/null' EXIT HUP INT TERM

stamp=$(timestamp)
session="$OUTROOT/$stamp-$mode"
mkdir -p "$session/before" "$session/after" "$session/trace" "$session/state"

{
  echo "profile=touchgrass-parity-probe-v1.1"
  echo "mode=$mode"
  echo "duration_seconds=$duration"
  echo "automatic_screen_cycle=$auto_cycle"
  echo "started=$stamp"
  echo "kernel=$(uname -a 2>/dev/null)"
  echo "boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
  echo "ksu=${KSU:-unknown}"
  echo "privacy=archive-may-contain-properties-logs-package-names-and-device-identifiers"
} >"$session/SESSION.txt"

"$MODDIR/bin/snapshot.sh" "$session" before \
  >"$session/state/before-snapshot.log" 2>&1

"$MODDIR/bin/trace-session.sh" "$session" "$duration" "$auto_cycle" \
  >"$session/state/trace-session.log" 2>&1
trace_rc=$?

"$MODDIR/bin/snapshot.sh" "$session" after \
  >"$session/state/after-snapshot.log" 2>&1

{
  echo "trace_return_code=$trace_rc"
  echo "finished=$(timestamp)"
  echo "session_bytes=$(du -sk "$session" 2>/dev/null | awk '{print $1 * 1024}')"
} >>"$session/SESSION.txt"

"$MODDIR/bin/package-result.sh" "$session"
exit "$trace_rc"
