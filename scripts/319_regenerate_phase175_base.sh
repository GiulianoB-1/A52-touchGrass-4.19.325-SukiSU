#!/usr/bin/env bash
set -Eeuo pipefail

# Preserve the reviewed 26f37f3d replay repair and fix only the Python string
# delimiter collision in its nested Workflow99 insertion block. Use ordinary
# escaped strings here so the repair wrapper itself cannot collide with the
# triple-quoted strings intentionally embedded in the target script.
BASE_REF=26f37f3d80e015331e29f7e1803119cbeac50217
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

: "${GITHUB_REPOSITORY:?}"
curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/${BASE_REF}/scripts/319_regenerate_phase175_base.sh" \
  -o "$TMP"
test -s "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old_start = "block = r'''# Historical Workflow123 consumed Workflow99 artifact 8590238316. Its exact\n"
new_start = 'block = r"""# Historical Workflow123 consumed Workflow99 artifact 8590238316. Its exact\n'
old_end = "bash \"$TMP\" \"$@\"\n'''\n\ntext = text.replace(anchor, block, 1)\n"
new_end = "bash \"$TMP\" \"$@\"\n\"\"\"\n\ntext = text.replace(anchor, block, 1)\n"

if text.count(old_start) != 1:
    raise SystemExit(f"Workflow99 outer-string start delimiter expected 1, found {text.count(old_start)}")
if text.count(old_end) != 1:
    raise SystemExit(f"Workflow99 outer-string end delimiter expected 1, found {text.count(old_end)}")
text = text.replace(old_start, new_start, 1)
text = text.replace(old_end, new_end, 1)
if old_start in text or old_end in text:
    raise SystemExit("Workflow99 nested-string quoting repair left stale delimiter")
if text.count('block = r"""# Historical Workflow123 consumed Workflow99 artifact 8590238316.') != 1:
    raise SystemExit("Workflow99 outer-string start repair verification failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: nested Workflow99 replay quoting PASS")
PY

bash "$TMP" "$@"
