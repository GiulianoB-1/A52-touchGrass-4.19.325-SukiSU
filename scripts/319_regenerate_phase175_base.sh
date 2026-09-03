#!/usr/bin/env bash
set -Eeuo pipefail

# Historical Phase319 replay wrappers recursively fetch pinned scripts from this
# repository through raw.githubusercontent.com. GitHub Actions now returns 410
# for some old commit-backed raw URLs even though the exact Git objects remain
# available through the authenticated Contents API. Route only current-repo raw
# URLs whose ref is an exact 40-hex commit through that API. All other curl use,
# including external TouchGrass and Actions artifact downloads, remains unchanged.
#
# This is transport-only reconstruction compatibility. It does not change any
# historical script bytes, kernel source semantics, Phase319 observer behavior,
# or the authoritative Phase175 SHA256 identity gate.

P319_REPO_RAW_PREFIX="https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/"

curl() {
  local args=("$@")
  local url="" out=""
  local i next

  for ((i=0; i<${#args[@]}; i++)); do
    case "${args[$i]}" in
      -o|--output)
        next=$((i + 1))
        if (( next < ${#args[@]} )); then
          out="${args[$next]}"
        fi
        ;;
      http://*|https://*)
        url="${args[$i]}"
        ;;
    esac
  done

  if [[ "$url" == "${P319_REPO_RAW_PREFIX}"* ]]; then
    local rest="${url#${P319_REPO_RAW_PREFIX}}"
    local ref="${rest%%/*}"
    local rel="${rest#*/}"
    if [[ "$ref" =~ ^[0-9a-f]{40}$ ]] && [[ "$rel" != "$rest" ]]; then
      local api="https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/${rel}?ref=${ref}"
      local json
      json="$(mktemp)"
      if ! command curl -fL --retry 5 --retry-all-errors --silent --show-error \
          -H "Authorization: Bearer ${GH_TOKEN}" \
          -H 'Accept: application/vnd.github+json' \
          "$api" -o "$json"; then
        rm -f "$json"
        return 1
      fi
      if [[ -n "$out" ]]; then
        python3 - "$json" "$out" <<'PY'
import base64
import json
from pathlib import Path
import sys

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
content = meta.get("content")
encoding = meta.get("encoding")
if encoding != "base64" or not isinstance(content, str):
    raise SystemExit("GitHub Contents API did not return inline base64 content")
Path(sys.argv[2]).write_bytes(base64.b64decode(content))
PY
      else
        python3 - "$json" <<'PY'
import base64
import json
from pathlib import Path
import sys

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
content = meta.get("content")
encoding = meta.get("encoding")
if encoding != "base64" or not isinstance(content, str):
    raise SystemExit("GitHub Contents API did not return inline base64 content")
sys.stdout.buffer.write(base64.b64decode(content))
PY
      fi
      rm -f "$json"
      return 0
    fi
  fi

  command curl "${args[@]}"
}
export -f curl
export P319_REPO_RAW_PREFIX

BASE_REF=7fc51cf40eb04a98da81d5da619160c4fbaa3a90
PHASE175_PRODUCER_REF=188f775518c298021339791de7bcea5f5ce94d76
PHASE175_SCRIPT=scripts/175_apply_a52_display_bindcore.py
TMP="$(mktemp)"
PHASE175_BACKUP="$(mktemp)"

cleanup() {
  if [[ -s "$PHASE175_BACKUP" ]]; then
    cp "$PHASE175_BACKUP" "$PHASE175_SCRIPT"
  fi
  rm -f "$TMP" "$PHASE175_BACKUP"
}
trap cleanup EXIT

# The current branch carries the later no-driver_find Phase175 patcher. The
# original Phase175 workflow that produced the authoritative bindcore source
# patch required driver_find(name, &platform_bus_type), and commit 188f775 is
# the matching producer-era patcher revision. Replay that exact script only for
# source reconstruction, then restore the checkout before returning.
cp "$PHASE175_SCRIPT" "$PHASE175_BACKUP"
curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "${P319_REPO_RAW_PREFIX}${PHASE175_PRODUCER_REF}/${PHASE175_SCRIPT}" \
  -o "$PHASE175_SCRIPT"
test -s "$PHASE175_SCRIPT"
grep -Fq 'driver_find(name, &platform_bus_type)' "$PHASE175_SCRIPT"
printf '%s\n' "Phase319 regeneration: replaying producer-era Phase175 patcher ${PHASE175_PRODUCER_REF}"

curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "${P319_REPO_RAW_PREFIX}${BASE_REF}/scripts/319_regenerate_phase175_base.sh" \
  -o "$TMP"
test -s "$TMP"
printf '%s\n' 'Phase319 regeneration: historical current-repo raw transport routed through Contents API'

bash "$TMP" "$@"
