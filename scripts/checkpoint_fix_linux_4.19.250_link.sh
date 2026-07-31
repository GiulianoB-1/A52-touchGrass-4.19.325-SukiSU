#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
CLOSURE_TEMPLATE="$SCRIPT_DIR/06zzz_fix_linux_4.19.325_final_link_closure.sh"
BKOPS_TEMPLATE="$SCRIPT_DIR/06zzzz_fix_linux_4.19.325_mmc_bkops_state.sh"
CLOSURE_TEMP="$SCRIPT_DIR/.checkpoint-link-4.19.250-$$.sh"
BKOPS_TEMP="$SCRIPT_DIR/.checkpoint-bkops-4.19.250-$$.sh"

cleanup() {
  rm -f "$CLOSURE_TEMP" "$BKOPS_TEMP"
}
trap cleanup EXIT

for template in "$CLOSURE_TEMPLATE" "$BKOPS_TEMPLATE"; do
  test -f "$template" || {
    echo "Missing reviewed linker template: $template" >&2
    exit 1
  }
done

# Reuse the reviewed 4.19.325 closure logic against the 4.19.250 checkpoint.
# These scripts restore the Samsung timer, procfs, RTC and MMC providers, then
# restore the vendor BKOPS state bit consumed by MMC clock scaling.
sed \
  -e 's/^TARGET_VERSION=4\.19\.325$/TARGET_VERSION=4.19.250/' \
  -e 's/result=linux-4\.19\.325-final-link-closure-repaired/result=linux-4.19.250-final-link-closure-repaired/' \
  "$CLOSURE_TEMPLATE" > "$CLOSURE_TEMP"
chmod +x "$CLOSURE_TEMP"
"$CLOSURE_TEMP"

sed \
  -e 's/^TARGET_VERSION=4\.19\.325$/TARGET_VERSION=4.19.250/' \
  -e 's/result=linux-4\.19\.325-mmc-bkops-state-repaired/result=linux-4.19.250-mmc-bkops-state-repaired/' \
  "$BKOPS_TEMPLATE" > "$BKOPS_TEMP"
chmod +x "$BKOPS_TEMP"
"$BKOPS_TEMP"
