#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/23_build_ack_6_1_logdump_probe_v3.sh"

[ -f "$TARGET" ] || {
  echo "Missing logdump probe v3 wrapper: $TARGET" >&2
  exit 1
}

# The original v3 wrapper retained the old marker block terminator while its
# replacement block already supplied its own 'done'. That generated two
# consecutive 'done' tokens and made bash -n fail before the ACK checkout/build
# could begin. Correct only that exact anchor, then run the audited wrapper.
python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

old = "marker_end = '\\ndone\\n\\ninfo \"Building Qualcomm DTBs as a platform-source sanity check\"'"
new = "marker_end = '\\n\\ninfo \"Building Qualcomm DTBs as a platform-source sanity check\"'"

count = text.count(old)
if count != 1:
    raise SystemExit(f"marker-end repair anchor: expected one match, found {count}")

text = text.replace(old, new, 1)
path.write_text(text)
PY

bash -n "$TARGET"
exec "$TARGET"
