#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TEMPLATE="$SCRIPT_DIR/06zzz_fix_linux_4.19.325_final_link_closure.sh"
TEMP_SCRIPT="$SCRIPT_DIR/.checkpoint-link-4.19.250-$$.sh"

cleanup() {
  rm -f "$TEMP_SCRIPT"
}
trap cleanup EXIT

test -f "$TEMPLATE" || {
  echo "Missing reviewed final-link closure template: $TEMPLATE" >&2
  exit 1
}

# Reuse the reviewed 4.19.325 closure logic against the 4.19.250 checkpoint.
# The unresolved provider set is identical, while the template itself remains
# the single maintained implementation for Samsung timer, procfs, RTC and MMC
# compatibility repairs.
sed \
  -e 's/^TARGET_VERSION=4\.19\.325$/TARGET_VERSION=4.19.250/' \
  -e 's/result=linux-4\.19\.325-final-link-closure-repaired/result=linux-4.19.250-final-link-closure-repaired/' \
  "$TEMPLATE" > "$TEMP_SCRIPT"
chmod +x "$TEMP_SCRIPT"

"$TEMP_SCRIPT"
