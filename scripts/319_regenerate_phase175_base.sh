#!/usr/bin/env bash
set -Eeuo pipefail

# Preserve the reviewed exact-Run32 reconstruction at 7fc51cf, but repair its
# access path for the two PR-merge script blobs. GitHub's raw endpoint returns
# 410 for that old ephemeral merge commit even though the repository Contents
# API still serves the exact blobs. Keep the historical bytes and all identity
# gates unchanged; only route RUN32_EXEC_REF fetches through the Contents API.
BASE_REF=7fc51cf40eb04a98da81d5da619160c4fbaa3a90
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
anchor = '    "\\n# Exact Workflow140 run32 producer scripts from PR merge 46c95749.\\n"\n'
insertion = r'''    'eval "$(declare -f fetch_script | sed \'1s/^fetch_script /fetch_script_raw /\')"\n'
    'fetch_script() {\n'
    '  if [[ "$1" != "$RUN32_EXEC_REF" ]]; then fetch_script_raw "$@"; return; fi\n'
    '  local ref="$1" name="$2" out_dir="$3"\n'
    '  mkdir -p "$out_dir"\n'
    '  curl -fL --retry 5 --retry-all-errors --silent --show-error \\\n'
    '    -H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github+json" \\\n'
    '    "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/scripts/${name}?ref=${ref}" \\\n'
    '    | python3 -c \'import base64,json,pathlib,sys; j=json.load(sys.stdin); pathlib.Path(sys.argv[1]).write_bytes(base64.b64decode(j["content"]))\' "$out_dir/$name"\n'
    '  test -s "$out_dir/$name"\n'
    '}\n'
'''
if text.count(anchor) != 1:
    raise SystemExit(f"Run32 exact-fetch insertion anchor expected 1, found {text.count(anchor)}")
text = text.replace(anchor, anchor + insertion, 1)
if text.count('api.github.com/repos/${GITHUB_REPOSITORY}/contents/scripts/${name}?ref=${ref}') != 1:
    raise SystemExit("Run32 Contents-API route insertion failed")
if text.count('fetch_script "$RUN32_EXEC_REF" "141_apply_a52xq_ack_secure_parameter_probe.py" "$R32"') != 2:
    raise SystemExit("Run32 141 exact-fetch call/check identity changed unexpectedly")
if text.count('fetch_script "$RUN32_EXEC_REF" "143_run_a52xq_early_mirrored_boot_probe.py" "$R32"') != 2:
    raise SystemExit("Run32 143 exact-fetch call/check identity changed unexpectedly")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: old PR-merge exact blobs routed through Contents API")
PY

bash "$TMP" "$@"
