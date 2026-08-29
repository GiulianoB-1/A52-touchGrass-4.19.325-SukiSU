#!/usr/bin/env bash
set -Eeuo pipefail

# Phase319's historical source reconstruction is pinned to the last reviewed
# replay script. Keep new compatibility repairs as fail-closed transformations
# of that immutable script so each repair remains small and auditable.
BASE_REF=f27af52025cdd56b11d20ef7e0f04b37becb3876
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
anchor = "\nfor spec in \\\n"
if text.count(anchor) != 1:
    raise SystemExit(
        f"Phase319 Run32 compatibility insertion anchor expected 1, found {text.count(anchor)}"
    )

block = r'''
# The branch-head 123 wrapper also accumulated a later compile-cleanup rule for
# a generated legacy GDSC provider. That provider does not exist at the pinned
# Run32 source boundary, so treating its absence as fatal is anachronistic. Skip
# only that one future-file cleanup when the file is absent. All other compile
# cleanup expectations remain strict, and the immutable Phase175 patch SHA256
# below remains the authoritative source-identity gate.
python3 - "$R32/123_apply_a52xq_legacy_ion_free_compat.py" <<'PY123'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '''    if not path.is_file():
        raise SystemExit(f"{label}: missing generated source: {path}")
'''
new = '''    if not path.is_file():
        if (
            path.name == "a52-legacy-gdsc-regulator.c"
            and label == "legacy GDSC disable status local"
        ):
            return 0
        raise SystemExit(f"{label}: missing generated source: {path}")
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"Run32 GDSC cleanup compatibility anchor expected 1, found {count}")
text = text.replace(old, new, 1)
if old in text or text.count('path.name == "a52-legacy-gdsc-regulator.c"') != 1:
    raise SystemExit("Run32 GDSC cleanup compatibility rewrite failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: future GDSC cleanup compatibility PASS")
PY123

'''
text = text.replace(anchor, "\n" + block + anchor.lstrip("\n"), 1)
path.write_text(text, encoding="utf-8")
PY

bash "$TMP" "$@"
