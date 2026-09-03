#!/usr/bin/env bash
set -Eeuo pipefail

# Phase319 needs the exact historical Phase175 cumulative source patch. Replaying
# every pre-175 mutator from later checkout revisions proved semantically correct
# but byte-inexact. Reconstruct from the immutable Phase174 lifecycle artifact
# that the original successful Phase175 workflow consumed, then execute the exact
# Phase175 producer mutator and retain the authoritative final SHA256 gate.

ROOT="$PWD/gki/common"
OUT="${1:?usage: 319_regenerate_phase175_base.sh OUTPUT_PATCH}"
WORK="$(mktemp -d)"
ARTIFACT_ZIP="$WORK/phase174.zip"
EXTRACTED="$WORK/phase174"
STAGE="$WORK/phase175-stage"
P175_SCRIPT="$WORK/175_apply_a52_display_bindcore.py"

LIFECYCLE_ARTIFACT_ID=8669625459
LIFECYCLE_ARTIFACT_SHA256=e6b0e8223a061e379b2e98deba3937b1f68d0360b89206776e595b68cd411826
PHASE175_PRODUCER_REF=188f775518c298021339791de7bcea5f5ce94d76
PHASE175_PATCH_SHA256=8604330234635526495004951ac27a9dd6d091f5c7dc19cf6ece90425a5a6b1f

cleanup() {
  rm -rf "$WORK"
}
trap cleanup EXIT

: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
: "${GKI_COMMON_SHA:?}"
test -d "$ROOT/.git"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
mkdir -p "$EXTRACTED" "$STAGE" "$(dirname "$OUT")"

printf '%s\n' "Phase319 regeneration: downloading exact Phase174 artifact ${LIFECYCLE_ARTIFACT_ID}"
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${LIFECYCLE_ARTIFACT_ID}/zip" \
  --output "$ARTIFACT_ZIP"
printf '%s  %s\n' "$LIFECYCLE_ARTIFACT_SHA256" "$ARTIFACT_ZIP" | sha256sum -c -
unzip -q "$ARTIFACT_ZIP" -d "$EXTRACTED"
(
  cd "$EXTRACTED"
  sha256sum -c SHA256SUMS
)

P174="$EXTRACTED/stage/heap19-display-lifecycle-source.patch"
test -s "$P174"
grep -Fq '"persistent_profile": "heap19-bufops-display-lifecycle-v1"' "$EXTRACTED/final-audit.json"
test "$(tr -d '\r\n' < "$EXTRACTED/compile/make-return-code.txt")" = 0
printf '%s\n' 'Phase319 regeneration: exact Phase174 lifecycle artifact verified'

# Fetch the exact Phase175 producer script through the authenticated Contents API.
# This avoids raw.githubusercontent.com retention/410 behavior for old commit refs.
META="$WORK/phase175-script.json"
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/scripts/175_apply_a52_display_bindcore.py?ref=${PHASE175_PRODUCER_REF}" \
  --output "$META"
python3 - "$META" "$P175_SCRIPT" <<'PY'
import base64
import json
from pathlib import Path
import sys

meta = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
if meta.get('encoding') != 'base64' or not isinstance(meta.get('content'), str):
    raise SystemExit('Phase175 producer script fetch did not return inline base64 content')
Path(sys.argv[2]).write_bytes(base64.b64decode(meta['content']))
PY
test -s "$P175_SCRIPT"
python3 -m py_compile "$P175_SCRIPT"
python3 "$P175_SCRIPT" --self-test >/dev/null
grep -Fq 'driver_find(name, &platform_bus_type)' "$P175_SCRIPT"
printf 'Phase319 regeneration: exact Phase175 producer script sha256=%s\n' \
  "$(sha256sum "$P175_SCRIPT" | awk '{print $1}')"

# Reproduce the original Phase175 source construction exactly from its proven
# Phase174 input boundary.
git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" clean -fd
git -C "$ROOT" apply --check "$P174"
git -C "$ROOT" apply "$P174"
python3 "$P175_SCRIPT" --gki "$ROOT" --output "$STAGE"

grep -Fq '"status": "a52-display-bindcore-v1-staged"' "$STAGE/phase33-a52-display-bindcore-report.json"
grep -Fq 'profile=heap19-bufops-display-bindcore-v1' "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
grep -Fq 'DISP bind reg=msm_drm rc=%d' "$ROOT/drivers/a52_display/msm/msm_drv.c"
grep -Fq 'DISP bind reg=dsi_display rc=%d' "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
grep -Fq 'late_initcall(a52_display_bind_audit_init);' "$ROOT/drivers/a52_secure/a52_display_bind_audit.c"
git -C "$ROOT" diff --check
git -C "$ROOT" add -N .
git -C "$ROOT" diff --binary --no-ext-diff > "$OUT"
test -s "$OUT"

ACTUAL="$(sha256sum "$OUT" | awk '{print $1}')"
printf 'Phase319 regeneration: Phase175 patch identity expected=%s actual=%s\n' \
  "$PHASE175_PATCH_SHA256" "$ACTUAL"
if [[ "$ACTUAL" != "$PHASE175_PATCH_SHA256" ]]; then
  cp "$OUT" /tmp/p319gki-phase175-regenerated.patch
  printf '%s\n' 'Phase319 regeneration: exact Phase174 -> Phase175 identity mismatch' >&2
  exit 1
fi

printf '%s  %s\n' "$PHASE175_PATCH_SHA256" "$OUT" | sha256sum -c -
cp "$OUT" /tmp/p319gki-phase175-regenerated.patch
printf '%s\n' 'Phase319 regeneration: exact historical Phase174 -> Phase175 replay PASS'
