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
TMP="$(mktemp)"
BACKUP_DIR="$(mktemp -d)"

PRODUCER_MUTATORS=(
  scripts/154_apply_a52xq_failure_window_probe.py
  scripts/160_apply_a52xq_refgen_regulator.py
  scripts/164_apply_a52_refgen_critical_retention.py
  scripts/165_apply_a52_active_display_scopes.py
  scripts/166_apply_a52_qseecom_ta_heap19.py
  scripts/169_apply_a52_heap19_kernel_map.py
  scripts/171_audit_touchgrass_qseecom_contract.py
  scripts/174_apply_a52_combined_display_lifecycle.py
  scripts/175_apply_a52_display_bindcore.py
)

cleanup() {
  local rel base
  for rel in "${PRODUCER_MUTATORS[@]}"; do
    base="$(basename "$rel")"
    if [[ -f "$BACKUP_DIR/$base" ]]; then
      cp "$BACKUP_DIR/$base" "$rel"
    fi
  done
  rm -f "$TMP"
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT

# The authoritative Phase175 full-source patch was produced by a coherent set
# of mutators that existed together at commit 188f775. Several of those scripts
# were revised later, so replaying current-branch bytes cannot reproduce the
# historical patch identity. Hydrate the complete producer-era mutation window,
# execute the historical reconstruction, then restore the checkout.
for rel in "${PRODUCER_MUTATORS[@]}"; do
  base="$(basename "$rel")"
  test -f "$rel"
  cp "$rel" "$BACKUP_DIR/$base"
  curl -fL --retry 5 --retry-all-errors --silent --show-error \
    "${P319_REPO_RAW_PREFIX}${PHASE175_PRODUCER_REF}/${rel}" \
    -o "$rel"
  test -s "$rel"
  printf 'Phase319 regeneration: producer mutator %s sha256=%s\n' \
    "$rel" "$(sha256sum "$rel" | awk '{print $1}')"
done

grep -Fq 'driver_find(name, &platform_bus_type)' scripts/175_apply_a52_display_bindcore.py
printf '%s\n' "Phase319 regeneration: replaying complete producer-era Phase154-175 mutator set ${PHASE175_PRODUCER_REF}"

curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "${P319_REPO_RAW_PREFIX}${BASE_REF}/scripts/319_regenerate_phase175_base.sh" \
  -o "$TMP"
test -s "$TMP"
printf '%s\n' 'Phase319 regeneration: historical current-repo raw transport routed through Contents API'

# The 7fc51cf replay reconstructs Workflow99 manually. It fetched the historical
# Phase95 producer script but omitted its execution, jumping from Phase94b to
# Phase96. Workflow95 is source-mutating: it adds the Lagoon RPMh clock provider
# and Samsung downstream RPMh regulator bridge. Restore that exact stage at the
# historical boundary before Phase96. Keep all downstream identity checks intact.
python3 - "$TMP" <<'PYPHASE95'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = (
    "    'python3 \"$HIST/94b_stage_a52xq_ufs_phy_bridge.py\" --gki \"$ROOT\" --output \"$STAGE/94b\"\\n'\n"
    "    'python3 \"$WF99/96_stage_a52xq_legacy_gdsc_regulator.py\" --gki \"$ROOT\" --output \"$STAGE/96\"\\n'\n"
)
new = (
    "    'python3 \"$HIST/94b_stage_a52xq_ufs_phy_bridge.py\" --gki \"$ROOT\" --output \"$STAGE/94b\"\\n'\n"
    "    'python3 \"$HIST/95_stage_a52xq_rpmh_provider_bridge.py\" --gki \"$ROOT\" --output \"$STAGE/95\"\\n'\n"
    "    'python3 \"$WF99/96_stage_a52xq_legacy_gdsc_regulator.py\" --gki \"$ROOT\" --output \"$STAGE/96\"\\n'\n"
)
if text.count(old) != 1:
    raise SystemExit(f"Workflow95 execution insertion anchor expected 1, found {text.count(old)}")
if 'python3 "$HIST/95_stage_a52xq_rpmh_provider_bridge.py" --gki "$ROOT" --output "$STAGE/95"' in text:
    raise SystemExit("Workflow95 execution unexpectedly already present")
text = text.replace(old, new, 1)
if text.count('95_stage_a52xq_rpmh_provider_bridge.py') != 1:
    raise SystemExit("Workflow95 execution insertion verification failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: restored exact historical Workflow95 RPMh provider stage")
PYPHASE95

bash "$TMP" "$@"
