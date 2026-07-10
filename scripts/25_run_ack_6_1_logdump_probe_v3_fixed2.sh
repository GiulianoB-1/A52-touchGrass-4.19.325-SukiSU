#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/23_build_ack_6_1_logdump_probe_v3.sh"

[ -f "$TARGET" ] || {
  echo "Missing logdump probe v3 wrapper: $TARGET" >&2
  exit 1
}

# The v3 wrapper's replacement dictionary handled the generic substring
# 'a52xq-hybrid-probe-v2' before the more specific artifact filenames that
# contain it. That consumed the later anchors and caused an immediate
# 'missing replacement anchor' failure. Restrict the generic replacement to
# the DEFAULT_HOSTNAME command so the filename replacements remain available.
python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

old = "    'a52xq-hybrid-probe-v2': 'a52xq-hybrid-logdump-v3',"
new = (
    "    '--set-str DEFAULT_HOSTNAME a52xq-hybrid-probe-v2': "
    "'--set-str DEFAULT_HOSTNAME a52xq-hybrid-logdump-v3',"
)

count = text.count(old)
if count != 1:
    raise SystemExit(
        f"generic replacement repair anchor: expected one match, found {count}"
    )

text = text.replace(old, new, 1)
path.write_text(text)
PY

bash -n "$TARGET"
exec "$TARGET"
