#!/usr/bin/env bash
set -Eeuo pipefail

# Phase319 retention-safe Phase175 reconstruction.
#
# The direct Phase174 and Phase175 Actions artifacts have expired. Reuse the
# already-reviewed full historical replay from 00a36a2, but correct its only
# known late-producer mistake: it hydrated the Phase154-175 mutation window from
# 188f775, which belongs to failed Phase175 attempts. The successful Phase175
# producer was run 30347373944 at head 040a2fabae74dc24c95c37eda4d2aed29edad916.
# That successful revision deliberately removed the private driver_find() audit
# which changes the cumulative source patch bytes.
#
# The historical replay below still owns the immutable Phase175 SHA256 identity
# gate. This wrapper changes producer provenance only and does not weaken any
# source identity check or alter the Phase319 observer.
# Phase181 is replayed by the caller with sysfs/probe-completion anchors scoped
# specifically to really_probe(), preserving other driver_sysfs_add call sites.
# Phase182 is replayed by the caller with the historical link declaration
# restored when the retained hybrid preimage contains only the function body.

REPLAY_REF=00a36a285627e293cb4a3f9813717fa136d5deda
FAILED_PHASE175_REF=188f775518c298021339791de7bcea5f5ce94d76
SUCCESS_PHASE175_REF=040a2fabae74dc24c95c37eda4d2aed29edad916
PHASE180_AUDIT_SHA256=e4102aa4d0a98a18f5c689e5b9e515c01ad0dce39f0692323157ded4f6417043
TMP="$(mktemp)"
META="$(mktemp)"
trap 'rm -f "$TMP" "$META"' EXIT

: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"

# Phase319's retained replay advances through Phase180 after the known Phase175
# mismatch is reproduced. Fail closed here unless the recovered historical
# public-API audit source is byte-exact before spending time on the replay.
test -s scripts/180_a52_display_bind_audit.c
printf '%s  %s\n' "$PHASE180_AUDIT_SHA256" scripts/180_a52_display_bind_audit.c | sha256sum -c -

# Fetch the reviewed replay through the authenticated Contents API so old raw
# commit URLs are not themselves subject to the historical 410 behavior.
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/scripts/319_regenerate_phase175_base.sh?ref=${REPLAY_REF}" \
  --output "$META"

python3 - "$META" "$TMP" "$FAILED_PHASE175_REF" "$SUCCESS_PHASE175_REF" <<'PY'
import base64
import json
from pathlib import Path
import sys

meta = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
if meta.get('encoding') != 'base64' or not isinstance(meta.get('content'), str):
    raise SystemExit('Phase319 replay fetch did not return inline base64 content')

text = base64.b64decode(meta['content']).decode('utf-8')
failed_ref = sys.argv[3]
success_ref = sys.argv[4]

old_ref = f'PHASE175_PRODUCER_REF={failed_ref}'
new_ref = f'PHASE175_PRODUCER_REF={success_ref}'
if text.count(old_ref) != 1:
    raise SystemExit(f'Phase175 producer ref anchor expected 1, found {text.count(old_ref)}')
text = text.replace(old_ref, new_ref, 1)

old_check = "grep -Fq 'driver_find(name, &platform_bus_type)' scripts/175_apply_a52_display_bindcore.py"
new_check = "grep -Fq 'driver_find(name, &platform_bus_type) is deliberately not used here' scripts/175_apply_a52_display_bindcore.py"
if text.count(old_check) != 1:
    raise SystemExit(f'Phase175 producer semantic check anchor expected 1, found {text.count(old_check)}')
text = text.replace(old_check, new_check, 1)

# Keep the wrapper diagnostics truthful. These comment replacements are not
# semantic, but make any future failure log unambiguous about producer history.
text = text.replace(
    'mutators that existed together at commit 188f775. Several of those scripts',
    'mutators present at the successful Phase175 producer commit. Several scripts',
    1,
)
text = text.replace(
    'execute the historical reconstruction, then restore the checkout.',
    'execute the historical reconstruction, then restore the checkout.',
    1,
)

Path(sys.argv[2]).write_text(text, encoding='utf-8')
PY

test -s "$TMP"
grep -Fq "PHASE175_PRODUCER_REF=${SUCCESS_PHASE175_REF}" "$TMP"
! grep -Fq "PHASE175_PRODUCER_REF=${FAILED_PHASE175_REF}" "$TMP"
grep -Fq "driver_find(name, &platform_bus_type) is deliberately not used here" "$TMP"

printf '%s\n' "Phase319 regeneration: retention-safe historical replay ${REPLAY_REF}"
printf '%s\n' "Phase319 regeneration: successful Phase175 producer ${SUCCESS_PHASE175_REF}"
printf '%s\n' 'Phase319 regeneration: immutable expected Phase175 SHA256 remains fail-closed in historical replay'

bash "$TMP" "$@"
