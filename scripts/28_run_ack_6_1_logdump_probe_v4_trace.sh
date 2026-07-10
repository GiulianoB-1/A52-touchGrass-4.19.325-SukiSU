#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$SCRIPT_DIR/27_run_ack_6_1_logdump_probe_v4.sh"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$ROOT_DIR/artifacts-hybrid-gki}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs-hybrid-gki}"
TRACE_LOG="$LOG_DIR/v4-wrapper-trace.log"
STATUS_FILE="$ARTIFACTS_DIR/v4-wrapper.status"

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"

[ -f "$TARGET" ] || {
  echo "Missing v4 wrapper: $TARGET" | tee "$TRACE_LOG" >&2
  printf 'wrapper_status=missing\nwrapper_rc=127\n' > "$STATUS_FILE"
  exit 127
}

set +e
{
  echo "=== A52XQ V4 WRAPPER TRACE ==="
  echo "project_commit=${GITHUB_SHA:-local}"
  echo "started_at=$(date -Iseconds)"
  echo
  PS4='+ ${BASH_SOURCE}:${LINENO}: '
  export PS4
  bash -x "$TARGET"
} 2>&1 | tee "$TRACE_LOG"
rc=${PIPESTATUS[0]}
set -e

{
  printf 'wrapper_status=%s\n' "$([ "$rc" -eq 0 ] && echo success || echo failure)"
  printf 'wrapper_rc=%s\n' "$rc"
  printf 'project_commit=%s\n' "${GITHUB_SHA:-local}"
  printf 'finished_at=%s\n' "$(date -Iseconds)"
} > "$STATUS_FILE"

# Preserve the exact generated wrapper/build scripts for post-failure audit.
for file in \
  "$SCRIPT_DIR/23_build_ack_6_1_logdump_probe_v3.sh" \
  "$SCRIPT_DIR/27_run_ack_6_1_logdump_probe_v4.sh" \
  "$SCRIPT_DIR/21_build_ack_6_1_probe.sh"; do
  [ -f "$file" ] && cp "$file" "$ARTIFACTS_DIR/$(basename "$file").post-run" || true
done

exit "$rc"
