#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the reviewed Phase319 reconstruction wrapper immutable and repair only
# proven replay/orchestration mismatches as fail-closed transformations.
BASE_REF=2da61048dc896f3ab7fe2427997a10e53b944a4b
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

: "${GITHUB_REPOSITORY:?}"
curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/${BASE_REF}/scripts/319_regenerate_phase175_base.sh" \
  -o "$TMP"
test -s "$TMP"

# Repair the diagnostic verifier introduced at 2da61048. This changes only its
# self-check; the immutable Phase175 SHA256 gate remains byte-for-byte present.
python3 - "$TMP" <<'PYDIAGVERIFY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '''if old in text or text.count('cp "$OUT" /tmp/p319gki-phase175-regenerated.patch') != 1:
    raise SystemExit("Phase175 mismatch diagnostic insertion failed")
'''
new = '''if text.count('cp "$OUT" /tmp/p319gki-phase175-regenerated.patch') != 1:
    raise SystemExit("Phase175 mismatch diagnostic patch-copy insertion failed")
if text.count("Phase319 regeneration: Phase175 patch identity expected=%s actual=%s") != 1:
    raise SystemExit("Phase175 mismatch diagnostic identity-print insertion failed")
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"Phase175 diagnostic postcheck repair anchor expected 1, found {count}")
text = text.replace(old, new, 1)
if old in text or text.count(new) != 1:
    raise SystemExit("Phase175 diagnostic postcheck repair failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: Phase175 diagnostic postcheck repair PASS")
PYDIAGVERIFY

# Historical Workflow123 consumed Workflow99 artifact 8590238316. Its exact
# successful producer was HEAD 657612e0, and that source artifact includes the
# Workflow96 legacy-GDSC provider, Workflow97 UFS ICE-safe bringup, Workflow98
# UFS dependency instrumentation and Workflow99 RPMh mode-ABI correction.
# The reviewed reconstruction currently stops after 94b (which internally
# materializes Workflow95), so hydrate and execute only the four proven missing
# source stages here. This is reconstruction fidelity only; Phase319 observer
# source and the authoritative Phase175 SHA gate are untouched.
python3 - "$TMP" <<'PYWF99'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

run32 = "RUN32_REF=a51d9b5107821176950cb9a235ba953b95cd6e7c\n"
wf99 = "WF99_REF=657612e050f762d25d5bbe4eda91212364fb0cb6\n"
if text.count(run32) != 1 or wf99 in text:
    raise SystemExit("Workflow99 exact-ref insertion anchor mismatch")
text = text.replace(run32, run32 + wf99, 1)

r32_dir = 'R32="$WORK/run32-$RUN32_REF/scripts"\n'
wf99_dir = 'WF99="$WORK/workflow99-$WF99_REF/scripts"\n'
if text.count(r32_dir) != 1 or wf99_dir in text:
    raise SystemExit("Workflow99 script-directory insertion anchor mismatch")
text = text.replace(r32_dir, r32_dir + wf99_dir, 1)

mkdir_old = 'mkdir -p "$HIST" "$R32" "$STAGE" "$UP/drivers/interconnect/qcom" "$UP/include/dt-bindings/interconnect" "$(dirname "$OUT")"\n'
mkdir_new = 'mkdir -p "$HIST" "$R32" "$WF99" "$STAGE" "$UP/drivers/interconnect/qcom" "$UP/include/dt-bindings/interconnect" "$(dirname "$OUT")"\n'
if text.count(mkdir_old) != 1:
    raise SystemExit("Workflow99 mkdir insertion anchor mismatch")
text = text.replace(mkdir_old, mkdir_new, 1)

run32_comment = "# Run32 complete-source producer scripts, pinned to the surviving historical\n"
fetch_block = '''# Exact missing source stages from the Workflow99 artifact producer consumed by
# historical Workflow123. Stage94b already invokes its sibling Workflow95
# provider bridge, so only 96 through 99 are missing from the replay.
for name in \\
  96_stage_a52xq_legacy_gdsc_regulator.py \\
  97_stage_a52xq_ufs_ice_safe_bringup.py \\
  98_stage_a52xq_ufs_dependency_audit.py \\
  99_stage_a52xq_rpmh_mode_abi_fix.py; do
  fetch_script "$WF99_REF" "$name" "$WF99"
done

'''
if text.count(run32_comment) != 1 or fetch_block in text:
    raise SystemExit("Workflow99 script-fetch insertion anchor mismatch")
text = text.replace(run32_comment, fetch_block + run32_comment, 1)

old_exec = '''python3 "$HIST/94b_stage_a52xq_ufs_phy_bridge.py" --gki "$ROOT" --output "$STAGE/94b"

git -C "$ROOT" diff --check
printf '%s\\n' 'Phase319 regeneration: exact historical Workflow99 source staging PASS'
'''
new_exec = '''python3 "$HIST/94b_stage_a52xq_ufs_phy_bridge.py" --gki "$ROOT" --output "$STAGE/94b"
python3 "$WF99/96_stage_a52xq_legacy_gdsc_regulator.py" --gki "$ROOT" --output "$STAGE/96"
python3 "$WF99/97_stage_a52xq_ufs_ice_safe_bringup.py" --gki "$ROOT" --output "$STAGE/97"
python3 "$WF99/98_stage_a52xq_ufs_dependency_audit.py" --gki "$ROOT" --output "$STAGE/98"
python3 "$WF99/99_stage_a52xq_rpmh_mode_abi_fix.py" --gki "$ROOT" --output "$STAGE/99"

git -C "$ROOT" diff --check
printf '%s\\n' 'Phase319 regeneration: exact historical Workflow99 source staging PASS'
'''
if text.count(old_exec) != 1:
    raise SystemExit(f"Workflow99 execution insertion anchor expected 1, found {text.count(old_exec)}")
text = text.replace(old_exec, new_exec, 1)

checks = (
    'WF99_REF=657612e050f762d25d5bbe4eda91212364fb0cb6',
    '96_stage_a52xq_legacy_gdsc_regulator.py" --gki "$ROOT" --output "$STAGE/96"',
    '97_stage_a52xq_ufs_ice_safe_bringup.py" --gki "$ROOT" --output "$STAGE/97"',
    '98_stage_a52xq_ufs_dependency_audit.py" --gki "$ROOT" --output "$STAGE/98"',
    '99_stage_a52xq_rpmh_mode_abi_fix.py" --gki "$ROOT" --output "$STAGE/99"',
)
for token in checks:
    if text.count(token) != 1:
        raise SystemExit(f"Workflow99 replay verification failed for {token!r}")

path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: exact Workflow96-99 replay insertion PASS")
PYWF99

bash "$TMP" "$@"
