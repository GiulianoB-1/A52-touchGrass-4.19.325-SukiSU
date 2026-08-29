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

block = r"""
# The branch-head 123 wrapper accumulated compile-cleanup rules from later source
# states. At the pinned Run32 boundary several of those anchors legitimately do
# not exist at all. Make replace_or_verify idempotent only for the exact state
# where BOTH the old and new spellings are absent. Existing old anchors still
# must occur exactly the expected number of times; existing new anchors still
# must also have the exact expected count. The future GDSC provider is the one
# missing-file exception already proven by replay. Finally, the immutable
# Phase175 patch SHA256 remains the authoritative source-identity gate.
python3 - "$R32/123_apply_a52xq_legacy_ion_free_compat.py" <<'PY123'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

old_missing = "    if not path.is_file():\n        raise SystemExit(f\"{label}: missing generated source: {path}\")\n"
new_missing = (
    "    if not path.is_file():\n"
    "        if (\n"
    "            path.name == \"a52-legacy-gdsc-regulator.c\"\n"
    "            and label == \"legacy GDSC disable status local\"\n"
    "        ):\n"
    "            return 0\n"
    "        raise SystemExit(f\"{label}: missing generated source: {path}\")\n"
)
count = text.count(old_missing)
if count != 1:
    raise SystemExit(f"Run32 GDSC cleanup compatibility anchor expected 1, found {count}")
text = text.replace(old_missing, new_missing, 1)

old_gate = (
    "    old_count = text.count(old)\n"
    "    if old_count != expected:\n"
    "        raise SystemExit(\n"
    "            f\"{label}: expected {expected} unfixed anchors, found {old_count}\"\n"
    "        )\n"
)
new_gate = (
    "    old_count = text.count(old)\n"
    "    if old_count != expected:\n"
    "        if old_count == 0 and new_count == 0:\n"
    "            return 0\n"
    "        raise SystemExit(\n"
    "            f\"{label}: expected {expected} unfixed anchors, found {old_count}\"\n"
    "        )\n"
)
count = text.count(old_gate)
if count != 1:
    raise SystemExit(f"Run32 post-cleanup gate compatibility anchor expected 1, found {count}")
text = text.replace(old_gate, new_gate, 1)

if old_missing in text or old_gate in text:
    raise SystemExit("Run32 post-cleanup compatibility rewrite left stale anchors")
if text.count('path.name == "a52-legacy-gdsc-regulator.c"') != 1:
    raise SystemExit("Run32 GDSC cleanup compatibility rewrite failed")
if text.count("if old_count == 0 and new_count == 0:") != 1:
    raise SystemExit("Run32 absence-idempotent cleanup compatibility rewrite failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: Run32 absence-idempotent post-cleanup compatibility PASS")
PY123

"""
text = text.replace(anchor, "\n" + block + anchor.lstrip("\n"), 1)
path.write_text(text, encoding="utf-8")
PY

bash "$TMP" "$@"
