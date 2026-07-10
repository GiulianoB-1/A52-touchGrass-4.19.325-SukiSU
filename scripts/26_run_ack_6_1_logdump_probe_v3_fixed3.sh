#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/23_build_ack_6_1_logdump_probe_v3.sh"

[ -f "$TARGET" ] || {
  echo "Missing logdump probe v3 wrapper: $TARGET" >&2
  exit 1
}

# Apply both independent repairs to a fresh checkout of the original v3
# wrapper before executing it:
#   1. Keep specific artifact-name replacement anchors intact.
#   2. Do not retain the old marker-loop 'done' when the replacement block
#      already supplies its own 'done'.
python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

repairs = [
    (
        "    'a52xq-hybrid-probe-v2': 'a52xq-hybrid-logdump-v3',",
        "    '--set-str DEFAULT_HOSTNAME a52xq-hybrid-probe-v2': "
        "'--set-str DEFAULT_HOSTNAME a52xq-hybrid-logdump-v3',",
        "generic replacement",
    ),
    (
        "marker_end = '\\ndone\\n\\ninfo \"Building Qualcomm DTBs as a platform-source sanity check\"'",
        "marker_end = '\\n\\ninfo \"Building Qualcomm DTBs as a platform-source sanity check\"'",
        "marker-loop terminator",
    ),
]

for old, new, label in repairs:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} repair anchor: expected one match, found {count}")
    text = text.replace(old, new, 1)

path.write_text(text)
PY

bash -n "$TARGET"
exec "$TARGET"
