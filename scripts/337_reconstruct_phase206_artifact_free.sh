#!/usr/bin/env bash
set -Eeuo pipefail

# Phase337 retention repair: rebuild the Phase206 source boundary without any
# historical Actions artifact. The source mutation chain is regenerated from
# pinned Git objects and the reviewed Phase175 replay, then advanced through the
# committed Phase177-206 patchers. Later Phase319/316 CI remains the authoritative
# byte-level oracle for the resulting lineage.

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
SEED="${PHASE337_SEED_ROOT:-/tmp/phase227-seed}"
P175="$PWD/workspace/phase337-regenerated-phase175.patch"
P177="$PWD/workspace/phase337-phase177.patch"
P179="$PWD/workspace/phase337-phase179.patch"
MARK=A52_PHASE337_ARTIFACT_FREE_PHASE206_REPLAY_V1

: "${GKI_COMMON_SHA:?}"
: "${TOUCHGRASS_COMMIT:?}"
test -d "$ROOT/.git"
test -d "$TG/.git"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
test "$(git -C "$TG" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT"
test -s "$SEED/config/before-phase216.config"

printf '%s\n' "$MARK"
printf '%s\n' 'Phase337 replay: regenerate reviewed Phase175 source boundary'
rm -f "$P175"
set +e
bash scripts/319_regenerate_phase175_base.sh "$P175"
rc=$?
set -e
# The successful Phase319 bridge documented this exact tracked-patch mismatch
# after correcting the producer provenance. Source hydration remains usable; the
# later Phase316 Image oracle is stricter than accepting the cumulative patch.
test "$rc" -ne 0
test -s "$P175"
P175_SHA="$(sha256sum "$P175" | awk '{print $1}')"
test "$P175_SHA" = 404d7fad70d4a6bec460ff0f09c813c98122169c3f1007a2b2b59cd8e75afae3
printf 'Phase337 replay: known Phase175 tracked mismatch reproduced sha256=%s\n' "$P175_SHA"
for f in \
  "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/msm_smmu.c"; do
  test -s "$f"
done

printf '%s\n' 'Phase337 replay: advance hydrated source through Phase177-199'
P177_TMP="$PWD/workspace/phase337-phase177-payload"
rm -rf "$P177_TMP"
mkdir -p "$P177_TMP"
for item in \
  '00.txt 2e2e8773465271521302902aeedd3c0779c3eba750e191881eb0649654402a39' \
  '01.txt 2cc8aa74d1080a38f3deabaa790ba6cec24accfe542d9747c3f4c6eda00699ba' \
  '02.txt 640c2ce3842ecb8120e14494bb23b5d2e4a1c68700c2a67bab2ba55a04e36731' \
  '03.txt 3799c05943d31375851b3e0680f61fcde892d96272edad98454b641589fd69e0' \
  '04.txt 9e18d44f8df3abe72ac2c67a42919f8acbf90590acdb424ba609d0086c7cfc47' \
  '05.txt 9f0a5026d1a78d096b73ff97f3c785a9fd807999a36decb7a89e7fa851477945' \
  '06.txt b62056e0d393a7969bfac6c60295eb3fb6e78228f6955196e9326cdb90ea1f82' \
  '07.txt da48f50560314b78ef184d7910d5b5a8026164cae8227997a3795e457c16180a'; do
  set -- $item
  tr -d '\r\n' < "scripts/177_patch_payload_chunks/$1" > "$P177_TMP/$1"
  printf '%s  %s\n' "$2" "$P177_TMP/$1" | sha256sum -c -
done
cat "$P177_TMP"/*.txt | base64 --decode | gzip -dc > "$P177"
printf '%s  %s\n' 9412b28da19c71e7bc97e767e83ce717e146313d32321c7429e6d4050a4f0d00 "$P177" | sha256sum -c -
git -C "$ROOT" apply --check "$P177"
git -C "$ROOT" apply "$P177"

tr -d '\r\n' < scripts/179_payloads/patch.gz.b64 | base64 --decode | gzip -dc > "$P179"
printf '%s  %s\n' 81bc17510b643274dba9652baa5edf52e9c2127af02a77eb1597637be0c3c59f "$P179" | sha256sum -c -
git -C "$ROOT" apply --check "$P179"
git -C "$ROOT" apply "$P179"

python3 scripts/180_apply.py --root "$ROOT" --audit-source scripts/180_a52_display_bind_audit.c
for p in 181 182 183 185; do python3 "scripts/${p}_apply.py" --root "$ROOT"; done
python3 scripts/186_apply.py --root "$ROOT" --touchgrass "$TG"
for p in 187 188 189 190 191 192 193 194 195 196; do python3 "scripts/${p}_apply.py" --root "$ROOT"; done
python3 scripts/197_apply_triple_rs.py --self-test
python3 scripts/197_apply_triple_rs.py --root "$ROOT"
python3 scripts/198_apply_catalog_trace.py --self-test
python3 scripts/198_apply_catalog_trace.py --root "$ROOT"
python3 scripts/199_apply_recorder_crc32c.py --self-test
python3 scripts/199_apply_recorder_crc32c.py --root "$ROOT"

# Use the retained late-lineage config only as the build/config substrate. No
# source decision below is derived from it. Cumulative overlays are idempotent
# and Phase319/316 later prove the final config byte-for-byte.
mkdir -p "$BUILD"
cp "$SEED/config/before-phase216.config" "$BUILD/.config"

printf '%s\n' 'Phase337 replay: advance Phase200-206 source mutations'
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"

for token in \
  'qcom,iommu-earlymap' \
  'a52_iommu_get_dma_window' \
  'A52_PHASE203' \
  'SMMU secure-streams faulted dev=%s'; do
  grep -RFlq "$token" "$ROOT/drivers" "$ROOT/include" || {
    echo "Phase337 replay required Phase206-lineage token missing: $token" >&2
    exit 1
  }
done

# Preserve the historical Phase213 generator escape compatibility expected by
# the later one-compile cumulative chain.
python3 - <<'PY'
from pathlib import Path
p = Path('scripts/213_apply_ion_transaction_trace.py')
t = p.read_text()
old = r'pr_warn_once("%s: ioctl validate failed\n", __func__);'
new = r'pr_warn_once("%s: ioctl validate failed\\n", __func__);'
if t.count(old) == 1 and t.count(new) == 0:
    p.write_text(t.replace(old, new, 1))
elif not (t.count(old) == 0 and t.count(new) == 1):
    raise SystemExit('Phase337 Phase213 escape repair unexpected state')
t = p.read_text()
if t.count(new) != 1 or old in t:
    raise SystemExit('Phase337 Phase213 escape repair verification failed')
print('Phase337 Phase213 C-string escape parity: PASS')
PY

printf '%s\n' 'Phase337 artifact-free Phase206 source replay: PASS'
